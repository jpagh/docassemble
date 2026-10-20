"""Bounded published boot routing, durable detection and status projection."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from docassemble.base.managed_preparation import runtime_status
from docassemble_executor.boot_observation import published_boot_required, reconcile_published_boot, installation_host_id
from docassemble_executor.config import DatabaseSettings
from docassemble_executor.journal import Journal, Operation
from docassemble_executor.paths import ExecutorPaths
from docassemble_executor.startup import StartupError, managed_requested
from docassemble_executor.state import AppliedState


def paths_at(tmp_path):
    return replace(ExecutorPaths.from_env(), state_dir=tmp_path, status_file=tmp_path / 'status.json')


@pytest.mark.parametrize('kind', ['normal', 'core-only', 'unknown', 'development-override', 'invalid'])
def test_shared_status_separates_normal_history_recovery_override_unknown(kind):
    value = runtime_status({'desired_revision_id': 'desired', 'runtime_generation': 7, 'snapshot': {
        'runtime': {'kind': kind, 'revision_id': 'running', 'core_baseline': 'core', 'materialized_revision_id': 'bytes'},
        'last_normally_verified': {'revision_id': 'historical'}}})
    assert value['desired_revision_id'] == 'desired'
    assert value['runtime_generation'] == 7
    assert value['runtime_kind'] == ('unknown' if kind == 'invalid' else kind)
    assert value['verified_running_revision_id'] == ('running' if kind == 'normal' else None)
    assert value['recovery_core'] == ('core' if kind == 'core-only' else None)
    assert value['last_normally_verified_revision_id'] == 'historical'
    assert value['materialized_revision_id'] == ('bytes' if kind in ('unknown', 'invalid') else None)


def test_boot_detection_survives_descriptor_removal_and_feature_disable(tmp_path, monkeypatch):
    paths = paths_at(tmp_path)
    monkeypatch.delenv('DA_QUALIFIED_IMAGE_BASELINE', raising=False)
    assert not published_boot_required(paths, Journal())
    descriptor = tmp_path / 'baseline.json'
    descriptor.write_text('{}')
    monkeypatch.setenv('DA_QUALIFIED_IMAGE_BASELINE', str(descriptor))
    assert published_boot_required(paths, Journal())
    marker = tmp_path / 'published-boot.json'
    marker.write_text('{}')
    descriptor.unlink()
    assert published_boot_required(paths, Journal())
    with pytest.raises(StartupError, match='legacy boot refused'):
        managed_requested(False, tmp_path)
    marker.unlink()
    for kind in ('published-apply', 'published-materialization'):
        assert published_boot_required(paths, Journal(operation=Operation('a', kind, 'r')))


def test_configured_bridge_missing_descriptor_after_initialization_requires_db(tmp_path, monkeypatch):
    paths = paths_at(tmp_path)
    monkeypatch.setenv('DA_QUALIFIED_IMAGE_BASELINE', str(tmp_path / 'absent.json'))
    assert not published_boot_required(paths, Journal())  # Explicit fresh fixture boot only.
    (tmp_path / 'initialized.json').write_text('{}')
    assert published_boot_required(paths, Journal())
    (tmp_path / 'initialized.json').unlink()
    monkeypatch.delenv('DA_QUALIFIED_IMAGE_BASELINE')
    installation_host_id(paths)
    assert published_boot_required(paths, Journal())
    with pytest.raises(StartupError, match='legacy boot refused'):
        managed_requested(False, tmp_path)


def test_db_unavailability_stays_closed_redacts_error_and_preserves_detection(tmp_path, monkeypatch):
    paths = paths_at(tmp_path)
    context = SimpleNamespace(paths=paths, db=DatabaseSettings(), config={}, journal=Journal(), applied=AppliedState())
    calls = []
    monkeypatch.setattr('docassemble_executor.boot_observation.create_engine',
        lambda *a, **kw: SimpleNamespace(dispose=lambda: calls.append('disposed')))
    def unavailable(*args):
        assert (tmp_path / 'published-boot.json').exists()
        raise RuntimeError('postgresql://user:SECRET@example.invalid')
    monkeypatch.setattr('docassemble_executor.boot_observation.BootObservation.reconcile', unavailable)
    # Preparation enforces the dialect before reconcile.
    monkeypatch.setattr('docassemble_executor.boot_observation.BootObservation.__init__', lambda *a: None)
    with pytest.raises(StartupError) as error:
        reconcile_published_boot(context)
    assert str(error.value) == 'published DB boot reconciliation unavailable; maintenance required'
    assert 'SECRET' not in str(error.value)
    assert calls == ['disposed']
    assert published_boot_required(paths, Journal())
    assert not paths.status_file.exists()


def test_installation_host_identity_is_stable_and_invalid_identity_rejects(tmp_path):
    paths = paths_at(tmp_path)
    assert installation_host_id(paths) == installation_host_id(paths)
    (tmp_path / 'installation-host.json').write_text('{"id": "bad"}')
    with pytest.raises(ValueError):
        installation_host_id(paths)
