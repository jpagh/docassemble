#!/usr/bin/env python3
"""Pinned-image cold boot reconciliation; requires the full image-bridge proof first."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = root / os.environ.get('PROOF_EVIDENCE_DIR', 'uv-experiment/05c3/evidence/accepted/boot')
    evidence.mkdir(parents=True, exist_ok=True)
    image = os.environ.get('PROOF_IMAGE', 'da-uv-05c3:local')
    pending = os.environ.get('PROOF_PENDING_CONTAINER', 'da-05c3-boot-pending')
    failed = os.environ.get('PROOF_FAILED_CONTAINER', 'da-05c3-image-bridge')
    python = '/usr/share/docassemble/executor-venv/bin/python'
    script = '/repo/uv-experiment/preparation/boot_observation_proof.py'
    commands = []

    def run(command, check=True, env=None):
        result = subprocess.run(command, capture_output=True, text=True, env=env)
        commands.append({'command': command, 'exit': result.returncode,
                         'proof_environment': {key: value for key, value in (env or {}).items() if key.startswith('PROOF_')}})
        (evidence / 'commands.json').write_text(json.dumps(commands, indent=2))
        if check and result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result

    def action(name, step):
        result = run(['docker', 'exec', name, python, script, step])
        print(result.stdout, end='', flush=True)

    def restart(name, unavailable=False):
        run(['docker', 'restart', '--time', '90', name])
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            result = run(['docker', 'exec', name, 'curl', '-fsS', 'http://127.0.0.1/status.json'], check=False)
            if result.returncode == 0:
                status = json.loads(result.stdout)
                if unavailable and 'reconciliation unavailable' in status.get('detail', ''):
                    assert status['normal_ready'] is False and status['recovery_available'] is False
                    (evidence / 'unavailable-status.json').write_text(json.dumps(status, indent=2))
                    return
                if not unavailable and status.get('reconciliation') == 'observed-maintenance':
                    return
            time.sleep(1)
        raise RuntimeError('boot observation timeout: ' + run(['docker', 'logs', name]).stdout)

    environment = dict(os.environ, PROOF_IMAGE=image, PROOF_CONTAINER_NAME=pending,
        PROOF_EVIDENCE_DIR=str(evidence / 'pending-publication'), PROOF_FLOW_ACTION='pending')
    result = run([sys.executable, str(root / 'uv-experiment/run-image-bridge-proof.py')], check=False, env=environment)
    (evidence / 'pending-publication.log').write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout, end='', flush=True)
    identity = run(['docker', 'image', 'inspect', image, '--format', '{{.Id}}']).stdout.strip()
    (evidence / 'image.txt').write_text(identity + '\n')
    try:
        for name in (pending, failed):
            assert run(['docker', 'inspect', name, '--format', '{{.Image}}']).stdout.strip() == identity
            action(name, 'capture')
            if name == pending:
                action(name, 'hide-descriptor')
            restart(name)
            action(name, 'verify')
            if name == pending:
                action(name, 'restore-descriptor')
                print('PASS publication before journal/boot marker and missing descriptor still selects DB intent', flush=True)
            action(name, 'hide-descriptor')
            restart(name)
            action(name, 'verify')
            action(name, 'restore-descriptor')
            print('PASS durable boot detection survives operator descriptor removal; no fixture-authority fallback', flush=True)
            run(['docker', 'cp', name + ':/tmp/boot-observation-proof', str(evidence / name)])
        action(pending, 'hide-tables')
        restart(pending, unavailable=True)
        print('PASS unavailable management tables keep independent maintenance/normal-not-ready; no runtime fallback', flush=True)
        action(pending, 'restore-tables')
        restart(pending)
        action(pending, 'verify')
    finally:
        for name in (pending, failed):
            result = run(['docker', 'logs', name], check=False)
            (evidence / (name + '.log')).write_text(result.stdout + result.stderr)
    print('PASS bounded 05c3a published DB boot reconciliation (no polling/apply/recovery resumption)', flush=True)


if __name__ == '__main__':
    main()
