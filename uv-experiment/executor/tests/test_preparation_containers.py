"""Exit authority must fail closed; real daemon proof lives in preparation/."""
import fcntl
import json
from unittest.mock import Mock

import pytest

from docassemble_executor.preparation import PreparationError
from docassemble_executor.preparation_containers import ContainerWorkers


def workers(tmp_path):
    preparation = Mock()
    authority = ContainerWorkers(preparation, tmp_path / 'receipts', tmp_path,
                                 'trusted-image', tmp_path / 'store')
    directory = authority.receipts / 'worker'
    directory.mkdir()
    authority._write(directory / 'receipt.json', {
        'worker': 'worker', 'candidate': 'candidate', 'container': 'c' * 64, 'daemon': 'daemon'})
    return authority, directory


def test_reconciliation_missing_receipt_or_daemon_uncertainty_never_proves_exit(tmp_path):
    authority, directory = workers(tmp_path)
    authority._docker = Mock(side_effect=PreparationError('authority unavailable'))
    with pytest.raises(PreparationError):
        authority.reconcile()
    authority.preparation.worker_exited.assert_not_called()
    (directory / 'receipt.json').unlink()
    assert authority.reconcile() == 0
    authority.preparation.worker_exited.assert_not_called()


def test_reconciliation_cannot_race_live_creator_lock(tmp_path):
    authority, directory = workers(tmp_path)
    authority._docker = Mock()
    with (directory / 'lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        assert authority.reconcile() == 0
    authority._docker.assert_not_called()
    authority.preparation.worker_exited.assert_not_called()


@pytest.mark.parametrize('pid_mode,privileged,status', [
    ('host', False, 'exited'), ('', True, 'exited'), ('', False, 'running'),
    ('', False, 'dead'), ('', False, 'restarting'),
])
def test_reconciliation_rejects_untrusted_or_unproven_namespace(tmp_path, pid_mode, privileged, status):
    authority, directory = workers(tmp_path)
    info = {'Config': {'Labels': {'da.preparation.worker': 'worker'}, 'User': '1000:1000'},
            'HostConfig': {'PidMode': pid_mode, 'Privileged': privileged,
                           'RestartPolicy': {'Name': 'no'}, 'CapDrop': ['ALL']},
            'Mounts': [], 'State': {'Status': status}}
    authority._docker = Mock(side_effect=['daemon', 'c' * 64, json.dumps([info])])
    if pid_mode or privileged:
        with pytest.raises(PreparationError, match='untrusted'):
            authority.reconcile()
    else:
        assert authority.reconcile() == 0
    authority.preparation.worker_exited.assert_not_called()
    assert authority._docker.call_count == 3


def test_reconciliation_replays_removed_container_after_db_ack_loss(tmp_path):
    authority, _ = workers(tmp_path)
    authority._docker = Mock(side_effect=['daemon', '', 'daemon', ''])  # Authoritative absence.
    assert authority.reconcile() == 1
    assert authority.reconcile() == 1
    assert authority.preparation.worker_exited.call_count == 2


def test_reconciliation_daemon_switch_is_not_exit_proof(tmp_path):
    authority, _ = workers(tmp_path)
    authority._docker = Mock(return_value='different-daemon')
    with pytest.raises(PreparationError, match='identity changed'):
        authority.reconcile()
    authority.preparation.worker_exited.assert_not_called()


@pytest.mark.parametrize('config', [
    {'index_url': 'https://user:DISPOSABLE_SECRET_TOKEN@private.test/simple'},
    {'index_url': 'https://DISPOSABLE_SECRET_TOKEN@private.test/simple'},
    {'index_url': 'https://pypi.org/simple?token=DISPOSABLE_SECRET_TOKEN'},
])
def test_container_policy_rejects_credentials_before_staging(tmp_path, config):
    from docassemble_executor.preparation import Claim
    authority, _ = workers(tmp_path)
    authority._docker = Mock()
    inputs = {'baseline': {'target': {'python': '3.12'}, 'tool': {'uv': '0.12.21'},
                          'config': config}, 'contributions': [],
              'requirement': {'declaration': 'six==1.17.0'}}
    with pytest.raises(PreparationError) as error:
        authority.build_claim(Claim('candidate', 'new-worker', 1, inputs))
    assert 'DISPOSABLE_SECRET_TOKEN' not in str(error.value)
    assert not (authority.receipts / 'new-worker').exists()
    authority._docker.assert_not_called()


def test_artifact_transport_copies_only_verified_objects_and_removes_temporary_bytes(tmp_path):
    from test_registry_builder import zip_inputs
    inputs, root = zip_inputs(tmp_path)
    authority, directory = workers(tmp_path)
    authority.artifact_root = root
    observed = []
    def docker(*arguments):
        assert arguments[0] == 'cp'
        from pathlib import Path
        source = Path(arguments[1])
        assert source.parent.parent == directory
        assert source.parent.stat().st_mode & 0o777 == 0o700
        reference = inputs['artifact']['digest']
        assert (source / reference).read_bytes() == (root / reference).read_bytes()
        assert arguments[2] == 'container:/tmp/incoming-artifacts'
        observed.append(source)
    authority._docker = docker
    authority._copy_artifacts('container', inputs, directory)
    assert len(observed) == 1 and not observed[0].exists()
    assert (root / inputs['artifact']['digest']).exists()
    (root / inputs['artifact']['digest']).write_bytes(b'corrupt')
    with pytest.raises(PreparationError, match='digest mismatch'):
        authority._copy_artifacts('container', inputs, directory)
    assert len(observed) == 1 and not list(directory.glob('.artifact-copy-*'))


def test_artifact_transport_crash_cleanup_requires_worker_exit_and_keeps_retained_store(tmp_path):
    authority, directory = workers(tmp_path)
    transport = directory / '.artifact-copy-crash'
    transport.mkdir()
    (transport / 'uploaded-distribution').write_bytes(b'candidate staging')
    authority._docker = Mock(side_effect=['daemon', ''])
    with (directory / 'lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        assert authority.reconcile() == 0
        assert transport.exists()
    assert authority.reconcile() == 1
    authority.preparation.worker_exited.assert_called_once_with('worker')
    assert not transport.exists()


def test_worker_image_git_qualification_requires_git_and_cold_egress(monkeypatch, capsys):
    from docassemble_executor import preparation_containers as module
    monkeypatch.setattr(module.subprocess, 'check_output', lambda argv, **kwargs: (
        'git version 2.53.0\n' if argv[:2] == ['git', '--version'] else 'sha\trefs/tags/3.12.0\n'))
    module._qualify_git()
    receipt = json.loads(capsys.readouterr().out.strip())
    assert receipt == {'git': 'git version 2.53.0', 'cold_ls_remote': True,
                       'repository': 'https://github.com/pypa/flit.git'}
    monkeypatch.setattr(module.subprocess, 'check_output', lambda argv, **kwargs: 'git version 2.53.0\n')
    with pytest.raises(SystemExit):
        module._qualify_git()
    monkeypatch.setattr(module.subprocess, 'check_output',
                        lambda argv, **kwargs: (_ for _ in ()).throw(OSError('git unavailable')))
    with pytest.raises(SystemExit):
        module._qualify_git()


def test_preparation_child_requires_inputs_unless_qualifying(monkeypatch):
    from docassemble_executor import preparation_containers as module
    monkeypatch.setattr('sys.argv', ['preparation_containers'])
    with pytest.raises(SystemExit):
        module.main()
