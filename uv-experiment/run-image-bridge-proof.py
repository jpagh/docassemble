#!/usr/bin/env python3
"""Capture current-image authenticated 05c2 all-role qualification."""
import json
import os
from pathlib import Path
import subprocess
import time

from proof_resources import refuse_existing


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = root / os.environ.get('PROOF_EVIDENCE_DIR', 'uv-experiment/05c2/evidence/accepted/image-bridge')
    evidence.mkdir(parents=True, exist_ok=True)
    image = os.environ.get('PROOF_IMAGE', 'da-uv-05c2:local')
    name = os.environ.get('PROOF_CONTAINER_NAME', 'da-05c2-image-bridge')
    action = os.environ.get('PROOF_FLOW_ACTION', 'flow')
    if action not in ('flow', 'pending', 'normal', 'recovery', 'registry'):
        raise ValueError('unsupported image bridge proof action')
    commands = []
    def run(command, check=True):
        result = subprocess.run(command, capture_output=True, text=True)
        commands.append({'command': command, 'exit': result.returncode})
        (evidence / 'commands.json').write_text(json.dumps(commands, indent=2))
        if check and result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result
    refuse_existing(run, containers=(name,))
    identity = run(['docker', 'image', 'inspect', image, '--format', '{{.Id}}']).stdout
    (evidence / 'image.txt').write_text(identity)
    durable = ['-e', 'DA_DURABLE_WORK_STORE=/var/lib/docassemble-build/qualified-retained'] if os.environ.get('PROOF_DURABLE_WORK') == '1' else []
    run(['docker', 'run', '-d', '--name', name, '-v', str(root) + ':/repo:ro', *durable,
         '-e', 'DA_ADMIN_EMAIL=admin@example.com', '-e', 'DA_ADMIN_PASSWORD=FixtureAdmin101!',
         '-e', 'DA_QUALIFIED_IMAGE_BASELINE=/etc/docassemble/qualified-image/baseline.json',
         image])
    try:
        deadline = time.monotonic() + 900
        while True:
            ready = run(['docker', 'exec', name, 'curl', '-fsS', 'http://127.0.0.1/health_check?ready=1'], check=False)
            if ready.returncode == 0:
                break
            logs = run(['docker', 'logs', name], check=False)
            if 'managed boot refused:' in logs.stdout:
                raise RuntimeError(logs.stdout + logs.stderr)
            if time.monotonic() > deadline:
                raise RuntimeError('initial all-role boot timed out')
            time.sleep(2)
        python = '/usr/share/docassemble/executor-venv/bin/python'
        script = '/repo/uv-experiment/preparation/image_bridge_proof.py'
        for step in ('setup', action):
            step_script = '/repo/uv-experiment/preparation/registry_changes_image_proof.py' if step == 'registry' else script
            result = run(['docker', 'exec', name, python, step_script, step], check=False)
            (evidence / (step + '.log')).write_text(result.stdout + result.stderr)
            print(result.stdout + result.stderr, end='', flush=True)
            if result.returncode:
                raise RuntimeError(step + ' failed')
        run(['docker', 'cp', name + ':/tmp/image-bridge-proof/.', str(evidence)])
    finally:
        (evidence / 'container.log').write_text(run(['docker', 'logs', name], check=False).stdout)
        run(['docker', 'cp', name + ':/var/lib/docassemble/managed', str(evidence / 'local-state')], check=False)
        run(['docker', 'cp', name + ':/usr/share/docassemble/log', str(evidence / 'logs')], check=False)
    print('PASS targeted registry update/removal through normal frozen apply' if action == 'registry' else
          'PASS attempt-bound restricted serving core recovery' if action == 'recovery' else
          'PASS qualified 05c2 all-role image bridge' if action == 'flow' else
          'PASS authenticated published-normal boot fixture' if action == 'normal' else
          'PASS authenticated published-pending boot fixture (no application attempt)', flush=True)


if __name__ == '__main__':
    main()
