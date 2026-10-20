"""Preparation-only dispatch with container and Linux direct-worker exit authority."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import fcntl
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import create_engine

from .authority import MutationLock, guarded_popen, mutation_fd
from .paths import ExecutorPaths
from .preparation import Preparation, PreparationError
from .registry_builder import RegistryBuilder
from .preparation_containers import ContainerWorkers
from .preparation_processes import ProcessWorkers
from docassemble.base.managed_direct import direct_declarations


class Builder(Protocol):
    def build(self, inputs: dict[str, Any], *, retain_direct=None) -> dict[str, Any]: ...


class PreparationPoller:
    def __init__(self, preparation: Preparation, builder: Builder, *, lease_seconds: int = 30,
                 containers: ContainerWorkers | None = None, material_root: Path | None = None,
                 artifact_root: Path | None = None):
        if not 1 <= lease_seconds <= 300:
            raise PreparationError('invalid preparation lease')
        self.preparation = preparation
        self.builder = builder
        self.lease_seconds = lease_seconds
        self.containers = containers
        self.material_root = material_root
        self.artifact_root = artifact_root
        receipt_root = artifact_root if artifact_root is not None else material_root
        self.process_receipts = (Path(receipt_root) / '.direct-workers'
                                 if receipt_root is not None and containers is None else None)
        self.processes: ProcessWorkers | None = None

    def _reconcile(self) -> None:
        # Non-direct dispatch must not create a guardian store. Existing receipts
        # still need discovery after a dispatcher restart, even with no new jobs.
        if self.processes is None and self.process_receipts is not None and self.process_receipts.exists():
            self.processes = ProcessWorkers(self.preparation, self.process_receipts)
        if self.processes is not None:
            self.processes.reconcile()
        if self.containers is not None:
            self.containers.reconcile()
        if self.artifact_root is not None:
            self.preparation.reconcile_artifacts(self.artifact_root)
        if self.material_root is not None:
            self.preparation.reconcile_material(self.material_root)

    def run_once(self) -> str:
        privileged = os.geteuid() == 0 and bool(os.environ.get('DA_EXECUTOR_ARTIFACT_ROOT'))
        if privileged:
            from .legacy_adoption import ingest_registered_material, drop_to_preparation_worker
            # Root only reconciles the disjoint ingress from durable exit evidence.
            ingress = Path(os.environ['DA_MANAGED_ARTIFACT_ROOT'])
            self.preparation.reconcile_artifacts(ingress)
            self.preparation.reconcile_material(Path(os.environ.get('DA_MANAGED_WEB_SECRET_ROOT') or str(ingress) + '-secrets'))
        else:
            self._reconcile()
        claim = self.preparation.claim_next(lease_seconds=self.lease_seconds)
        if privileged:
            ingestion_failed = False
            if claim is not None:
                try:
                    ingest_registered_material(self.preparation, claim)
                except Exception:
                    ingestion_failed = True
                    self.preparation.fail(claim)
                    # No build/descendant has started; this root dispatch is the worker.
                    self.preparation.worker_exited(claim.worker_id)
            self.preparation.engine.dispose()
            drop_to_preparation_worker()
            self._reconcile()
            if ingestion_failed:
                return 'failed'
        if claim is None:
            return 'idle'
        try:
            return self._run_claim(claim)
        finally:
            self._reconcile()

    def _run_claim(self, claim) -> str:
        with ExitStack() as stack:
            if self.containers is not None:
                try:
                    stack.enter_context(self.containers.claim_lock(claim))
                except Exception:
                    return 'failed' if self.preparation.fail(claim) else 'fenced'
            process_build = None
            from docassemble.base.managed_git import source_declarations
            needs_receipt = bool(direct_declarations(claim.inputs) or source_declarations(claim.inputs) or
                                 claim.inputs.get('artifact') or claim.inputs.get('git_credentials') or
                                 any(row.get('artifact_ref') for row in claim.inputs['contributions']))
            if self.containers is None and needs_receipt:
                try:
                    if self.process_receipts is None:
                        raise PreparationError('direct URL preparation requires the restricted artifact store')
                    if self.processes is None:
                        self.processes = ProcessWorkers(self.preparation, self.process_receipts)
                    # Fork before starting renewal; the child gets its own DB pool.
                    process_build = stack.enter_context(self.processes.build_claim(claim, lambda:
                        self.builder.build(self._resolve_credentials(claim), retain_direct=lambda reference, source=None:
                            self.preparation.retain_direct_artifact(claim, self.artifact_root, reference, source))))
                except Exception:
                    return 'failed' if self.preparation.fail(claim) else 'fenced'
            stop, lost = threading.Event(), threading.Event()

            def renew() -> None:
                while not stop.wait(self.lease_seconds / 3):
                    try:
                        if self.preparation.renew(claim, lease_seconds=self.lease_seconds):
                            continue
                    except Exception:
                        # DB uncertainty never authorizes publication or credential use.
                        pass
                    lost.set()
                    return

            renewal = threading.Thread(target=renew, name='preparation-renewal')
            renewal.start()
            try:
                try:
                    if self.containers is not None:
                        if claim.inputs.get('git_credentials'):
                            raise PreparationError('Git credentials are unsupported by container preparation workers')
                        result = self.containers.build_claim(claim)
                    elif process_build is not None:
                        result = process_build.result()
                    else:
                        result = self.builder.build(self._resolve_credentials(claim))
                finally:
                    # Drain in-flight renewal before either terminal write: uncertainty
                    # must be observed even if the existing DB lease is still valid.
                    stop.set()
                    renewal.join()
                    # Exit reporting follows terminal writes and namespace destruction.
            except Exception:
                # Never expose raw tooling output or exception text.
                return 'fenced' if lost.is_set() else (
                    'failed' if self.preparation.fail(claim) else 'fenced')
            if lost.is_set():
                return 'fenced'
            return 'ready' if self.preparation.publish_ready(claim, result) else 'fenced'

    def _resolve_credentials(self, claim) -> dict:
        """Resolve opaque Git credential references under the live claim.

        Secrets never enter persisted inputs or a container; a revoked or missing
        reference fails the claim closed with a fixed actionable error.
        """
        references = claim.inputs.get('git_credentials') or {}
        if not references:
            return claim.inputs
        if self.material_root is None:
            raise PreparationError('Git credentials require the restricted preparation worker')
        authorizations = {name: self.preparation.read_git_credential(claim, self.material_root, reference)
                          for name, reference in references.items()}
        return {**claim.inputs, 'git_authorizations': authorizations}

    def run(self, poll_seconds: float = 1) -> None:
        if poll_seconds <= 0:
            raise PreparationError('invalid polling interval')
        while True:
            print(self.run_once(), flush=True)
            time.sleep(poll_seconds)


def guarded_preparation_once(paths: ExecutorPaths, arguments: list[str], *, env: dict | None = None) -> int:
    """Exclude cache maintenance for the entire dispatch and descendant lifetime."""
    with MutationLock(paths.state_dir):
        from .checkpoint_restore import require_restore_ready
        require_restore_ready(paths)
        fd = mutation_fd()
        process = guarded_popen([sys.executable, '-m', 'docassemble_executor.preparation_poller',
                                 *arguments, '--guarded-once', str(fd)], fd, env=env, inherit_lock=True)
        try:
            return process.wait()
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass  # Guardian retains the mutation lock until descendants exit.
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description='Internal preparation-only worker')
    parser.add_argument('--store', required=True, type=Path)
    parser.add_argument('--prefix', default='')
    parser.add_argument('--image-baseline', type=Path, help='explicit operator-provisioned experimental image wheels')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--guarded-once', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--material-root', type=Path)
    parser.add_argument('--index-credential-root', type=Path,
                        help='explicit operator-provisioned restricted index-credential root')
    parser.add_argument('--artifact-root', type=Path, default=os.environ.get('DA_MANAGED_ARTIFACT_ROOT'))
    parser.add_argument('--worker-image')
    parser.add_argument('--worker-repository', type=Path)
    parser.add_argument('--worker-receipts', type=Path)
    args = parser.parse_args()
    if args.worker_image:
        parser.error('locked preparation requires direct/in-process workers; Docker daemon worker exclusion is not qualified')
    from .cache import validate_retained_paths
    retained = [value for value in (args.store, args.image_baseline, args.material_root,
                args.index_credential_root, args.artifact_root, args.worker_repository,
                args.worker_receipts) if value is not None]
    if args.image_baseline is not None:
        retained.append(args.image_baseline.parent)
    validate_retained_paths(ExecutorPaths.from_env(), retained)
    if args.guarded_once is None:
        paths = ExecutorPaths.from_env()
        arguments = [arg for arg in sys.argv[1:] if arg != '--once']
        while True:
            try:
                code = guarded_preparation_once(paths, arguments)
            except BlockingIOError:
                code = 75
            if args.once:
                raise SystemExit(code)
            time.sleep(1)
    # Private child mode must actually hold the inherited mutation authority,
    # not merely trust a CLI flag. The guardian retains it through descendant exit.
    fd = args.guarded_once
    expected = (ExecutorPaths.from_env().state_dir / 'mutation.lock').stat()
    inherited = os.fstat(fd)
    if (inherited.st_dev, inherited.st_ino) != (expected.st_dev, expected.st_ino):
        raise PreparationError('preparation mutation authority mismatch')
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    # URL is restricted environment configuration, never a printed argument.
    engine = create_engine(os.environ['DA_PREPARATION_DATABASE_URL'])
    preparation = Preparation(engine, lambda *_: False, args.prefix)
    poller = PreparationPoller(preparation, RegistryBuilder(args.store, image_baseline=args.image_baseline,
                               artifact_root=args.artifact_root, index_credential_root=args.index_credential_root),
                               material_root=args.material_root, artifact_root=args.artifact_root)
    try:
        if args.once or args.guarded_once is not None:
            print(poller.run_once(), flush=True)
        else:
            poller.run()
    except Exception:
        raise SystemExit('preparation worker stopped; restricted operator inspection required') from None
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
