"""Disposable pinned-image private index proof, without shell redirection."""
import json
import os
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
evidence = root / os.environ.get('PROOF_EVIDENCE_DIR', 'uv-experiment/05a2b4/evidence/accepted/source')
evidence.mkdir(parents=True, exist_ok=True)
image = os.environ.get('PROOF_IMAGE', 'da-uv-05c5-fix:local')
name = os.environ.get('PROOF_CONTAINER_NAME', 'da-05a2b4-private-index')


def run(*args):
    return subprocess.check_output(args, stderr=subprocess.STDOUT, text=True)


(evidence / 'image.json').write_text(run('docker', 'image', 'inspect', image))
command = ['docker', 'run', '--name', name, '--user', '1000:1000', '--cap-drop', 'ALL',
           '--security-opt', 'no-new-privileges', '--mount', f'type=bind,src={root},dst=/repo,readonly',
           '--env', 'PYTHONPATH=/repo/uv-experiment/executor/src:/repo/docassemble_base',
           '--env', 'UV_PROJECT_ENVIRONMENT=/tmp/executor', '--env', 'UV_CACHE_DIR=/tmp/tool-cache',
           '--entrypoint', '/usr/local/libexec/uv-pinned', image,
           'run', '--project', '/repo/uv-experiment/executor', '--frozen', 'python',
           '/repo/uv-experiment/preparation/private_index_proof.py']
result = subprocess.run(command, capture_output=True, text=True)
output = result.stdout + result.stderr
(evidence / 'container.log').write_text(output)
(evidence / 'command.json').write_text(json.dumps({'command': command, 'exit': result.returncode}, indent=2))
(evidence / 'container.json').write_text(run('docker', 'inspect', name))
print(output, end='')
if result.returncode == 0:
    run('docker', 'cp', name + ':/tmp/proof/.', str(evidence))
raise SystemExit(result.returncode)
