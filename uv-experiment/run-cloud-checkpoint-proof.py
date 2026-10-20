#!/usr/bin/env python3
"""Disposable pinned-image managed backup proof, with explicit read-only source overlay."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

from proof_resources import cleanup, refuse_existing


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = root / os.environ.get('PROOF_EVIDENCE_DIR', 'uv-experiment/22/evidence/container')
    evidence.mkdir(parents=True, exist_ok=True)
    image = os.environ.get('PROOF_IMAGE', 'da-uv-16:local')
    name = 'da-22-cloud-checkpoint'
    commands = []

    def run(command, check=True, timeout=1800):
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        commands.append({'command': command, 'exit': result.returncode})
        (evidence / 'commands.json').write_text(json.dumps(commands, indent=2))
        if check and result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result

    refuse_existing(run, containers=(name,))
    (evidence / 'image.json').write_text(run(['docker', 'image', 'inspect', image]).stdout)
    sources = ['Docker/backup.sh', 'uv-experiment/executor/src/docassemble_executor/managed_backup.py',
               'uv-experiment/executor/src/docassemble_executor/scheduled_backup.py',
               'uv-experiment/preparation/managed_backup_proof.py',
               'uv-experiment/executor/src/docassemble_executor/checkpoints.py',
               'uv-experiment/executor/src/docassemble_executor/cloud_checkpoints.py',
               'uv-experiment/backup/cloud_cli_fake.py',
               'uv-experiment/preparation/cloud_checkpoint_proof.py']
    (evidence / 'source-hashes.json').write_text(json.dumps({path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in sources}, indent=2))
    run(['docker', 'run', '-d', '--name', name, '--entrypoint', '/bin/bash',
         '-v', str(root) + ':/repo:ro', '-e', 'PYTHONPATH=/repo/uv-experiment/executor/src:/repo/docassemble_base',
         image, '-c', 'service postgresql start && exec sleep infinity'])
    try:
        proof = run(['docker', 'exec', name, '/usr/share/docassemble/executor-venv/bin/python',
                     '/repo/uv-experiment/preparation/cloud_checkpoint_proof.py'])
        (evidence / 'proof.log').write_text(proof.stdout + proof.stderr)
        print(proof.stdout, end='')
    finally:
        (evidence / 'container.log').write_text(run(['docker', 'logs', name], check=False).stdout)
        # Do not copy credential-bearing object/DB backups into repository
        # evidence. Fixture PAT/URLs stay inside the disposable proof container.
        run(['docker', 'cp', name + ':/tmp/managed-backup-proof/result.json', str(evidence / 'result.json')], check=False)
        run(['docker', 'cp', name + ':/tmp/managed-backup-proof/cloud-checkpoint-result.json', str(evidence / 'cloud-checkpoint-result.json')], check=False)
        cleanup(run, containers=(name,))


if __name__ == '__main__':
    main()
