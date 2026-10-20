#!/usr/bin/env python3
"""04b2c: real cron/interview work, Exim queues and accepted helper trees."""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor as BasePool
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import threading
import time

from harness import ContainerHttp, Docker, Evidence, check
from maintenance import wait

NAME = 'da-uv-helper-proof'
CONFIG = '/var/run/docassemble/managed-supervisor/supervisord.conf'
RUNTIME = '/usr/share/docassemble/local3.12/bin/python'
STATE = '/var/lib/docassemble/managed/'
ROOT = '/tmp/da-helper-proof/'
SERVICE_ROOT = '/tmp/da-service-proof/'
PROOF_ENV = {'PYTHONPATH': '/opt/docassemble-executor/src:/opt/docassemble-fixture'}
BACKUP_RECEIPTS = '/tmp/da-backup-proof/receipts.jsonl'
CONTROL_HOOKS = {
    'run-certbot.sh': 'certbot',
    'reset.sh': 'reset',
    'update.sh': 'update',
    'sync.sh': 'sync',
    'run-nginx.sh': 'nginx',
    'run-apache.sh': 'apache',
    'run-uwsgi.sh': 'uwsgi',
    'restart-nginx.sh': 'restart-nginx',
    'restart-post-logrotate.sh': 'restart-post-logrotate',
}

# Disposable observation only: wrappers exec the unchanged backup scripts. The
# resolved backup environment drops the proof label, so find it in the schedule
# controller's ancestry without recording any credentials or capability tokens.
BACKUP_PROBE = '''import json
import os
from pathlib import Path
import sys

label = ''
nested_helper = False
pid = os.getpid()
while pid > 1:
    proc = Path('/proc') / str(pid)
    environment = dict(item.split(b'=', 1) for item in
                       (proc / 'environ').read_bytes().split(b'\\0') if b'=' in item)
    label = label or environment.get(b'DA_HELPER_PROOF_LABEL', b'').decode()
    command = (proc / 'cmdline').read_bytes().split(b'\\0')
    nested_helper = nested_helper or any(
        part.endswith(b'/helper_runtime.py') or part == b'run-helper' for part in command)
    pid = int(next(line.split()[1] for line in (proc / 'status').read_text().splitlines()
                   if line.startswith('PPid:')))
if label:
    receipt = {'label': label, 'phase': sys.argv[1], 'uid': os.getuid(),
               'nested_helper': nested_helper,
               'helper_completed': Path('/tmp/da-helper-proof/' + label + '.completed').is_file(),
               'accepted_caller': bool(os.environ.get('DA_ACCEPTED_CALLER')),
               'in_cron': bool(os.environ.get('IN_CRON'))}
    with Path('/tmp/da-backup-proof/receipts.jsonl').open('a') as output:
        output.write(json.dumps(receipt) + '\\n')
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', default='da-uv-helpers:local')
    parser.add_argument('--evidence-dir')
    parser.add_argument('--keep-container', action='store_true')
    args = parser.parse_args()
    root = Path(args.evidence_dir) if args.evidence_dir else Path(__file__).resolve().parents[1] / 'helpers-mail/evidence' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    evidence, results = Evidence(root), []
    docker = Docker(evidence)
    http = ContainerHttp(docker, NAME)
    repo = Path(__file__).resolve().parents[2]
    source_overlay = os.environ.get('DA_HELPER_PROOF_SOURCE_OVERLAY') == '1'
    fixture_sources = [
        ('fixture/sentinel-user/docassemble/sentinel_fixture/helper_work.py',
         '/opt/docassemble-fixture/sentinel-user/docassemble/sentinel_fixture/helper_work.py'),
        ('fixture/service_probe_hooks.py', '/opt/docassemble-fixture/service_probe_hooks.py'),
        ('fixture/helper_probe_hooks.py', '/opt/docassemble-fixture/helper_probe_hooks.py'),
    ]
    overlay_manifest = {'overlay': source_overlay, 'sources': [
        {'host_source': str(repo / 'uv-experiment' / source),
         'host_sha256': hashlib.sha256((repo / 'uv-experiment' / source).read_bytes()).hexdigest(),
         'destination': destination, 'destination_sha256': None}
        for source, destination in fixture_sources]}
    detached_active = False
    failure_exported = False
    child_identity = None
    container_id = None
    events_start = None
    events_collector = None
    events_threads = []
    exec_futures = {}
    exec_lock = threading.Lock()

    class Pool(BasePool):
        def __exit__(self, kind, error, traceback):
            if kind is not None:
                if detached_active and not failure_exported:
                    detached_failure()
                def failure_text(text):
                    return redact(text) if detached_active else text
                evidence.write('failure.txt', failure_text(str(error) + '\n' + docker.logs(NAME)))
                evidence.write('mail-log.txt', failure_text(docker.read_file(NAME, '/var/log/exim4/mainlog')))
                evidence.write('worker-log.txt', failure_text(docker.read_file(NAME, '/usr/share/docassemble/log/managed-worker-normal.log')))
                evidence.write('backup-log.txt', failure_text(docker.read_file(NAME, '/usr/share/docassemble/log/managed-backup.log')))
                evidence.write('backup-invocations.jsonl', failure_text(docker.read_file(NAME, BACKUP_RECEIPTS)))
                evidence.write('failure-processes.txt', failure_text(docker.exec(NAME, ['ps', '-eo', 'user,pid,ppid,args'], check_result=False).stdout))
                evidence.write('failure-status.json', failure_text(http.body('/status.json')))
                evidence.write('failure-authority.txt', docker.exec_shell(NAME,
                    'for pid in /proc/[0-9]*; do for fd in "$pid"/fd/*; do target=$(readlink "$fd"); '
                    'case "$target" in /var/lib/docassemble/managed/callers/*) '
                    'echo "$fd /var/lib/docassemble/managed/callers/[capability-present]" ;; '
                    '/var/lib/docassemble/managed/*) echo "$fd $target" ;; esac; done; done',
                    check_result=False).stdout)
                if args.keep_container:
                    # Release fixture barriers only after recording the failed
                    # state. This is inspection cleanup, not successful drain.
                    docker.exec_shell(NAME,
                        'for file in /tmp/da-helper-proof/*.started /tmp/da-service-proof/*.started; do '
                        'touch "${file%.started}.release" "${file%.started}.commit-release"; done',
                        check_result=False)
                else:
                    docker.rm(NAME)  # Disposable cleanup is never a successful drain.
            return super().__exit__(kind, error, traceback)

    def utc():
        return datetime.now(timezone.utc).isoformat()

    def redact(text):
        if isinstance(text, bytes):
            text = text.decode(errors='replace')
        text = str(text)
        capability = os.environ.get('DA_ACCEPTED_CALLER')
        if capability:
            for value in (capability, json.dumps(capability)[1:-1]):
                text = text.replace(value, '[redacted-capability]')
        text = re.sub(r'/var/lib/docassemble/managed/callers/[^/\s"\']+',
                      '/var/lib/docassemble/managed/callers/[capability-present]', text)
        return re.sub(r'\b[0-9a-fA-F]{64}\b', '[redacted-capability]', text)

    def capture_error(operation, error):
        try:
            path = root / 'detached-debug/capture-errors.jsonl'
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a') as output:
                output.write(json.dumps({'utc': utc(), 'operation': operation,
                                         'type': type(error).__name__, 'message': redact(error)}) + '\n')
        except BaseException:
            pass

    def diagnostic(operation, call):
        try:
            return call()
        except BaseException as error:
            capture_error(operation, error)
            return None

    def diagnostic_exec(command):
        result = docker.exec(NAME, command, timeout=10, check_result=False)
        if result.returncode:
            capture_error('docker exec diagnostic', RuntimeError(redact(result.stderr)))
        return result

    def save_exec_result(name, result_or_error):
        def save():
            with exec_lock:
                error = result_or_error if isinstance(result_or_error, BaseException) else None
                stdout = getattr(result_or_error, 'stdout', None) or ''
                stderr = getattr(result_or_error, 'stderr', None) or ''
                evidence.write('detached-debug/' + name + '.stdout', redact(stdout))
                evidence.write('detached-debug/' + name + '.stderr', redact(stderr))
                metadata = {'returncode': getattr(result_or_error, 'returncode', None),
                            'completed_utc': utc(), 'exception_type': type(error).__name__ if error else None,
                            'exception_message': redact(error) if error else None,
                            'status_subject': 'Docker exec/wrapper, not detached child'}
                if isinstance(error, subprocess.TimeoutExpired):
                    metadata['timeout'] = error.timeout
                evidence.write_json('detached-debug/' + name + '.json', metadata)
        diagnostic('save exec result ' + name, save)

    def register_exec(name, future):
        exec_futures[name] = future

        def completed(done):
            try:
                result = done.result()
            except BaseException as error:
                result = error
            save_exec_result(name, result)
        future.add_done_callback(completed)
        return future

    def event_record(line):
        try:
            event = json.loads(line)
        except (ValueError, TypeError) as error:
            capture_error('events JSON parse', error)
            return json.dumps(redact(line))

        def clean(value, path=()):
            # Only Docker's structural identities/times bypass bearer redaction.
            if path in (('time',), ('timeNano',), ('Type',), ('Action',), ('id',), ('Actor', 'ID')):
                return value
            if (path and path[-1] in ('container', 'image') and isinstance(value, str)
                    and re.fullmatch(r'(?:sha256:)?[0-9a-f]{64}', value)):
                return value
            if isinstance(value, dict):
                return {redact(k): clean(v, path + (k,)) for k, v in value.items()}
            if isinstance(value, list):
                return [clean(item, path + (index,)) for index, item in enumerate(value)]
            return redact(value) if isinstance(value, str) else value
        return json.dumps(clean(event))

    def start_events():
        nonlocal container_id, events_start, events_collector
        events_start = utc()
        result = diagnostic('container immutable ID', lambda: docker.run(
            ['inspect', NAME, '--format', '{{.Id}}'], timeout=10, check_result=False))
        if result is None or result.returncode or not result.stdout.strip():
            capture_error('container immutable ID', RuntimeError('immutable ID unavailable'))
            return
        container_id = result.stdout.strip()
        evidence.write_json('detached-debug/events-interval.json',
                            {'container_id': container_id, 'start_utc': events_start})
        evidence.write('detached-debug/events.jsonl', '')
        evidence.write('detached-debug/events.stderr', '')
        events_collector = subprocess.Popen(
            ['docker', 'events', '--filter', 'type=container', '--filter', 'container=' + container_id,
             '--since', events_start, '--format', '{{json .}}'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace')

        def drain(stream, name, json_lines):
            try:
                with stream:
                    for line in stream:
                        try:
                            content = event_record(line) + '\n' if json_lines else redact(line)
                            path = root / 'detached-debug' / name
                            with path.open('a') as output:
                                output.write(content)
                        except BaseException as error:
                            capture_error('event reader ' + name, error)
            except BaseException as error:
                capture_error('event reader ' + name, error)
        for stream, name, json_lines in ((events_collector.stdout, 'events.jsonl', True),
                                         (events_collector.stderr, 'events.stderr', False)):
            thread = threading.Thread(target=drain, args=(stream, name, json_lines), daemon=True)
            events_threads.append(thread)
            start_reader = thread.start
            start_reader()

    def stop_events():
        terminal = utc()
        if events_collector is not None:
            diagnostic('terminate host event collector', events_collector.terminate)
            diagnostic('wait host event collector', lambda: events_collector.wait(timeout=10))
            if events_collector.returncode is None:
                diagnostic('kill unresponsive host event collector', events_collector.kill)
                diagnostic('reap host event collector', lambda: events_collector.wait(timeout=10))
            for thread in events_threads:
                diagnostic('drain host event collector', lambda: thread.join(timeout=10))
            diagnostic('event collector status', lambda: evidence.write_json(
                'detached-debug/events-collector.json',
                {'container_id': container_id, 'start_utc': events_start, 'terminal_utc': terminal,
                 'returncode': events_collector.returncode,
                 'readers_drained': all(not thread.is_alive() for thread in events_threads)}))
        for name, future in list(exec_futures.items()):
            if future.done():
                try:
                    result = future.result()
                except BaseException as error:
                    result = error
                save_exec_result(name, result)

    def detached_snapshot(point):
        nonlocal child_identity
        directory = 'detached-debug/' + point + '/'
        diagnostic('snapshot directory', lambda: (root / directory).mkdir(parents=True, exist_ok=True))
        child_script = r"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
root = Path('/tmp/da-helper-proof')
record = {'utc': datetime.now(timezone.utc).isoformat(), 'markers': {}, 'pid': None,
          'original_identity': json.loads(sys.argv[1]), 'state': None, 'starttime': None,
          'raw_stat': None, 'ppid': None, 'pgid': None, 'sid': None,
          'identity_matches': None, 'absent': False, 'inaccessible': False, 'ancestors': []}
for suffix in ('started', 'parent-exited', 'release', 'completed'):
    path = root / ('detached-helper.' + suffix)
    try:
        record['markers'][suffix] = {'contents': path.read_text(), 'exists': True}
    except OSError as error:
        record['markers'][suffix] = {'exists': False, 'error_type': type(error).__name__, 'error': str(error)}
def proc_stat(pid):
    text = (Path('/proc') / str(pid) / 'stat').read_text()
    tail = text[text.rfind(')') + 2:].split()
    return {'pid': pid, 'raw_stat': text, 'state': tail[0], 'ppid': int(tail[1]),
            'pgid': int(tail[2]), 'sid': int(tail[3]), 'starttime': int(tail[19])}
try:
    record['pid'] = int(record['markers']['started']['contents'])
    child = proc_stat(record['pid'])
    record.update(child)
    identity = [child['pid'], child['starttime']]
    record['identity_matches'] = identity == record['original_identity'] if record['original_identity'] else None
    parent = child['ppid']
    seen = {child['pid']}
    while parent > 0 and parent not in seen:
        seen.add(parent)
        try:
            ancestor = proc_stat(parent)
        except OSError as error:
            record['ancestors'].append({'pid': parent, 'error_type': type(error).__name__, 'error': str(error)})
            break
        record['ancestors'].append(ancestor)
        parent = ancestor['ppid']
except BaseException as error:
    record.update(error_type=type(error).__name__, error=str(error),
                  absent=isinstance(error, FileNotFoundError), inaccessible=isinstance(error, PermissionError))
    if record['original_identity'] is not None:
        record['identity_matches'] = False
print(json.dumps(record))
"""
        def child_capture():
            nonlocal child_identity
            result = diagnostic_exec(['/usr/share/docassemble/executor-venv/bin/python', '-c',
                                      child_script, json.dumps(child_identity)])
            record = json.loads(redact(result.stdout))
            if point == '01-started' and record['pid'] is not None and record['starttime'] is not None:
                child_identity = [record['pid'], record['starttime']]
                record['original_identity'] = child_identity
                record['identity_matches'] = True
            evidence.write_json(directory + 'child.json', record)
        diagnostic('child ' + point, child_capture)
        def processes():
            result = diagnostic_exec(['ps', '-e', '-o', 'pid,ppid,pgid,sid,stat,args'])
            evidence.write(directory + 'processes.txt', redact(result.stdout + result.stderr))
        diagnostic('processes ' + point, processes)
        fd_script = r"""
import os
from pathlib import Path
import re
import sys
child = sys.argv[1]
for proc in sorted(Path('/proc').glob('[0-9]*')):
    try:
        fds = list((proc / 'fd').iterdir())
    except OSError as error:
        print(proc.name, 'fd-inaccessible', type(error).__name__)
        continue
    for fd in fds:
        try:
            target = os.readlink(fd)
            managed = target.startswith('/var/lib/docassemble/managed/')
            if managed or (proc.name == child and fd.name in ('0', '1', '2')):
                target = re.sub(r'/var/lib/docassemble/managed/callers/[^/\s]+',
                                '/var/lib/docassemble/managed/callers/[capability-present]', target)
                target = re.sub(r'\b[0-9a-f]{64}\b', '[redacted-capability]', target)
                print(proc.name, fd.name, target)
        except OSError as error:
            print(proc.name, fd.name, 'unavailable', type(error).__name__)
"""
        def fds():
            result = diagnostic_exec(['/usr/share/docassemble/executor-venv/bin/python', '-c', fd_script,
                                      str(child_identity[0]) if child_identity else ''])
            evidence.write(directory + 'authority-fds.txt', redact(result.stdout + result.stderr))
        diagnostic('authority fds ' + point, fds)
        def inspect():
            result = docker.run(['inspect', container_id, '--format',
                                 '{{json .State.StartedAt}} {{json .RestartCount}} {{json .State.OOMKilled}} '
                                 '{{json .State.Running}} {{json .State.FinishedAt}} {{json .State.ExitCode}}'],
                                timeout=10, check_result=False)
            fields = result.stdout.split()
            if result.returncode:
                capture_error('inspect ' + point, RuntimeError(redact(result.stderr)))
            evidence.write_json(directory + 'inspect.json', {
                'utc': utc(), 'container_id': container_id, 'returncode': result.returncode,
                'stderr': redact(result.stderr), 'fields': dict(zip(
                    ('StartedAt', 'RestartCount', 'OOMKilled', 'Running', 'FinishedAt', 'ExitCode'),
                    [json.loads(field) for field in fields]))})
        diagnostic('inspect ' + point, inspect)
        def memory():
            result = diagnostic_exec(['cat', '/sys/fs/cgroup/memory.events'])
            evidence.write(directory + 'memory.events', redact(result.stdout))
            evidence.write_json(directory + 'memory-events.json',
                                {'returncode': result.returncode, 'stderr': redact(result.stderr)})
        diagnostic('memory events ' + point, memory)
        diagnostic('snapshot export ' + point, lambda: detached_export(point))

    def detached_export(point):
        directory = 'detached-debug/' + point + '/'
        diagnostic('export directory', lambda: (root / directory).mkdir(parents=True, exist_ok=True))
        listing_script = r"""
import json
from pathlib import Path
import stat
import sys
entries = []
for path in sorted(Path(sys.argv[1]).glob('detached-helper.*')):
    try:
        info = path.lstat()
        entries.append({'path': str(path), 'name': path.name,
                        'type': 'file' if stat.S_ISREG(info.st_mode) else 'other',
                        'mode': stat.S_IMODE(info.st_mode), 'uid': info.st_uid, 'gid': info.st_gid,
                        'size': info.st_size, 'mtime_ns': info.st_mtime_ns})
    except OSError as error:
        entries.append({'path': str(path), 'error_type': type(error).__name__, 'error': str(error)})
print(json.dumps(entries))
"""
        # The stable fd 2 sink may briefly contain raw application output by design:
        # keep original stream semantics in the child, redact retention at export.
        artifact_script = r"""
import base64
import json
from pathlib import Path
import sys
try:
    payload = Path(sys.argv[1]).read_bytes()
    print(json.dumps({'payload': base64.b64encode(payload).decode('ascii')}))
except OSError as error:
    print(json.dumps({'error_type': type(error).__name__}))
"""

        def retained_artifact(name, payload):
            # Documents are binary even when (RTF) they happen to decode as text.
            # Other artifacts: explicit text suffix or UTF-8 without binary controls.
            # Unknown binary formats stay byte-exact.
            suffix = Path(name).suffix.lower()
            if suffix in ('.rtf', '.docx', '.pdf') or payload.startswith((
                    b'PK\x03\x04', b'%PDF-', b'\x1f\x8b', b'\x89PNG', b'\xff\xd8', b'GIF8')):
                return payload, False
            if suffix in ('.txt', '.json', '.jsonl', '.log', '.stdout', '.stderr', '.traceback'):
                return redact(payload).encode('utf-8'), True
            try:
                text = payload.decode('utf-8')
            except UnicodeError:
                return payload, False
            if any(ord(char) < 32 and char not in '\t\n\r' for char in text):
                return payload, False
            return redact(text).encode('utf-8'), True

        captures = []
        for category, source in (('helper', ROOT), ('service', SERVICE_ROOT)):
            def artifacts(category=category, source=source):
                result = diagnostic_exec(['/usr/share/docassemble/executor-venv/bin/python', '-c', listing_script, source])
                entries = json.loads(result.stdout)
                evidence.write(directory + category + '-listing.txt', redact(json.dumps(entries, indent=2)) + '\n')
                for entry in entries:
                    if 'error' in entry:
                        capture_error('artifact listing', RuntimeError(entry['error']))
                    if entry.get('type') != 'file':
                        continue
                    target = root / directory / 'artifacts' / category / entry['name']
                    record = {'source': entry['path'], 'category': category, 'retained': False}
                    captures.append(record)
                    def copy(entry=entry, target=target, record=record):
                        try:
                            # Encoded transport into memory: never a raw host cp/temp file.
                            result = diagnostic_exec(['/usr/share/docassemble/executor-venv/bin/python',
                                                      '-c', artifact_script, entry['path']])
                            if result.returncode:
                                raise RuntimeError(redact(result.stderr))
                            captured = json.loads(result.stdout)
                            if 'error_type' in captured:
                                record['error_type'] = captured['error_type']
                                raise OSError('artifact unreadable: ' + captured['error_type'])
                            payload = base64.b64decode(captured['payload'], validate=True)
                            retained, is_text = retained_artifact(entry['name'], payload)
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(retained)
                            record.update(retained=True, text_redacted=is_text, bytes=len(retained))
                            if category == 'helper' and entry['name'] in ('detached-helper.trace.jsonl', 'detached-helper.traceback.txt'):
                                latest = 'trace.jsonl' if entry['name'].endswith('.jsonl') else 'traceback.txt'
                                (root / 'detached-debug' / latest).write_bytes(retained)
                        except Exception as error:
                            record.setdefault('error_type', type(error).__name__)
                            raise
                    diagnostic('artifact copy ' + entry['path'], copy)
            diagnostic(category + ' artifacts ' + point, artifacts)
        evidence.write(directory + 'artifacts.json', redact(json.dumps(captures, indent=2)) + '\n')
        terminal = utc()
        if container_id and events_start:
            def historical():
                result = docker.run(['events', '--since', events_start, '--until', terminal,
                                     '--filter', 'type=container', '--filter', 'container=' + container_id,
                                     '--format', '{{json .}}'], timeout=10, check_result=False)
                if result.returncode:
                    capture_error('historical events ' + point, RuntimeError(redact(result.stderr)))
                evidence.write(directory + 'events-history.jsonl',
                               ''.join(event_record(line) + '\n' for line in result.stdout.splitlines()))
                evidence.write(directory + 'events-history.stderr', redact(result.stderr))
                evidence.write_json(directory + 'events-history.json', {
                    'container_id': container_id, 'start_utc': events_start, 'terminal_utc': terminal,
                    'returncode': result.returncode, 'completeness': 'bounded historical retention, not guaranteed'})
            diagnostic('historical events ' + point, historical)
        def container_logs():
            result = docker.run(['logs', container_id], timeout=10, check_result=False)
            if result.returncode:
                capture_error('container logs ' + point, RuntimeError(redact(result.stderr)))
            evidence.write(directory + 'container.stdout', redact(result.stdout))
            evidence.write(directory + 'container.stderr', redact(result.stderr))
            evidence.write_json(directory + 'container-logs.json', {'returncode': result.returncode})
        diagnostic('container logs ' + point, container_logs)
        log_script = r"""
import json
import os
from pathlib import Path
import re
from docassemble_executor.config import load_config
config_error = None
try:
    config = load_config(Path(os.environ.get('DA_CONFIG_FILE', '/usr/share/docassemble/config/config.yml')))
except BaseException as error:
    config = {}
    config_error = {'error_type': type(error).__name__, 'error': str(error)}
paths = ['/usr/share/docassemble/log/docassemble.log', '/usr/share/docassemble/log/managed-schedules.log',
         config.get('flask log') or '/tmp/flask.log', '/var/log/cron.log',
         '/usr/share/docassemble/log/managed-service-nltk.log',
         '/usr/share/docassemble/log/managed-service-conversion.log',
         '/usr/share/docassemble/log/managed-service-websockets.log',
         '/var/run/docassemble/managed-supervisor/supervisord.log']
root = Path('/tmp/da-helper-proof-log-export')
root.mkdir(exist_ok=True)
records = []
for index, source in enumerate(paths):
    path = Path(source)
    record = {'source': str(path), 'exists': path.exists(), 'destination': None}
    try:
        text = path.read_text(errors='replace')
        capability = os.environ.get('DA_ACCEPTED_CALLER')
        if capability:
            text = text.replace(capability, '[redacted-capability]')
        text = re.sub(r'/var/lib/docassemble/managed/callers/[^/\s"\']+',
                      '/var/lib/docassemble/managed/callers/[capability-present]', text)
        text = re.sub(r'\b[0-9a-f]{64}\b', '[redacted-capability]', text)
        target = root / (str(index) + '-' + path.name)
        target.write_text(text)
        record['destination'] = str(target)
        record['returncode'] = 0
    except OSError as error:
        record.update(returncode=1, error_type=type(error).__name__, error=str(error))
    records.append(record)
print(json.dumps({'records': records, 'config_error': config_error}))
"""
        def logs():
            result = diagnostic_exec(['/usr/share/docassemble/executor-venv/bin/python', '-c', log_script])
            manifest = json.loads(redact(result.stdout))
            records = manifest['records']
            if manifest['config_error']:
                capture_error('configured Flask log', RuntimeError(manifest['config_error']['error']))
            for record in records:
                if record['returncode']:
                    capture_error('log read ' + record['source'], RuntimeError(record['error']))
                if record['destination'] is None:
                    continue
                def copy_log(record=record):
                    destination = root / directory / 'logs' / Path(record['destination']).name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    result = docker.run(['cp', container_id + ':' + record['destination'], str(destination)],
                                        timeout=10, check_result=False)
                    record['copy_returncode'] = result.returncode
                    record['copy_stderr'] = redact(result.stderr)
                    if result.returncode:
                        raise RuntimeError(redact(result.stderr))
                diagnostic('log copy ' + record['source'], copy_log)
            evidence.write_json(directory + 'logs.json', {
                'records': records, 'config_error': manifest['config_error'], 'application_cron_error_capture': [
                    'detached-debug/helper-exec.stderr', 'artifacts/helper/detached-helper.stderr.txt'],
                'candidate_files_are_not_guaranteed_cron_error_sinks': True})
        diagnostic('application log export ' + point, logs)
        diagnostic('overlay manifest', lambda: evidence.write_json('detached-debug/overlay-manifest.json', overlay_manifest))
        def source_identity():
            script = ('import hashlib, json; from pathlib import Path; import sys; '
                      'print(json.dumps({p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in sys.argv[1:]}))')
            result = diagnostic_exec(['/usr/share/docassemble/executor-venv/bin/python', '-c', script,
                                      *(destination for _, destination in fixture_sources)])
            hashes = json.loads(result.stdout)
            for source in overlay_manifest['sources']:
                source['destination_sha256'] = hashes.get(source['destination'])
            trace_path = root / 'detached-debug/trace.jsonl'
            records = [json.loads(line) for line in trace_path.read_text().splitlines()] if trace_path.exists() else []
            fd_closed = next((record['details'] for record in records if record['phase'] == 'fd-closed'), {})
            imported = next((record['details'] for record in records if record['phase'] == 'nested-import-return'), {})
            overlay_manifest.update(installed_origin=fd_closed.get('installed_origin'),
                                    helper_module_file=fd_closed.get('helper_module_file'),
                                    service_module_file=imported.get('module_file'))
            helper_file = fd_closed.get('helper_module_file')
            service_file = imported.get('module_file')
            installed_origin = fd_closed.get('installed_origin')
            loaded_hashes = {}
            if helper_file and service_file:
                loaded = diagnostic_exec(['/usr/share/docassemble/executor-venv/bin/python', '-c', script,
                                          helper_file, service_file])
                loaded_hashes = json.loads(loaded.stdout)
            overlay_manifest['helper_module_sha256'] = loaded_hashes.get(helper_file)
            overlay_manifest['service_module_sha256'] = loaded_hashes.get(service_file)
            expected_helper = fixture_sources[0][1] if source_overlay else installed_origin
            overlay_manifest['identity_valid'] = bool(helper_file and service_file and installed_origin) and (
                helper_file == expected_helper)
            overlay_manifest['identity_valid'] = overlay_manifest['identity_valid'] and (
                service_file == fixture_sources[1][1]) and (
                loaded_hashes.get(helper_file) == overlay_manifest['sources'][0]['host_sha256'] and
                loaded_hashes.get(service_file) == overlay_manifest['sources'][1]['host_sha256']) and (
                not source_overlay or all(source['host_sha256'] == source['destination_sha256']
                                          for source in overlay_manifest['sources']))
            evidence.write_json('detached-debug/overlay-manifest.json', overlay_manifest)
            if point in ('05-completed', '06-success', '99-failure') and not overlay_manifest['identity_valid']:
                capture_error('source identity', RuntimeError('diagnostic source identity missing or mismatched'))
        diagnostic('source identity ' + point, source_identity)

    def detached_failure():
        nonlocal failure_exported
        diagnostic('failure snapshot', lambda: detached_snapshot('99-failure'))
        diagnostic('failure export', lambda: detached_export('99-failure'))
        failure_exported = True

    def status():
        return json.loads(http.body('/status.json'))

    def uv_log():
        return docker.read_file(NAME, '/var/log/docassemble-uv-invocations.log')

    def present(name):
        return docker.exec(NAME, ['test', '-f', ROOT + name], check_result=False).returncode == 0

    def release(*labels):
        docker.exec(NAME, ['touch', *(ROOT + label + '.release' for label in labels),
                                   *(SERVICE_ROOT + label + '.release' for label in labels)])

    def helper(label, kind='weekly', hold=False, nested=False, detached=False, script=False):
        env = dict(PROOF_ENV, DA_HELPER_PROOF_LABEL=label, DA_HELPER_PROOF_HOLD=str(hold).lower(),
                   DA_HELPER_PROOF_NESTED=str(nested).lower(), DA_HELPER_PROOF_DETACHED=str(detached).lower())
        command = ['bash', '/etc/cron.' + kind + '/docassemble'] if script else ['da-executor', 'run-helper', '--kind', kind]
        if detached and source_overlay:
            env['DA_HELPER_PROOF_SOURCE_OVERLAY'] = '1'
        try:
            return docker.exec(NAME, command, env=env, check_result=False)
        except subprocess.TimeoutExpired as error:
            if detached:
                save_exec_result('helper-exec', error)
            raise

    def apply(timeout='90'):
        return docker.exec(NAME, ['da-executor', 'apply-runtime', '--revision', 'r1'],
                           env={'DA_DRAIN_TIMEOUT': timeout}, check_result=False)

    def send(subject):
        # The sender exits/disconnects after SMTP 250. Real Exim owns the delivery.
        output = docker.exec(NAME, ['/usr/share/docassemble/executor-venv/bin/python', '-c', '''
import smtplib, sys
with smtplib.SMTP('127.0.0.1') as smtp:
    assert smtp.sendmail('sender@helper.test', 'helper@helper.test',
                        'From: sender@helper.test\\nTo: helper@helper.test\\nSubject: ' + sys.argv[1] + '\\n\\nactual body') == {}
print('SMTP accepted')
''', subject])
        check('SMTP accepted' in output.stdout, 'SMTP did not retain responsibility')

    def messages():
        output = docker.exec(NAME, [RUNTIME, '/opt/docassemble-fixture/helper_client.py', 'messages'], user='www-data', env=PROOF_ENV)
        return json.loads(output.stdout.split('PROOF_JSON=')[-1])

    def queue():
        text = docker.exec(NAME, ['exim4', '-bp']).stdout
        return set(re.findall(r'\b[0-9A-Za-z]{6}-[0-9A-Za-z]{11}-[0-9A-Za-z]{4}\b', text)), text

    def infra_pids():
        return {role: int(docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'pid', role]).stdout)
                for role in ('cron', 'exim4')}

    def nested_receipts(*labels, mode='draining'):
        for label in labels:
            for protocol in ('nlp', 'uno'):
                receipt = json.loads(docker.read_file(NAME, SERVICE_ROOT + label + '.' + protocol + '-completed'))
                check(receipt['uid'] == 33 and receipt['mode'] == mode, 'nested receiver did not finish during closure: ' + label)
                if protocol == 'nlp':
                    check(receipt['result'] == 'cats', 'Pattern wire result missing')
                evidence.write_json('nested-receipts/' + label + '-' + protocol + '.json', receipt)

    def backup_invocations(label):
        receipts = [json.loads(line) for line in docker.read_file(NAME, BACKUP_RECEIPTS).splitlines()]
        return [receipt for receipt in receipts if receipt['label'] == label]

    def control_fences(label):
        before, pids = uv_log(), infra_pids()
        commands = [(['bash', '/usr/share/docassemble/webapp/' + script], kind)
                    for script, kind in CONTROL_HOOKS.items()]
        commands += [([entrypoint, '--version'], 'certbot') for entrypoint in
                     ('certbot', '/usr/share/docassemble/local3.14/bin/certbot')]
        receipts = []
        for command, kind in commands:
            output = docker.exec(NAME, command, env={
                'DA_RUNTIME_VENV': '/missing-runtime',
                'DASUPERVISORUSERNAME': 'helper-proof-user',
                'DASUPERVISORPASSWORD': 'helper-proof-credential-must-not-appear',
            }, check_result=False)
            # 04b3 refuses before runtime activation or credential-bearing control.
            reason = 'managed consumer: ' + kind + ' is unsupported in managed mode; launch refused\n'
            check(output.returncode == 75 and output.stdout == '' and output.stderr == reason,
                  'certbot/control hook did not give its fixed credential-free refusal: ' + command[0] + ':' + kind)
            receipts.append({'command': command, 'returncode': output.returncode,
                             'stdout': output.stdout, 'stderr': output.stderr})
        check(uv_log() == before and infra_pids() == pids, 'refused control hook mutated runtime or infrastructure')
        evidence.write_json(label + '/control-refusals.json', receipts)

    def fences():
        control_fences('closed-' + status()['admission']['mode'])
        for kind in ('cron', 'hourly', 'daily', 'weekly', 'monthly', 'mail'):
            output = docker.exec(NAME, ['da-executor', 'run-helper', '--kind', kind],
                                 env={'DA_RUNTIME_VENV': '/missing-runtime'}, check_result=False)
            check(output.returncode == 75, 'closed helper CLI entered runtime: ' + kind)
        for script in ('run-cron.sh', 'process-email.sh'):
            check(docker.exec(NAME, ['bash', '/usr/share/docassemble/webapp/' + script],
                              check_result=False).returncode == 75, 'closed manual shell entered runtime')
        for kind in ('hourly', 'daily', 'weekly', 'monthly'):
            marker = '/usr/share/docassemble/files/closed-backup-' + kind
            if kind in ('hourly', 'daily'):
                docker.exec_shell(NAME, 'echo independent-closed-tick > ' + marker)
            label = 'denied-' + kind
            before = backup_invocations(label)
            check(helper(label, kind, script=True).returncode == 75, 'closed runtime tick was not skipped')
            check(not present(label + '.started'), 'closed schedule executed interview code')
            # 04b3 keeps one root backup phase independent of refused interview ticks.
            expected = [{'label': label, 'phase': 'hourly' if kind == 'hourly' else 'full', 'uid': 0,
                         'nested_helper': False, 'helper_completed': False,
                         'accepted_caller': False, 'in_cron': False}] if kind in ('hourly', 'daily') else []
            check(backup_invocations(label) == before + expected,
                  'closed tick duplicated, nested or skipped its independent backup: ' + kind)
            if kind in ('hourly', 'daily'):
                check(docker.read_file(NAME, '/usr/share/docassemble/backup/files/closed-backup-' + kind) ==
                      'independent-closed-tick\n', 'runtime closure stopped independent scheduled backup')

    def capture(label):
        evidence.write_json(label + '/status.json', status())
        evidence.write(label + '/supervisor.txt', docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'status']).stdout)
        evidence.write(label + '/processes.txt', docker.exec(NAME, ['ps', '-eo', 'user,pid,ppid,args']).stdout)
        evidence.write(label + '/mail-log.txt', docker.read_file(NAME, '/var/log/exim4/mainlog'))
        evidence.write(label + '/worker-log.txt', docker.read_file(NAME, '/usr/share/docassemble/log/managed-worker-normal.log'))
        evidence.write(label + '/queue.txt', queue()[1])
        evidence.write(label + '/uv-invocations.txt', uv_log())
        evidence.write(label + '/logs.txt', docker.logs(NAME))
        evidence.write(label + '/backup-invocations.jsonl', docker.read_file(NAME, BACKUP_RECEIPTS))
        for name in ('admission.json', 'lifecycle.json', 'applied.json'):
            evidence.write(label + '/' + name, docker.read_file(NAME, STATE + name))
        results.append({'scenario': label, 'ok': True})
        evidence.note('PASS ' + label)

    def ready():
        wait(lambda: http.status('/health_check?ready=1') == 200, 900)
        wait(lambda: all(pid > 0 for pid in infra_pids().values()))
        wait(lambda: docker.exec(NAME, [RUNTIME, '/opt/docassemble-fixture/service_client.py', 'normal'],
                                user='www-data', env=PROOF_ENV, check_result=False).returncode == 0, 120)

    def wrapper_pid(kind):
        text = docker.exec(NAME, ['ps', '-eo', 'pid,args']).stdout
        return next(int(line.split()[0]) for line in text.splitlines()
                    if '/bin/da-executor run-helper --kind ' + kind in line)

    try:
        evidence.write('image-id.txt', docker.run(['image', 'inspect', args.image, '--format', '{{.Id}}']).stdout)
        cold_setup = ('mkdir -p /tmp/da-helper-proof /tmp/da-service-proof; '
                      'chmod 777 /tmp/da-helper-proof /tmp/da-service-proof; '
                      'mv /etc/docassemble/managed-backup.json /tmp/managed-backup.json; '
                      'cp /opt/docassemble-fixture/helper_probe_hooks.py /opt/docassemble-executor/src/sitecustomize.py; '
                      'export PYTHONPATH=/opt/docassemble-executor/src:/opt/docassemble-fixture')
        if source_overlay:
            script = 'from pathlib import Path\n'
            for source, destination in fixture_sources:
                script += ('p = Path(' + repr(destination) + ')\n'
                           'p.parent.mkdir(parents=True, exist_ok=True)\n'
                           'p.write_bytes(' + repr((repo / 'uv-experiment' / source).read_bytes()) + ')\n'
                           'p.chmod(0o644)\n')
            cold_setup = '/usr/share/docassemble/executor-venv/bin/python -c ' + shlex.quote(script) + '; ' + cold_setup
        docker.start(NAME, args.image, env={'DA_EXIM_QUEUE_INTERVAL': '5s'}, cold_setup=cold_setup)
        ready()
        # 04b3 qualifies only these explicit OS entries, not the combined legacy
        # schedules, standalone duplicate backups, or certbot/control hooks.
        expected_jobs = [
            '17 * * * * root /etc/cron.hourly/docassemble >> /usr/share/docassemble/log/managed-schedules.log 2>&1',
            '25 6 * * * root /etc/cron.daily/docassemble >> /usr/share/docassemble/log/managed-schedules.log 2>&1',
            '47 6 * * 7 root /etc/cron.weekly/docassemble >> /usr/share/docassemble/log/managed-schedules.log 2>&1',
            '52 6 1 * * root /etc/cron.monthly/docassemble >> /usr/share/docassemble/log/managed-schedules.log 2>&1',
        ]
        crontab = docker.read_file(NAME, '/etc/crontab')
        jobs = [line.strip() for line in crontab.splitlines() if line.strip()
                and not line.lstrip().startswith('#') and not re.match(r'^[A-Z_]+=', line)]
        check(jobs == expected_jobs, 'qualified managed cron entries changed or included backup/control duplicates')
        cron_files = docker.exec(NAME, ['find', '/etc/cron.d', '-type', 'f', '-print0']).stdout
        for path in filter(None, cron_files.split('\0')):
            content = docker.exec(NAME, ['cat', path]).stdout
            extra_jobs = [line.strip() for line in content.splitlines() if line.strip()
                          and not line.lstrip().startswith('#') and not re.match(r'^[A-Z_]+=', line)]
            check(not extra_jobs, 'unqualified backup/control jobs were installed alongside managed schedules: '
                  + path + ':\n' + '\n'.join(extra_jobs))
        evidence.write('managed-crontab.txt', crontab)
        gate = docker.read_file(NAME, '/usr/share/docassemble/webapp/managed-consumer.sh')
        check(gate == (repo / 'Docker/managed-consumer.sh').read_text() and
              'cron-*) exec "$da_consumer_executor" run-schedule --kind "${1#cron-}" ;;' in gate,
              'installed cron gate no longer dispatches admitted managed schedule phases')
        for kind in ('hourly', 'daily', 'weekly', 'monthly'):
            path = '/etc/cron.' + kind + '/docassemble'
            script = docker.read_file(NAME, path)
            check(script == (repo / ('Docker/cron/docassemble-cron-' + kind + '.sh')).read_text() and
                  'source "$da_gate" cron-' + kind + ' || exit 75' in script,
                  'installed schedule content bypassed the admitted helper path: ' + path)
            evidence.write('installed-schedules/' + kind + '.sh', script)
        control_fences('normal')
        check(all(pid > 0 for pid in infra_pids().values()), 'backup configuration absence disabled infrastructure')
        docker.exec(NAME, ['mv', '/tmp/managed-backup.json', '/etc/docassemble/managed-backup.json'])
        docker.exec(NAME, ['/usr/share/docassemble/executor-venv/bin/python', '-c', '''
import json
from pathlib import Path
import sys
from docassemble_executor.image_baseline import provision_image_baseline
from docassemble_executor.paths import ExecutorPaths

root = Path('/tmp/da-backup-proof')
root.mkdir(mode=0o755)
descriptor = provision_image_baseline(ExecutorPaths.from_env(), root / 'image-baseline')
retained = root / 'retained'
retained.mkdir(mode=0o700)
# Backup-local inputs: globally enabling the store would add durable workers
# and change the helper-lifecycle topology being qualified.
environment = Path('/etc/docassemble/managed-backup.json')
inputs = json.loads(environment.read_text())
inputs.update(DA_DURABLE_WORK_STORE=str(retained), DA_QUALIFIED_IMAGE_BASELINE=str(descriptor))
environment.write_text(json.dumps(inputs))
environment.chmod(0o600)
(root / 'probe.py').write_text(sys.argv[1])
(root / 'receipts.jsonl').touch(mode=0o600)
for script, phase in [('backup-hourly.sh', 'hourly'), ('backup.sh', 'full')]:
    path = Path('/usr/share/docassemble/webapp') / script
    original = root / script
    original.write_bytes(path.read_bytes())
    path.write_text('#!/bin/bash\\n'
                    '/usr/share/docassemble/executor-venv/bin/python /tmp/da-backup-proof/probe.py '
                    + phase + ' || exit 1\\nexec /bin/bash ' + str(original) + ' "$@"\\n')
''', BACKUP_PROBE])
        capture('independent-infrastructure-without-backup-configuration')
        qualification = status()['consumer_qualification']
        check({'cron', 'mail'} <= set(qualification['enabled']) and
              qualification.get('helper_mail_proof') == 'os-cron-exim-queue', 'proved helper/mail protocols not declared')
        check(not qualification['pending'] and qualification['complete'] is True,
              'full supported qualification is missing')
        docker.exec(NAME, [RUNTIME, '/opt/docassemble-fixture/helper_client.py', 'seed'], user='www-data', env=PROOF_ENV)
        docker.exec_shell(NAME, 'install -d -o www-data -g www-data /tmp/SavedFile-helper-proof-old; '
                          'touch -d "2 hours ago" /tmp/SavedFile-helper-proof-old; '
                          'echo hourly-copy-proof > /usr/share/docassemble/files/helper-hourly-proof')
        for kind in ('hourly', 'daily', 'weekly', 'monthly'):
            label = 'manual-' + kind
            output = helper(label, kind, script=True)
            evidence.write('schedule-output/' + kind + '.txt', output.stdout + output.stderr)
            check(output.returncode == 0, 'actual schedule failed: ' + output.stdout + output.stderr)
            check(json.loads(docker.read_file(NAME, ROOT + label + '.completed')) == {'kind': kind, 'uid': 33},
                  'root schedule lost the requested interview kind or payload privilege')
            # The 04b3 informational line is not a refusal: prove the separate
            # backup executes once, as root, after and outside the admitted helper.
            expected = [{'label': label, 'phase': 'hourly' if kind == 'hourly' else 'full', 'uid': 0,
                         'nested_helper': False, 'helper_completed': True,
                         'accepted_caller': False, 'in_cron': False}] if kind in ('hourly', 'daily') else []
            check(backup_invocations(label) == expected, 'managed schedule lost independent backup separation: ' + kind)
            if kind == 'hourly':
                check(docker.exec(NAME, ['test', '-e', '/tmp/SavedFile-helper-proof-old'], check_result=False).returncode != 0,
                      'admitted hourly cache cleanup did not run')
                check(docker.read_file(NAME, '/usr/share/docassemble/backup/files/helper-hourly-proof') == 'hourly-copy-proof\n',
                      'independent hourly file-copy phase was lost')
        for kind in ('hourly', 'daily'):
            label = 'helper-only-' + kind
            output = helper(label, kind)
            check(output.returncode == 0 and
                  json.loads(docker.read_file(NAME, ROOT + label + '.completed')) == {'kind': kind, 'uid': 33},
                  'admitted helper-only tick failed: ' + output.stdout + output.stderr)
            check(not backup_invocations(label), 'interview helper duplicated the independent backup phase')
        direct = docker.exec(NAME, ['bash', '/usr/share/docassemble/webapp/run-cron.sh', 'cron_weekly'],
                             env=dict(PROOF_ENV, DA_HELPER_PROOF_LABEL='root-direct-cron'), check_result=False)
        check(direct.returncode == 0 and json.loads(docker.read_file(NAME, ROOT + 'root-direct-cron.completed')) ==
              {'kind': 'weekly', 'uid': 33}, 'manual cron argument or payload privilege was lost')
        release('root-mail')
        manual = docker.run(['exec', '-i', '-e', 'PYTHONPATH=' + PROOF_ENV['PYTHONPATH'], NAME,
                             'bash', '/usr/share/docassemble/webapp/process-email.sh'],
                            input_text='From: sender@helper.test\nTo: helper@helper.test\nEnvelope-to: helper@helper.test\nSubject: hold:root-mail\n\nactual body\n\n')
        manual_receipt = json.loads(docker.read_file(NAME, ROOT + 'root-mail.completed'))
        check(manual.returncode == 0 and manual_receipt['uid'] == 33 and manual_receipt['in_cron'] is False,
              'root manual mail did not execute the actual handler as www-data in normal mail context')
        check(len([message for message in messages() if message['subject'] == 'hold:root-mail']) == 1,
              'one root manual delivery executed the actual handler more than once')
        check(docker.exec(NAME, ['test', '-s', '/usr/share/docassemble/backup/postgres/docassemble'], check_result=False).returncode == 0,
              'separated daily backup did not produce an actual PostgreSQL dump')
        docker.exec_shell(NAME, 'cp /etc/docassemble/managed-backup.json /tmp/untrusted-backup.json; chown www-data:www-data /tmp/untrusted-backup.json')
        untrusted = docker.exec(NAME, ['da-executor', 'run-backup', '--environment', '/tmp/untrusted-backup.json'], check_result=False)
        check(untrusted.returncode == 75, 'root backup accepted a runtime-owned environment file')
        docker.exec_shell(NAME, '''mkdir -p /opt/trusted-backup-tools
cat > /opt/trusted-backup-tools/rsync <<'EOF'
#!/bin/bash
echo "$HOME:$LANG" >> /tmp/trusted-backup-tool.used
exec /usr/bin/rsync "$@"
EOF
chmod 755 /opt/trusted-backup-tools/rsync''')
        docker.exec(NAME, ['/usr/share/docassemble/executor-venv/bin/python', '-c',
                          "import json; from pathlib import Path; p=Path('/etc/docassemble/managed-backup.json'); "
                          "v=json.loads(p.read_text()); v.update(PATH='/opt/trusted-backup-tools:/usr/bin:/bin', "
                          "HOME='/tmp/backup-proof-home', LANG='C.UTF-8'); "
                          "p=Path('/opt/trusted-backup-tools/environment.json'); p.write_text(json.dumps(v)); p.chmod(0o600)"])
        tools = docker.exec(NAME, ['da-executor', 'run-backup', '--phase', 'hourly', '--environment',
                                  '/opt/trusted-backup-tools/environment.json'], check_result=False)
        check(tools.returncode == 0 and docker.read_file(NAME, '/tmp/trusted-backup-tool.used').strip() ==
              '/tmp/backup-proof-home:C.UTF-8', 'trusted backup tool PATH/HOME/locale were replaced')
        bootstrap = docker.exec(NAME, ['/usr/share/docassemble/executor-venv/bin/python', '/opt/docassemble-fixture/check_helper_mail.py'], env=PROOF_ENV)
        evidence.write('smtp-bootstrap.txt', bootstrap.stdout + bootstrap.stderr)
        wait(lambda: present('incoming-email.completed'))
        check(json.loads(docker.read_file(NAME, ROOT + 'incoming-email.completed'))['uid'] == 33,
              'real incoming-email background action did not execute')
        check(not queue()[0], 'successful incoming delivery remained queued')
        capture('actual-schedules-mail-body-and-independent-backup')

        send('hold:mail-commit')
        wait(lambda: present('mail-commit.started'))
        release('mail-commit')
        wait(lambda: present('mail-commit.published'))
        try:
            wait(lambda: present('email-event-hold:mail-commit.completed'), 20)
            check(not present('mail-commit.completed'), 'publication barrier did not hold the actual producer')
        finally:
            docker.exec(NAME, ['touch', ROOT + 'mail-commit.commit-release'])
        wait(lambda: present('mail-commit.completed') and not queue()[0])
        check(len([message for message in messages() if message['subject'] == 'hold:mail-commit']) == 1,
              'one successful SMTP delivery persisted more than one message')
        capture('actual-incoming-event-sees-committed-message')

        release('mail-output')
        send('hold:mail-output')
        wait(lambda: present('mail-output.completed') and not queue()[0], 30)
        check('managed-mail-output:' in docker.read_file(NAME, '/usr/share/docassemble/log/managed-mail.log'),
              'actual mail output did not use a stable payload log')
        capture('accepted-mail-output-does-not-trigger-exim-kill-limit')

        # A genuine OS cron tick while normal service is open.
        docker.exec_shell(NAME, '''cat > /etc/cron.d/da-helper-proof <<'EOF'
SHELL=/bin/sh
DA_UV_MANAGED=true
DA_CONFIG_FILE=/usr/share/docassemble/config/config.yml
DA_RUNTIME_VENV=/usr/share/docassemble/local3.12
DA_MANAGED_STATE_DIR=/var/lib/docassemble/managed
* * * * * root DA_HELPER_PROOF_LABEL=os-cron DA_HELPER_PROOF_HOLD=true /etc/cron.weekly/docassemble >> /tmp/da-helper-proof/cron.log 2>&1
EOF
chmod 644 /etc/cron.d/da-helper-proof''')
        wait(lambda: present('os-cron.started'), 100)
        docker.exec(NAME, ['rm', '/etc/cron.d/da-helper-proof'])
        before = uv_log()
        with Pool(max_workers=1) as pool:
            operation = pool.submit(apply, '3')
            wait(lambda: status()['admission']['mode'] == 'draining')
            docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'restart', 'cron'])
            old = infra_pids()['cron']
            docker.exec(NAME, ['kill', '-KILL', str(old)])
            wait(lambda: infra_pids()['cron'] not in (0, old))
            check(operation.result(timeout=90).returncode == 3 and uv_log() == before,
                  'real cron death released an accepted scheduled handler or forced drain')
        release('os-cron')
        wait(lambda: present('os-cron.completed'))
        check(json.loads(docker.read_file(NAME, ROOT + 'os-cron.completed'))['uid'] == 33, 'real cron did not finish admitted interview work')
        capture('actual-os-cron-dispatch-and-daemon-death-exclusion')

        with Pool(max_workers=2) as pool:
            job = pool.submit(helper, 'cron-nested', 'weekly', True, True)
            wait(lambda: present('cron-nested.started'))
            send('hold:mail-nested')
            wait(lambda: present('mail-nested.started'))
            before, pids = uv_log(), infra_pids()
            operation = pool.submit(apply, '150')
            wait(lambda: status()['admission']['mode'] == 'draining')
            fences()
            send('deferred-after-closure')
            wait(lambda: 'defer' in docker.read_file(NAME, '/var/log/exim4/mainlog').split('deferred-after-closure')[-1]
                 or len(queue()[0]) >= 2)
            ids, text = queue()
            check(len(ids) == 2, 'Exim did not retain active and deferred messages')
            evidence.write('closed-queue.txt', text)
            wait(lambda: 'returned 75' in docker.read_file(NAME, '/var/log/exim4/mainlog'))
            check(not any(item['subject'] == 'deferred-after-closure' for item in messages()), 'closed mail handler executed')
            check(infra_pids() == pids and uv_log() == before, 'closure stopped infrastructure or mutated under accepted work')
            # Restart/actual autorestart only the OS listeners, not admitted
            # handler trees. Exim's spool locks and guardian authority survive.
            for role in ('cron', 'exim4'):
                docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'restart', role])
                old = infra_pids()[role]
                docker.exec(NAME, ['kill', '-KILL', str(old)])
                wait(lambda role=role, old=old: infra_pids()[role] not in (0, old))
            check(uv_log() == before and not operation.done(), 'infrastructure restart released accepted helpers')
            release('cron-nested', 'mail-nested')
            check(job.result(timeout=120).returncode == 0, 'accepted cron failed after closure')
            wait(lambda: present('mail-nested.completed'), 120)
            nested_receipts('cron-nested', 'mail-nested')
            drained = operation.result(timeout=300)
            evidence.write('nested-apply.txt', drained.stdout + drained.stderr)
            check(drained.returncode == 0, 'accepted helpers did not drain naturally: ' + drained.stdout + drained.stderr)
            check(len(uv_log().splitlines()) == len(before.splitlines()) + 1, 'drain did not perform exactly one later sync')
        ready()
        # Observe the actual daemon queue runner; do not force/thaw messages.
        wait(lambda: not queue()[0], 150)
        completed = messages()
        check(any(item['subject'] == 'hold:mail-nested' for item in completed) and
              any(item['subject'] == 'deferred-after-closure' for item in completed), 'Exim did not eventually redeliver retained mail')
        for item in completed:
            check([part['index'] for part in item['attachments']] == [0, 1], 'retained mail lost linked header/body attachments')
            check(bytes.fromhex(item['attachments'][1]['data']) == b'actual body\n\n', 'retained Exim body changed')
            wait(lambda item=item: present('email-event-' + item['subject'] + '.completed'), 120)
            received = json.loads(docker.read_file(NAME, ROOT + 'email-event-' + item['subject'] + '.completed'))
            check(received == {'uid': 33, 'subject': item['subject'], 'body': 'actual body\n\n'}, 'Actual application incoming_email did not receive the retained body')
        evidence.write_json('retained-delivered-messages.json', completed)
        capture('accepted-cron-mail-nested-drain-and-real-redelivery')

        with Pool(max_workers=2) as pool:
            job = pool.submit(helper, 'missed-schedule', 'weekly', True)
            wait(lambda: present('missed-schedule.started'))
            before, pids = uv_log(), infra_pids()
            operation = pool.submit(apply, '100')
            wait(lambda: status()['admission']['mode'] == 'draining')
            docker.exec_shell(NAME, '''cat > /etc/cron.d/da-helper-proof <<'EOF'
SHELL=/bin/sh
DA_UV_MANAGED=true
DA_CONFIG_FILE=/usr/share/docassemble/config/config.yml
DA_RUNTIME_VENV=/usr/share/docassemble/local3.12
DA_MANAGED_STATE_DIR=/var/lib/docassemble/managed
* * * * * root date > /tmp/da-helper-proof/missed-tick; DA_HELPER_PROOF_LABEL=must-not-replay /etc/cron.weekly/docassemble >> /tmp/da-helper-proof/missed.log 2>&1
EOF
chmod 644 /etc/cron.d/da-helper-proof''')
            wait(lambda: present('missed-tick'), 90)
            wait(lambda: 'unavailable' in docker.read_file(NAME, ROOT + 'missed.log'))
            docker.exec(NAME, ['rm', '/etc/cron.d/da-helper-proof'])
            check(not present('must-not-replay.started'), 'closed OS schedule entered runtime')
            check(operation.result(timeout=120).returncode == 3, 'long schedule was force-finished instead of aborting drain')
            check(uv_log() == before and infra_pids() == pids, 'timeout changed infrastructure or invoked sync')
            check(http.status('/health_check?ready=1') == 200, 'timeout did not resume unchanged service')
            release('missed-schedule')
            check(job.result(timeout=60).returncode == 0, 'accepted schedule was cancelled')
            check(not present('must-not-replay.started'), 'missed schedule was replayed after reopen')
        evidence.write('missed-schedule.txt', docker.read_file(NAME, ROOT + 'missed.log'))
        capture('actual-missed-tick-timeout-and-no-replay')

        detached_active = True
        with Pool(max_workers=2) as pool:
            try:
                diagnostic('start event collector', start_events)
                job = register_exec('helper-exec', pool.submit(helper, 'detached-helper', 'monthly', True, True, True))
                wait(lambda: present('detached-helper.started') and present('detached-helper.parent-exited'))
                diagnostic('snapshot 01-started', lambda: detached_snapshot('01-started'))
                before = uv_log()
                operation = register_exec('controller-exec', pool.submit(docker.exec_shell, NAME,
                                        'echo $$ > /tmp/helper-controller.pid; exec da-executor apply-runtime --revision r1',
                                        check_result=False))
                wait(lambda: status()['admission']['mode'] == 'draining')
                docker.exec(NAME, ['kill', '-KILL', str(wrapper_pid('monthly'))])
                diagnostic('snapshot 02-after-wrapper-kill', lambda: detached_snapshot('02-after-wrapper-kill'))
                try:
                    result = job.result(timeout=30)
                except BaseException as error:
                    save_exec_result('helper-exec-wait', error)
                    raise
                save_exec_result('helper-exec', result)
                check(result.returncode != 0, 'helper wrapper did not die')
                docker.exec_shell(NAME, 'kill -KILL $(cat /tmp/helper-controller.pid)')
                diagnostic('snapshot 03-after-controller-kill', lambda: detached_snapshot('03-after-controller-kill'))
                try:
                    result = operation.result(timeout=30)
                except BaseException as error:
                    save_exec_result('controller-exec-wait', error)
                    raise
                save_exec_result('controller-exec', result)
                check(result.returncode != 0, 'applying controller did not die')
                try:
                    result = apply('2')
                except BaseException as error:
                    save_exec_result('retry-apply-exec', error)
                    raise
                save_exec_result('retry-apply-exec', result)
                check(result.returncode == 3 and uv_log() == before, 'FD-closing descendant lost runtime authority after deaths')
                time.sleep(6)  # Give the independent role-incarnation monitor multiple turns.
                check(status()['admission']['mode'] == 'closed', 'monitor reopened an interrupted handoff as preparing')
                check(http.status('/user/sign-in') == 503, 'controller interruption reopened admission')
                diagnostic('snapshot 04-before-release', lambda: detached_snapshot('04-before-release'))
                release('detached-helper')
                wait(lambda: present('detached-helper.completed'), 120)
                diagnostic('snapshot 05-completed', lambda: detached_snapshot('05-completed'))
                diagnostic('export 05-completed', lambda: detached_export('05-completed'))
                # A dead controller leaves conservatively closed/unknown status;
                # a timed-out retry must not reopen it. Live callers still finish.
                nested_receipts('detached-helper', mode='closed')
                try:
                    result = apply()
                except BaseException as error:
                    save_exec_result('final-apply-exec', error)
                    raise
                save_exec_result('final-apply-exec', result)
                check(result.returncode == 0, 'detached accepted helper did not finish naturally')
                ready()
                capture('detached-fd-closing-helper-wrapper-and-controller-death')
                diagnostic('export 06-success', lambda: detached_export('06-success'))
                detached_active = False
            except BaseException:
                detached_failure()
                raise
            finally:
                diagnostic('stop event collector and save results', stop_events)

        send('hold:mail-crash')
        wait(lambda: present('mail-crash.started'))
        before = uv_log()
        retained = queue()[0]
        check(len(retained) == 1, 'active incoming mail is not present in Exim spool')
        with Pool(max_workers=1) as pool:
            operation = pool.submit(apply, '3')
            wait(lambda: status()['admission']['mode'] == 'draining')
            docker.exec(NAME, ['kill', '-KILL', str(wrapper_pid('mail'))])
            check(operation.result(timeout=90).returncode == 3 and uv_log() == before, 'mail wrapper death released accepted handler authority')
            check(queue()[0], 'Exim lost mail after pipe-wrapper death')
            release('mail-crash')
            wait(lambda: present('mail-crash.completed'), 120)
            check(retained <= queue()[0], 'original mail was bounced/lost after surviving handler completion')
            # freeze_signal plus automatic thaw must redeliver the original
            # message through the normal queue runner, with no forced retry.
            wait(lambda: not queue()[0], 150)
            delivered = [item for item in messages() if item['subject'] == 'hold:mail-crash']
            check(len(delivered) >= 2, 'wrapper crash was not followed by actual redelivery')
            wait(lambda: present('email-event-hold:mail-crash.completed'))
        capture('accepted-mail-survives-wrapper-death-and-redelivers')

        recovery = docker.exec(NAME, ['da-executor', 'recover', '--reason', 'helpers-mail-proof'], check_result=False)
        check(recovery.returncode == 0 and status().get('recovery_available') is True, 'restricted recovery failed')
        fences()
        before = uv_log()
        send('retained-in-recovery')
        wait(lambda: bool(queue()[0]))
        observed = docker.exec(NAME, ['runuser', '-u', 'postgres', '--', 'psql', '-d', 'docassemble', '-Atqc',
                                      "SELECT count(*) FROM email WHERE subject='retained-in-recovery'"])
        check(observed.stdout.strip() == '0', 'recovery dispatched incoming runtime work')
        for role in ('cron', 'exim4'):
            docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'restart', role])
            old = infra_pids()[role]
            docker.exec(NAME, ['kill', '-KILL', str(old)])
            wait(lambda role=role, old=old: infra_pids()[role] not in (0, old))
        wait(lambda: bool(queue()[0]))
        check(uv_log() == before, 'infrastructure autorestart synced runtime')
        backup = docker.exec(NAME, ['da-executor', 'run-backup'])
        check(backup.returncode == 0, 'independent backup unavailable in recovery')
        check(status()['consumer_qualification']['complete'] is True and not status()['normal_ready'], 'qualified recovery claimed normal readiness')
        capture('recovery-runtime-fences-independent-backup-and-infra-autorestart')
        return 0
    except Exception as error:
        if not (root / 'failure.txt').exists():
            def failure_text(text):
                return redact(text) if detached_active else text
            evidence.write('failure.txt', failure_text(str(error) + '\n' + docker.logs(NAME)))
            evidence.write('mail-log.txt', failure_text(docker.read_file(NAME, '/var/log/exim4/mainlog')))
            evidence.write('worker-log.txt', failure_text(docker.read_file(NAME, '/usr/share/docassemble/log/managed-worker-normal.log')))
            evidence.write('backup-log.txt', failure_text(docker.read_file(NAME, '/usr/share/docassemble/log/managed-backup.log')))
            evidence.write('backup-invocations.jsonl', failure_text(docker.read_file(NAME, BACKUP_RECEIPTS)))
        raise
    finally:
        if detached_active and not failure_exported:
            detached_failure()
        evidence.write_json('results.json', results)
        if not args.keep_container:
            docker.rm(NAME)


if __name__ == '__main__':
    raise SystemExit(main())
