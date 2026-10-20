"""Bounded installation claims/terminal replay against real PostgreSQL."""
# ruff: noqa: F811 -- pytest fixtures are explicitly imported for registration.
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from sqlalchemy import select, update

from test_preparation_postgres import protocol, snapshot  # noqa: F401
from test_publication_postgres import publisher, ready, approve  # noqa: F401
from docassemble_executor.installation import Installation, InstallationPoller
from docassemble_executor.journal import Journal, Operation
from docassemble_executor.paths import ExecutorPaths
from docassemble_executor.preparation import PreparationError


def installer(publisher):
    return Installation(publisher.engine, lambda *_: False, 'test_')


def receipt(claim, outcome='materialized-stopped'):
    return {'identity': claim.identity, 'outcome': outcome, 'child_safe': True, 'normal_verified': False}


def test_concurrent_installation_claim_never_lease_reclaimed(publisher):
    response = approve(publisher, ready(publisher))
    installation = installer(publisher)
    def claim():
        try:
            return installation.claim_installation('host')
        except PreparationError:
            return None
    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(lambda _: claim(), range(2)))
    assert claims.count(None) == 1
    claimed = next(c for c in claims if c)
    assert claimed.attempt_id == response['attempt_id']
    before = snapshot(publisher)
    with pytest.raises(PreparationError, match='uncertain'):
        installation.claim_installation('replacement')
    assert snapshot(publisher) == before


@pytest.mark.parametrize('outcome', ['materialized-stopped', 'failed-stopped', 'blocked'])
def test_terminal_replay_preserves_desired_and_separates_runtime(publisher, outcome):
    response = approve(publisher, ready(publisher))
    installation = installer(publisher)
    claim = installation.claim_installation('host')
    assert installation.report_terminal(receipt(claim, outcome)) == 'reported'
    before = snapshot(publisher)
    assert installation.report_terminal(receipt(claim, outcome)) == 'replayed'
    assert snapshot(publisher) == before
    environment = before['environment_state'][0]
    assert environment['active_attempt_id'] is None
    assert environment['desired_revision_id'] == response['revision_id']
    assert environment['snapshot']['runtime']['kind'] == 'unknown'
    assert environment['snapshot']['last_normally_verified']['kind'] == 'normal'
    status = publisher.publication_status('alice', attempt_id=claim.attempt_id)
    assert status['verified_running_revision_id'] is None
    assert status['terminal_outcome'] == outcome
    assert not status['retry_supported'] and not status['approval_supported']
    assert status['attempt_phase'] != 'reported-terminal'


def test_stale_observation_and_late_receipt_never_release_other_attempt(publisher):
    approve(publisher, ready(publisher))
    installation = installer(publisher)
    claim = installation.claim_installation('host')
    table = publisher.tables['environment_state']
    with publisher.engine.begin() as connection:
        row = connection.execute(select(table)).mappings().one()
        observed = dict(row['snapshot'], runtime={'kind': 'development-override', 'inventory': {}})
        connection.execute(update(table).values(runtime_generation=99, snapshot=observed))
    assert installation.report_terminal(receipt(claim)) == 'reported'
    with publisher.engine.begin() as connection:
        connection.execute(publisher.tables['installation_attempt'].insert().values(
            id='another', revision_id=claim.revision_id, record={'phase': 'running'}))
        connection.execute(update(table).values(active_attempt_id='another'))
    before = snapshot(publisher)
    assert installation.report_terminal(receipt(claim)) == 'replayed'
    assert snapshot(publisher) == before
    assert before['environment_state'][0]['snapshot']['runtime']['kind'] == 'development-override'
    assert before['environment_state'][0]['runtime_generation'] == 99


@pytest.mark.parametrize('field,value', [('fence', 99), ('host_id', 'other'), ('attempt_id', 'absent'), ('bundle_digest', '0' * 64)])
def test_mismatched_terminal_binding_rejected(publisher, field, value):
    approve(publisher, ready(publisher))
    installation = installer(publisher)
    claim = installation.claim_installation('host')
    report = receipt(claim)
    report['identity'][field] = value
    before = snapshot(publisher)
    with pytest.raises(PreparationError, match='fence mismatch'):
        installation.report_terminal(report)
    assert snapshot(publisher) == before


def test_lost_db_report_replays_journal_without_sync(publisher, tmp_path, monkeypatch):
    approve(publisher, ready(publisher))
    installation = installer(publisher)
    claim = installation.claim_installation('host')
    paths = replace(ExecutorPaths.from_env(), state_dir=tmp_path, journal_file=tmp_path / 'lifecycle.json',
                    run_dir=tmp_path / 'run', status_file=tmp_path / 'status.json',
                    repair_request_file=tmp_path / 'requests/repair.json', cache_dir=tmp_path / 'cache', log_dir=tmp_path / 'log')
    journal = Journal()
    journal.begin_mutation(Operation(id=claim.attempt_id, kind='published-materialization',
                                    revision=claim.revision_id, binding=claim.identity))
    journal.attempt_result = receipt(claim)
    journal.save(paths.journal_file)
    monkeypatch.setattr('docassemble_executor.installation.sync_frozen', lambda *a, **k: pytest.fail('reinstalled'))
    poller = InstallationPoller(installation, paths, tmp_path, tmp_path / 'absent-baseline')
    report = installation.report_terminal
    def lost_ack(value):
        report(value)
        raise RuntimeError('lost DB acknowledgement')
    monkeypatch.setattr(installation, 'report_terminal', lost_ack)
    with pytest.raises(RuntimeError, match='lost DB'):
        poller.run_once()
    monkeypatch.setattr(installation, 'report_terminal', report)
    assert poller.run_once() == 'replayed'
    assert Journal.load(paths.journal_file).attempt_result == receipt(claim)


def test_child_lease_blocks_terminal_replay(publisher, tmp_path):
    from docassemble_executor.authority import RuntimeLock
    approve(publisher, ready(publisher))
    installation = installer(publisher)
    claim = installation.claim_installation('host')
    paths = replace(ExecutorPaths.from_env(), state_dir=tmp_path, journal_file=tmp_path / 'lifecycle.json',
                    run_dir=tmp_path / 'run', status_file=tmp_path / 'status.json',
                    repair_request_file=tmp_path / 'requests/repair.json', cache_dir=tmp_path / 'cache', log_dir=tmp_path / 'log')
    journal = Journal(attempt_result=receipt(claim), operation=Operation(
        id=claim.attempt_id, kind='published-materialization', revision=claim.revision_id, binding=claim.identity))
    journal.save(paths.journal_file)
    before = snapshot(publisher)
    with RuntimeLock(tmp_path, shared=True):
        with pytest.raises(BlockingIOError):
            InstallationPoller(installation, paths, tmp_path, tmp_path).run_once()
    assert snapshot(publisher) == before


@pytest.mark.parametrize('stage,reason', [
    ('gates', 'updates-disabled'), ('gates', 'baseline-invalid'),
    ('gates', 'target-mismatch'), ('gates', 'bundle-invalid'),
    ('context', 'context-load-failed'), ('drain', 'drain-failed'),
    ('sync', 'sync-failed'), ('inventory', 'inventory-mismatch'),
    ('drain', 'drain-aborted'),
])
def test_failure_reason_journal_db_replay(publisher, tmp_path, monkeypatch, stage, reason):
    from docassemble_executor.authority import RuntimeLock
    from docassemble_executor.journal import PHASE_INSTALLATION_PREPARING

    approve(publisher, ready(publisher))
    installation = installer(publisher)
    claim = installation.claim_installation('host')
    paths = replace(ExecutorPaths.from_env(), state_dir=tmp_path, journal_file=tmp_path / 'lifecycle.json',
                    run_dir=tmp_path / 'run', status_file=tmp_path / 'status.json',
                    repair_request_file=tmp_path / 'requests/repair.json', cache_dir=tmp_path / 'cache', log_dir=tmp_path / 'log')
    aborted = reason == 'drain-aborted'
    result = dict(receipt(claim, 'aborted-before-sync' if aborted else 'failed-stopped'),
                  failure={'stage': stage, 'reason': reason}, mutation_started=not aborted, child_safe=not aborted)
    journal = Journal(operation=Operation(id=claim.attempt_id, kind='published-materialization',
                                         revision=claim.revision_id, binding=claim.identity),
                      attempt_result=result, phase=PHASE_INSTALLATION_PREPARING if aborted else 'mutation_started')
    journal.save(paths.journal_file)
    monkeypatch.setattr('docassemble_executor.installation.sync_frozen', lambda *a, **k: pytest.fail('reinstalled'))
    poller = InstallationPoller(installation, paths, tmp_path, tmp_path / 'missing-baseline')
    if aborted:
        # Normal consumers may still run during both initial abort reporting and replay.
        with RuntimeLock(tmp_path, shared=True):
            assert poller.run_once() == 'reported'
            assert poller.run_once() == 'replayed'
    else:
        assert poller.run_once() == 'reported'
        assert poller.run_once() == 'replayed'
    current = snapshot(publisher)
    assert current['environment_state'][0]['active_attempt_id'] is None
    assert current['installation_attempt'][0]['record']['terminal'] == result
    assert current['installation_attempt'][0]['record']['phase'] == ('aborted' if aborted else 'failed')
    assert Journal.load(paths.journal_file).attempt_result == result


def test_terminal_rejects_unallowlisted_failure_reason(publisher):
    approve(publisher, ready(publisher))
    installation = installer(publisher)
    claim = installation.claim_installation('host')
    result = dict(receipt(claim, 'blocked'), failure={'stage': 'gates', 'reason': 'secret://user:password'})
    before = snapshot(publisher)
    with pytest.raises(PreparationError, match='invalid failure reason'):
        installation.report_terminal(result)
    assert snapshot(publisher) == before


def test_fixture_only_seed_is_explicit_idempotent_unknown_not_production(protocol, tmp_path):
    import importlib.util
    import json
    from pathlib import Path
    source = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location('fixture_seed', source / 'uv-experiment/fixture/managed_baseline.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = {'inventories': {'core-r1': {'distributions': {'fixture-core': '1'}, 'files': {}},
                               'runtime-r1': {'distributions': {'fixture-core': '1'}}},
                'identity': {'fixture-target': True}, 'uv_binary_sha256': 'a' * 64}
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(manifest))
    with protocol.engine.begin() as connection:
        connection.execute(protocol.tables['environment_state'].delete())
    module.seed(protocol.engine, 'test_', path)
    before = snapshot(protocol)
    assert before['environment_state'][0]['snapshot']['fixture_only'] is True
    assert before['environment_state'][0]['snapshot']['runtime']['kind'] == 'unknown'
    module.seed(protocol.engine, 'test_', path)
    assert snapshot(protocol) == before
    assert 'fixture_managed_baseline' not in (source / 'docassemble_webapp/docassemble/webapp/create_tables.py').read_text()


def test_running_without_local_terminal_is_fenced(publisher, tmp_path):
    approve(publisher, ready(publisher))
    installation = installer(publisher)
    installation.claim_installation('host')
    before = snapshot(publisher)
    with pytest.raises(PreparationError, match='uncertain'):
        installation.claim_installation('host')
    assert snapshot(publisher) == before


def test_uncertain_schema_forces_clearance_before_any_rerun(publisher, tmp_path, monkeypatch):
    # 14b: a prior uncertain schema outcome blocks a new attempt before mutation
    # and records an allowlisted blocked terminal; ordinary Retry cannot clear it.
    from docassemble_executor.installation import schema_clearance_file
    from docassemble_executor.journal import PHASE_VERIFIED, SchemaWork
    approve(publisher, ready(publisher))
    installation = installer(publisher)
    paths = replace(ExecutorPaths.from_env(), state_dir=tmp_path, journal_file=tmp_path / 'lifecycle.json',
                    run_dir=tmp_path / 'run', status_file=tmp_path / 'status.json',
                    repair_request_file=tmp_path / 'requests/repair.json', cache_dir=tmp_path / 'cache', log_dir=tmp_path / 'log')
    journal = Journal(phase=PHASE_VERIFIED,
                      schema=SchemaWork(attempt_id='prior-attempt', revision='prior-rev', identity='schema-id'))
    journal.save(paths.journal_file)
    monkeypatch.setattr('docassemble_executor.installation.sync_frozen', lambda *a, **k: pytest.fail('reinstalled'))
    poller = InstallationPoller(installation, paths, tmp_path, tmp_path / 'absent-baseline')
    # R3: the clearance gate refuses BEFORE consuming the authorized pending work, so
    # no blocked terminal is written and a later matching clearance can still resume it.
    assert poller.run_once() == 'schema-clearance-required'
    refused = Journal.load(paths.journal_file)
    assert refused.attempt_result is None
    assert refused.schema_uncertain
    # A clearance bound to a different attempt/schema never clears this work.
    schema_clearance_file(paths).write_text(json.dumps(
        {'format': 1, 'attempt_id': 'other', 'schema_identity': 'schema-id', 'observed_schema': 'r2'}))
    with pytest.raises(PreparationError, match='schema-clearance-required'):
        poller._require_schema_clearance(Journal.load(paths.journal_file))
    assert not Journal.load(paths.journal_file).schema_cleared
    # The work is still pending and still refuses without matching clearance.
    assert poller.run_once() == 'schema-clearance-required'
    assert Journal.load(paths.journal_file).attempt_result is None
