#!/usr/bin/env python3
"""Disposable shared-state deployment replacement in both release directions."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from proof_resources import cleanup, refuse_existing


def expected_image_schema(root):
    versions = root / 'docassemble_webapp/docassemble/webapp/alembic/versions'
    sources = {'docassemble/base/managed_preparation.py':
               root / 'docassemble_base/docassemble/base/managed_preparation.py'}
    sources.update({'docassemble/webapp/alembic/versions/' + path.name: path
                    for path in versions.glob('*.py')})
    return {member: hashlib.sha256(path.read_bytes()).hexdigest()
            for member, path in sources.items()}


def schema_failure(image, detail):
    return RuntimeError('Image migration proof schema preflight failed for ' + image +
        '. This proof requires current managed schema code and Alembic migration 05b500000001 '
        '(managed_candidate.credential_request_binding). Rebuild the base fixture from the current '
        'checkout and BOTH replacement images with Dockerfile.replacement-proof; then set '
        'PROOF_OLD_IMAGE/PROOF_NEW_IMAGE to the rebuilt tags and rerun. Do not stamp or alter the '
        'database to bypass this check.\n' + detail)


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = root / os.environ.get('PROOF_EVIDENCE_DIR', 'uv-experiment/19/evidence/container')
    evidence.mkdir(parents=True, exist_ok=True)
    commands = []
    names = ('da-19-source', 'da-19-newer', 'da-19-older')
    volumes = {'database': '/var/lib/postgresql', 'state': '/var/lib/docassemble',
        'retained': '/var/lib/docassemble-build', 'config': '/usr/share/docassemble/config',
        'files': '/usr/share/docassemble/files'}
    python = '/usr/share/docassemble/executor-venv/bin/python'
    schema_probe = '/repo/uv-experiment/preparation/image_migration_schema_probe.py'
    def run(command, check=True, timeout=1800):
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        commands.append({'command': command, 'exit': result.returncode})
        (evidence / 'commands.json').write_text(json.dumps(commands, indent=2))
        if check and result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result
    def wait(name):
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            if run(['docker', 'exec', name, 'curl', '-fsS', 'http://127.0.0.1/health_check?ready=1'], check=False).returncode == 0:
                return
            time.sleep(2)
        raise RuntimeError(name + ' normal readiness timed out')
    def step(name, module, *arguments):
        result = run(['docker', 'exec', name, 'env',
            'PYTHONDONTWRITEBYTECODE=1', python, '/repo/uv-experiment/preparation/' + module + '.py', *arguments], check=False)
        (evidence / (name + '-' + '-'.join(arguments) + '.log')).write_text(result.stdout + result.stderr)
        print(result.stdout + result.stderr, end='', flush=True)
        if result.returncode:
            raise RuntimeError(module + ' ' + str(arguments) + ' failed')
    def verify_database(name, image):
        result = run(['docker', 'exec', name, 'env', 'PYTHONDONTWRITEBYTECODE=1',
                      python, schema_probe, 'database'], check=False)
        (evidence / (name + '-schema.json')).write_text(result.stdout + result.stderr)
        if result.returncode:
            raise schema_failure(image, result.stdout + result.stderr)
    shared = ['-v', str(root) + ':/repo:ro', '-e', 'PYTHONDONTWRITEBYTECODE=1',
        '-e', 'PYTHONPATH=/repo/uv-experiment/executor/src:/repo/docassemble_base',
        '-e', 'DA_ADMIN_EMAIL=admin@example.com', '-e', 'DA_ADMIN_PASSWORD=FixtureAdmin101!',
        '-e', 'DA_QUALIFIED_IMAGE_BASELINE=/etc/docassemble/qualified-image/baseline.json',
        '-e', 'DA_DURABLE_WORK_STORE=/var/lib/docassemble-build/qualified-retained']
    for key, target in volumes.items():
        shared += ['-v', 'da-19-' + key + ':' + target]
    refuse_existing(run, containers=names, volumes=tuple('da-19-' + key for key in volumes))
    old_image = os.environ.get('PROOF_OLD_IMAGE', 'da-uv-19-old:local')
    new_image = os.environ.get('PROOF_NEW_IMAGE', 'da-uv-19-new:local')
    expected = expected_image_schema(root)
    for label, image in [('old', old_image), ('new', new_image)]:
        (evidence / (label + '-image.json')).write_text(run(['docker', 'image', 'inspect', image]).stdout)
        result = run(['docker', 'run', '--rm', '--network', 'none',
            '-v', str(root) + ':/repo:ro', '-e', 'PYTHONDONTWRITEBYTECODE=1',
            '--entrypoint', python, image, schema_probe, 'image', json.dumps(expected, sort_keys=True)], check=False)
        (evidence / (label + '-image-schema.json')).write_text(result.stdout + result.stderr)
        if result.returncode:
            raise schema_failure(image, result.stdout + result.stderr)
    for key in volumes:
        run(['docker', 'volume', 'create', 'da-19-' + key])
    try:
        probe = run(['docker', 'run', '--rm', '-v', str(root) + ':/repo:ro', '--entrypoint', '/bin/bash', old_image,
            '-c', 'exec runuser -u da-environment -- ' + python + ' /repo/uv-experiment/preparation/image_migration_uv_probe.py'])
        (evidence / 'pinned-core-refresh.json').write_text(probe.stdout)
        run(['docker', 'run', '-d', '--name', names[0], *shared, old_image])
        wait(names[0])
        verify_database(names[0], old_image)
        step(names[0], 'image_bridge_proof', 'setup')
        step(names[0], 'core_upgrade_image_proof', 'bootstrap')
        step(names[0], 'core_upgrade_image_proof', 'success')  # selected web core 1.10.14
        step(names[0], 'image_migration_proof', 'capture', 'before-newer')
        # Deliberate deployment replacement; no assertion the old service remains available.
        run(['docker', 'stop', '-t', '60', names[0]])
        for name, image, direction, before in [(names[1], new_image, 'newer', 'before-newer'),
                                               (names[2], old_image, 'older', 'before-older')]:
            provision = ('from pathlib import Path; from docassemble_executor.paths import ExecutorPaths; '
                'from docassemble_executor.image_baseline import provision_image_baseline; '
                'provision_image_baseline(ExecutorPaths.from_env(), Path("/etc/docassemble/qualified-image"))')
            boot = python + ' -c \'' + provision + '\' && rm -f /usr/share/docassemble/log/create-tables.log && exec /opt/docassemble-fixture/entrypoint.sh'
            run(['docker', 'run', '-d', '--name', name, *shared, '--entrypoint', '/bin/bash', image, '-c', boot])
            wait(name)
            verify_database(name, image)
            step(name, 'image_migration_proof', direction, before)
            step(name, 'image_migration_proof', 'conflict', before)
            step(name, 'image_migration_proof', 'capture', direction + '-restart')
            run(['docker', 'restart', name])
            wait(name)
            step(name, 'image_migration_proof', 'restart', direction + '-restart')
            if direction == 'newer':
                step(name, 'image_migration_proof', 'capture', 'before-older')
                run(['docker', 'stop', '-t', '60', name])
        print('PASS different shipped-core images above/below web-selected baseline, automatic system migration and frozen same-image restarts', flush=True)
    finally:
        for name in names:
            (evidence / (name + '.log')).write_text(run(['docker', 'logs', name], check=False).stdout)
            run(['docker', 'cp', name + ':/var/lib/docassemble/migration-proof/.', str(evidence)], check=False)
            run(['docker', 'cp', name + ':/var/log/docassemble-uv-invocations.log', str(evidence / (name + '-uv.log'))], check=False)
            run(['docker', 'cp', name + ':/usr/share/docassemble/log/create-tables.log', str(evidence / (name + '-setup.log'))], check=False)
        cleanup(run, containers=names, volumes=tuple('da-19-' + key for key in volumes))


if __name__ == '__main__':
    main()
