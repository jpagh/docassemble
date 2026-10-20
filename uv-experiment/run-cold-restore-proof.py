#!/usr/bin/env python3
"""Disposable old/replacement container proof; never touches host services."""
import json
import os
from pathlib import Path
import subprocess
import time

from proof_resources import cleanup, refuse_existing


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = root / os.environ.get('PROOF_EVIDENCE_DIR', 'uv-experiment/18/evidence/container')
    evidence.mkdir(parents=True, exist_ok=True)
    image = os.environ.get('PROOF_IMAGE', 'da-uv-16:local')
    old, new, volume = 'da-18-old', 'da-18-replacement', 'da-18-backup'
    commands = []
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
            result = run(['docker', 'exec', name, 'curl', '-fsS', 'http://127.0.0.1/health_check?ready=1'], check=False)
            if result.returncode == 0:
                return
            time.sleep(2)
        raise RuntimeError('normal readiness timed out')
    python = '/usr/share/docassemble/executor-venv/bin/python'
    def step(name, script, action):
        result = run(['docker', 'exec', name, python,
                      '/repo/uv-experiment/preparation/' + script + '.py', action], check=False)
        (evidence / (action + '.log')).write_text(result.stdout + result.stderr)
        print(result.stdout + result.stderr, end='', flush=True)
        if result.returncode:
            raise RuntimeError(action + ' failed')
    shared = ['-v', str(root) + ':/repo:ro', '-v', volume + ':/cold-backup',
        '-e', 'PYTHONPATH=/repo/uv-experiment/executor/src:/repo/docassemble_base',
        '-e', 'PYTHONDONTWRITEBYTECODE=1',
        '-e', 'DA_ADMIN_EMAIL=admin@example.com', '-e', 'DA_ADMIN_PASSWORD=FixtureAdmin101!',
        '-e', 'DA_QUALIFIED_IMAGE_BASELINE=/etc/docassemble/qualified-image/baseline.json',
        '-e', 'DA_DURABLE_WORK_STORE=/var/lib/docassemble-build/qualified-retained']
    refuse_existing(run, containers=(old, new), volumes=(volume,))
    run(['docker', 'volume', 'create', volume])
    (evidence / 'image.json').write_text(run(['docker', 'image', 'inspect', image]).stdout)
    try:
        run(['docker', 'run', '--rm', '-v', volume + ':/cold-backup', '--entrypoint', '/bin/bash', image,
             '-c', 'cp /var/log/docassemble-uv-invocations.log /cold-backup/image-uv.log'])
        run(['docker', 'run', '-d', '--name', old, *shared, image])
        wait(old)
        step(old, 'image_bridge_proof', 'setup')
        step(old, 'core_upgrade_image_proof', 'bootstrap')
        step(old, 'core_upgrade_image_proof', 'success')
        step(old, 'cold_restore_proof', 'backup')
        # This concrete proof excludes the old deployment before provisioning the
        # operator acknowledgement. Production exclusion remains operator authority.
        run(['docker', 'stop', '-t', '60', old])
        assert run(['docker', 'inspect', old, '--format', '{{.State.Running}}']).stdout.strip() == 'false'
        run(['docker', 'run', '-d', '--name', new, *shared, '--entrypoint', '/bin/bash', image,
             '-c', 'service postgresql start && exec sleep infinity'])
        step(new, 'cold_restore_proof', 'restore')
        # Setup is prohibited in this proof even if a regression tried it. The
        # independent executor still performs real read-only schema/role checks.
        boot = ('from unittest.mock import patch; from docassemble_executor.cli import main; '
                'from contextlib import ExitStack; '
                's=ExitStack(); s.enter_context(patch("docassemble_executor.published._create_tables", '
                'side_effect=AssertionError("successful restored setup rerun"))); main(["boot"])')
        run(['docker', 'exec', '-d', new, '/bin/bash', '-c',
             python + ' -c \'' + boot + '\' > /tmp/cold-boot.log 2>&1'])
        wait(new)
        step(new, 'cold_restore_proof', 'verify')
        print('PASS disposable source stopped; independent replacement DB/config/files restore and pinned-uv exact web-selected runtime qualification', flush=True)
    finally:
        for name in (old, new):
            (evidence / (name + '.log')).write_text(run(['docker', 'logs', name], check=False).stdout)
        run(['docker', 'cp', new + ':/tmp/cold-boot.log', str(evidence / 'cold-boot.log')], check=False)
        run(['docker', 'cp', new + ':/cold-backup/result.json', str(evidence / 'result.json')], check=False)
        cleanup(run, containers=(old, new), volumes=(volume,))


if __name__ == '__main__':
    main()
