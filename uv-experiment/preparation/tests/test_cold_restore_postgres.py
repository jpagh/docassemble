"""Restored DB authority, complete reference classification and stale work fences."""
# ruff: noqa: F811
import json
import uuid

import pytest
from sqlalchemy import select, update

from test_preparation_postgres import protocol, snapshot  # noqa: F401
from test_publication_postgres import publisher, ready, approve  # noqa: F401
from test_managed_backup_postgres import objects, install_snapshot  # noqa: F401
from docassemble_executor.cold_restore import (ColdRestoreError, establish_incarnation, inspect_backup,
                                              require_incarnation)
from docassemble_executor.cold_restore_boot import prepare_cold_boot
from docassemble_executor.installation import Installation, InstallationPoller
from docassemble_executor.journal import Journal, Operation
from docassemble_executor.managed_backup import create_backup
from docassemble_executor.preparation import PreparationError


def authority():
    return {'incarnation': str(uuid.uuid4()), 'dump_sha256': 'a' * 64}


def report():
    return {'incomplete_backup': False, 'unrestorable_revision_ids': [], 'warnings': [], 'unavailable_objects': []}


@pytest.mark.parametrize('damage', ['current', 'recovery', 'historical', 'auth', 'artifact', 'journal', 'index'])
def test_restore_checks_every_snapshot_reference_and_distinguishes_history(protocol, objects, tmp_path, damage):
    install_snapshot(protocol, objects)
    kwargs, reference, artifact = objects
    if damage == 'historical':
        with protocol.engine.begin() as connection:
            baseline = connection.scalar(select(protocol.tables['revision'].c.snapshot).where(protocol.tables['revision'].c.id == 'baseline'))
            connection.execute(update(protocol.tables['environment_state']).values(desired_revision_id='baseline', snapshot=baseline))
    dump = tmp_path / 'database'
    document = create_backup(protocol.engine, dump, **kwargs)
    directory = tmp_path / 'database.managed'
    if damage in ('current', 'historical'):
        (directory / 'objects/retained' / reference / 'uv.lock').unlink()
    else:
        lookup = {'recovery': 'image/core-r1/uv.lock', 'journal': 'state/lifecycle.json', 'index': 'index/private'}
        if damage in lookup:
            name = lookup[damage]
        else:
            source = str(kwargs['secret_root'] / 'credential') if damage == 'auth' else str(kwargs['artifact_roots'][0] / artifact)
            name = next(name for name, entry in document['files'].items() if entry['source'] == source)
        (directory / 'objects' / name).unlink()
    if damage in ('historical',):
        _, status = inspect_backup(dump, directory, 'test_')
        assert status['incomplete_backup'] and status['unrestorable_revision_ids'] == ['retained']
        assert 'INCOMPLETE BACKUP' in status['warnings'][0]
    elif damage in ('auth', 'artifact', 'index', 'current', 'recovery', 'journal'):
        with pytest.raises(ColdRestoreError, match='service blocked'):
            inspect_backup(dump, directory, 'test_')


def test_preparation_input_ancestry_is_history_not_current_runtime_material(protocol, objects, tmp_path):
    install_snapshot(protocol, objects)
    kwargs, reference, _ = objects
    with protocol.engine.begin() as connection:
        historical = connection.scalar(select(protocol.tables['revision'].c.snapshot).where(protocol.tables['revision'].c.id == 'retained'))
        baseline = connection.scalar(select(protocol.tables['revision'].c.snapshot).where(protocol.tables['revision'].c.id == 'baseline'))
        current = dict(baseline, publication={'inputs': {'baseline': historical}, 'result': {}})
        connection.execute(protocol.tables['revision'].insert().values(id='current', parent_id='retained', snapshot=current))
        connection.execute(update(protocol.tables['environment_state']).values(desired_revision_id='current', snapshot=current))
    dump = tmp_path / 'database'
    create_backup(protocol.engine, dump, **kwargs)
    directory = tmp_path / 'database.managed'
    (directory / 'objects/retained' / reference / 'uv.lock').unlink()
    _, status = inspect_backup(dump, directory, 'test_')
    assert status['unrestorable_revision_ids'] == ['retained']
    assert status['incomplete_backup']


def test_valid_restore_inventory_and_dump_binding(protocol, objects, tmp_path):
    install_snapshot(protocol, objects)
    dump = tmp_path / 'database'
    create_backup(protocol.engine, dump, **objects[0])
    _, status = inspect_backup(dump, tmp_path / 'database.managed', 'test_')
    assert status == report()
    dump.write_bytes(dump.read_bytes() + b'damage')
    with pytest.raises(ColdRestoreError, match='binding'):
        inspect_backup(dump, tmp_path / 'database.managed', 'test_')


@pytest.mark.parametrize('phase', ['pending', 'running', 'succeeded', 'failed', 'blocked', 'aborted', 'stopped'])
@pytest.mark.parametrize('schema', ['none', 'success', 'uncertain'])
def test_new_incarnation_preserves_attempt_schema_and_failure_history_without_retry(publisher, objects, phase, schema):
    response = approve(publisher, ready(publisher))
    paths = objects[0]['paths']
    journal = Journal(phase='verified')
    operation = Operation(response['attempt_id'], 'published-apply', response['revision_id'])
    if schema != 'none':
        journal.begin_schema(operation, 'schema-identity')
        if schema == 'success':
            journal.record_schema_success()
    journal.save(paths.journal_file)
    with publisher.engine.begin() as connection:
        row = connection.execute(select(publisher.tables['installation_attempt'])).mappings().one()
        record = dict(row['record'], phase=phase, copied_pid=123, heartbeat='fresh', lease_expired=True)
        connection.execute(update(publisher.tables['installation_attempt']).values(record=record))
    before = snapshot(publisher)
    restored = establish_incarnation(publisher, paths, authority(), report())
    after = snapshot(publisher)
    assert restored['state'] == ('pending' if phase in ('pending', 'succeeded') and schema != 'uncertain' and
                                 (phase == 'pending' or schema == 'success') else 'blocked')
    assert after['environment_state'][0]['active_attempt_id'] is None
    assert after['environment_state'][0]['runtime_generation'] > before['environment_state'][0]['runtime_generation']
    assert after['revision'] == before['revision']
    assert after['requirement_contribution'] == before['requirement_contribution']
    assert Journal.load(paths.journal_file).to_dict() == journal.to_dict()
    require_incarnation(publisher, paths)
    paths.state_dir.joinpath('installation-host.json').write_text(json.dumps({'id': str(uuid.uuid4())}))
    with pytest.raises(ColdRestoreError, match='foreign'):
        require_incarnation(publisher, paths)


def test_restore_cancels_candidates_and_late_workers_and_discards_only_candidate_secrets(publisher, objects):
    first = ready(publisher)
    pending = publisher.prepare('alice', 'Example>=2', 'stale-worker')
    late = publisher.claim(pending)
    candidate, result_digest = ready(publisher, key='unpublished-ready')
    approve(publisher, first)
    paths = objects[0]['paths']
    secret = paths.state_dir / 'candidate-secret'
    secret.write_text('discard me')
    committed = paths.state_dir / 'committed-secret'
    committed.write_text('keep me')
    with publisher.engine.begin() as connection:
        for identity, path, keep in [('staging', secret, False), ('persistent', committed, True)]:
            connection.execute(publisher.tables['candidate_material'].insert().values(id=identity, candidate_id=candidate,
                path=str(path), usable=True, committed=keep, shared=False, deleted=False))
    establish_incarnation(publisher, paths, authority(), report())
    assert not secret.exists() and committed.read_text() == 'keep me'
    assert publisher.status('alice', candidate)['state'] == 'cancelled'
    assert publisher.status('alice', pending)['state'] == 'cancelled'
    from test_preparation_postgres import result
    assert publisher.publish_ready(late, result(late)) is False
    with pytest.raises(PreparationError):
        publisher.approve('alice', candidate, result_digest, 'stale-approve')
    installation = Installation(publisher.engine, lambda *_: False, 'test_')
    assert installation.claim_installation('copied-host') is None


@pytest.mark.parametrize('flag', ['false', 'bogus'])
def test_copied_success_cannot_authorize_disabled_or_invalid_convergence(publisher, objects, monkeypatch, flag):
    approve(publisher, ready(publisher))
    paths = objects[0]['paths']
    establish_incarnation(publisher, paths, authority(), report())
    monkeypatch.setenv('DAUPDATEONSTART', flag)
    with pytest.raises(ColdRestoreError):
        prepare_cold_boot(publisher, paths)
    with publisher.engine.connect() as connection:
        assert connection.scalar(select(publisher.tables['environment_state'].c.active_attempt_id)) is None


@pytest.mark.parametrize('flag', ['true', 'initial'])
def test_restore_requests_exact_revision_once_with_new_claim_and_schema_evidence(publisher, objects, monkeypatch, flag):
    response = approve(publisher, ready(publisher))
    paths = objects[0]['paths']
    journal = Journal(phase='verified')
    journal.begin_schema(Operation(response['attempt_id'], 'published-apply', response['revision_id']), 'successful-schema')
    journal.record_schema_success()
    journal.save(paths.journal_file)
    with publisher.engine.begin() as connection:
        row = connection.execute(select(publisher.tables['installation_attempt'])).mappings().one()
        connection.execute(update(publisher.tables['installation_attempt']).values(record=dict(row['record'], phase='succeeded')))
        connection.execute(update(publisher.tables['environment_state']).values(active_attempt_id=None))
    restored = establish_incarnation(publisher, paths, authority(), report())
    before = snapshot(publisher)
    (paths.state_dir / 'cold-restore-inventory.json').write_text('{"files":{}}')
    monkeypatch.setenv('DAUPDATEONSTART', flag)
    monkeypatch.setenv('DA_DURABLE_WORK_STORE', str(paths.state_dir))
    monkeypatch.setenv('DA_QUALIFIED_IMAGE_BASELINE', str(objects[0]['baseline']))
    monkeypatch.setattr(InstallationPoller, '_gates', lambda *_args, **_kwargs: paths.state_dir)
    prepare_cold_boot(publisher, paths)
    prepare_cold_boot(publisher, paths)
    after = snapshot(publisher)
    assert after['revision'] == before['revision']
    assert len(after['installation_attempt']) == len(before['installation_attempt']) + 1
    installation = Installation(publisher.engine, lambda *_: False, 'test_')
    with pytest.raises(PreparationError, match='foreign'):
        installation.claim_installation('copied-host')
    claim = installation.claim_installation(restored['incarnation'])
    assert claim.revision_id == response['revision_id']
    assert claim.attempt_id != response['attempt_id']
    assert claim.restore_schema == journal.to_dict()['schema']
    assert Journal.load(paths.journal_file).attempt_result is None


@pytest.mark.parametrize('damage', ['none', 'identity', 'missing-binding', 'inventory', 'readiness', 'schema'])
def test_lost_success_report_is_historical_evidence_not_local_health(publisher, objects, damage):
    from test_image_bridge_postgres import normal_receipt
    approve(publisher, ready(publisher))
    installation = Installation(publisher.engine, lambda *_: False, 'test_')
    claim = installation.claim_installation('old-host')
    receipt = normal_receipt(claim)
    journal = Journal(phase='verified', verified_revision=claim.revision_id,
        operation=Operation(claim.attempt_id, 'published-apply', claim.revision_id, binding=claim.identity), attempt_result=receipt)
    journal.begin_schema(journal.operation, 'schema-success')
    journal.record_schema_success()
    if damage == 'identity':
        receipt['identity'] = dict(receipt['identity'], fence=999)
    elif damage == 'missing-binding':
        journal.operation.binding = None
        receipt.pop('identity')
        with publisher.engine.begin() as connection:
            row = connection.execute(select(publisher.tables['installation_attempt'])).mappings().one()
            connection.execute(update(publisher.tables['installation_attempt']).values(record=dict(row['record'], claim=None)))
    elif damage == 'inventory':
        receipt['inventory'] = {}
    elif damage == 'readiness':
        receipt['readiness'] = {'schema_verified': True, 'roles': []}
    elif damage == 'schema':
        journal.schema.result = None
    paths = objects[0]['paths']
    journal.save(paths.journal_file)
    restored = establish_incarnation(publisher, paths, authority(), report())
    assert restored['state'] == ('pending' if damage == 'none' else 'blocked')
    with publisher.engine.connect() as connection:
        env = connection.execute(select(publisher.tables['environment_state'])).mappings().one()
        assert env['snapshot']['runtime']['kind'] == 'unknown' and env['active_attempt_id'] is None
    if damage != 'missing-binding':
        with pytest.raises(PreparationError, match='historical|invalid normal evidence'):
            installation.report_terminal(receipt)
    assert Journal.load(paths.journal_file).to_dict() == journal.to_dict()


def test_backups_of_restored_deployments_can_establish_another_fresh_incarnation(publisher, objects):
    approve(publisher, ready(publisher))
    paths = objects[0]['paths']
    first = establish_incarnation(publisher, paths, authority(), report())
    with pytest.raises(ColdRestoreError, match='fresh'):
        establish_incarnation(publisher, paths, first, report())
    second = establish_incarnation(publisher, paths, authority(), report())
    assert first['incarnation'] != second['incarnation']
    assert second['previous_incarnation'] == first['incarnation']
