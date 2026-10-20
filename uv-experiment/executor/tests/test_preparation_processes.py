"""Linux guardian receipts prove descendant exit, not builder/dispatcher return."""
import json
import os
from pathlib import Path
import select
import signal
from types import SimpleNamespace

import pytest

from docassemble_executor.preparation import Preparation, PreparationError
from docassemble_executor.preparation_processes import ProcessWorkers


class FixturePreparation:
    _secret_root = staticmethod(Preparation._secret_root)
    engine = SimpleNamespace(dispose=lambda **kwargs: None)

    def __init__(self):
        self.exited = []

    def worker_exited(self, worker):
        self.exited.append(worker)


def receive(fd):
    assert select.select([fd], [], [], 10)[0], 'child did not reach interleaving seam'
    assert os.read(fd, 1) == b'x'


def test_guardian_waits_for_daemonized_descendants_and_terminal_lock(tmp_path):
    preparation = FixturePreparation()
    workers = ProcessWorkers(preparation, tmp_path / 'receipts')
    claim = SimpleNamespace(worker_id='worker')
    arrived, notify = os.pipe()
    release, resume = os.pipe()

    def build():
        child = os.fork()
        if child == 0:
            os.setsid()
            # A double fork must be adopted even though it escaped the process group.
            if os.fork() != 0:
                os._exit(0)
            os.write(notify, b'x')
            os.read(release, 1)
            os._exit(0)
        return {'built': True}

    try:
        with workers.build_claim(claim, build) as process:
            receive(arrived)
            assert not (process.directory / 'exit.json').exists()
            assert workers.reconcile() == 0
            os.write(resume, b'x')
            assert process.result() == {'built': True}
            assert (process.directory / 'exit.json').exists()
            assert workers.reconcile() == 0  # Terminal DB write has not completed.
        assert workers.reconcile() == 1
        assert preparation.exited == ['worker']
        assert not (process.directory / 'result.json').exists()
        assert not (process.directory / 'result.pending').exists()
        assert json.loads((process.directory / 'exit.json').read_text()) == {'worker': 'worker'}
    finally:
        for fd in (arrived, notify, release, resume):
            os.close(fd)


def test_result_pending_writer_crash_waits_for_descendants_then_removes_sensitive_staging(tmp_path, monkeypatch):
    preparation = FixturePreparation()
    workers = ProcessWorkers(preparation, tmp_path / 'receipts')
    sentinel = 'CRASH_STAGING_SENTINEL_CREDENTIAL'
    arrived, notify = os.pipe()
    release, resume = os.pipe()
    replace = Path.replace
    process = None
    reaped = False

    def crash_before_rename(path, target):
        if path.name == 'result.pending':
            assert sentinel in path.read_text()
            os.write(notify, b'x')
            os.kill(os.getpid(), signal.SIGKILL)
        return replace(path, target)

    monkeypatch.setattr(Path, 'replace', crash_before_rename)

    def build():
        child = os.fork()
        if child == 0:
            os.setsid()
            os.read(release, 1)
            os._exit(0)
        return {'source': 'https://user:' + sentinel + '@example.test/source.whl'}

    try:
        with workers.build_claim(SimpleNamespace(worker_id='crashed-writer'), build) as process:
            receive(arrived)
            pending = process.directory / 'result.pending'
            assert sentinel in pending.read_text()
            assert not (process.directory / 'result.json').exists()
            assert not (process.directory / 'exit.json').exists()
            assert workers.reconcile() == 0  # Dispatcher still owns terminal-write exclusion.
        assert workers.reconcile() == 0  # Guardian retains the lock until its descendant exits.
        assert sentinel in pending.read_text()
        assert preparation.exited == []
        os.write(resume, b'x')
        with pytest.raises(PreparationError, match='worker failed'):
            process.result()
        reaped = True
        receipt = {'worker': 'crashed-writer'}
        assert json.loads((process.directory / 'exit.json').read_text()) == receipt
        assert workers.reconcile() == 1
        assert preparation.exited == ['crashed-writer']
        assert {path.name for path in process.directory.iterdir()} == {'claim.json', 'exit.json', 'lock'}
        assert json.loads((process.directory / 'claim.json').read_text()) == receipt
        assert json.loads((process.directory / 'exit.json').read_text()) == receipt
        for path in workers.receipts.rglob('*'):
            assert path.name not in ('result.json', 'result.pending')
            if path.is_file():
                assert sentinel.encode() not in path.read_bytes()
        assert workers.reconcile() == 1  # Minimal exit evidence remains safe to replay.
    finally:
        os.write(resume, b'x')
        if process is not None and not reaped:
            os.waitpid(process.pid, 0)
        for fd in (arrived, notify, release, resume):
            os.close(fd)


@pytest.mark.parametrize('proof', [None, {'worker': 'foreign-worker'}])
def test_sensitive_result_cleanup_requires_matching_exit_proof(tmp_path, proof):
    from docassemble_executor.preparation_containers import ContainerWorkers
    preparation = FixturePreparation()
    workers = ProcessWorkers(preparation, tmp_path / 'receipts')
    directory = workers.receipts / 'worker'
    directory.mkdir(mode=0o700)
    ContainerWorkers._write(directory / 'claim.json', {'worker': 'worker'})
    sentinel = 'UNPROVEN_EXIT_SENTINEL_CREDENTIAL'
    for name in ('result.json', 'result.pending'):
        (directory / name).write_text(sentinel)
    if proof is None:
        assert workers.reconcile() == 0
    else:
        ContainerWorkers._write(directory / 'exit.json', proof)
        with pytest.raises(PreparationError, match='untrusted direct preparation exit receipt'):
            workers.reconcile()
    assert preparation.exited == []
    assert (directory / 'result.json').read_text() == sentinel
    assert (directory / 'result.pending').read_text() == sentinel


def test_guardian_crash_or_missing_receipt_never_proves_exit(tmp_path, monkeypatch):
    from docassemble_executor import preparation_processes
    preparation = FixturePreparation()
    workers = ProcessWorkers(preparation, tmp_path / 'receipts')
    monkeypatch.setattr(preparation_processes, '_subreaper', lambda: os.kill(os.getpid(), signal.SIGKILL))
    with workers.build_claim(SimpleNamespace(worker_id='crashed'), lambda: {}) as process:
        with pytest.raises(PreparationError, match='exit authority unavailable'):
            process.result()
    assert workers.reconcile() == 0
    assert preparation.exited == []


def test_unsupported_platform_refuses_to_launch_or_report_exit(tmp_path, monkeypatch):
    from docassemble_executor import preparation_processes
    preparation = FixturePreparation()
    workers = ProcessWorkers(preparation, tmp_path / 'receipts')
    monkeypatch.setattr(preparation_processes.sys, 'platform', 'unsupported')
    with pytest.raises(PreparationError, match='requires Linux'):
        with workers.build_claim(SimpleNamespace(worker_id='worker'), lambda: pytest.fail('launched')):
            pass
    assert workers.reconcile() == 0
    assert preparation.exited == []


def test_guardian_crash_with_live_builder_never_authorizes_cleanup(tmp_path):
    import ctypes
    from docassemble_executor.preparation_processes import _subreaper
    preparation = FixturePreparation()
    workers = ProcessWorkers(preparation, tmp_path / 'receipts')
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    assert libc.prctl(37, ctypes.byref(previous), 0, 0, 0) == 0
    _subreaper()  # Test-only adoption lets us reap the builder orphan ourselves.
    arrived, notify = os.pipe()
    release, resume = os.pipe()
    builder_read, builder_write = os.pipe()
    builder = None

    def build():
        os.write(builder_write, str(os.getpid()).encode())
        os.write(notify, b'x')
        os.read(release, 1)
        return {'built': True}

    try:
        with workers.build_claim(SimpleNamespace(worker_id='crashed'), build) as process:
            receive(arrived)
            builder = int(os.read(builder_read, 32))
            os.kill(process.pid, signal.SIGKILL)
            with pytest.raises(PreparationError, match='exit authority unavailable'):
                process.result()
        assert workers.reconcile() == 0
        assert preparation.exited == []
        os.write(resume, b'x')
        _, status = os.waitpid(builder, 0)
        builder = None
        assert status == 0
        assert (process.directory / 'result.json').exists()
        assert not (process.directory / 'exit.json').exists()
        assert workers.reconcile() == 0  # Even a successful orphan builder is not exit proof.
        assert preparation.exited == []
    finally:
        os.write(resume, b'x')
        if builder is not None:
            os.waitpid(builder, 0)
        assert libc.prctl(36, previous.value, 0, 0, 0) == 0
        for fd in (arrived, notify, release, resume, builder_read, builder_write):
            os.close(fd)


def test_non_direct_reconciliation_does_not_create_guardian_receipts(tmp_path):
    from docassemble_executor.preparation_poller import PreparationPoller
    preparation = FixturePreparation()
    preparation.reconcile_artifacts = lambda root: 0
    poller = PreparationPoller(preparation, object(), artifact_root=tmp_path)
    poller._reconcile()
    assert poller.processes is None
    assert not list(tmp_path.iterdir())
