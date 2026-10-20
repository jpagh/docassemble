"""Outer installation contention is no work; later failures remain fail-closed."""
import errno
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docassemble_executor import durable_work
from docassemble_executor.admission import Admission
from docassemble_executor.authority import MutationLock, MutationLockBusy, RuntimeLock
from docassemble_executor.installation import InstallationBusy, InstallationPoller
from docassemble_executor.paths import ExecutorPaths
from docassemble_executor.status import read_status_file, write_status


@pytest.fixture
def paths(tmp_path, monkeypatch):
    for name, path in {'DA_ROOT': tmp_path, 'DA_MANAGED_STATE_DIR': tmp_path / 'state',
                       'DA_RUN_DIR': tmp_path / 'run', 'UV_CACHE_DIR': tmp_path / 'cache',
                       'DA_LOG_DIR': tmp_path / 'log'}.items():
        monkeypatch.setenv(name, str(path))
    paths = ExecutorPaths.from_env()
    paths.ensure_directories()
    return paths


def test_installation_poller_outer_mutation_flock_busy_does_no_work(paths, tmp_path, monkeypatch):
    poller = InstallationPoller(SimpleNamespace(), paths, tmp_path, tmp_path)
    locked_turn = Mock()
    monkeypatch.setattr(poller, '_run_once_locked', locked_turn)
    with MutationLock(paths.state_dir):
        with pytest.raises(InstallationBusy) as caught:
            poller.run_once(retain_live_health=True)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True
    busy = caught.value.__context__
    assert isinstance(busy, MutationLockBusy)
    assert busy.errno in (errno.EAGAIN, errno.EWOULDBLOCK)
    assert isinstance(busy.__cause__, BlockingIOError)
    assert busy.args == busy.__cause__.args
    locked_turn.assert_not_called()
    with MutationLock(paths.state_dir):
        pass  # The failed acquisition left no lock behind.


@pytest.mark.parametrize('error', [
    pytest.param(PermissionError(errno.EACCES, 'lock open denied'), id='eacces'),
    pytest.param(BlockingIOError(errno.EIO, 'lock open unavailable'), id='eio'),
    pytest.param(BlockingIOError(errno.EAGAIN, 'lock open temporarily unavailable'), id='eagain'),
    pytest.param(BlockingIOError(errno.EWOULDBLOCK, 'lock open would block'), id='ewouldblock'),
])
def test_installation_poller_outer_lock_open_error_propagates(paths, tmp_path, monkeypatch, error):
    poller = InstallationPoller(SimpleNamespace(), paths, tmp_path, tmp_path)
    locked_turn = Mock()
    flock = Mock()
    monkeypatch.setattr(poller, '_run_once_locked', locked_turn)
    monkeypatch.setattr('docassemble_executor.authority.fcntl.flock', flock)
    original_open = Path.open

    def open_lock(path, *args, **kwargs):
        if path == paths.state_dir / 'mutation.lock':
            raise error
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', open_lock)
    with pytest.raises(type(error)) as caught:
        poller.run_once()
    assert caught.value is error
    flock.assert_not_called()
    locked_turn.assert_not_called()


def test_installation_poller_inner_runtime_flock_busy_is_not_translated(paths, tmp_path, monkeypatch):
    poller = InstallationPoller(SimpleNamespace(), paths, tmp_path, tmp_path)

    def locked_turn(*, retain_live_health):
        assert retain_live_health is True
        with RuntimeLock(paths.state_dir):
            return 'idle'

    monkeypatch.setattr(poller, '_run_once_locked', locked_turn)
    with RuntimeLock(paths.state_dir, shared=True):
        with pytest.raises(BlockingIOError):
            poller.run_once(retain_live_health=True)
        with MutationLock(paths.state_dir):
            pass  # The outer lock was released despite the inner failure.
    assert poller.run_once(retain_live_health=True) == 'idle'
    with MutationLock(paths.state_dir):
        pass


@pytest.fixture
def installation_worker(paths, tmp_path, monkeypatch):
    monkeypatch.setenv('DA_UV_MANAGED', 'true')
    monkeypatch.setenv('DA_DURABLE_WORK_STORE', str(tmp_path / 'store'))
    monkeypatch.setenv('DA_QUALIFIED_IMAGE_BASELINE', str(tmp_path / 'baseline.json'))
    engine, installation, poller = Mock(), Mock(), Mock()
    monkeypatch.setattr(durable_work, 'configuration', lambda _: ('unused', ''))
    monkeypatch.setattr(durable_work, 'create_engine', lambda *a, **k: engine)
    monkeypatch.setattr(durable_work, 'Installation', lambda *a: installation)
    monkeypatch.setattr(durable_work, 'InstallationPoller', lambda *a: poller)
    monkeypatch.setattr('docassemble_executor.cold_restore.require_incarnation', lambda *a: None)
    monkeypatch.setattr('docassemble_executor.controller.load_context', lambda *a, **k: None)
    monkeypatch.setattr('docassemble_executor.legacy_adoption.observe_adoption', lambda _: None)
    monkeypatch.setattr('docassemble_executor.image_migration.observe_migration', lambda _: None)
    publish = Mock(side_effect=RuntimeError('publication unavailable'))
    monkeypatch.setattr(durable_work, 'publish_live_status', publish)
    sleep = Mock(side_effect=SystemExit)
    monkeypatch.setattr(durable_work.time, 'sleep', sleep)
    return SimpleNamespace(paths=paths, engine=engine, installation=installation,
                           poller=poller, publish=publish, sleep=sleep)


@pytest.mark.parametrize('initial_mode', ['normal', 'recovery', 'closed'])
def test_run_durable_work_installation_busy_preserves_admission_status_and_ready(installation_worker, monkeypatch, capsys, initial_mode):
    worker = installation_worker
    admission = Admission(worker.paths.state_dir)
    if initial_mode == 'closed':
        admission.close('operator fence')
    else:
        admission.open(consumers=initial_mode == 'normal')
    write_status(worker.paths, {'mode': initial_mode, 'normal_ready': initial_mode == 'normal',
                               'recovery_available': initial_mode == 'recovery'})
    ready = worker.paths.run_dir / 'ready'
    ready.write_bytes(b'ready marker')
    admission_before = admission.path.read_bytes()
    status_before = worker.paths.status_file.read_bytes()
    write = Mock(wraps=write_status)
    remove_ready = Mock()
    monkeypatch.setattr(durable_work, 'write_status', write)
    monkeypatch.setattr(durable_work, '_remove_ready', remove_ready)
    worker.poller.run_once.side_effect = InstallationBusy()

    with pytest.raises(SystemExit):
        durable_work.run_durable_work(worker.paths, 'installation')

    assert admission.path.read_bytes() == admission_before
    assert admission.state()['mode'] == ('closed' if initial_mode == 'closed' else 'open')
    assert worker.paths.status_file.read_bytes() == status_before
    assert ready.read_bytes() == b'ready marker'
    write.assert_not_called()
    remove_ready.assert_not_called()
    worker.publish.assert_not_called()
    worker.poller.run_once.assert_called_once_with(retain_live_health=True)
    worker.sleep.assert_called_once_with(2)
    worker.engine.dispose.assert_called_once_with()
    assert capsys.readouterr().out == 'mutation-busy\n'


@pytest.mark.parametrize('error', [RuntimeError('observation unavailable'),
                                  BlockingIOError(errno.EAGAIN, 'inner runtime busy')])
def test_run_durable_work_other_installation_exception_still_fences(installation_worker, capsys, error):
    worker = installation_worker
    admission = Admission(worker.paths.state_dir)
    admission.open()
    write_status(worker.paths, {'mode': 'recovery', 'normal_ready': False,
                               'recovery_available': True, 'publication': {'stale': True}})
    ready = worker.paths.run_dir / 'ready'
    ready.touch()
    worker.poller.run_once.side_effect = error

    with pytest.raises(SystemExit):
        durable_work.run_durable_work(worker.paths, 'installation')

    detail = 'durable installation observation unavailable or fenced'
    assert admission.state() == {'mode': 'closed', 'detail': detail}
    assert not ready.exists()
    assert read_status_file(worker.paths.status_file) == {
        'mode': 'maintenance', 'normal_ready': False, 'recovery_available': False,
        'publication': None, 'verified_running_revision_id': None,
        'runtime_kind': 'unknown', 'runtime_state': 'unknown',
        'locked_converged': False, 'detail': detail}
    worker.publish.assert_called_once_with(worker.installation, worker.paths)
    worker.poller.run_once.assert_called_once_with(retain_live_health=True)
    worker.sleep.assert_called_once_with(2)
    worker.engine.dispose.assert_called_once_with()
    assert capsys.readouterr().out == 'durable work unavailable or fenced\n'
