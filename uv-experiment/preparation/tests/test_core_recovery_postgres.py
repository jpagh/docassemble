"""Attempt-bound stopped core recovery reporting under publication's PostgreSQL lock."""
# ruff: noqa: F811 -- explicit pytest fixture registration.
from copy import deepcopy

import pytest
from sqlalchemy import select, update

from test_preparation_postgres import protocol, result, snapshot  # noqa: F401
from test_publication_writers_postgres import publisher, adapter  # noqa: F401
from test_image_bridge_postgres import bridge, ready_bridge  # noqa: F401
from docassemble_executor.installation import Installation
from docassemble_executor.preparation import PreparationError


def recovery_receipt(claim, result='succeeded'):
    return {'identity': claim.identity, 'outcome': 'failed-stopped', 'mutation_started': True,
        'child_safe': True, 'normal_verified': False, 'failure': {'stage': 'sync', 'reason': 'sync-failed'},
        'recovery': {'id': claim.attempt_id + ':core:1', 'attempt': 1, 'result': result,
            'core_baseline': {'image_target': 'fixture-image', 'bundle': 'core-r1', 'bundle_digest': 'b' * 64},
            'inventory': {'core': '1.0'}}}


@pytest.mark.parametrize('result', ['succeeded', 'failed'])
def test_recovery_is_failed_attempt_not_intent_or_normal_success_and_replay_is_idempotent(bridge, result):
    client, service, _ = bridge
    response = client.post('/preview', json=ready_bridge(bridge)).json
    installation = Installation(service.engine, lambda *_: False, 'test_')
    claim = installation.claim_installation('host')
    before = snapshot(service)
    receipt = recovery_receipt(claim, result)
    assert installation.report_terminal(receipt) == 'reported'
    after = snapshot(service)
    assert installation.report_terminal(receipt) == 'replayed'
    assert snapshot(service) == after
    assert before['revision'] == after['revision']
    assert before['requirement_contribution'] == after['requirement_contribution']
    environment = after['environment_state'][0]
    assert environment['desired_revision_id'] == response['revision_id']
    assert environment['publication_generation'] == before['environment_state'][0]['publication_generation']
    assert environment['runtime_generation'] == claim.runtime_generation + 1
    assert environment['active_attempt_id'] is None
    assert after['installation_attempt'][0]['record']['phase'] == 'failed'
    status = service.publication_status('2', attempt_id=claim.attempt_id)
    assert status['installation'] == 'failed' and status['verified_running_revision_id'] is None
    assert status['last_recovery_id'] == receipt['recovery']['id']
    assert status['last_recovery_result'] == result
    assert status['runtime_kind'] == ('core-only' if result == 'succeeded' else 'unknown')
    assert status['recovery_core'] == (receipt['recovery']['core_baseline'] if result == 'succeeded' else None)
    # 13.2: a completed, child-safe recovery makes the retained revision exactly
    # retryable; an unsuccessful recovery does not.
    assert status['retry_supported'] is (result == 'succeeded')
    retry = client.post('/preview', json={'action': 'retry', 'revision_id': claim.revision_id, 'idempotency_key': 'uncertain'})
    if result == 'succeeded':
        # 13.2: a completed child-safe recovery makes the retained revision exactly
        # retryable. Child-safe completion is NOT schema/setup clearance - the
        # executor journal still refuses mutation without fresh operator clearance
        # (test_schema_journal.py::test_missing_clearance_blocks_rerun_*), so this
        # authorization decision is not the clearance gate.
        assert retry.status_code == 202
        assert retry.json['revision_id'] == claim.revision_id
        retried = snapshot(service)
        for name in ('revision', 'package_identity', 'requirement_contribution', 'host_environment'):
            assert retried[name] == after[name]
        attempt_id = retry.json['attempt_id']
        expected_environment = dict(after['environment_state'][0], active_attempt_id=attempt_id)
        assert retried['environment_state'] == [expected_environment]
        assert len(retried['installation_attempt']) == len(after['installation_attempt']) + 1
        assert [row for row in retried['installation_attempt'] if row['id'] != attempt_id] == after['installation_attempt']
        pending = next(row for row in retried['installation_attempt'] if row['id'] == attempt_id)
        assert pending['revision_id'] == claim.revision_id
        assert pending['record']['phase'] == 'pending' and pending['record']['reason'] == 'retry'
        assert pending['record']['fence'] == 0 and pending['record']['actor_id'] == '2'
        replay = client.post('/preview', json={
            'action': 'retry', 'revision_id': claim.revision_id, 'idempotency_key': 'uncertain'})
        assert replay.status_code == 202 and replay.json == retry.json
        assert snapshot(service) == retried  # Exactly one new attempt and slot, including idempotent replay.
    else:
        assert retry.status_code == 409
        assert snapshot(service) == after


@pytest.mark.parametrize('field,value,superseded', [
    ('id', 'other:core:1', False), ('attempt', 2, False), ('result', 'running', False),
    ('core_baseline', {'image_target': 'other'}, False),
    ('core_baseline', {'image_target': 'other'}, True)])
def test_invalid_or_foreign_recovery_evidence_cannot_release_attempt(bridge, field, value, superseded):
    client, service, _ = bridge
    client.post('/preview', json=ready_bridge(bridge))
    installation = Installation(service.engine, lambda *_: False, 'test_')
    claim = installation.claim_installation('host')
    receipt = recovery_receipt(claim)
    receipt['recovery'][field] = value
    if superseded:
        table = service.tables['environment_state']
        with service.engine.begin() as connection:
            state = connection.execute(select(table)).mappings().one()
            changed = deepcopy(state['snapshot'])
            changed['runtime'] = {'kind': 'development-override', 'inventory': {}}
            connection.execute(update(table).values(snapshot=changed, runtime_generation=claim.runtime_generation + 1))
    before = snapshot(service)
    with pytest.raises(PreparationError, match='recovery'):
        installation.report_terminal(receipt)
    assert snapshot(service) == before


def test_recovery_report_never_overwrites_newer_observation_or_releases_later_slot(bridge):
    client, service, _ = bridge
    client.post('/preview', json=ready_bridge(bridge))
    installation = Installation(service.engine, lambda *_: False, 'test_')
    claim = installation.claim_installation('host')
    table = service.tables['environment_state']
    with service.engine.begin() as connection:
        state = connection.execute(select(table)).mappings().one()
        changed = deepcopy(state['snapshot'])
        changed['runtime'] = {'kind': 'development-override', 'inventory': {}}
        connection.execute(update(table).values(snapshot=changed, runtime_generation=99))
    receipt = recovery_receipt(claim)
    assert installation.report_terminal(receipt) == 'reported'
    with service.engine.begin() as connection:
        connection.execute(service.tables['installation_attempt'].insert().values(
            id='later', revision_id=claim.revision_id, record={'phase': 'running'}))
        connection.execute(update(table).values(active_attempt_id='later'))
    before = snapshot(service)
    assert installation.report_terminal(receipt) == 'replayed'
    assert snapshot(service) == before
    assert before['environment_state'][0]['snapshot']['runtime']['kind'] == 'development-override'
    assert before['environment_state'][0]['runtime_generation'] == 99
