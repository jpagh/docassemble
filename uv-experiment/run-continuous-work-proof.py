#!/usr/bin/env python3
"""Pinned-image supervised continuous durable work and healthy cold resumption."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = root / os.environ.get('PROOF_EVIDENCE_DIR', 'uv-experiment/05c4/evidence/accepted/continuous')
    evidence.mkdir(parents=True, exist_ok=True)
    image = os.environ.get('PROOF_IMAGE', 'da-uv-05c4:local')
    name = os.environ.get('PROOF_CONTAINER_NAME', 'da-05c4-continuous')
    commands = []
    def run(command, check=True, env=None):
        result = subprocess.run(command, capture_output=True, text=True, env=env)
        commands.append({'command': command, 'exit': result.returncode,
                         'proof_environment': {k: v for k, v in (env or {}).items() if k.startswith('PROOF_')}})
        (evidence / 'commands.json').write_text(json.dumps(commands, indent=2))
        if check and result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result
    environment = dict(os.environ, PROOF_IMAGE=image, PROOF_CONTAINER_NAME=name, PROOF_FLOW_ACTION='normal',
        PROOF_DURABLE_WORK='1', PROOF_EVIDENCE_DIR=str(evidence / 'normal-fixture'))
    result = run([sys.executable, str(root / 'uv-experiment/run-image-bridge-proof.py')], check=False, env=environment)
    (evidence / 'normal-fixture.log').write_text(result.stdout + result.stderr)
    print(result.stdout + result.stderr, end='', flush=True)
    assert result.returncode == 0
    python = '/usr/share/docassemble/executor-venv/bin/python'
    script = '/repo/uv-experiment/preparation/continuous_work_proof.py'
    run(['docker', 'exec', name, python, script, 'capture'])
    run(['docker', 'restart', '--time', '90', name])
    deadline = time.monotonic() + 180
    try:
        while time.monotonic() < deadline:
            result = run(['docker', 'exec', name, 'curl', '-fsS', 'http://127.0.0.1/health_check?ready=1'], check=False)
            if result.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('healthy resumption timeout: ' + run(['docker', 'logs', name]).stdout)
        result = run(['docker', 'exec', name, python, script, 'verify'], check=False)
        (evidence / 'verify.log').write_text(result.stdout + result.stderr)
        print(result.stdout + result.stderr, end='', flush=True)
        assert result.returncode == 0
    finally:
        (evidence / 'container.log').write_text(run(['docker', 'logs', name], check=False).stdout)
        run(['docker', 'cp', name + ':/tmp/continuous-work-proof/.', str(evidence)], check=False)
        run(['docker', 'cp', name + ':/var/run/docassemble/managed-supervisor', str(evidence / 'supervisor')], check=False)
    print('PASS bounded 05c4 continuous durable work and healthy resumption', flush=True)


if __name__ == '__main__':
    main()
