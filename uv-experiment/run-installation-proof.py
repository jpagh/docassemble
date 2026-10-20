#!/usr/bin/env python3
"""Capture the bounded 05c pinned-uv disposable container proof."""
from pathlib import Path
import json
import os
import subprocess
from functools import partial

from proof_resources import refuse_existing


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = Path(os.environ.get('PROOF_EVIDENCE_DIR', root / 'uv-experiment/05c/evidence/accepted/installation'))
    evidence.mkdir(parents=True, exist_ok=True)
    image = os.environ.get('PROOF_IMAGE', 'da-uv-full-maintenance:local')
    name = os.environ.get('PROOF_CONTAINER_NAME', 'da-05c-installation-proof')
    identity = subprocess.run(['docker', 'image', 'inspect', image, '--format', '{{.Id}}'],
                              capture_output=True, text=True, check=True).stdout
    (evidence / 'image.txt').write_text(identity)
    refuse_existing(partial(subprocess.run, capture_output=True, text=True, check=True), containers=(name,))
    command = ['docker', 'run', '--name', name, '--entrypoint', '/usr/local/libexec/uv-pinned',
        '--network', 'container:da-05a2-postgres', '-v', str(root) + ':/repo:ro',
        '-e', 'PYTHONPATH=/repo/uv-experiment/executor/src:/repo/docassemble_base',
        '-e', 'DA_PREPARATION_DATABASE_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:5432/postgres',
        '-e', 'UV_PROJECT_ENVIRONMENT=/tmp/executor', '-e', 'UV_CACHE_DIR=/tmp/tool-cache',
        image, 'run', '--project', '/repo/uv-experiment/executor', '--frozen',
        'python', '/repo/uv-experiment/preparation/installation_proof.py']
    (evidence / 'command.json').write_text(json.dumps(command, indent=2))
    completed = subprocess.run(command, capture_output=True, text=True)
    (evidence / 'container.log').write_text(completed.stdout + completed.stderr)
    print(completed.stdout + completed.stderr, end='')
    if completed.returncode:
        raise SystemExit(completed.returncode)
    subprocess.run(['docker', 'cp', name + ':/tmp/proof/.', str(evidence)], check=True)


if __name__ == '__main__':
    main()
