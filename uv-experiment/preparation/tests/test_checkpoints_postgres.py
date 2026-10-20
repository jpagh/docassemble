"""Snapshot-backed local pre-change checkpoints; PostgreSQL, not a mock dump."""
# ruff: noqa: F811
import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from test_preparation_postgres import protocol, result  # noqa: F401
from test_publication_postgres import publisher  # noqa: F401
from test_managed_backup_postgres import objects, install_snapshot  # noqa: F401
from docassemble.base.managed_preparation import PreparationError
from docassemble_executor.checkpoints import capture, verify_checkpoint
from docassemble_executor.cold_restore import inspect_backup, ColdRestoreError
from docassemble_executor.managed_backup import ObjectCopy


def setup_checkpoint(protocol, objects, monkeypatch):
    install_snapshot(protocol, objects)
    kwargs, _, _ = objects
    paths = kwargs['paths']
    (paths.da_root / 'files').mkdir(parents=True)
    (paths.da_root / 'files/interview').write_text('pre-change progress')
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(json.dumps({'db': {}, 'uploads': str(paths.da_root / 'files')}))
    # Desired is already advanced, but checkpoint evidence must select APPLIED.
    with protocol.engine.begin() as connection:
        env = protocol._environment(connection)
        applied = dict(env['snapshot'], selected_baseline={'name': 'old-applied'})
        connection.execute(protocol.tables['revision'].insert().values(id='advanced-desired', snapshot=applied))
        connection.execute(update(protocol.tables['environment_state']).values(desired_revision_id='advanced-desired'))
    pre_change = {'runtime': {'kind': 'normal', 'revision_id': 'retained', 'inventory': {}},
                  'target': 'old-target', 'image_baseline': {'image': 'old-image'}}
    claim = SimpleNamespace(attempt_id=str(uuid.uuid4()), pre_change=pre_change,
                            identity={'attempt_id': '', 'revision_id': 'advanced-desired'})
    claim.identity['attempt_id'] = claim.attempt_id
    monkeypatch.setattr('docassemble_executor.checkpoints.read_database_revision', lambda _: 'pre-schema')
    return kwargs, claim


def test_option_defaults_off_is_digest_bound_and_prepare_authorized(protocol):
    plain = protocol.prepare('alice', 'Example>=2', 'plain')
    requested = protocol.prepare('alice', 'Example>=2', 'checkpoint', pre_change_checkpoint=True)
    assert protocol.status('alice', plain)['checkpoint']['requested'] is False
    assert protocol.status('alice', requested)['checkpoint']['adds_maintenance_time'] is True
    with pytest.raises(PreparationError, match='body mismatch'):
        protocol.prepare('alice', 'Example>=2', 'plain', pre_change_checkpoint=True)
    with pytest.raises(PreparationError, match='forbidden'):
        protocol.prepare('intruder', 'Example>=2', 'unauthorized', pre_change_checkpoint=True)
    claim = protocol.claim(requested)
    prepared = result(claim)
    protocol.publish_ready(claim, prepared)
    with protocol.engine.connect() as connection:
        row = connection.execute(select(protocol.tables['candidate']).where(
            protocol.tables['candidate'].c.id == requested)).mappings().one()
    from docassemble.base.managed_preparation import digest
    assert row['result_digest'] == digest({'inputs': row['inputs'], 'result': row['result']})
    assert row['inputs']['pre_change_checkpoint'] is True


def test_same_day_unique_retention_advanced_desired_and_prechange_metadata(protocol, objects, monkeypatch):
    kwargs, claim = setup_checkpoint(protocol, objects, monkeypatch)
    # capture kwargs includes paths/prefix, while capture obtains prefix from service.
    kwargs = {k: v for k, v in kwargs.items() if k != 'prefix'}
    first = capture(protocol, claim=claim, inputs={'DABACKUPDAYS': '14'}, **kwargs)
    second = capture(protocol, claim=claim, inputs={'DABACKUPDAYS': '14'}, **kwargs)
    assert first['id'] != second['id']
    directories = sorted((kwargs['paths'].da_root / 'backup/checkpoints').iterdir())
    assert len(directories) == 2
    for directory in directories:
        document = verify_checkpoint(directory, 'test_')
        assert document['id'] in (first['id'], second['id'])
        assert document['pre_change'] == claim.pre_change
        assert document['database_revision'] == 'pre-schema'
        assert document['restore']['desired_revision_id'] == 'retained'
        assert document['restore']['replay_pending_work'] is False
        assert document['restore']['suppress_attempt_ids'] == [claim.attempt_id]
        assert document['retention'] == 'operator-pruned-no-auto-deletion'
        assert document['known_good'] is False  # no verified installed material
        with pytest.raises(ColdRestoreError, match='separate full checkpoint'):
            inspect_backup(directory / 'database', directory / 'database.managed', 'test_')
    assert first['duration_seconds'] >= 0 and first['complete']


@pytest.mark.parametrize('kind', ['core-only', 'unknown', 'development-override'])
def test_recovery_and_broken_runtime_never_known_good(protocol, objects, monkeypatch, kind):
    kwargs, claim = setup_checkpoint(protocol, objects, monkeypatch)
    claim.pre_change['runtime'] = {'kind': kind, 'inventory': {'core': '1.0'}}
    kwargs.pop('prefix')
    status = capture(protocol, claim=claim, inputs={}, **kwargs)
    assert status['complete'] and not status['known_good']
    directory = next((kwargs['paths'].da_root / 'backup/checkpoints').iterdir())
    assert verify_checkpoint(directory, 'test_')['runtime_label'] == 'recovery-or-unverified'


@pytest.mark.parametrize('failure', ['interrupted-copy', 'changed-file', 'missing-object'])
def test_failed_or_interrupted_capture_has_no_complete_checkpoint(protocol, objects, monkeypatch, failure):
    kwargs, claim = setup_checkpoint(protocol, objects, monkeypatch)
    kwargs.pop('prefix')
    original = ObjectCopy.file
    def copy(self, source, relative):
        original(self, source, relative)
        if relative == 'files/interview':
            if failure == 'interrupted-copy':
                raise InterruptedError('copy interrupted')
            if failure == 'changed-file':
                source.write_text('concurrent operator edit')
    monkeypatch.setattr(ObjectCopy, 'file', copy)
    if failure == 'missing-object':
        (kwargs['secret_root'] / 'credential').unlink()
    with pytest.raises((RuntimeError, OSError)):
        capture(protocol, claim=claim, inputs={}, **kwargs)
    root = kwargs['paths'].da_root / 'backup/checkpoints'
    assert not list(root.rglob('checkpoint.json'))
    status = json.loads((kwargs['paths'].state_dir / 'checkpoints' / (claim.attempt_id + '.json')).read_text())
    assert status['state'] == 'failed-incomplete' and not status['complete']


def test_upgrade_checkpoint_selection_is_per_operation_and_ready_digest_bound(protocol):
    from test_core_upgrade_postgres import upgrade_result
    candidate = protocol.prepare('admin', None, 'upgrade-checkpoint', operation='upgrade',
        release_selection={'mode': 'exact', 'version': '2.0.0'}, pre_change_checkpoint=True)
    claim = protocol.claim(candidate)
    assert claim.inputs['pre_change_checkpoint'] is True
    assert protocol.publish_ready(claim, upgrade_result(claim))
    assert protocol.status('admin', candidate)['checkpoint']['adds_maintenance_time'] is True
    with pytest.raises(PreparationError, match='body mismatch'):
        protocol.prepare('admin', None, 'upgrade-checkpoint', operation='upgrade',
            release_selection={'mode': 'exact', 'version': '2.0.0'})


def test_claim_records_applied_image_and_target_not_advanced_desired(publisher):
    from docassemble_executor.installation import Installation
    service = publisher
    with service.engine.begin() as connection:
        env = service._environment(connection)
        old = dict(env['snapshot'], target='applied-target', image_baseline={'id': 'applied-image'})
        connection.execute(service.tables['revision'].insert().values(id='applied', snapshot=old))
        current = dict(env['snapshot'], runtime=dict(env['snapshot']['runtime'], revision_id='applied'))
        connection.execute(update(service.tables['environment_state']).values(snapshot=current))
    candidate = service.prepare('alice', 'Example>=2', 'advanced', pre_change_checkpoint=True)
    claim = service.claim(candidate)
    assert service.publish_ready(claim, result(claim))
    status = service.status('alice', candidate)
    publication = service.approve('alice', candidate, status['result_digest'], 'approve')
    installation = Installation(service.engine, service.authorize, prefix='test_')
    claimed = installation.claim_installation('host')
    assert claimed.pre_change['runtime']['revision_id'] == 'applied'
    assert claimed.pre_change['target'] == 'applied-target'
    assert claimed.pre_change['image_baseline'] == {'id': 'applied-image'}
    assert claimed.revision_id == publication['revision_id'] != 'applied'
    installation.record_checkpoint(claimed, {'state': 'capturing', 'complete': False, 'known_good': False,
                                            'created_at': '2020-01-01T00:00:00+00:00'})
    checkpoint = service.publication_status('alice', attempt_id=claimed.attempt_id)['checkpoint']
    assert checkpoint['state'] == 'capturing' and checkpoint['age_seconds'] > 0


@pytest.mark.parametrize('setting', ['uploads', 'literal-overlay', 'numeric-days'])
def test_unsupported_upload_or_effective_disabled_backup_refuses_before_any_copy(protocol, objects, monkeypatch, setting):
    kwargs, claim = setup_checkpoint(protocol, objects, monkeypatch)
    kwargs.pop('prefix')
    paths = kwargs['paths']
    config = {'db': {}, 'uploads': str(paths.da_root / 'files')}
    if setting == 'uploads':
        config['uploads'] = str(paths.da_root / 'custom-uploads')
    elif setting == 'literal-overlay':
        config['config from'] = {'backup file storage': False}
    else:
        config['backup days'] = '00'
    paths.config_file.write_text(json.dumps(config))
    monkeypatch.setattr(ObjectCopy, 'file', lambda *_: pytest.fail('unsupported policy must refuse before any copy'))
    with pytest.raises(RuntimeError, match='upload root|disabled'):
        capture(protocol, claim=claim, inputs={}, **kwargs)
    assert not (paths.da_root / 'backup/checkpoints').exists()
    assert (paths.da_root / 'files/interview').read_text() == 'pre-change progress'
