#!/usr/bin/env python3
"""Capture ticket 15's changed-inventory fixture release container proof."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import time

from proof_resources import cleanup, refuse_existing


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = root / os.environ.get('PROOF_EVIDENCE_DIR', 'uv-experiment/15/evidence/container')
    evidence.mkdir(parents=True, exist_ok=True)
    image = os.environ.get('PROOF_IMAGE', 'da-uv-15:local')
    name = 'da-15-historical-rollback'
    commands = []

    def run(command, check=True, timeout=1800):
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        commands.append({'command': command, 'exit': result.returncode})
        (evidence / 'commands.json').write_text(json.dumps(commands, indent=2))
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
    script = '/repo/uv-experiment/preparation/historical_rollback_image_proof.py'

    def step(action, path=script):
        result = run(['docker', 'exec', name, python, path, action], check=False)
        (evidence / (action + '.log')).write_text(result.stdout + result.stderr)
        print(result.stdout + result.stderr, end='', flush=True)
        if result.returncode:
            raise RuntimeError(action + ' failed')

    refuse_existing(run, containers=(name,))
    (evidence / 'image.json').write_text(run(['docker', 'image', 'inspect', image]).stdout)
    run(['docker', 'run', '-d', '--name', name, '-v', str(root) + ':/repo:ro',
         '-e', 'DA_ADMIN_EMAIL=admin@example.com', '-e', 'DA_ADMIN_PASSWORD=FixtureAdmin101!',
         '-e', 'DA_QUALIFIED_IMAGE_BASELINE=/etc/docassemble/qualified-image/baseline.json',
         '-e', 'DA_DURABLE_WORK_STORE=/var/lib/docassemble-build/qualified-retained', image])
    try:
        files = [
            'docassemble_base/docassemble/base/managed_' + name + '.py'
            for name in ('preparation', 'publication', 'qualification', 'rollback')]
        files += ['docassemble_webapp/docassemble/webapp/packages/managed.py']
        files += ['uv-experiment/executor/src/docassemble_executor/' + name + '.py' for name in
                  ('registry_builder', 'rollback_schema', 'alembic_probe', 'selected_baseline',
                   'installation', 'published', 'boot_observation', 'controller', 'image_baseline')]
        hashes = {}
        for path in files:
            target = ('/opt/docassemble-executor/' + path.removeprefix('uv-experiment/executor/')
                      if path.startswith('uv-experiment/executor/') else '/opt/da-sources/' + path)
            expected = hashlib.sha256((root / path).read_bytes()).hexdigest()
            actual = run(['docker', 'exec', name, 'sha256sum', target]).stdout.split()[0]
            assert actual == expected, path + ' is not the source in this proof image'
            hashes[path] = actual
        (evidence / 'source-hashes.json').write_text(json.dumps(hashes, indent=2, sort_keys=True))
        wait()
        bridge = '/repo/uv-experiment/preparation/image_bridge_proof.py'
        step('setup', bridge)
        step('bootstrap')
        step('success')
        step('rollback')
        run(['docker', 'restart', name])
        wait()
        run(['docker', 'exec', name, 'supervisorctl', '-c',
             '/var/run/docassemble/managed-supervisor/supervisord.conf', 'stop',
             'durable-preparation', 'durable-installation'])
        step('restart-rollback')
        step('incompatible')
        print('PASS exact retained rollback, current ownership/deleted account handling, unchanged DB/setup mode, restart, missing material and incompatible advanced DB', flush=True)
    finally:
        (evidence / 'container.log').write_text(run(['docker', 'logs', name], check=False).stdout)
        run(['docker', 'cp', name + ':/tmp/image-bridge-proof/.', str(evidence)], check=False)
        run(['docker', 'cp', name + ':/var/lib/docassemble/managed', str(evidence / 'local-state')], check=False)
        run(['docker', 'cp', name + ':/usr/share/docassemble/log', str(evidence / 'logs')], check=False)
        # Input wheels are large, retained in the disposable container only. Hashes,
        # metadata, locks and manifests are sufficient source evidence, not binaries.
        for directory in evidence.glob('fixture-release-*'):
            if directory.is_dir():
                import shutil
                shutil.rmtree(directory)
        cleanup(run, containers=(name,))


if __name__ == '__main__':
    main()
