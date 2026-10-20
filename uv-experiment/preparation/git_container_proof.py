"""Qualify HTTPS Git support inside the preparation worker image (git + cold egress).

The container runs the repository's mounted code read-only, mirroring
`ContainerWorkers`. TLS verification stays on; the public ref is only a liveness
probe, so no credential is mounted or needed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def worker_image() -> str:
    explicit = os.environ.get('DA_PREPARATION_WORKER_IMAGE')
    if explicit:
        return explicit
    for candidate in ('da-uv-10b:local', 'da-uv-09b:local'):
        if subprocess.run(['docker', 'image', 'inspect', candidate], capture_output=True).returncode == 0:
            return candidate
    raise SystemExit('preparation worker image unavailable; set DA_PREPARATION_WORKER_IMAGE')


def docker(*arguments: str) -> str:
    result = subprocess.run(['docker', *arguments], capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def main():
    image = worker_image()
    version = docker('run', '--rm', '--user', '1000:1000', '--entrypoint', 'sh', image, '-c', 'git --version')
    assert version.startswith('git version '), version
    output = docker('run', '--rm', '--user', '1000:1000',
        '--mount', f'type=bind,src={ROOT},dst=/repo,readonly',
        '--env', 'PYTHONPATH=/repo/uv-experiment/executor/src:/repo/docassemble_base',
        '--env', 'UV_PROJECT_ENVIRONMENT=/tmp/executor',
        '--env', 'UV_CACHE_DIR=/tmp/tool-cache',
        '--entrypoint', 'sh', image, '-c',
        'mkdir -m 700 /tmp/artifacts && /usr/local/libexec/uv-pinned run --project /repo/uv-experiment/executor '
        '--frozen python -m docassemble_executor.preparation_containers --qualify-git')
    receipt = json.loads(output.splitlines()[-1])
    assert receipt['git'] == version and receipt['cold_ls_remote'] is True
    assert receipt['repository'] == 'https://github.com/pypa/flit.git'
    print(json.dumps({'image': image, **receipt,
                      'scope': 'disposable-worker-image-liveness-probe'}, sort_keys=True))
    print('PASS: preparation worker image runs git and reaches HTTPS Git cold')


if __name__ == '__main__':
    main()
