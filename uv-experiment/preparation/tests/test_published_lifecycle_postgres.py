"""Persistent published journal transitions against PostgreSQL authority, not just publication."""
# ruff: noqa: F811 -- explicit pytest fixture registration.
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from types import SimpleNamespace
import json

import pytest
from sqlalchemy import select, text, update

from test_preparation_postgres import protocol, result, snapshot  # noqa: F401
from test_publication_writers_postgres import publisher, adapter  # noqa: F401
from test_image_bridge_postgres import bridge, ready_bridge, normal_receipt  # noqa: F401
from test_repair_postgres import recovery_receipt, repair_result, recovery_http  # noqa: F401
from docassemble_executor.admission import Admission
from docassemble_executor.authority import MutationLock, RuntimeLock
from docassemble_executor.boot_observation import (BootObservation, installation_host_id,
                                                resume_published_boot, resume_recovery_boot)
from docassemble_executor.health import Check
from docassemble_executor.installation import Installation, InstallationPoller, schema_clearance_file
from docassemble_executor.journal import Journal, Operation, SchemaWork
from docassemble_executor.paths import ExecutorPaths
from docassemble_executor.state import AppliedState


def paths_at(tmp_path):
    return replace(ExecutorPaths.from_env(), state_dir=tmp_path, journal_file=tmp_path / 'lifecycle.json',
        run_dir=tmp_path / 'run', log_dir=tmp_path / 'log', cache_dir=tmp_path / 'cache',
        status_file=tmp_path / 'status.json', repair_request_file=tmp_path / 'requests/repair',
        config_file=tmp_path / 'config.yml', applied_file=tmp_path / 'applied.json')


def published_claim(bridge):
    bridge[0].post('/preview', json=ready_bridge(bridge))
    installation = Installation(bridge[1].engine, lambda *_: False, 'test_')
    return installation, installation.claim_installation('host')


def recovered_journal(paths, claim, *, uncertain=False):
    journal = Journal()
    journal.begin_mutation(Operation(claim.attempt_id, 'published-apply', claim.revision_id, binding=claim.identity))
    if uncertain:
        journal.begin_schema(journal.operation, 'uncertain-schema')
    journal.begin_recovery('failed desired application')
    journal.finish_recovery(True)
    journal.attempt_result = recovery_receipt(claim)
    journal.save(paths.journal_file)
    return journal


@pytest.mark.parametrize('reason', ['automatic-migration', 'automatic-adoption', 'cold-restore'])
@pytest.mark.parametrize('mode', ['false', 'invalid'])
def test_published_automatic_request_is_not_claimed_when_current_startup_policy_disabled(bridge, reason, mode, monkeypatch):
    client, service, _ = bridge
    approved = client.post('/preview', json=ready_bridge(bridge)).json
    installation = Installation(service.engine, lambda *_: False, 'test_')
    attempts = installation.tables['installation_attempt']
    environment = installation.tables['environment_state']
    with service.engine.begin() as connection:
        attempt = connection.execute(select(attempts)).mappings().one()
        record = dict(attempt['record'], reason=reason)
        if reason == 'cold-restore':
            record['incarnation'] = 'host'
            row = connection.execute(select(environment)).mappings().one()
            connection.execute(update(environment).values(snapshot=dict(row['snapshot'], cold_restore={
                'state': 'requested', 'incarnation': 'host', 'attempt_id': attempt['id']})))
        connection.execute(update(attempts).values(record=record))
    before = snapshot(service)
    monkeypatch.setenv('DAUPDATEONSTART', mode)
    assert installation.claim_installation('host') is None
    assert snapshot(service) == before  # The pending request/revision is preserved, not terminally blocked.
    monkeypatch.setenv('DAUPDATEONSTART', 'true')
    claim = installation.claim_installation('host')
    assert claim.attempt_id == approved['attempt_id'] and claim.revision_id == approved['revision_id']
    assert claim.automatic
    assert snapshot(service)['revision'] == before['revision']


@pytest.mark.parametrize('retry', [False, True])
@pytest.mark.parametrize('uncertain', [False, True])
@pytest.mark.parametrize('interrupted', [False, True])
def test_recovered_terminal_rolls_over_to_approved_repair_or_exact_retry_and_preserves_schema(
        bridge, recovery_http, tmp_path, monkeypatch, retry, uncertain, interrupted):
    installation, previous = published_claim(bridge)
    paths = paths_at(tmp_path)
    paths.config_file.write_text('{}')
    old_journal = recovered_journal(paths, previous, uncertain=uncertain)
    assert installation.report_terminal(old_journal.attempt_result) == 'reported'
    client, service = recovery_http
    if retry:
        approved = service.retry('2', previous.revision_id, 'recovered-retry')
        assert approved['revision_id'] == previous.revision_id
    else:
        candidate = service.prepare('2', None, 'repair', operation='repair')
        build = service.claim(candidate)
        assert service.publish_ready(build, repair_result(build))
        with client.session_transaction() as session:
            session['recovery_repair_candidate'] = {'candidate_id': candidate}
        response = client.post('/recovery/publication', data={'action': 'approve', 'candidate_id': candidate})
        assert response.status_code == 302
        with service.engine.connect() as connection:
            state = installation._environment(connection)
        approved = {'attempt_id': state['active_attempt_id'], 'revision_id': state['desired_revision_id']}
        assert approved['revision_id'] != previous.revision_id
    assert installation.next_after_terminal(old_journal.attempt_result)
    changed = dict(old_journal.attempt_result, failure=None)
    assert not installation.next_after_terminal(changed)  # Equality with durable prior terminal is mandatory.
    poller = InstallationPoller(installation, paths, tmp_path, tmp_path)
    monkeypatch.setattr(poller, '_gates', lambda claim: tmp_path)
    monkeypatch.setattr('docassemble_executor.installation.load_context', lambda *a, **kw: SimpleNamespace())
    monkeypatch.setattr('docassemble_executor.installation.enter_maintenance', lambda _: True)
    monkeypatch.setattr('docassemble_executor.published.close_published', lambda _: None)
    syncs, setups = [], []
    def sync(*args, **kw):
        syncs.append(Journal.load(paths.journal_file).operation.id)
        return SimpleNamespace(ok=True)
    monkeypatch.setattr('docassemble_executor.installation.sync_frozen', sync)
    monkeypatch.setattr('docassemble_executor.installation.verify_environment', lambda *a: SimpleNamespace(ok=True))
    def prepare(paths, claim):
        journal = Journal.load(paths.journal_file)
        if uncertain:
            assert journal.schema_uncertain and journal.schema_cleared
            assert journal.schema.attempt_id == previous.attempt_id
        journal.begin_schema(journal.operation, 'authorized-schema')
        journal.save(paths.journal_file)
        setups.append(claim.attempt_id)
        journal.record_schema_success()
        journal.save(paths.journal_file)
        return {'schema_verified': True, 'roles': normal_receipt(previous)['readiness']['roles']}
    monkeypatch.setattr('docassemble_executor.published.prepare_published', prepare)
    def record(paths, claim, receipt):
        journal = Journal.load(paths.journal_file)
        journal.record_verified(claim.revision_id)
        journal.attempt_result = receipt
        journal.save(paths.journal_file)
    monkeypatch.setattr('docassemble_executor.published.record_published', record)
    monkeypatch.setattr(poller, '_report_normal', installation.report_terminal)
    (tmp_path / 'installed-material.json').write_text('{}')
    history_path = tmp_path / 'attempt-history' / (previous.attempt_id + '.json')
    pending = snapshot(installation)
    if uncertain:
        # Use the real PostgreSQL schema probe, not a fabricated clearance result.
        with installation.engine.begin() as connection:
            connection.execute(text('CREATE TABLE alembic_version (version_num varchar(255))'))
            connection.execute(text("INSERT INTO alembic_version VALUES ('observed-schema')"))
        settings = SimpleNamespace(connect_kwargs={**installation.engine.url.translate_connect_args(
            database='dbname', username='user'), 'options': '-csearch_path=preparation_test'})
        monkeypatch.setattr('docassemble_executor.installation.database_settings', lambda _: settings)
        # Missing, foreign, wrong-job and stale-schema clearance cannot consume the
        # pending attempt, prior terminal, failure or uncertain schema evidence.
        for invalid in (None, {'attempt_id': 'foreign'}, {'schema_identity': 'foreign'},
                        {'observed_schema': 'stale'}):
            if invalid is not None:
                schema_clearance_file(paths).write_text(json.dumps(dict(
                    format=1, attempt_id=previous.attempt_id, schema_identity='uncertain-schema',
                    observed_schema='observed-schema') | invalid))
                schema_clearance_file(paths).chmod(0o600)
            # New poller and reloaded journal model restart while waiting.
            waiting = InstallationPoller(installation, paths, tmp_path, tmp_path)
            assert waiting.run_once() == 'schema-clearance-required'
            assert snapshot(installation) == pending
            assert Journal.load(paths.journal_file).to_dict() == old_journal.to_dict()
            assert not history_path.exists()
            assert syncs == setups == []
        schema_clearance_file(paths).write_text(json.dumps({
            'format': 1, 'attempt_id': previous.attempt_id, 'schema_identity': 'uncertain-schema',
            'observed_schema': 'observed-schema'}))
        schema_clearance_file(paths).chmod(0o600)
    if interrupted:
        # A crash after archive/rollover but before DB claim still cannot turn
        # missing clearance on restart into a claimed/blocked attempt.
        claim_installation = installation.claim_installation
        def crash(_):
            raise RuntimeError('crash before claim')
        monkeypatch.setattr(installation, 'claim_installation', crash)
        with pytest.raises(RuntimeError, match='crash before claim'):
            poller.run_once()
        assert snapshot(installation) == pending
        assert syncs == setups == []
        assert Journal.load(paths.journal_file).attempt_result is None
        monkeypatch.setattr(installation, 'claim_installation', claim_installation)
        if uncertain:
            schema_clearance_file(paths).unlink()
            with installation.engine.begin() as connection:
                connection.execute(text("UPDATE alembic_version SET version_num = 'new-observed-schema'"))
            before_restart = Journal.load(paths.journal_file).to_dict()
            assert poller.run_once() == 'schema-clearance-required'
            assert snapshot(installation) == pending
            assert Journal.load(paths.journal_file).to_dict() == before_restart
            assert syncs == setups == []
            schema_clearance_file(paths).write_text(json.dumps({
                'format': 1, 'attempt_id': previous.attempt_id, 'schema_identity': 'uncertain-schema',
                'observed_schema': 'new-observed-schema'}))
            schema_clearance_file(paths).chmod(0o600)
    # Pause the authorized executor after its claim, then race another poller.
    # MutationLock must exclude the second writer from changing the journal/DB.
    started, release = Event(), Event()
    def gates(claim):
        assert claim.attempt_id == approved['attempt_id']
        started.set()
        assert release.wait(10)
        return tmp_path
    monkeypatch.setattr(poller, '_gates', gates)
    with ThreadPoolExecutor(max_workers=1) as executor:
        running = executor.submit(poller.run_once)
        try:
            assert started.wait(10)
            during = snapshot(installation)
            during_journal = Journal.load(paths.journal_file).to_dict()
            with pytest.raises(BlockingIOError):
                InstallationPoller(installation, paths, tmp_path, tmp_path).run_once()
            assert snapshot(installation) == during
            assert Journal.load(paths.journal_file).to_dict() == during_journal
            assert syncs == setups == []
        finally:
            release.set()
        assert running.result(timeout=20) == 'reported'
    terminal = Journal.load(paths.journal_file).attempt_result
    assert terminal['identity']['attempt_id'] == approved['attempt_id']
    history = json.loads(history_path.read_text())
    expected_history = old_journal.to_dict()
    if uncertain:
        assert history['clearance']['attempt_id'] == previous.attempt_id
        assert history['clearance']['schema_identity'] == 'uncertain-schema'
        assert history['clearance']['observed_schema'] == 'observed-schema'
        expected_history['clearance'] = history['clearance']
    assert history == expected_history  # Failure, recovery and schema history are immutable.
    assert syncs == setups == [approved['attempt_id']]
    assert terminal['outcome'] == 'applied-normal'
    assert terminal['normal_verified'] is True
    after = snapshot(installation)
    assert after['revision'] == pending['revision']
    assert after['requirement_contribution'] == pending['requirement_contribution']
    assert after['environment_state'][0]['active_attempt_id'] is None
    assert next(row for row in after['installation_attempt'] if row['id'] == previous.attempt_id) == next(
        row for row in pending['installation_attempt'] if row['id'] == previous.attempt_id)
    assert Journal.load(paths.journal_file).schema.attempt_id == approved['attempt_id']
    assert Journal.load(paths.journal_file).schema_succeeded
    assert installation.report_terminal(terminal) == 'replayed'
    before_replay = snapshot(installation)
    assert poller.run_once() == 'replayed'
    assert snapshot(installation) == before_replay
    assert syncs == setups == [approved['attempt_id']]


@pytest.mark.parametrize('phase', ['mutation_started', 'recovery_attempt_started', 'recovery_succeeded', 'recovery_failed'])
def test_crashed_published_mutation_gets_at_most_one_recovery_and_a_durable_terminal(
        bridge, tmp_path, monkeypatch, phase):
    installation, claim = published_claim(bridge)
    paths = paths_at(tmp_path)
    journal = Journal()
    journal.begin_mutation(Operation(claim.attempt_id, 'published-apply', claim.revision_id, binding=claim.identity))
    journal.begin_schema(journal.operation, 'uncertain-schema')
    if phase != 'mutation_started':
        journal.begin_recovery('interrupted apply')
        if phase != 'recovery_attempt_started':
            journal.finish_recovery(phase == 'recovery_succeeded')
    journal.save(paths.journal_file)
    manifest = {'identity': {'core_baseline': 'r1'}, 'bundles': {'core-r1': 'b' * 64},
                'inventories': {'core-r1': {'distributions': {'core': '1.0'}}}}
    def context(*a, **kw):
        return SimpleNamespace(paths=paths, manifest=manifest, journal=Journal.load(paths.journal_file),
            bundle_name=lambda kind, revision: kind + '-' + revision)
    monkeypatch.setattr('docassemble_executor.installation.load_context', context)
    monkeypatch.setattr('docassemble_executor.startup.target_id', lambda _: 'fixture-image')
    restorations = []
    def restore(context, *a, **kw):
        restorations.append('core-only')
        context.journal.begin_recovery('interrupted application')
        context.journal.save(paths.journal_file)
        context.journal.finish_recovery(True)
        context.journal.save(paths.journal_file)
        return 'recovery'
    monkeypatch.setattr('docassemble_executor.controller._restore_core', restore)
    def failed(context, reason):
        context.journal.finish_recovery(False, reason)
        context.journal.save(paths.journal_file)
    monkeypatch.setattr('docassemble_executor.controller._recovery_failed', failed)
    monkeypatch.setattr('docassemble_executor.installation.sync_frozen', lambda *a, **kw: pytest.fail('desired sync replay'))
    poller = InstallationPoller(installation, paths, tmp_path, tmp_path)
    with MutationLock(paths.state_dir), RuntimeLock(paths.state_dir, shared=True):
        with pytest.raises(BlockingIOError):
            poller.reconcile_interrupted(Journal.load(paths.journal_file))
    assert Journal.load(paths.journal_file).to_dict() == journal.to_dict()
    assert restorations == []
    with MutationLock(paths.state_dir):
        poller.reconcile_interrupted(Journal.load(paths.journal_file))
    after = Journal.load(paths.journal_file)
    assert after.recovery.attempt == 1
    assert after.schema_uncertain and after.schema.attempt_id == claim.attempt_id
    assert after.attempt_result['outcome'] == 'failed-stopped'
    assert after.attempt_result['recovery']['result'] == ('succeeded' if phase in ('mutation_started', 'recovery_succeeded') else 'failed')
    assert after.attempt_result['identity'] == claim.identity
    assert restorations == (['core-only'] if phase == 'mutation_started' else [])
    before = snapshot(installation)
    assert installation.report_terminal(after.attempt_result) == 'replayed'
    assert poller.run_once() == 'replayed'
    assert snapshot(installation) == before
    assert Journal.load(paths.journal_file).to_dict() == after.to_dict()


@pytest.mark.parametrize('lost_report', [False, True])
def test_published_normal_boot_requalifies_and_reconciles_lost_report_without_sync_or_setup(
        bridge, tmp_path, monkeypatch, lost_report):
    installation, claim = published_claim(bridge)
    receipt = normal_receipt(claim)
    if not lost_report:
        installation.report_terminal(receipt)
    paths = paths_at(tmp_path)
    paths.ensure_directories()
    journal = Journal(phase='verified', operation=Operation(claim.attempt_id, 'published-apply', claim.revision_id,
        binding=claim.identity), attempt_result=receipt, verified_revision=claim.revision_id,
        schema=SchemaWork(claim.attempt_id, claim.revision_id, 'schema', result='succeeded'))
    journal.save(paths.journal_file)
    journal = Journal.load(paths.journal_file)
    observer = BootObservation(installation.engine, lambda *_: False, 'test_')
    applied = AppliedState(revision=claim.revision_id)
    observation = observer.reconcile(installation_host_id(paths), journal, applied)
    assert observer.resumable(observation, journal, applied) == claim.snapshot
    assert observation['normal_ready'] is False
    if lost_report:
        assert observation['runtime_generation'] == claim.runtime_generation
        assert observation['active'] is True
    directory = tmp_path / 'material'
    directory.mkdir()
    (directory / 'installed-material.json').write_text('{}')
    monkeypatch.setenv('DA_DURABLE_WORK_STORE', str(tmp_path))
    monkeypatch.setenv('DA_QUALIFIED_IMAGE_BASELINE', str(tmp_path / 'baseline'))
    monkeypatch.setattr(InstallationPoller, '_gates', lambda *a, **kw: directory)
    monkeypatch.setattr('docassemble_executor.inventory.verify_environment', lambda *a: SimpleNamespace(ok=True))
    monkeypatch.setattr('docassemble_executor.alembic_probe.check_schema', lambda *a: SimpleNamespace(usable=True))
    monkeypatch.setattr('docassemble_executor.health.probe_runtime', lambda *a: SimpleNamespace(ok=True))
    monkeypatch.setattr('docassemble_executor.controller._start_web', lambda *a, **kw: True)
    monkeypatch.setattr('docassemble_executor.roles.prepare_roles', lambda *a: [Check('roles', True)])
    monkeypatch.setattr('docassemble_executor.controller._write_status', lambda *a, **kw: {'normal_ready': True})
    monkeypatch.setattr('docassemble_executor.installation.sync_frozen', lambda *a, **kw: pytest.fail('boot sync'))
    monkeypatch.setattr('docassemble_executor.controller._create_tables', lambda *a: pytest.fail('boot setup'))
    context = SimpleNamespace(paths=paths, journal=journal, applied=applied, config={'db': {'table prefix': 'test_'}},
                              manifest={}, db=None, expected_core_versions={})
    status = resume_published_boot(context, observer, observation, {})
    assert status['normal_ready'] is True
    assert Admission(paths.state_dir).state()['mode'] == 'open'
    after = snapshot(installation)
    assert after['environment_state'][0]['active_attempt_id'] is None
    assert after['installation_attempt'][0]['record']['terminal'] == receipt
    assert after['environment_state'][0]['snapshot']['runtime']['kind'] == 'normal'
    assert Journal.load(paths.journal_file).to_dict() == journal.to_dict()


@pytest.mark.parametrize('healthy', [True, False])
def test_completed_recovery_boot_restarts_only_restricted_administration_with_fresh_health(bridge, tmp_path, monkeypatch, healthy):
    installation, claim = published_claim(bridge)
    paths = paths_at(tmp_path)
    journal = recovered_journal(paths, claim, uncertain=True)
    # Simulate DB reporting loss at shutdown. Reconcile before boot invalidation.
    poller = InstallationPoller(installation, paths, tmp_path, tmp_path)
    with MutationLock(paths.state_dir):
        poller.report_stopped_boot(Journal.load(paths.journal_file))
    observer = BootObservation(installation.engine, lambda *_: False, 'test_')
    observation = observer.reconcile('host', journal, AppliedState())
    assert observer.recovery_resumable(observation, journal)
    manifest = {'identity': {'core_baseline': 'r1'}, 'bundles': {'core-r1': 'b' * 64},
                'inventories': {'core-r1': {'distributions': {'core': '1.0'}, 'files': {}}}}
    monkeypatch.setattr('docassemble_executor.startup.target_id', lambda _: 'fixture-image')
    monkeypatch.setattr('docassemble_executor.image_baseline.verify_image_recovery_core', lambda _: None)
    monkeypatch.setattr('docassemble_executor.inventory.verify_environment', lambda *a: SimpleNamespace(ok=healthy))
    monkeypatch.setattr('docassemble_executor.health.probe_runtime', lambda *a: SimpleNamespace(ok=True))
    monkeypatch.setattr('docassemble_executor.alembic_probe.check_schema', lambda *a: SimpleNamespace(usable=True))
    launched = []
    monkeypatch.setattr('docassemble_executor.controller._start_web', lambda *a, **kw: launched.append(kw['recovery']) or True)
    monkeypatch.setattr('docassemble_executor.controller._write_status', lambda *a, **kw: {
        'mode': 'recovery', 'recovery_available': True, 'normal_ready': False})
    monkeypatch.setattr('docassemble_executor.controller._restore_core', lambda *a: pytest.fail('second recovery'))
    monkeypatch.setattr('docassemble_executor.controller._create_tables', lambda *a: pytest.fail('recovery setup'))
    context = SimpleNamespace(paths=paths, journal=Journal.load(paths.journal_file), manifest=manifest, db=None,
                              bundle_name=lambda kind, revision: kind + '-' + revision)
    monkeypatch.setattr('docassemble_executor.controller.stop_web', lambda _: None)
    status = resume_recovery_boot(context, observer, observation, {'mode': 'maintenance', 'normal_ready': False})
    after = snapshot(installation)['environment_state'][0]
    if healthy:
        assert status['mode'] == 'recovery' and status['recovery_available'] and not status['normal_ready']
        assert launched == [True]
        assert after['snapshot']['runtime']['kind'] == 'core-only'
        assert after['snapshot']['runtime']['recovery_id'] == receipt_id(claim)
    else:
        assert status['mode'] == 'maintenance' and not status['normal_ready']
        assert launched == []
        assert Admission(paths.state_dir).state()['mode'] == 'closed'
        assert after['snapshot']['runtime']['kind'] == 'unknown'
    with Admission(paths.state_dir).lease(consumer=True) as lease:
        assert lease is None
    assert Journal.load(paths.journal_file).to_dict() == journal.to_dict()
    assert Journal.load(paths.journal_file).schema_uncertain  # Serving recovery is not schema clearance.


def receipt_id(claim):
    return claim.attempt_id + ':core:1'


@pytest.mark.parametrize('eligible', [True, False])
def test_executor_retry_claim_and_rollover_use_durable_recovery_eligibility_independently_of_web(
        bridge, tmp_path, eligible):
    import uuid
    from docassemble_executor.preparation import PreparationError
    installation, previous = published_claim(bridge)
    journal = recovered_journal(paths_at(tmp_path), previous)
    if not eligible:
        journal.attempt_result['recovery']['result'] = 'failed'
    installation.report_terminal(journal.attempt_result)
    attempt_id = str(uuid.uuid4())
    with installation.engine.begin() as connection:
        # A durable pending request is executor authority. Web authorization is
        # deliberately exercised separately by the end-to-end retry regression.
        connection.execute(installation.tables['installation_attempt'].insert().values(
            id=attempt_id, revision_id=previous.revision_id, record={'phase': 'pending', 'reason': 'retry',
                'fence': 0, 'created_at': installation._now(connection).isoformat()}))
        connection.execute(update(installation.tables['environment_state']).values(active_attempt_id=attempt_id))
    before = snapshot(installation)
    assert installation.next_after_terminal(journal.attempt_result) is eligible
    if eligible:
        claim = installation.claim_installation('host')
        assert claim.attempt_id == attempt_id and claim.revision_id == previous.revision_id
        assert claim.snapshot == previous.snapshot
        assert snapshot(installation)['revision'] == before['revision']
    else:
        with pytest.raises(PreparationError, match='eligible durable terminal'):
            installation.claim_installation('host')
        assert snapshot(installation) == before


@pytest.mark.parametrize('heads', [[], ['known'], ['known', 'incompatible'], [''], ['known', '']])
def test_alembic_probe_reads_all_heads_and_refuses_ambiguous_or_empty_schema(protocol, heads):
    from sqlalchemy import text
    from docassemble_executor.alembic_probe import read_database_revision
    with protocol.engine.begin() as connection:
        connection.execute(text('CREATE TABLE alembic_version (version_num varchar(255))'))
        for head in heads:
            connection.execute(text('INSERT INTO alembic_version VALUES (:head)'), {'head': head})
    settings = SimpleNamespace(connect_kwargs={**protocol.engine.url.translate_connect_args(database='dbname', username='user'),
                                               'options': '-csearch_path=preparation_test'})
    if heads in ([], ['known']):
        assert read_database_revision(settings) == ('known' if heads else None)
    else:
        with pytest.raises(ValueError, match='exactly one nonempty Alembic head'):
            read_database_revision(settings)


@pytest.mark.parametrize('reason', ['automatic-migration', 'automatic-adoption', 'cold-restore'])
def test_disabled_pending_automatic_work_waits_then_applies_same_frozen_revision(
        bridge, tmp_path, monkeypatch, reason):
    client, service, _ = bridge
    approved = client.post('/preview', json=ready_bridge(bridge)).json
    installation = Installation(service.engine, lambda *_: False, 'test_')
    attempts = installation.tables['installation_attempt']
    with installation.engine.begin() as connection:
        attempt = connection.execute(select(attempts)).mappings().one()
        record = dict(attempt['record'], reason=reason)
        if reason == 'cold-restore':
            record['incarnation'] = 'host'
            state = installation._environment(connection)
            connection.execute(update(installation.tables['environment_state']).values(snapshot=dict(
                state['snapshot'], cold_restore={'incarnation': 'host', 'state': 'requested',
                                                'attempt_id': approved['attempt_id']})))
        connection.execute(update(attempts).values(record=record))
    paths = paths_at(tmp_path)
    paths.config_file.write_text('{}')
    # Match the already-requested cold restore incarnation without importing old receipts.
    (tmp_path / 'installation-host.json').write_text('{"id": "host"}')
    Journal().save(paths.journal_file)
    before = snapshot(installation)
    poller = InstallationPoller(installation, paths, tmp_path, tmp_path)
    monkeypatch.setenv('DAUPDATEONSTART', 'false')
    assert poller.run_once() == 'idle'
    assert snapshot(installation) == before
    assert Journal.load(paths.journal_file).phase == 'idle'
    monkeypatch.setenv('DAUPDATEONSTART', 'true')
    monkeypatch.setattr(poller, '_gates', lambda claim: tmp_path)
    monkeypatch.setattr('docassemble_executor.installation.load_context', lambda *a, **kw: SimpleNamespace())
    monkeypatch.setattr('docassemble_executor.installation.enter_maintenance', lambda _: True)
    syncs = []
    def sync(*a, **kw):
        syncs.append(Journal.load(paths.journal_file).operation.revision)
        return SimpleNamespace(ok=True)
    monkeypatch.setattr('docassemble_executor.installation.sync_frozen', sync)
    monkeypatch.setattr('docassemble_executor.installation.verify_environment', lambda *a: SimpleNamespace(ok=True))
    from docassemble_executor.roles import RUNTIME_ROLES, INFRASTRUCTURE_ROLES
    monkeypatch.setattr('docassemble_executor.published.prepare_published', lambda *a: {
        'schema_verified': True, 'roles': sorted(RUNTIME_ROLES + INFRASTRUCTURE_ROLES)})
    def record(paths, claim, receipt):
        journal = Journal.load(paths.journal_file)
        journal.record_verified(claim.revision_id)
        journal.attempt_result = receipt
        journal.save(paths.journal_file)
    monkeypatch.setattr('docassemble_executor.published.record_published', record)
    monkeypatch.setattr(poller, '_report_normal', installation.report_terminal)
    (tmp_path / 'installed-material.json').write_text('{}')
    assert poller.run_once() == 'reported'
    assert syncs == [approved['revision_id']]
    after = snapshot(installation)
    assert after['revision'] == before['revision']
    assert after['environment_state'][0]['active_attempt_id'] is None
    assert after['installation_attempt'][0]['record']['terminal']['outcome'] == 'applied-normal'
    assert Journal.load(paths.journal_file).attempt_result['identity']['attempt_id'] == approved['attempt_id']


@pytest.mark.parametrize('change', ['desired', 'claim', 'restore-incarnation'])
def test_interrupted_recovery_refuses_foreign_or_superseded_original_authority(bridge, tmp_path, change):
    from docassemble_executor.preparation import PreparationError
    installation, claim = published_claim(bridge)
    journal = Journal()
    journal.begin_mutation(Operation(claim.attempt_id, 'published-apply', claim.revision_id, binding=claim.identity))
    paths = paths_at(tmp_path)
    journal.save(paths.journal_file)
    with installation.engine.begin() as connection:
        environment = installation.tables['environment_state']
        if change == 'desired':
            connection.execute(update(environment).values(desired_revision_id='baseline'))
        elif change == 'claim':
            journal.operation.binding = dict(claim.identity, fence=99)
        else:
            current = installation._environment(connection)
            connection.execute(update(environment).values(snapshot=dict(current['snapshot'], cold_restore={
                'incarnation': 'foreign-restored-host'})))
    before = snapshot(installation)
    with pytest.raises(PreparationError, match='claim mismatch'):
        installation.interrupted_claim(journal)
    assert snapshot(installation) == before
    assert not Journal.load(paths.journal_file).recovery_attempted
