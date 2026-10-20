#!/usr/bin/env python3
"""Disposable automatic-adoption image proof; never touches host application services."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from proof_resources import cleanup, refuse_existing


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = root / os.environ.get('PROOF_EVIDENCE_DIR', 'uv-experiment/16/evidence/container')
    evidence.mkdir(parents=True, exist_ok=True)
    image = os.environ.get('PROOF_IMAGE', 'da-uv-16:local')
    name = 'da-16-legacy-adoption'
    commands = []

    def run(command, check=True, timeout=1800):
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        commands.append({'command': command, 'exit': result.returncode})
        (evidence / 'commands.json').write_text(json.dumps(commands, indent=2))
        if check and result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result

    def wait():
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            result = run(['docker', 'exec', name, 'curl', '-fsS', 'http://127.0.0.1/health_check?ready=1'], check=False)
            if result.returncode == 0:
                return
            time.sleep(2)
        status = run(['docker', 'exec', name, 'cat', '/var/run/docassemble/recovery/status.json'], check=False)
        raise RuntimeError('adoption readiness timed out: ' + status.stdout)

    python = '/usr/share/docassemble/executor-venv/bin/python'
    script = '/repo/uv-experiment/preparation/legacy_adoption_image_proof.py'
    refuse_existing(run, containers=(name,))
    (evidence / 'image.json').write_text(run(['docker', 'image', 'inspect', image]).stdout)
    run(['docker', 'run', '-d', '--name', name, '-v', str(root) + ':/repo:ro',
        '-e', 'DA_ADMIN_EMAIL=admin@example.com', '-e', 'DA_ADMIN_PASSWORD=FixtureAdmin101!',
        '-e', 'DA_QUALIFIED_IMAGE_BASELINE=/etc/docassemble/qualified-image/baseline.json',
        '-e', 'DA_DURABLE_WORK_STORE=/var/lib/docassemble-build/qualified-retained', image])
    try:
        files = ['docassemble_base/docassemble/base/managed_' + module + '.py' for module in
            ('adoption', 'preparation', 'publication', 'qualification', 'direct')]
        files += ['uv-experiment/executor/src/docassemble_executor/' + module + '.py' for module in
            ('legacy_adoption', 'durable_work', 'boot_observation', 'controller', 'registry_builder', 'direct_sources')]
        hashes = {}
        for path in files:
            target = ('/opt/docassemble-executor/' + path.removeprefix('uv-experiment/executor/')
                      if path.startswith('uv-experiment/executor/') else '/opt/da-sources/' + path)
            actual = run(['docker', 'exec', name, 'sha256sum', target]).stdout.split()[0]
            assert actual == hashlib.sha256((root / path).read_bytes()).hexdigest(), path
            hashes[path] = actual
        (evidence / 'source-hashes.json').write_text(json.dumps(hashes, indent=2, sort_keys=True))
        wait()
        setup = run(['docker', 'exec', name, python, script, 'setup'])
        (evidence / 'setup.log').write_text(setup.stdout + setup.stderr)
        print(setup.stdout, end='', flush=True)
        run(['docker', 'restart', name])
        wait()
        verified = run(['docker', 'exec', name, python, script, 'verify'])
        (evidence / 'verify.log').write_text(verified.stdout + verified.stderr)
        print(verified.stdout, end='', flush=True)
    finally:
        (evidence / 'container.log').write_text(run(['docker', 'logs', name], check=False).stdout)
        run(['docker', 'cp', name + ':/tmp/legacy-adoption-proof/.', str(evidence)], check=False)
        run(['docker', 'cp', name + ':/var/run/docassemble/managed-supervisor/.', str(evidence / 'supervisor')], check=False)
        run(['docker', 'cp', name + ':/usr/share/docassemble/log', str(evidence / 'logs')], check=False)
        cleanup(run, containers=(name,))


if __name__ == '__main__':
    main()
