#!/usr/bin/env python3
"""Ticket 24: rerun accepted proof harnesses; never infer success from old evidence."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

from proof_resources import cleanup, refuse_existing, resources

ROOT = Path(__file__).resolve().parents[1]
PYTHON = '/usr/share/docassemble/executor-venv/bin/python'
SCENARIOS = ('proof', 'boot', 'maintenance', 'consumers', 'workers', 'service_sessions', 'helpers_mail', 'bypasses', 'full_maintenance')
SOURCES = ('config_publication', 'zip_install', 'playground_install', 'git_install', 'private_git',
           'private_index_credentials', 'direct_url')
RUNNERS = ('core-upgrade', 'historical-rollback', 'legacy-adoption', 'managed-backup', 'cold-restore',
           'image-migration', 'cache', 'checkpoint', 'cloud-checkpoint')
PHASES = (*SCENARIOS, 'browser-api', 'registry', *SOURCES, *RUNNERS,
          'restore-local', 'restore-s3', 'restore-azure')
PROTECTED = ('caddy', 'vaultwarden', 'ja', 'int', 'backup', 'da-postgres', 'da-redis')
SOURCE_DATABASE = 'da-24-source-postgres'
SOURCE_DATABASE_READY = '''\
import os
import time
import psycopg2

url = os.environ['DA_PREPARATION_TEST_URL'].replace('postgresql+psycopg2:', 'postgresql:', 1)
deadline = time.monotonic() + 25
while True:
    try:
        with psycopg2.connect(url, connect_timeout=1) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
                assert cursor.fetchone() == (1,)
        break
    except psycopg2.OperationalError:
        if time.monotonic() >= deadline:
            raise
        time.sleep(0.25)
print('source PostgreSQL authenticated SELECT 1: ready')
'''


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def uv_counts(log: str) -> dict[str, int]:
    return {verb: sum(line.partition('\t')[2].startswith(verb + ' ') for line in log.splitlines())
            for verb in ('lock', 'build', 'sync', 'pip')}


def complete(results: list[dict]) -> bool:
    return {row['phase'] for row in results} == set(PHASES) and all(row['exit'] == 0 for row in results)


def source_hashes() -> dict[str, str]:
    roots = ('Docker', 'docassemble_base', 'docassemble_demo', 'docassemble_webapp',
             'uv-experiment/executor/src', 'uv-experiment/preparation', 'uv-experiment/scenarios',
             'uv-experiment/fixture')
    files = [p for root in roots for p in (ROOT / root).rglob('*') if p.is_file() and
             not any(part in ('.venv', '__pycache__', 'node_modules') for part in p.parts) and
             (p.suffix in ('.py', '.sh', '.toml', '.lock', '.html', '.js', '.yml', '.json') or 'Dockerfile' in p.name)]
    files += list((ROOT / 'uv-experiment').glob('run-*'))
    files += [ROOT / 'uv-experiment/executor/pyproject.toml', ROOT / 'uv-experiment/executor/uv.lock',
              ROOT / 'uv-experiment/proof_resources.py']
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(files))}


class Run:
    def __init__(self, evidence: Path, image: str):
        self.evidence, self.image = evidence, image
        self.commands: list[dict] = []
        self.results: list[dict] = []
        self.source_password = secrets.token_hex(24)
        self.source_database_url = 'postgresql+psycopg2://postgres:' + self.source_password + '@127.0.0.1:5432/postgres'

    def redact(self, value: str) -> str:
        return value.replace(self.source_database_url, '[source database URL]').replace(self.source_password, '[source database password]')

    def command(self, argv: list[str], *, env=None, check=True, timeout=7200):
        start = time.monotonic()
        record = {'argv': argv, 'status': 'running', 'started_at': datetime.now(timezone.utc).isoformat(),
                  'timeout': timeout}
        output = self.evidence / ('command-' + str(len(self.commands)).zfill(4))
        self.commands.append(record)
        write(self.evidence / 'commands.json', self.commands)
        try:
            result = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            record.update(status='timeout', exit=-1, seconds=time.monotonic() - start)
            for stream in ('stdout', 'stderr'):
                value = getattr(error, stream) or ''
                value = self.redact(value.decode(errors='replace') if isinstance(value, bytes) else value)
                setattr(error, stream, value)
                output.with_suffix('.' + stream).write_text(value)
            write(self.evidence / 'commands.json', self.commands)
            raise
        except Exception:
            record.update(status='error', exit=-1, seconds=time.monotonic() - start)
            write(self.evidence / 'commands.json', self.commands)
            raise
        record.update(status='finished', exit=result.returncode, seconds=time.monotonic() - start)
        result.stdout, result.stderr = self.redact(result.stdout), self.redact(result.stderr)
        output.with_suffix('.stdout').write_text(result.stdout)
        output.with_suffix('.stderr').write_text(result.stderr)
        write(self.evidence / 'commands.json', self.commands)
        if check and result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result

    def inventory(self) -> dict:
        return {name: self.command(['docker', 'inspect', name, '--format', '{{.Id}} {{.State.StartedAt}}'], check=False).stdout.strip()
                for name in PROTECTED}

    def disposable_sources(self, phase: str, directory: Path):
        name = 'da-24-source-' + phase.replace('_', '-')
        refuse_existing(self.command, containers=(name, SOURCE_DATABASE), volumes=(name, SOURCE_DATABASE))
        env = dict(os.environ, POSTGRES_PASSWORD=self.source_password,
                   DA_PREPARATION_TEST_URL=self.source_database_url)
        try:
            self.command(['docker', 'volume', 'create', SOURCE_DATABASE])
            self.command(['docker', 'run', '-d', '--name', SOURCE_DATABASE,
                '-e', 'POSTGRES_PASSWORD', '-v', SOURCE_DATABASE + ':/var/lib/postgresql/data',
                'postgres:16'], env=env)
            self.command(['docker', 'run', '-d', '--name', name, '--entrypoint', '/bin/sh',
                '--network', 'container:' + SOURCE_DATABASE, '-v', str(ROOT) + ':/repo:ro',
                '-e', 'PYTHONDONTWRITEBYTECODE=1',
                '-e', 'PYTHONPATH=/repo/uv-experiment/executor/src:/repo/docassemble_base',
                '-e', 'DA_PREPARATION_TEST_URL', self.image, '-c', 'exec sleep infinity'], env=env)
            self.command(['docker', 'exec', '--user', 'da-environment', name, PYTHON,
                          '-c', SOURCE_DATABASE_READY], timeout=30)
            before = self.command(['docker', 'exec', name, 'cat', '/var/log/docassemble-uv-invocations.log']).stdout
            result = self.command(['docker', 'exec', '--user', 'da-environment', name, PYTHON,
                '/repo/uv-experiment/preparation/source_qualification_proof.py', phase], check=False)
            # Setup can fail before the builder writes inventories. Retain that
            # diagnostic before attempting the still-mandatory export.
            (directory / 'driver.log').write_text(result.stdout + result.stderr)
            self.command(['docker', 'cp', name + ':/tmp/qualification-inventories.json',
                          str(directory / 'inventories.json')])
            after = self.command(['docker', 'exec', name, 'cat', '/var/log/docassemble-uv-invocations.log']).stdout
            (directory / 'uv-invocations.log').write_text(after[len(before):])
            write(directory / 'uv-counts.json', uv_counts(after[len(before):]))
            return result
        finally:
            failure = sys.exc_info()[1]
            try:
                cleanup(self.command, containers=(name, SOURCE_DATABASE), volumes=(SOURCE_DATABASE,))
            except Exception as error:
                if failure is not None:
                    raise RuntimeError(self.redact(str(failure)) + '\n' + self.redact(str(error))) from error
                raise

    def phase(self, phase: str):
        directory = self.evidence / phase
        directory.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PROOF_IMAGE=self.image, PROOF_EVIDENCE_DIR=str(directory), SKIP_BUILD='1', PROOF_REPAIR_FLOW='1',
                   PROOF_OLD_IMAGE=os.environ.get('PROOF_OLD_IMAGE', 'da-uv-24-old:local'),
                   PROOF_NEW_IMAGE=os.environ.get('PROOF_NEW_IMAGE', 'da-uv-24-new:local'))
        before_containers, before_volumes = resources(self.command)
        started = time.monotonic()
        print('RUN ' + phase, flush=True)
        row = {'phase': phase, 'exit': -1, 'seconds': 0}
        try:
            if phase in SOURCES:
                result = self.disposable_sources(phase, directory)
            else:
                if phase in SCENARIOS:
                    argv = [sys.executable, 'uv-experiment/scenarios/' + phase + '.py', '--image', self.image,
                            '--evidence' if phase == 'proof' else '--evidence-dir', str(directory)]
                elif phase in ('browser-api', 'registry'):
                    env.update(PROOF_CONTAINER_NAME='da-24-' + phase, PROOF_FLOW_ACTION='pending' if phase == 'browser-api' else 'registry')
                    argv = [sys.executable, 'uv-experiment/run-image-bridge-proof.py']
                elif phase.startswith('restore-'):
                    env['PROOF_CHECKPOINT_PROVIDER'] = phase.removeprefix('restore-')
                    argv = [sys.executable, 'uv-experiment/run-checkpoint-restore-proof.py']
                else:
                    argv = [sys.executable, 'uv-experiment/run-' + phase + '-proof.py']
                result = self.command(argv, env=env, check=False)
            (directory / 'driver.log').write_text(result.stdout + result.stderr)
            row = {'phase': phase, 'exit': result.returncode, 'seconds': time.monotonic() - started}
        except Exception as error:
            (directory / 'driver-error.txt').write_text(self.redact(str(error)))
            if isinstance(error, subprocess.TimeoutExpired):
                output = [value.decode(errors='replace') if isinstance(value, bytes) else value or ''
                          for value in (error.stdout, error.stderr)]
                (directory / 'driver.log').write_text(''.join(output))
            row = {'phase': phase, 'exit': -1, 'seconds': time.monotonic() - started}
        finally:
            # Some historical drivers intentionally keep fixtures. Remove only
            # newly-created da-* objects, never an existing deployment or volume.
            try:
                after_containers, after_volumes = resources(self.command)
                cleanup(self.command,
                        containers=sorted(name for name in after_containers - before_containers
                                          if name.startswith('da-') and name not in PROTECTED),
                        volumes=sorted(name for name in after_volumes - before_volumes if name.startswith('da-')))
            except Exception as error:
                (directory / 'cleanup-error.txt').write_text(self.redact(str(error)))
                row['exit'] = -1
                row['cleanup_error'] = self.redact(str(error))
        self.results.append(row)
        write(self.evidence / 'results.json', self.results)
        print(('PASS ' if row['exit'] == 0 else 'FAIL ') + phase, flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='da-uv-24:local')
    parser.add_argument('--evidence', type=Path, default=ROOT / 'uv-experiment/24/evidence' /
                        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    parser.add_argument('--only', nargs='+', choices=PHASES, help='diagnostic subset; never reports complete qualification')
    args = parser.parse_args(argv)
    if args.evidence.exists():
        parser.error('evidence destination must be new (preserve failed runs)')
    run = Run(args.evidence.resolve(), args.image)
    run.evidence.mkdir(parents=True)
    before = run.inventory()
    containers, volumes = resources(run.command)
    occupied = sorted(name for name in containers if name.startswith('da-') and name not in PROTECTED)
    occupied += sorted(name for name in volumes if name.startswith('da-'))
    if occupied:
        parser.error('existing disposable-name containers/volumes must be excluded first: ' + ', '.join(occupied))
    sources = source_hashes()
    write(run.evidence / 'source-hashes.json', sources)
    write(run.evidence / 'image.json', json.loads(run.command(['docker', 'image', 'inspect', args.image]).stdout))
    identity = run.command(['docker', 'run', '--rm', '--entrypoint', '/bin/bash', args.image, '-c',
        'cat /usr/share/docassemble/managed/bundles/manifest.json; '
        '/usr/local/libexec/uv-pinned --version; /usr/share/docassemble/executor-venv/bin/python -VV; '
        'uname -a; ldd --version | head -1']).stdout
    (run.evidence / 'tooling.txt').write_text(identity)
    (run.evidence / 'docker-version.txt').write_text(run.command(['docker', 'version']).stdout)
    try:
        for phase in args.only or PHASES:
            run.phase(phase)
    finally:
        after = run.inventory()
        unchanged = before == after and source_hashes() == sources
        write(run.evidence / 'summary.json', {
            'container_gates_passed': complete(run.results) and unchanged and not args.only,
            'results': run.results, 'protected_before': before, 'protected_after': after,
            'sources_and_protected_services_unchanged': unchanged,
            'missing_phases': sorted(set(PHASES) - {r['phase'] for r in run.results}),
            'limitations': ['External S3/Azure CLIs are fakes, not live provider qualification.',
                'Core releases 1.10.14/15/16 are synthetic repacked image wheels.',
                'Package locks do not certify arbitrary data compatibility or upstream availability.',
                'Native/PostgreSQL/act/lint gates must ALSO pass; this report is not acceptance.']})
    return 0 if complete(run.results) and unchanged and not args.only else 1


if __name__ == '__main__':
    raise SystemExit(main())
