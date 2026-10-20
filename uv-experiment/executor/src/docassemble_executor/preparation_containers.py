"""Opt-in trusted Docker exit authority for preparation, never installation.

The daemon and restricted receipt directory are operator authority. A private
PID namespace is destroyed before exit is reported; builder return is not proof.
No missing receipt, lease expiry, PID lookup or daemon error grants exit.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import Any, Iterator

from .preparation import Claim, Preparation, PreparationError
from .registry_builder import RegistryBuilder, validate_public_policy


class ContainerWorkers:
    def __init__(self, preparation: Preparation, receipts: Path, repository: Path, image: str, store: Path,
                 artifact_root: Path | None = None):
        self.preparation = preparation
        self.receipts = receipts.resolve()
        self.repository = repository.resolve()
        self.image = image
        self.store = store.resolve()
        self.artifact_root = artifact_root
        self.receipts.mkdir(parents=True, exist_ok=True, mode=0o700)

    @staticmethod
    def _docker(*arguments: str) -> str:
        result = subprocess.run(['docker', *arguments], capture_output=True, text=True)
        if result.returncode:
            raise PreparationError('preparation container authority unavailable')
        return result.stdout.strip()

    @staticmethod
    def _write(path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_suffix('.pending')
        with temporary.open('w') as handle:
            json.dump(value, handle)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @contextmanager
    def claim_lock(self, claim: Claim) -> Iterator[None]:
        """Exclude reconciliation through renewal drain and terminal DB writes.

        Process death releases the OS lock so orphan recovery remains possible.
        """
        validate_public_policy(claim.inputs)
        directory = self.receipts / claim.worker_id
        directory.mkdir(mode=0o700)
        with (directory / 'lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def build_claim(self, claim: Claim) -> dict[str, Any]:
        """Build under the caller's claim_lock, retained until terminal DB write."""
        validate_public_policy(claim.inputs)
        from docassemble.base.managed_direct import direct_declarations
        if direct_declarations(claim.inputs):
            # The container has no host content-addressed store; never fetch bytes
            # into a disposable namespace that cannot register them.
            raise PreparationError('direct URL sources require the restricted in-process preparation worker')
        directory = self.receipts / claim.worker_id
        inputs = directory / 'inputs.json'
        self._write(inputs, claim.inputs)
        # No daemon socket, host PID namespace, capabilities or credential env.
        daemon = self._docker('info', '--format', '{{.ID}}')
        if not daemon:
            raise PreparationError('unidentified preparation daemon')
        container = self._docker('create', '--user', '1000:1000', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--restart', 'no',
            '--label', 'da.preparation.worker=' + claim.worker_id,
            '--mount', f'type=bind,src={self.repository},dst=/repo,readonly',
            '--env', 'PYTHONPATH=/repo/uv-experiment/executor/src:/repo/docassemble_base',
            '--env', 'UV_PROJECT_ENVIRONMENT=/tmp/executor',
            '--env', 'UV_CACHE_DIR=/tmp/tool-cache', '--entrypoint', 'sh', self.image,
            '-c', 'mkdir -m 700 /tmp/artifacts && cp -R /tmp/incoming-artifacts/. /tmp/artifacts/ && '
            'chmod -R go-rwx /tmp/artifacts && /usr/local/libexec/uv-pinned run --project /repo/uv-experiment/executor '
            '--frozen python -m docassemble_executor.preparation_containers '
            '--inputs /tmp/inputs.json')
        self._write(directory / 'receipt.json', {'container': container,
            'worker': claim.worker_id, 'candidate': claim.candidate_id, 'daemon': daemon})
        self._docker('cp', str(inputs), container + ':/tmp/inputs.json')
        self._copy_artifacts(container, claim.inputs, directory)
        self._docker('start', container)
        if self._docker('wait', container) != '0':
            raise PreparationError('registry preparation command failed')
        self._docker('cp', container + ':/tmp/result.json', str(directory / 'result.json'))
        # Retained bytes live outside the disposable worker namespace.
        result = json.loads((directory / 'result.json').read_text())
        from .registry_builder import verify_retained, publish_retained
        self.store.mkdir(parents=True, exist_ok=True, mode=0o700)
        retained = Path(tempfile.mkdtemp(prefix='.retain-', dir=self.store))
        try:
            self._docker('cp', container + ':/tmp/retained/.', str(retained))
            source = retained / result['bundle']['digest']
            verify_retained(source, result['bundle']['digest'])
            for path in source.rglob('*'):
                if path.is_file():
                    with path.open('rb') as handle:
                        os.fsync(handle.fileno())
            for path in [p for p in source.rglob('*') if p.is_dir()] + [source]:
                fd = os.open(path, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            publish_retained(source, self.store / result['bundle']['digest'], result['bundle']['digest'])
            fd = os.open(self.store, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            shutil.rmtree(retained)
        return result

    def _copy_artifacts(self, container, inputs, receipt_directory):
        from .registry_builder import retained_artifacts
        from .bundles import sha256_file
        # Root verifies the API-owned store before crossing into the build UID.
        # Temporary transport bytes never become a second durable artifact store.
        with tempfile.TemporaryDirectory(prefix='.artifact-copy-', dir=receipt_directory) as temporary:
            artifacts = Path(temporary) / 'artifacts'
            artifacts.mkdir(mode=0o755)
            for identity, source in retained_artifacts(inputs, self.artifact_root).values():
                target = artifacts / identity['digest']
                shutil.copyfile(source, target)
                if sha256_file(target) != identity['digest']:
                    raise PreparationError('distribution artifact digest mismatch')
                target.chmod(0o644)  # Temporary ancestor is 0700; worker transport is readable.
            self._docker('cp', str(artifacts), container + ':/tmp/incoming-artifacts')

    def reconcile(self) -> int:
        """Nonblocking orphan pass; live creators/builders keep their receipt lock."""
        exited = 0
        for directory in sorted(self.receipts.iterdir()):
            receipt = directory / 'receipt.json'
            if not directory.is_dir() or not receipt.is_file():
                continue  # Missing identity is NOT proof of exit.
            with (directory / 'lock').open('a') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                value = json.loads(receipt.read_text())
                container = value['container']
                if self._docker('info', '--format', '{{.ID}}') != value['daemon']:
                    raise PreparationError('preparation daemon identity changed')
                # A successful daemon listing distinguishes absence from outage.
                present = self._docker('ps', '-aq', '--no-trunc').splitlines()
                if container in present:
                    info = json.loads(self._docker('inspect', container))[0]
                    host = info['HostConfig']
                    if (info['Config']['Labels'].get('da.preparation.worker') != value['worker']
                            or host['Privileged'] or host['PidMode'] != ''
                            or host['RestartPolicy']['Name'] != 'no'
                            or info['Config']['User'] != '1000:1000'
                            or host['CapDrop'] != ['ALL']
                            or any(m['Destination'] != '/repo' or m['RW'] for m in info['Mounts'])):
                        raise PreparationError('untrusted preparation container')
                    if info['State']['Status'] not in ('created', 'exited'):
                        continue
                    # Non-force removal cannot race a running container successfully.
                    # Removal makes restarting this namespace impossible.
                    self._docker('rm', container)
                self.preparation.worker_exited(value['worker'])
                # A crash during docker cp can leave host transport bytes. The
                # receipt lock and destroyed namespace now prove they are unused.
                for transport in directory.glob('.artifact-copy-*'):
                    if transport.is_symlink() or not transport.is_dir():
                        raise PreparationError('untrusted artifact transport staging')
                    shutil.rmtree(transport)
                exited += 1
        return exited


def _qualify_git() -> None:
    """Prove the preparation worker image can reach an HTTPS Git source (cold)."""
    try:
        version = subprocess.check_output(['git', '--version'], text=True, timeout=60).strip()
        if not version.startswith('git version '):
            raise ValueError()
        output = subprocess.check_output(['git', '-c', 'http.sslVerify=true', '-c', 'http.followRedirects=false',
            '-c', 'protocol.allow=never', '-c', 'protocol.https.allow=always', 'ls-remote', '--symref',
            'https://github.com/pypa/flit.git', 'refs/tags/3.12.0'], text=True, timeout=120)
        if 'refs/tags/3.12.0' not in output:
            raise ValueError()
    except (OSError, ValueError, subprocess.SubprocessError):
        raise SystemExit('preparation worker git/egress qualification failed') from None
    print(json.dumps({'git': version, 'cold_ls_remote': True, 'repository': 'https://github.com/pypa/flit.git'}))


def main() -> None:
    parser = argparse.ArgumentParser(description='Restricted disposable preparation child')
    parser.add_argument('--inputs', type=Path)
    parser.add_argument('--qualify-git', action='store_true',
                        help='assert the worker image can run git and reach HTTPS Git')
    args = parser.parse_args()
    if args.qualify_git:
        _qualify_git()
        return
    if args.inputs is None:
        parser.error('--inputs required for a preparation build')
    try:
        result = RegistryBuilder(Path('/tmp/retained'), '/usr/local/libexec/uv-pinned', artifact_root=Path('/tmp/artifacts')).build(
            json.loads(args.inputs.read_text()))
        Path('/tmp/result.json').write_text(json.dumps(result))
    except Exception:
        raise SystemExit('preparation failed; diagnostics withheld') from None


if __name__ == '__main__':
    main()
