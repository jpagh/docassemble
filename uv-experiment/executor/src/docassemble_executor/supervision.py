"""OS-owned experimental Supervisor, separate from runtime-dependent stock boot.

Watchdog, qualified Celery workers and qualified session/request services are
registered. OS cron and fixture-local Exim stay independent of runtime admission.
Supervisor never owns payload authority.
Supervisor controls wrappers, never the lifetime of runtime authority.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

from .health import Check
from .paths import ExecutorPaths


def supervisor_config(paths: ExecutorPaths) -> Path:
    return paths.run_dir / 'managed-supervisor' / 'supervisord.conf'


def supervisor_command(paths: ExecutorPaths, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(['/usr/bin/supervisorctl', '-c', str(supervisor_config(paths)), *args],
                          text=True, capture_output=True, timeout=15, check=False)


def stop_supervisor(paths: ExecutorPaths) -> Check:
    def daemons_exited():
        result = subprocess.run(['/usr/bin/ps', '-C', 'supervisord,cron,syslog-ng,exim4', '-o', 'stat='],
                                text=True, capture_output=True, timeout=10, check=False)
        return result.returncode in (0, 1) and all(state.startswith('Z') for state in result.stdout.split())
    identity = supervisor_command(paths, ['pid'])
    if identity.returncode != 0:
        if daemons_exited():
            return Check('supervisor-stop', True, 'independent OS daemons are already stopped')
        return Check('supervisor-stop', False, 'independent Supervisor control is unavailable with surviving OS daemons')
    pid = int(identity.stdout.strip())
    requested = supervisor_command(paths, ['shutdown'])
    if requested.returncode != 0:
        return Check('supervisor-stop', False, 'independent Supervisor refused shutdown')
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            stat = (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[-1].split()
            exited = stat[0] == 'Z'
        except FileNotFoundError:
            exited = True
        if exited and daemons_exited():
            return Check('supervisor-stop', True, 'independent OS daemon trees stopped')
        time.sleep(0.05)
    return Check('supervisor-stop', False, 'independent Supervisor did not finish stopping; no database/broker stop authorized')


def start_supervisor(paths: ExecutorPaths) -> Check:
    # Stable, root-owned authority inodes are never unlinked/replaced while a
    # worker tree may survive a wrapper/controller death.
    for role in ('worker-normal', 'worker-single', 'service-websockets', 'service-nltk', 'service-conversion', 'watchdog'):
        slot = paths.state_dir / (role + '.lock')
        slot.touch(exist_ok=True)
        slot.chmod(0o644)
        log_path = paths.log_dir / ('managed-' + role + '.log')
        log_path.touch(exist_ok=True)
        log_path.chmod(0o644)
        if os.geteuid() == 0:
            os.chown(log_path, 33, 33)
    # Mail payload output must not reach Exim's bounded pipe output collector:
    # its default 20 KiB limit kills the pipe command. Keep a stable append log
    # outside disposable delivery pipes, including after wrapper death.
    mail_log = paths.log_dir / 'managed-mail.log'
    mail_log.touch(exist_ok=True)
    mail_log.chmod(0o644)
    if os.geteuid() == 0:
        os.chown(mail_log, 33, 33)
    health_directory = paths.state_dir / 'health'
    health_directory.mkdir(exist_ok=True)
    if os.geteuid() == 0:
        os.chown(health_directory, 33, 33)
    nltk_directory = Path('/var/run/nltk')
    nltk_directory.mkdir(exist_ok=True)
    if os.geteuid() == 0:
        os.chown(nltk_directory, 33, 33)
    directory = supervisor_config(paths).parent
    config = supervisor_config(paths)
    if config.is_file():
        existing = supervisor_command(paths, ['pid'])
        if existing.returncode == 0:
            return Check('supervisor', True, 'independent Supervisor is already running')
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    # A dead container may leave its socket/pid in a committed fixture snapshot.
    (directory / 'supervisor.sock').unlink(missing_ok=True)
    command = shlex.join([sys.executable, '-m', 'docassemble_executor.cli', 'run-watchdog'])
    config.write_text(f'''[supervisord]
logfile={directory}/supervisord.log
pidfile={directory}/supervisord.pid
childlogdir={directory}
user=root

[unix_http_server]
file={directory}/supervisor.sock
chmod=0700

[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface

[supervisorctl]
serverurl=unix://{directory}/supervisor.sock

[program:watchdog]
command={command}
user=www-data
autostart=true
autorestart=true
startsecs=0
stopwaitsecs=2
stopasgroup=false
killasgroup=false
stdout_logfile={directory}/watchdog.log
stderr_logfile={directory}/watchdog.log
''', encoding='utf-8')
    for adapter, kind, name in (
        ('run-worker', 'normal', 'celery'), ('run-worker', 'single', 'celerysingle'),
        ('run-service', 'websockets', 'websockets'), ('run-service', 'nltk', 'nltk'),
        ('run-service', 'conversion', 'conversion'),
    ):
        command = shlex.join([sys.executable, '-m', 'docassemble_executor.cli',
                              adapter, '--kind', kind, '--wait'])
        with config.open('a', encoding='utf-8') as handle:
            handle.write(f'''
[program:{name}]
command={command}
user=www-data
autostart=true
autorestart=true
startsecs=0
stopwaitsecs=2
stopasgroup=false
killasgroup=false
stdout_logfile={directory}/{name}.log
stderr_logfile={directory}/{name}.log
''')
    # OS daemons import no runtime and stay available during drain/recovery.
    # The image supplies an explicit qualified-only cron table and local Exim
    # configuration; never fall back to a legacy daemon/control bootstrap.
    queue_interval = os.environ.get('DA_EXIM_QUEUE_INTERVAL', '30m')
    if not re.fullmatch('[1-9][0-9]*[smh]', queue_interval):
        raise ValueError('invalid Exim queue interval')
    for name, daemon in (
        ('syslogng', ['/usr/sbin/syslog-ng', '--foreground', '--no-caps', '--cfgfile', '/opt/da-repo/Docker/syslog-ng.conf']),
        ('cron', ['/usr/sbin/cron', '-f']),
        ('exim4', ['/usr/sbin/exim4', '-bdf', '-q' + queue_interval]),
    ):
        with config.open('a', encoding='utf-8') as handle:
            handle.write(f'''
[program:{name}]
command={shlex.join(daemon)}
user=root
autostart=true
autorestart=true
startsecs=0
stopasgroup=false
killasgroup=false
stdout_logfile={directory}/{name}.log
stderr_logfile={directory}/{name}.log
''')
    if os.environ.get('DA_DURABLE_WORK_STORE'):
        for kind, user in (('preparation', 'root'), ('installation', 'root')):
            command = shlex.join([sys.executable, '-m', 'docassemble_executor.cli',
                                  'run-durable-work', '--kind', kind])
            with config.open('a', encoding='utf-8') as handle:
                handle.write(f'''
[program:durable-{kind}]
command={command}
user={user}
autostart=false
autorestart=true
startsecs=0
stopasgroup=true
killasgroup=true
stdout_logfile={directory}/durable-{kind}.log
stderr_logfile={directory}/durable-{kind}.log
''')
    config.chmod(0o600)
    output = subprocess.run(['/usr/bin/supervisord', '-c', str(config)],
                            text=True, capture_output=True, timeout=15, check=False)
    if output.returncode != 0:
        return Check('supervisor', False, output.stdout + output.stderr)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if supervisor_command(paths, ['pid']).returncode == 0:
            return Check('supervisor', True, 'independent Supervisor launched; consumers wait behind admission')
        time.sleep(0.05)
    return Check('supervisor', False, 'independent Supervisor did not become available')
