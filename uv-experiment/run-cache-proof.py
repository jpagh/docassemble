#!/usr/bin/env python3
"""Run ticket 20 in a disposable pinned image; never touch host service/cache paths."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

from proof_resources import cleanup, refuse_existing


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = root / os.environ.get('PROOF_EVIDENCE_DIR', 'uv-experiment/20/evidence/container')
    evidence.mkdir(parents=True, exist_ok=True)
    image = os.environ.get('PROOF_IMAGE', 'da-uv-16:local')
    name = 'da-20-cache-proof'
    commands = []

    def run(command, check=True):
        result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
        commands.append({'command': command, 'exit': result.returncode})
        (evidence / 'commands.json').write_text(json.dumps(commands, indent=2))
        if check and result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result

    refuse_existing(run, containers=(name,))
    sources = ['authority.py', 'cache.py', 'paths.py', 'uvsync.py', 'preparation_poller.py', 'durable_work.py', 'cli.py']
    files = ['uv-experiment/executor/src/docassemble_executor/' + file for file in sources]
    files += ['uv-experiment/preparation/cache_proof.py', 'uv-experiment/run-cache-proof.py']
    (evidence / 'source-hashes.json').write_text(json.dumps(
        {file: hashlib.sha256((root / file).read_bytes()).hexdigest() for file in files}, indent=2))
    (evidence / 'image.json').write_text(run(['docker', 'image', 'inspect', image]).stdout)
    run(['docker', 'run', '-d', '--name', name, '--entrypoint', '/bin/sh',
         '-v', str(root) + ':/repo:ro',
         '-e', 'PYTHONPATH=/repo/uv-experiment/executor/src:/repo/docassemble_base',
         image, '-c', 'exec sleep infinity'])
    try:
        proof = run(['docker', 'exec', name, '/usr/share/docassemble/executor-venv/bin/python',
                     '/repo/uv-experiment/preparation/cache_proof.py'], check=False)
        (evidence / 'proof.log').write_text(proof.stdout + proof.stderr)
        print(proof.stdout + proof.stderr, end='')
        if proof.returncode:
            raise RuntimeError('cache proof failed; see proof.log')
    finally:
        run(['docker', 'cp', name + ':/tmp/cache-proof/result.json', str(evidence / 'result.json')], check=False)
        run(['docker', 'cp', name + ':/tmp/cache-proof/uv-commands.log', str(evidence / 'uv-commands.log')], check=False)
        cleanup(run, containers=(name,))


if __name__ == '__main__':
    main()
