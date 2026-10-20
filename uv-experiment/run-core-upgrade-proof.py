#!/usr/bin/env python3
"""Capture ticket 14b's changed-inventory fixture release container proof."""
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import threading
import time

from proof_resources import cleanup, refuse_existing


REPAIR_WORKERS = ('durable-preparation', 'durable-installation')
SUPERVISOR = '/var/run/docassemble/managed-supervisor/supervisord.conf'


# These diagnostic projections always run before cleanup, on success or failure.
# They inform recovery investigation, not the repair decision or raw provenance.
DIAGNOSTIC_TIMEOUT = 15
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_DISCOVERY_ENTRIES = 2048


def redact_capabilities(text):
    capability = os.environ.get('DA_ACCEPTED_CALLER')
    if capability:
        for value in (capability, json.dumps(capability)[1:-1]):
            text = text.replace(value, '[redacted-capability]')
    return re.sub(r'\b[0-9a-fA-F]{64}\b', '[redacted-capability]', text)


def excluded_path(path):
    for part in Path(path).parts:
        name = part.lower()
        if name == 'callers' or 'environ' in name or re.search(r'(^|[._-])env([._-]|$)', name):
            return 'caller/environment material excluded'
        if 'capability' in name:
            return 'capability material excluded'
    return None


# Discovery never opens file contents or follows links, including parent links.
DISCOVER_ARTIFACTS = r'''
import json
from pathlib import Path
import re
import stat
import sys
root, recursive, limit = Path(sys.argv[1]), sys.argv[2] == 'tree', int(sys.argv[3])
records = []
def visit(path):
    if len(records) >= limit:
        raise RuntimeError('discovery entry limit reached')
    record = {'source': str(path), 'exists': True if path != root else None}
    records.append(record)
    try:
        for part in path.parts:
            name = part.lower()
            if name == 'callers' or 'environ' in name or re.search(r'(^|[._-])env([._-]|$)', name) or 'capability' in name:
                record['excluded'] = 'caller/environment/capability material excluded'
                return
        for parent in reversed(path.parents):
            if parent.is_symlink():
                record['excluded'] = 'symlink parent excluded'
                return
        info = path.lstat()
        record.update(exists=True, size_before=info.st_size)
        if stat.S_ISDIR(info.st_mode):
            record['directory'] = True
            if path.name.startswith('fixture-release-'):
                record['excluded'] = 'input wheels intentionally not retained'
            elif recursive or path == root:
                for child in path.iterdir():
                    if recursive or child.name.endswith('.log'):
                        visit(child)
        elif not stat.S_ISREG(info.st_mode):
            record['excluded'] = 'non-regular file excluded'
    except FileNotFoundError:
        record['exists'] = False
    except OSError as error:
        record['error'] = type(error).__name__
try:
    visit(root)
except Exception as error:
    records.append({'source': str(root), 'exists': None, 'error': type(error).__name__})
print(json.dumps(records))
'''


class DiagnosticRetention:
    def __init__(self, run, name, python, evidence):
        self.run, self.name, self.python, self.evidence = run, name, python, evidence
        self.records = []

    def retain(self, command, **kwargs):
        try:
            return self.run(command, check=False, timeout=DIAGNOSTIC_TIMEOUT, **kwargs)
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(command, -1, '', 'TimeoutExpired')
        except Exception as error:
            # Exception messages can themselves contain raw capabilities.
            return subprocess.CompletedProcess(command, -1, '', type(error).__name__)

    def write_text(self, destination, text, source, exit_code=0):
        record = {'source': source, 'destination': destination, 'exists': None, 'exit': exit_code,
                  'retained': False, 'redaction_applied': False}
        self.records.append(record)
        try:
            data = redact_capabilities(text).encode('utf-8')
            record.update(size_before=len(text.encode('utf-8')), size_after=len(data))
            self.write_bytes(destination, data)
            record.update(retained=True, mode='redacted', redaction_applied=True)
        except Exception as error:
            record['error'] = type(error).__name__
        return record

    def write_bytes(self, destination, data):
        target = self.evidence / destination
        if target.is_absolute() and not target.is_relative_to(self.evidence):
            raise ValueError('destination outside evidence')
        if '..' in Path(destination).parts:
            raise ValueError('unsafe destination')
        for path in (target, *target.parents):
            if path.is_symlink():
                raise ValueError('symlink destination')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def copy_archive(self, source):
        # Drain into bounded RAM, never a raw temporary file. A hung docker cp is
        # killed by retain's timeout; excess tar output is discarded, not saved.
        read_fd, write_fd = os.pipe()
        chunks, state = [], {'size': 0, 'overflow': False}
        limit = MAX_ARTIFACT_BYTES + 1024 * 1024

        def drain():
            try:
                with os.fdopen(read_fd, 'rb') as stream:
                    while chunk := stream.read(65536):
                        state['size'] += len(chunk)
                        if state['size'] <= limit:
                            chunks.append(chunk)
                        else:
                            state['overflow'] = True
            except Exception as error:
                state['error'] = type(error).__name__

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            result = self.retain(['docker', 'cp', self.name + ':' + source, '-'], stdout=write_fd)
        finally:
            os.close(write_fd)
        reader.join(timeout=DIAGNOSTIC_TIMEOUT)
        if reader.is_alive() or state.get('error') or state['overflow']:
            return result, None, 'archive capture incomplete or size limit exceeded'
        return result, b''.join(chunks), None

    def discover(self, source, mode):
        result = self.retain(['docker', 'exec', self.name, self.python, '-c', DISCOVER_ARTIFACTS,
                              source, mode, str(MAX_DISCOVERY_ENTRIES)])
        try:
            if result.returncode:
                raise ValueError('discovery failed')
            entries = json.loads(result.stdout)
            if not isinstance(entries, list):
                raise ValueError('invalid discovery response')
            return entries
        except Exception as error:
            self.records.append({'source': source, 'destination': None, 'exists': None,
                                 'exit': result.returncode, 'retained': False,
                                 'error': type(error).__name__, 'stderr': timeout_text(result.stderr)})
            return []

    def copy_file(self, source, destination, info=None):
        record = {'source': source, 'destination': destination, 'exists': None, 'exit': None,
                  'copy_exit': None, 'retained': False, 'redaction_applied': False,
                  'size_before': None, 'size_after': None}
        self.records.append(record)
        try:
            reason = excluded_path(source)
            if reason:
                record['excluded'] = reason
                return record
            # The legacy fixed artifacts retain their independent existence check.
            regular = self.retain(['docker', 'exec', self.name, 'sh', '-c',
                                  'path=$1; while [ "$path" != / ]; do '
                                  'test ! -L "$path" || exit 2; path=${path%/*}; '
                                  '[ -n "$path" ] || path=/; done; '
                                  'test -e "$1" || exit 1; test -f "$1" || exit 2; '
                                  'stat -c %s -- "$1"', 'regular-evidence', source])
            exists = {0: True, 1: False}.get(regular.returncode, (info or {}).get('exists'))
            record.update(exists=exists, missing=exists is False, probe_exit=regular.returncode)
            if regular.returncode != 0:
                record.update(exit=regular.returncode, stderr=timeout_text(regular.stderr))
                if regular.returncode == 2:
                    record['excluded'] = 'non-regular file or symlink parent excluded'
                return record
            size = timeout_text(regular.stdout).strip()
            record['size_before'] = int(size) if size.isdigit() else (info or {}).get('size_before')
            if record['size_before'] is not None and record['size_before'] > MAX_ARTIFACT_BYTES:
                record['excluded'] = 'artifact size limit exceeded'
                return record
            result, archive, error = self.copy_archive(source)
            record.update(exit=result.returncode, copy_exit=result.returncode, stderr=timeout_text(result.stderr))
            if error or result.returncode:
                record['error'] = error or 'docker cp failed'
                return record
            with tarfile.open(fileobj=io.BytesIO(archive), mode='r:') as exported:
                member = exported.next()
                if (member is None or not member.isfile() or member.size > MAX_ARTIFACT_BYTES or
                        member.name != Path(source).name):
                    raise ValueError('unsafe or oversized archive member')
                data = exported.extractfile(member).read(MAX_ARTIFACT_BYTES + 1)
                if len(data) != member.size or exported.next() is not None:
                    raise ValueError('unexpected archive contents')
            record['size_before'] = len(data)
            # Known binary formats stay byte-exact. Unknown/non-UTF-8 files fail
            # closed; known logs tolerate invalid UTF-8 without exposing tokens.
            binary = data.startswith((b'PK\x03\x04', b'\x1f\x8b', b'\x89PNG\r\n\x1a\n',
                                      b'\xff\xd8\xff', b'GIF87a', b'GIF89a', b'SQLite format 3\x00'))
            known_log = re.search(r'\.log(?:\.\d+)?$', source) is not None
            if binary:
                record['mode'] = 'byte-exact'
            else:
                if not known_log:
                    sample = data[:8192].decode('utf-8', errors='strict')
                    if '\x00' in sample:
                        raise ValueError('unclassified binary artifact')
                text = data.decode('utf-8', errors='replace' if known_log else 'strict')
                data = redact_capabilities(text).encode('utf-8')
                record.update(mode='redacted', redaction_applied=True)
            record['size_after'] = len(data)
            self.write_bytes(destination, data)
            record['retained'] = True
        except Exception as error:
            record.update(error=type(error).__name__, excluded='artifact could not be safely retained')
        return record

    def copy_tree(self, source, destination, mode='tree', skip=()):
        for info in self.discover(source, mode):
            try:
                path = Path(info['source'])
                relative = path.relative_to(source)
                if '..' in relative.parts:
                    raise ValueError('unsafe discovery path')
                target = str(Path(destination) / relative)
                if str(path) in skip:
                    continue
                if info.get('directory') and not info.get('excluded'):
                    continue
                if info.get('excluded') or info.get('error') or info.get('exists') is not True:
                    self.records.append(dict(info, destination=target, exit=None, copy_exit=None,
                                             retained=False, redaction_applied=False, size_after=None))
                    continue
                self.copy_file(str(path), target, info)
            except Exception as error:
                self.records.append({'source': source, 'destination': destination, 'exists': None,
                                     'exit': None, 'retained': False, 'error': type(error).__name__})


def repair_workers_running(stdout, returncode):
    rows = [line.split() for line in stdout.splitlines() if line.strip()]
    return (returncode == 0 and len(rows) == 2 and
            sorted(row[0] for row in rows) == sorted(REPAIR_WORKERS) and
            all(len(row) >= 2 and row[1] == 'RUNNING' for row in rows))


def wait_for_repair_workers(run, name, evidence):
    deadline = time.monotonic() + 120
    samples = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError('browser-repair workers-started timed out after 120s; see browser-repair-workers.json')
        try:
            result = run(['docker', 'exec', name, 'supervisorctl', '-c', SUPERVISOR,
                          'status', *REPAIR_WORKERS], check=False, timeout=min(15, remaining))
            sample = {'stdout': result.stdout, 'stderr': result.stderr, 'exit': result.returncode}
        except subprocess.TimeoutExpired as error:
            sample = {'stdout': timeout_text(error.stdout), 'stderr': timeout_text(error.stderr),
                      'exit': None, 'error': 'TimeoutExpired'}
        sample['monotonic'] = time.monotonic()
        samples.append(sample)
        (evidence / 'browser-repair-workers.json').write_text(redact_capabilities(json.dumps(samples, indent=2)))
        if repair_workers_running(sample['stdout'], sample['exit']):
            return
        if any(len(row) >= 2 and row[0] in REPAIR_WORKERS and row[1] in ('FATAL', 'EXITED')
               for row in (line.split() for line in sample['stdout'].splitlines())):
            raise RuntimeError('browser-repair durable worker FATAL/EXITED; see browser-repair-workers.json')
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(2, remaining))


def timeout_text(value):
    return value.decode(errors='replace') if isinstance(value, bytes) else value or ''


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = root / os.environ.get('PROOF_EVIDENCE_DIR', 'uv-experiment/14b/evidence/container')
    evidence.mkdir(parents=True, exist_ok=True)
    image = os.environ.get('PROOF_IMAGE', 'da-uv-14b:local')
    name = 'da-14b-core-upgrade'
    commands = []

    def run(command, check=True, timeout=1800, stdout=None):
        output = {'capture_output': True, 'text': True} if stdout is None else {
            'stdout': stdout, 'stderr': subprocess.PIPE}
        result = subprocess.run(command, timeout=timeout, **output)
        commands.append({'command': command, 'exit': result.returncode})
        (evidence / 'commands.json').write_text(redact_capabilities(json.dumps(commands, indent=2)))
        if check and result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result

    def wait(ready=True):
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            result = run(['docker', 'exec', name, 'curl', '-fsS',
                          'http://127.0.0.1/health_check' + ('?ready=1' if ready else '')], check=False)
            if result.returncode == 0:
                return
            time.sleep(2)
        raise RuntimeError('container readiness timed out')

    python = '/usr/share/docassemble/executor-venv/bin/python'
    script = '/repo/uv-experiment/preparation/core_upgrade_image_proof.py'

    def step(action, path=script):
        try:
            result = run(['docker', 'exec', name, python, path, action], check=False)
        except subprocess.TimeoutExpired as error:
            (evidence / (action + '.log')).write_text(redact_capabilities(timeout_text(error.stdout) + timeout_text(error.stderr)))
            raise
        (evidence / (action + '.log')).write_text(redact_capabilities(result.stdout + result.stderr))
        print(redact_capabilities(result.stdout + result.stderr), end='', flush=True)
        if result.returncode:
            raise RuntimeError(action + ' failed')

    refuse_existing(run, containers=(name,))
    (evidence / 'image.json').write_text(redact_capabilities(run(['docker', 'image', 'inspect', image]).stdout))
    run(['docker', 'run', '-d', '--name', name, '-v', str(root) + ':/repo:ro',
         '-e', 'DA_ADMIN_EMAIL=admin@example.com', '-e', 'DA_ADMIN_PASSWORD=FixtureAdmin101!',
         '-e', 'DA_QUALIFIED_IMAGE_BASELINE=/etc/docassemble/qualified-image/baseline.json',
         '-e', 'DA_DURABLE_WORK_STORE=/var/lib/docassemble-build/qualified-retained', image])
    try:
        wait()
        bridge = '/repo/uv-experiment/preparation/image_bridge_proof.py'
        step('setup', bridge)
        step('bootstrap')
        step('success')
        run(['docker', 'restart', name])
        wait()
        # Startup's real durable workers have observed the retained success. Stop
        # them only while injecting the next deterministic fault with this driver.
        run(['docker', 'exec', name, 'supervisorctl', '-c',
             '/var/run/docassemble/managed-supervisor/supervisord.conf', 'stop',
             'durable-preparation', 'durable-installation'])
        step('restart-success')
        step('failure')
        run(['docker', 'restart', name])
        wait(ready=False)
        step('restart-failure')
        if os.environ.get('PROOF_REPAIR_FLOW') == '1':
            wait_for_repair_workers(run, name, evidence)
            step('browser-repair', '/repo/uv-experiment/preparation/recovery_repair_proof.py')
        print('PASS changed coherent fixture core releases, real uv/schema/all-role health, one prior-core recovery, operator clearance and two same-image restart boundaries', flush=True)
    finally:
        diagnostics = DiagnosticRetention(run, name, python, evidence)
        retained = [('/var/run/docassemble/recovery/status.json', 'final-recovery-status.json'),
                    ('/var/log/docassemble-uv-invocations.log', 'uv-invocations.log')]
        retained += [('/var/run/docassemble/managed-supervisor/' + log + '.log',
                      'supervisor-logs/' + log + '.log')
                     for log in ('supervisord', 'durable-preparation', 'durable-installation')]
        copies = [diagnostics.copy_file(source, destination) for source, destination in retained]
        diagnostics.write_text('browser-repair-evidence-copies.json', json.dumps(copies, indent=2),
                               'legacy diagnostic copy manifest')
        diagnostics.copy_file('/var/run/docassemble/managed-supervisor/watchdog.log',
                              'supervisor-logs/watchdog.log')
        diagnostics.copy_tree('/var/run/docassemble/managed-supervisor', 'supervisor-logs',
                              mode='logs', skip=[source for source, _ in retained] + [
                                  '/var/run/docassemble/managed-supervisor/watchdog.log'])
        # Never recursively copy logs/state unchanged: exclude caller/environment
        # paths and redact text before host writes (not a universal secret scrubber).
        diagnostics.copy_tree('/usr/share/docassemble/log', 'logs')
        diagnostics.copy_tree('/tmp/image-bridge-proof', '.')
        diagnostics.copy_tree('/var/lib/docassemble/managed', 'local-state')
        result = diagnostics.retain(['docker', 'logs', name])
        diagnostics.write_text('container.log', timeout_text(result.stdout), 'docker logs', result.returncode)
        result = diagnostics.retain(['docker', 'exec', name, 'supervisorctl', '-c', SUPERVISOR,
                                     'status', *REPAIR_WORKERS])
        diagnostics.write_text('final-repair-workers.json', json.dumps({
            'stdout': result.stdout, 'stderr': result.stderr, 'exit': result.returncode}, indent=2),
            'supervisorctl status', result.returncode)
        diagnostics.write_text('recovery-diagnostics-manifest.json', json.dumps({
            'redaction_policy': 'nonempty DA_ACCEPTED_CALLER and standalone 64-hex strings',
            'redaction_applied_to_all_retained_text': True,
            'artifact_byte_limit': MAX_ARTIFACT_BYTES, 'command_timeout_seconds': DIAGNOSTIC_TIMEOUT,
            'discovery_entry_limit': MAX_DISCOVERY_ENTRIES, 'artifacts': diagnostics.records}, indent=2),
            'diagnostic retention manifest')
        cleanup(run, containers=(name,))


if __name__ == '__main__':
    main()
