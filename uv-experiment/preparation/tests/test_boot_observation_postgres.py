"""Published boot observations use PostgreSQL, never fixture desired authority."""
# ruff: noqa: F811 -- pytest fixtures are explicitly imported for registration.
import copy

import pytest
from sqlalchemy import select, update

from test_preparation_postgres import protocol, snapshot  # noqa: F401
from test_publication_postgres import publisher, ready, approve  # noqa: F401
from docassemble_executor.boot_observation import BootObservation
from docassemble_executor.installation import Installation
from docassemble_executor.journal import Journal, Operation, SchemaWork
from docassemble_executor.state import AppliedState
from docassemble_executor.preparation import PreparationError


def observer(publisher):
    return BootObservation(publisher.engine, lambda *_: False, 'test_')


def test_published_boot_preserves_intent_pending_slot_and_history(publisher):
    response = approve(publisher, ready(publisher))
    table = publisher.tables['environment_state']
    with publisher.engine.begin() as connection:
        row = connection.execute(select(table)).mappings().one()
        value = dict(row['snapshot'], runtime={'kind': 'normal', 'revision_id': 'baseline', 'inventory': {}})
        connection.execute(update(table).values(snapshot=value))
    before = snapshot(publisher)
    observed = observer(publisher).reconcile('host', Journal(), AppliedState())
    after = snapshot(publisher)
    assert observed['desired_revision_id'] == response['revision_id']
    assert observed['installation'] == 'pending'
    assert observed['attempt_id'] == response['attempt_id']
    assert observed['verified_running_revision_id'] is None
    assert observed['last_normally_verified_revision_id'] == 'baseline'
    assert observed['runtime_kind'] == 'unknown'
    assert observed['normal_ready'] is False and observed['diverged']
    assert after['environment_state'][0]['active_attempt_id'] == response['attempt_id']
    assert after['revision'] == before['revision']
    assert after['installation_attempt'] == before['installation_attempt']
    assert after['requirement_contribution'] == before['requirement_contribution']
    assert after['package_identity'] == before['package_identity']
    assert after['environment_state'][0]['publication_generation'] == before['environment_state'][0]['publication_generation']
    assert after['host_environment'][0]['observation'] == observed
    assert observed['runtime_generation'] == before['environment_state'][0]['runtime_generation'] + 1
    again = observer(publisher).reconcile('host', Journal(), AppliedState())
    assert again['host_generation'] == 2
    assert again['runtime_generation'] == observed['runtime_generation']
    authorized = publisher.publication_status('alice', revision_id=response['revision_id'])
    for key in ('desired_revision_id', 'runtime_kind', 'runtime_generation',
                'verified_running_revision_id', 'last_normally_verified_revision_id'):
        assert authorized[key] == again[key]


def test_boot_observation_invalidates_prepared_preview(publisher):
    candidate = ready(publisher)
    observer(publisher).reconcile('host', Journal(), AppliedState())
    with pytest.raises(PreparationError, match='stale candidate'):
        approve(publisher, candidate)
    assert not snapshot(publisher)['installation_attempt']


def test_boot_does_not_replay_success_or_release_running_slot(publisher):
    response = approve(publisher, ready(publisher))
    installation = Installation(publisher.engine, lambda *_: False, 'test_')
    claim = installation.claim_installation('host')
    receipt = {'identity': claim.identity, 'outcome': 'applied-normal', 'normal_verified': True}
    journal = Journal(phase='verified', operation=Operation(claim.attempt_id, 'published-apply',
        claim.revision_id, binding=claim.identity), attempt_result=receipt, verified_revision=claim.revision_id)
    before = snapshot(publisher)
    value = observer(publisher).reconcile('host', journal, AppliedState(revision=claim.revision_id))
    after = snapshot(publisher)
    assert value['installation'] == 'uncertain'
    assert value['local_receipt_bound'] is True
    assert value['local_attempt_id'] == response['attempt_id']
    assert not value['diverged'] and not value['normal_ready']
    assert after['installation_attempt'] == before['installation_attempt']
    assert after['environment_state'][0]['active_attempt_id'] == claim.attempt_id
    assert journal.attempt_result == receipt


@pytest.mark.parametrize('kind', ['normal', 'core-only', 'development-override', 'unknown'])
def test_boot_never_confuses_prior_runtime_with_current_health(publisher, kind):
    response = approve(publisher, ready(publisher))
    table = publisher.tables['environment_state']
    with publisher.engine.begin() as connection:
        row = connection.execute(select(table)).mappings().one()
        runtime = {'kind': kind, 'revision_id': response['revision_id'], 'core_baseline': 'core-r1', 'inventory': {}}
        connection.execute(update(table).values(snapshot=dict(row['snapshot'], runtime=runtime)))
    journal = Journal(phase='recovery_succeeded' if kind == 'core-only' else 'override' if kind == 'development-override' else 'verified')
    value = observer(publisher).reconcile('host', journal, AppliedState(revision=response['revision_id']))
    assert value['runtime_kind'] == 'unknown'
    assert value['verified_running_revision_id'] is None
    assert value['recovery_core'] is None
    assert value['historical_runtime_kind'] == kind
    assert value['historical_recovery_core'] == ('core-r1' if kind == 'core-only' else None)
    assert value['local_recovery_recorded'] == (kind == 'core-only')
    assert value['recovery_available'] is False
    assert value['normal_ready'] is False


@pytest.mark.parametrize('phase', ['failed', 'blocked', 'aborted', 'stopped', 'succeeded'])
def test_terminal_boot_status_not_fresh_verification(publisher, phase):
    response = approve(publisher, ready(publisher))
    table = publisher.tables['environment_state']
    attempts = publisher.tables['installation_attempt']
    with publisher.engine.begin() as connection:
        row = connection.execute(select(attempts)).mappings().one()
        record = dict(row['record'], phase=phase, terminal={'failure': {'stage': 'gates', 'reason': 'bundle-invalid'}})
        connection.execute(update(attempts).values(record=record))
        connection.execute(update(table).values(active_attempt_id=None))
    value = observer(publisher).reconcile('host', Journal(), AppliedState())
    assert value['attempt_id'] == response['attempt_id']
    assert value['attempt_phase'] == phase
    assert value['installation'] == ('failed' if phase in ('failed', 'blocked', 'aborted') else 'not-verified')
    assert not value['active'] and not value['normal_ready']
    assert value['failure'] == {'stage': 'gates', 'reason': 'bundle-invalid'}


def test_missing_desired_rolls_back_observation(publisher):
    # Missing/invalid management state must not create a pretend empty baseline.
    with publisher.engine.begin() as connection:
        connection.execute(update(publisher.tables['environment_state']).values(desired_revision_id=None))
    before = copy.deepcopy(snapshot(publisher))
    with pytest.raises(PreparationError, match='desired revision missing'):
        observer(publisher).reconcile('host', Journal(), AppliedState())
    assert snapshot(publisher) == before


def completed_normal(publisher):
    from test_image_bridge_postgres import normal_receipt
    table = publisher.tables["environment_state"]
    with publisher.engine.begin() as connection:
        row = connection.execute(select(table)).mappings().one()
        connection.execute(update(table).values(snapshot=dict(row["snapshot"], image_baseline={"fixture": True})))
    approve(publisher, ready(publisher))
    installation = Installation(publisher.engine, lambda *_: False, "test_")
    claim = installation.claim_installation("host")
    receipt = normal_receipt(claim)
    installation.report_terminal(receipt)
    journal = Journal(phase="verified", verified_revision=claim.revision_id,
        operation=Operation(claim.attempt_id, "published-apply", claim.revision_id, binding=claim.identity), attempt_result=receipt,
        schema=SchemaWork(attempt_id=claim.attempt_id, revision=claim.revision_id, identity="schema-id", result="succeeded",
                          completed_at="2026-01-01T00:00:00+00:00"))
    return installation, claim, receipt, journal, AppliedState(revision=claim.revision_id)


def test_resumption_requires_reported_exact_desired_normal_and_fresh_observation(publisher):
    installation, claim, receipt, journal, applied = completed_normal(publisher)
    boot = observer(publisher)
    observation = boot.reconcile("host", journal, applied)
    before = snapshot(publisher)
    candidate = boot.resumable(observation, journal, applied)
    assert candidate and candidate["image_baseline"]
    resumed = boot.record_resumed("host", observation, receipt, candidate)
    after = snapshot(publisher)
    assert resumed["verified_running_revision_id"] == claim.revision_id
    assert resumed["normal_ready"] and resumed["reconciliation"] == "resumed-normal"
    assert resumed["runtime_generation"] == observation["runtime_generation"] + 1
    assert before["installation_attempt"] == after["installation_attempt"]
    assert before["revision"] == after["revision"]
    assert after["environment_state"][0]["active_attempt_id"] is None
    assert boot.resumable(observation, journal, applied) is None
    with pytest.raises(PreparationError, match="superseded"):
        boot.record_resumed("host", observation, receipt, candidate)


def test_resumption_requires_durable_schema_evidence(publisher):
    installation, claim, receipt, journal, applied = completed_normal(publisher)
    boot = observer(publisher)
    observation = boot.reconcile("host", journal, applied)
    before = snapshot(publisher)
    # A crash before the schema success marker (or a foreign attempt's evidence)
    # must not resume over unverified schema state.
    journal.schema = None
    assert boot.resumable(observation, journal, applied) is None
    journal.schema = SchemaWork(attempt_id="other", revision=claim.revision_id, identity="schema-id", result="succeeded")
    assert boot.resumable(observation, journal, applied) is None
    journal.schema = SchemaWork(attempt_id=claim.attempt_id, revision="other", identity="schema-id", result="succeeded")
    assert boot.resumable(observation, journal, applied) is None
    assert snapshot(publisher) == before


@pytest.mark.parametrize("change", ["active", "desired", "generation", "journal", "terminal", "applied"])
def test_resumption_never_clears_pending_divergence_or_uncertain_work(publisher, change):
    installation, claim, receipt, journal, applied = completed_normal(publisher)
    boot = observer(publisher)
    observation = boot.reconcile("host", journal, applied)
    table = publisher.tables["environment_state"]
    with publisher.engine.begin() as connection:
        if change == "active":
            connection.execute(update(table).values(active_attempt_id=claim.attempt_id))
        if change == "desired":
            connection.execute(update(table).values(desired_revision_id="baseline"))
        if change == "generation":
            connection.execute(update(table).values(runtime_generation=observation["runtime_generation"] + 1))
        if change == "terminal":
            row = connection.execute(select(installation.tables["installation_attempt"])).mappings().one()
            connection.execute(update(installation.tables["installation_attempt"]).values(record=dict(row["record"], phase="running")))
    if change == "journal":
        journal.phase = "mutation_started"
    if change == "applied":
        applied.revision = "other"
    before = snapshot(publisher)
    assert boot.resumable(observation, journal, applied) is None
    assert snapshot(publisher) == before


def test_live_status_invalidates_readiness_on_new_pending_and_preserves_slot(publisher, tmp_path):
    from dataclasses import replace
    from docassemble_executor.admission import Admission
    from docassemble_executor.durable_work import publish_live_status
    from docassemble_executor.paths import ExecutorPaths
    from docassemble_executor.status import write_status, read_status_file
    installation, claim, receipt, journal, applied = completed_normal(publisher)
    paths = replace(ExecutorPaths.from_env(), state_dir=tmp_path, run_dir=tmp_path, status_file=tmp_path / "status.json")
    Admission(tmp_path).open(consumers=True)
    write_status(paths, {"normal_ready": True, "applied_revision": claim.revision_id})
    publish_live_status(installation, paths)
    assert read_status_file(paths.status_file)["normal_ready"]
    table = publisher.tables["environment_state"]
    attempts = publisher.tables["installation_attempt"]
    with publisher.engine.begin() as connection:
        connection.execute(attempts.insert().values(id="next", revision_id=claim.revision_id, record={"phase": "pending", "created_at": "later"}))
        connection.execute(update(table).values(active_attempt_id="next"))
    publish_live_status(installation, paths)
    status = read_status_file(paths.status_file)
    assert not status["normal_ready"] and status["installation"] == "pending"
    assert Admission(tmp_path).state()["mode"] == "closed"
    before = snapshot(publisher)
    assert installation.report_terminal(receipt) == "replayed"
    assert snapshot(publisher) == before
    assert before["environment_state"][0]["active_attempt_id"] == "next"


def test_old_success_health_replay_cannot_claim_newer_running_observation(publisher):
    installation, claim, receipt, journal, applied = completed_normal(publisher)
    assert installation.normal_replay_allowed(receipt)
    table = publisher.tables["environment_state"]
    with publisher.engine.begin() as connection:
        connection.execute(installation.tables["installation_attempt"].insert().values(
            id="newer", revision_id=claim.revision_id, record={"phase": "running"}))
        connection.execute(update(table).values(active_attempt_id="newer"))
    before = snapshot(publisher)
    assert not installation.normal_replay_allowed(receipt)
    assert installation.report_terminal(receipt) == "replayed"
    assert snapshot(publisher) == before
