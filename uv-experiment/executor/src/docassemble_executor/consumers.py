"""Independent launch boundary for supervised and manual runtime consumers."""
from __future__ import annotations

from contextlib import ExitStack
import fcntl
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .admission import Admission
from .authority import RuntimeLock, guarded_popen
from .capabilities import Capabilities, ENV, current
from .paths import ExecutorPaths
from .startup import managed_requested

TEMPFAIL = 75


def run_consumer(paths: ExecutorPaths, command: list[str], *, wait: bool = False,
                 observer: bool = False, singleton: Path | None = None,
                 log_file: Path | None = None, accepted_work: bool = True,
                 allow_nested: bool = True, launch: bool = False) -> int:
    """Jobs finish naturally; only the fixed watchdog observer may be signalled.

    Supervisor may kill its wrapper; the session-independent guardian retains
    both leases until all payload descendants exit. Waiting imports no runtime.
    """
    if not managed_requested(os.environ.get('DA_UV_MANAGED') == 'true', paths.state_dir):
        raise ValueError('managed consumer launch requires DA_UV_MANAGED=true')
    if os.geteuid() == 0:
        # Root retains crash authority, never executes arbitrary runtime or
        # third-party helper code as the payload's account.
        command = ['/usr/sbin/runuser', '-u', 'www-data', '--'] + command
    admission = Admission(paths.state_dir)
    stopping = False

    def request_stop(*args):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    while not stopping:
        with ExitStack() as leases:
            capability = None
            if accepted_work and not observer:
                capability = leases.enter_context(Capabilities(paths.state_dir).accept(
                    current() if allow_nested else '', launch=launch))
                traffic_fd = capability.traffic_fd if capability else None
            else:
                traffic_fd = leases.enter_context(admission.lease(consumer=True, launch=launch))
            if traffic_fd is not None:
                process = _launch(paths, command, traffic_fd, singleton, log_file, capability)
                while process is not None and process.poll() is None:
                    if observer and (stopping or not _role_admission(admission)):
                        process.terminate()
                        # A stuck observer is not killed; its lease causes a
                        # pre-mutation drain abort just like any other survivor.
                        return process.wait()
                    time.sleep(0.05)
                if process is not None:
                    return process.returncode
        if not wait:
            print('managed consumer admission or singleton authority is unavailable; retry after normal service resumes', file=sys.stderr)
            return TEMPFAIL
        time.sleep(0.05)
    return 0


def _launch(paths: ExecutorPaths, command: list[str], traffic_fd: int,
            singleton: Path | None, log_file: Path | None, capability):
    with ExitStack() as leases:
        extra_fds: tuple[int, ...] = (traffic_fd,) + (capability.fds if capability else ())
        environment = dict(os.environ, **{ENV: capability.name if capability else ''})
        if singleton is not None:
            try:
                slot = leases.enter_context(singleton.open('r'))
                fcntl.flock(slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return None  # A surviving worker tree, or missing authority, fences launch.
            extra_fds += (slot.fileno(),)
        runtime_fd = leases.enter_context(RuntimeLock(paths.state_dir, shared=True))
        if log_file is not None:
            # Surviving workers must not inherit Supervisor's disposable pipes.
            # Celery's warm signal handler writes synchronously to stdout.
            output = leases.enter_context(log_file.open('a'))
            return guarded_popen(command, runtime_fd, extra_fds=extra_fds, graceful=True,
                                 stdout=output, stderr=subprocess.STDOUT, env=environment)
        return guarded_popen(command, runtime_fd, extra_fds=extra_fds, graceful=True, env=environment)


def _role_admission(admission: Admission) -> bool:
    state = admission.state()
    return state['mode'] == 'preparing' or (state['mode'] == 'open' and state.get('consumers') is True)


def run_worker(paths: ExecutorPaths, kind: str, *, wait: bool = False) -> int:
    if kind not in ('normal', 'single'):
        raise ValueError('unknown worker kind')
    os.environ.update(HOME='/var/www', DA_CONFIG_FILE=str(paths.config_file),
                      PYTHONDONTWRITEBYTECODE='1')
    os.environ['PATH'] = str(paths.runtime_venv / 'bin') + ':' + os.environ.get('PATH', '')
    command = [str(paths.runtime_venv / 'bin/python'),
               str(Path(__file__).with_name('worker_runtime.py')), '--kind', kind]
    return run_consumer(paths, command, wait=wait, allow_nested=False, launch=True,
                        singleton=paths.state_dir / ('worker-' + kind + '.lock'),
                        log_file=paths.log_dir / ('managed-worker-' + kind + '.log'))


def run_service(paths: ExecutorPaths, kind: str, *, wait: bool = False) -> int:
    if kind not in ('websockets', 'nltk', 'conversion'):
        raise ValueError('unknown service kind')
    os.environ.update(HOME='/var/www', DA_CONFIG_FILE=str(paths.config_file),
                      PYTHONDONTWRITEBYTECODE='1')
    if kind == 'nltk' and paths.config_file.is_file():
        from .config import load_config
        config = load_config(paths.config_file)
        os.environ['NLTKSOCKET'] = str(config.get('nltk socket') or '/var/run/nltk/da_nltk.sock')
    # Conversion is OS-owned, including UNO/LibreOffice and its Python bindings.
    python = '/usr/bin/python3' if kind == 'conversion' else str(paths.runtime_venv / 'bin/python')
    command = [python, str(Path(__file__).with_name('service_runtime.py')), '--kind', kind]
    return run_consumer(paths, command, wait=wait, accepted_work=False, launch=True,
                        singleton=paths.state_dir / ('service-' + kind + '.lock'),
                        log_file=paths.log_dir / ('managed-service-' + kind + '.log'))


def run_watchdog(paths: ExecutorPaths) -> int:
    command = [str(paths.runtime_venv / 'bin/python'),
               str(Path(__file__).with_name('watchdog_runtime.py'))]
    return run_consumer(paths, command, wait=True, observer=True, launch=True,
                        singleton=paths.state_dir / 'watchdog.lock')
