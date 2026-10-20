"""Deliberate operator-only local installation, never a published revision."""
from __future__ import annotations

import json
import os
from pathlib import Path
import pwd
import shutil
import signal
import subprocess
import tempfile
import time
import uuid

from .admission import Admission
from .authority import MutationLock, RuntimeLock, guarded_popen
from .consumers import TEMPFAIL
from .paths import ExecutorPaths
from .startup import managed_requested


def install(paths: ExecutorPaths, source: Path) -> int:
    if not managed_requested(os.environ.get('DA_UV_MANAGED') == 'true', paths.state_dir):
        raise ValueError('development override requires managed mode')
    with MutationLock(paths.state_dir, wait_seconds=5):
        gate = Admission(paths.state_dir).state()
        if gate['mode'] != 'open' or gate.get('consumers') is not True:
            return TEMPFAIL
        from .controller import (_mark_ready, _persist, _start_web, _write_status,
                                 enter_maintenance, load_context, status)
        from .health import probe_runtime
        from .journal import Operation
        from .startup import validate_startup
        from .status import MODE_MAINTENANCE, MODE_NORMAL

        if status(paths).get('mode') != MODE_NORMAL:
            return TEMPFAIL
        if os.geteuid() != 0:
            raise ValueError('development installation requires the operator/root executor, not the runtime account')
        user = os.environ.get('DA_ENVIRONMENT_USER', '')
        account = pwd.getpwnam(user)
        if account.pw_uid == 0:
            raise ValueError('development builds must never run as root')
        source = source.resolve(strict=True)
        context = load_context(paths, start_infrastructure=False)
        validate_startup(context)
        if not enter_maintenance(context):
            return 3
        context.journal.begin_mutation(Operation(
            id=uuid.uuid4().hex, kind='development-override',
            revision=context.applied.revision or 'unknown', previous_revision=context.applied.revision))
        _persist(context)
        _write_status(context, MODE_MAINTENANCE, detail='development override in progress; runtime state unknown')
        code = _install_source(paths, source, user)
        if code == 0:
            probe = probe_runtime(paths.runtime_venv, paths.config_file)
            if probe.ok and _start_web(context, recovery=False):
                from .roles import prepare_roles
                role_checks = prepare_roles(paths)
                if not all(check.ok for check in role_checks):
                    Admission(paths.state_dir).close('development all-role health failed')
                    context.journal.record_failure('development-override', 'development all-role health failed; runtime state unknown')
                    _persist(context)
                    _write_status(context, MODE_MAINTENANCE, role_checks=role_checks, detail='development all-role health failed')
                    return 1
                context.journal.record_override()
                _persist(context)
                _write_status(context, MODE_NORMAL, probe_checks=probe.checks, role_checks=role_checks,
                              detail='deliberate development override; recorded lock is not applied health')
                Admission(paths.state_dir).open(consumers=True)
                _mark_ready(paths)
                return 0
        context.journal.record_failure('development-override', 'development installation or health check failed; runtime state unknown')
        _persist(context)
        _write_status(context, MODE_MAINTENANCE, detail='development override failed; explicit recovery or restart required')
        return 1


def reload_web(paths: ExecutorPaths) -> int:
    """Only signal the existing gunicorn master; readers/accepted work stay live."""
    from .controller import _web_alive, status
    if not managed_requested(os.environ.get('DA_UV_MANAGED') == 'true', paths.state_dir):
        raise ValueError('managed ordinary reload requires managed mode')
    with MutationLock(paths.state_dir, wait_seconds=5):
        gate = Admission(paths.state_dir).state()
        if gate['mode'] != 'open' or gate.get('consumers') is not True or status(paths).get('mode') != 'normal':
            return TEMPFAIL
        if not _web_alive(paths, 'normal'):
            return TEMPFAIL
        from .webapp import http_get
        def application_start():
            code, body = http_get(paths.backend_port, '/health_status', timeout=2)
            try:
                return json.loads(body)['server_start_time'] if code == 200 else None
            except (ValueError, KeyError, TypeError):
                return None
        previous = application_start()
        if previous is None:
            return TEMPFAIL
        pid = int((paths.run_dir / 'web-normal.pid').read_text())
        os.kill(pid, signal.SIGHUP)  # Guardian forwards to master, never its worker group.
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            started = application_start()
            if started is not None and started != previous:
                return 0  # Actual new application worker answered; old accepted work may still finish.
            time.sleep(0.1)
        return TEMPFAIL


def _install_source(paths: ExecutorPaths, source: Path, user: str) -> int:
    account = pwd.getpwnam(user)
    staging = Path(tempfile.mkdtemp(prefix='development-', dir=account.pw_dir))
    target = staging / source.name
    process = None
    try:
        if source.is_dir():
            shutil.copytree(source, target)  # Dereference the staged operator source.
        else:
            shutil.copy2(source, target)
        for path in [staging, *staging.rglob('*')]:
            os.chown(path, account.pw_uid, account.pw_gid)
        environment = dict(os.environ, HOME=account.pw_dir,
                           PATH='/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
                           UV_CACHE_DIR=str(paths.cache_dir), UV_PYTHON_DOWNLOADS='never',
                           PYTHONDONTWRITEBYTECODE='1', DA_ACCEPTED_CALLER='')
        for name in ('VIRTUAL_ENV', 'VIRTUAL_ENV_PROMPT', 'PYTHONPATH'):
            environment.pop(name, None)
        command = ['/usr/sbin/runuser', '-u', user, '--', paths.uv_binary, 'pip', 'install',
                   '--python', str(paths.runtime_venv / 'bin/python'), '--no-deps', '--reinstall', str(target)]
        # Use a stable output file, never disposable exec/Supervisor pipes.
        with RuntimeLock(paths.state_dir) as runtime_fd, (paths.log_dir / 'development-install.log').open('ab') as output:
            from .authority import mutation_fd
            process = guarded_popen(command, mutation_fd(), extra_fds=(runtime_fd,),
                                    cwd=staging, env=environment, stdout=output, stderr=subprocess.STDOUT)
            return process.wait()  # Includes detached/FD-closing descendants, not just uv.
    finally:
        if process is None or process.poll() is not None:
            shutil.rmtree(staging)
        # Controller interruption retains staging and both guardian-held locks.
