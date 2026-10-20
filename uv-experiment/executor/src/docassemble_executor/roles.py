"""Whole supported all-role inventory: fixed protocol checks behind admission."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import socket
import subprocess
import time

from .admission import Admission
from .authority import run_runtime
from .capabilities import BUSY_ROLES, Capabilities, ENV, role_busy
from .health import Check
from .paths import ExecutorPaths
from .services import pg_is_ready, redis_is_ready
from .supervision import supervisor_command
from .webapp import http_get

RUNTIME_ROLES = ('web', 'celery', 'celerysingle', 'websockets', 'nltk', 'conversion', 'watchdog')
INFRASTRUCTURE_ROLES = ('postgres', 'redis', 'cron', 'exim4', 'syslogng')


@dataclass
class RoleCheck(Check):
    processes: list[list[str]] = field(default_factory=list)


def _observer_ready(paths: ExecutorPaths) -> bool:
    try:
        receipt = json.loads((paths.state_dir / 'health/watchdog.json').read_text())
        stat = (Path('/proc') / str(int(receipt['pid'])) / 'stat').read_text().rsplit(')', 1)[-1].split()
        return stat[0] not in ('Z', 'T', 't') and stat[19] == receipt['start']
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return False


def check_roles(paths: ExecutorPaths, *, retained: dict | None = None, retained_busy: set[str] | None = None) -> list[Check]:
    # A protocol result belongs only to an incarnation stable across its probe.
    before = process_identities(paths)
    # No-mutation timeout may retain a previously qualified live incarnation
    # while accepted work occupies a serial protocol. Never use this after sync.
    cached: set[str] = set()
    if retained and retained.get('normal_ready') is True and retained.get('runtime_state') in ('locked', 'override'):
        previous = retained.get('role_processes', {})
        healthy = {item['name'] for item in retained.get('role_health', []) if item['ok']}
        cached = {name for name in retained_busy or set()
                  if name in healthy and before[name] and before[name] == previous.get(name) and role_busy(paths.state_dir, name)}
    checks = [Check('web', 'web' in cached or http_get(paths.backend_port, '/health_check', timeout=2)[0] == 200),
              Check('postgres', pg_is_ready()), Check('redis', redis_is_ready()),
              Check('watchdog', _observer_ready(paths))]
    output = supervisor_command(paths, ['status'])
    states = {line.split()[0]: line.split()[1] for line in output.stdout.splitlines() if len(line.split()) >= 2}
    for name in ('cron', 'exim4', 'syslogng'):
        checks.append(Check(name, states.get(name) == 'RUNNING', 'independent OS Supervisor'))
    try:
        with socket.create_connection(('127.0.0.1', 514), timeout=2):
            pass
    except OSError:
        checks[-1] = Check('syslogng', False, 'independent syslog protocol unavailable')
    results = {}
    with Capabilities(paths.state_dir).accept(launch=True) as caller:
        if caller is not None:
            environment = dict(os.environ, **{ENV: caller.name, 'DA_CONFIG_FILE': str(paths.config_file),
                                             'PYTHONDONTWRITEBYTECODE': '1', 'HOME': '/var/www',
                                             'DA_ROLE_PROBE_KINDS': json.dumps([name for name in ('websockets', 'nltk', 'conversion') if name not in cached]),
                                             'PYTHONPATH': str(Path(__file__).resolve().parent.parent) + ':' + os.environ.get('PYTHONPATH', '')})
            command = [str(paths.runtime_venv / 'bin/python'), '-m', 'docassemble_executor.role_probe']
            if os.geteuid() == 0:
                command = ['/usr/sbin/runuser', '-u', 'www-data', '--', *command]
            try:
                result = run_runtime(command, env=environment, timeout=30)
                if result.returncode == 0:
                    results = json.loads(result.stdout.split('ROLE_HEALTH=')[-1])
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
    checks.extend(Check(name, name in cached or results.get(name) is True,
                        'qualified live incarnation retained by accepted work' if name in cached else 'actual managed protocol')
                  for name in ('celery', 'celerysingle', 'websockets', 'nltk', 'conversion'))
    after = process_identities(paths)
    return [RoleCheck(check.name, check.ok and bool(before[check.name]) and before[check.name] == after[check.name],
                      check.detail, before[check.name]) for check in checks]


def process_identities(paths: ExecutorPaths) -> dict[str, list[list[str]]]:
    """Cheap live incarnation checks; changed trees require new protocol proof."""
    identities: dict[str, list[list[str]]] = {name: [] for name in RUNTIME_ROLES + INFRASTRUCTURE_ROLES}
    try:
        web_guardian = (paths.run_dir / 'web-normal.pid').read_text().strip()
    except OSError:
        web_guardian = ''
    for directory in Path('/proc').glob('[0-9]*'):
        try:
            command = (directory / 'cmdline').read_bytes().replace(b'\0', b' ').decode()
            stat = (directory / 'stat').read_text().rsplit(')', 1)[-1].split()
            if stat[0] in ('Z', 'T', 't') or 'authority.py' in command:
                continue
            role = None
            if 'worker_runtime.py --kind normal' in command:
                role = 'celery'
            elif 'worker_runtime.py --kind single' in command:
                role = 'celerysingle'
            elif 'service_runtime.py --kind ' in command:
                role = command.split('service_runtime.py --kind ', 1)[1].split()[0]
            elif 'watchdog_runtime.py' in command:
                role = 'watchdog'
            elif ('gunicorn' in command and 'docassemble_executor.web_runtime:application' in command
                  and stat[1] == web_guardian):
                role = 'web'  # Graceful worker reload does not change the master's incarnation.
            elif 'postgres -D ' in command:
                role = 'postgres'
            elif command.startswith('redis-server '):
                role = 'redis'
            elif command.startswith('/usr/sbin/cron -f'):
                role = 'cron'
            elif command.startswith('/usr/sbin/exim4 -bdf'):
                role = 'exim4'
            elif command.startswith('/usr/sbin/syslog-ng --foreground'):
                role = 'syslogng'
            if role in identities:
                identities[role].append([directory.name, stat[19]])
        except (OSError, ValueError, IndexError):
            pass
    return {name: sorted(values) for name, values in identities.items()}


def prepare_roles(paths: ExecutorPaths, *, retained: dict | None = None) -> list[Check]:
    # Snapshot pre-existing receiving-role authority before creating any probes
    # or launch traffic; an unrelated job never supplies a role's retained health.
    retained_busy = {name for name in BUSY_ROLES if role_busy(paths.state_dir, name)} if retained else set()
    Admission(paths.state_dir).prepare()
    deadline = time.monotonic() + float(os.environ.get('DA_ROLE_READY_TIMEOUT', '120'))
    while True:
        checks = check_roles(paths, retained=retained, retained_busy=retained_busy)
        if all(check.ok for check in checks) or time.monotonic() >= deadline:
            return checks
        time.sleep(0.2)
