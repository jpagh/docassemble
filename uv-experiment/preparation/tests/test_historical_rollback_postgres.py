"""Historical intent/ownership/publication invariants on real PostgreSQL."""
# ruff: noqa: F811
import copy

import pytest
from sqlalchemy import select, text, update

from test_preparation_postgres import protocol, snapshot, result  # noqa: F401
from test_publication_writers_postgres import publisher, adapter, intent  # noqa: F401
from docassemble.base.managed_preparation import PreparationError
from docassemble.base.managed_rollback import result_for


def publish_add(service, actor, declaration, key):
    candidate = service.prepare(actor, declaration, key)
    claim = service.claim(candidate)
    prepared = result(claim)
    inventory = prepared['bundle']['inventory']
    for field, before in [('desired_diff', claim.inputs['baseline']['desired_inventory']),
                          ('runtime_diff', claim.inputs['baseline']['runtime']['inventory'])]:
        prepared[field] = {name: {'before': before.get(name), 'after': inventory.get(name)}
                           for name in sorted(set(before) | set(inventory)) if before.get(name) != inventory.get(name)}
    assert service.publish_ready(claim, prepared)
    return service.approve(actor, candidate, service.status(actor, candidate)['result_digest'], key)


def free_slot(service):
    # Fixture simulation only: lifecycle reporting has its own executor tests.
    with service.engine.begin() as connection:
        connection.execute(update(service.tables['environment_state']).values(active_attempt_id=None))


def history(service):
    first = publish_add(service, '1', 'Example>=2', 'first')
    free_slot(service)
    publish_add(service, '1', 'Example<4', 'intervening')
    free_slot(service)
    return first['revision_id']


def ready(service, revision, assignments=None):
    candidate = service.prepare('2', None, 'rollback', operation='rollback', rollback_revision=revision,
                                assign_owners=assignments)
    claim = service.claim(candidate)
    assert service.publish_ready(claim, result_for(claim.inputs))
    return candidate, service.status('2', candidate)['result_digest']


def test_rollback_restores_historical_intent_preserves_current_grants_and_settings(publisher):
    revision = history(publisher)
    before = snapshot(publisher)
    grants = intent(publisher)[1]
    candidate, digest = ready(publisher, revision)
    preview = publisher.status('2', candidate)['preview']
    assert preview['validation']['no_solve']
    assert sorted(preview['rollback']['requirements']['after']) == sorted(['Separate[extra]<2; os_name == "posix"', 'Example>=2'])
    assert 'Example<4' in preview['rollback']['requirements']['before']
    with pytest.raises(PreparationError, match='acknowledgement'):
        publisher.approve('2', candidate, digest, 'rollback-approve')
    assert snapshot(publisher) == before
    response = publisher.approve('2', candidate, digest, 'rollback-approve', acknowledge_database_unchanged=True)
    assert publisher.approve('2', candidate, digest, 'rollback-approve', acknowledge_database_unchanged=True) == response
    after = snapshot(publisher)
    state = after['environment_state'][0]
    assert state['desired_revision_id'] == response['revision_id'] != revision
    assert state['snapshot']['schema_mode'] == 'unchanged'
    assert state['snapshot']['config'] == before['environment_state'][0]['snapshot']['config']
    assert intent(publisher)[1] == grants
    assert after['package_identity'] == before['package_identity']
    assert [row['declaration'] for row in after['requirement_contribution'] if row['active']] == preview['rollback']['requirements']['after']
    historical = next(row['snapshot'] for row in after['revision'] if row['id'] == revision)
    assert state['snapshot']['publication']['result']['bundle'] == historical['publication']['result']['bundle']
    assert publisher.publication_status('2', revision_id=response['revision_id'])['installation'] == 'pending'


def test_deleted_owner_requires_explicit_assignment_and_never_restores_account(publisher):
    revision = history(publisher)
    with publisher.authority_write() as connection:
        connection.execute(text('DELETE FROM test_package_auth WHERE user_id=1'))
        connection.execute(text('DELETE FROM test_user_roles WHERE user_id=1'))
        connection.execute(text('DELETE FROM test_user WHERE id=1'))
        connection.execute(update(publisher.tables['package_identity']).where(
            publisher.tables['package_identity'].c.normalized_name == 'example').values(owner_id=None))
    with pytest.raises(PreparationError, match='explicit assignment'):
        ready(publisher, revision)
    candidate, digest = ready(publisher, revision, ['example'])
    effects = publisher.status('2', candidate)['preview']['ownership_effects']['identities']
    assert any(row == {'owner_id': '2', 'assignment': True, 'name': 'example'} for row in effects.values())
    publisher.approve('2', candidate, digest, 'assign', acknowledge_database_unchanged=True)
    with publisher.engine.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM test_user WHERE id=1')) == 0
        assert connection.scalar(text('SELECT user_id FROM test_package_auth')) == 2


@pytest.mark.parametrize('mismatch', ['target', 'tool', 'image_baseline', 'missing'])
def test_ineligible_historical_revision_fails_without_intent_change(publisher, mismatch):
    revision = history(publisher)
    with publisher.engine.begin() as connection:
        historical = copy.deepcopy(connection.scalar(select(publisher.tables['revision'].c.snapshot).where(
            publisher.tables['revision'].c.id == revision)))
        if mismatch == 'missing':
            historical.pop('publication')
        else:
            historical[mismatch] = 'incompatible'
        connection.execute(publisher.tables['revision'].insert().values(id='ineligible', snapshot=historical))
    before = snapshot(publisher)
    with pytest.raises(PreparationError, match='historical'):
        ready(publisher, 'ineligible')
    assert snapshot(publisher) == before


def test_rollback_is_admin_only_and_exact_result_cannot_be_replaced(publisher):
    revision = history(publisher)
    with pytest.raises(PreparationError, match='forbidden'):
        publisher.prepare('1', None, 'not-admin', operation='rollback', rollback_revision=revision)
    candidate = publisher.prepare('2', None, 'exact', operation='rollback', rollback_revision=revision)
    claim = publisher.claim(candidate)
    prepared = copy.deepcopy(result_for(claim.inputs))
    prepared['bundle']['inventory']['example']['version'] = '99'
    with pytest.raises(PreparationError, match='exact rollback'):
        publisher.publish_ready(claim, prepared)
    assert publisher.status('2', candidate)['state'] == 'preparing'


def test_rollback_restores_only_managed_dependency_config_snapshot(publisher):
    revision = history(publisher)
    with publisher.authority_write() as connection:
        state = connection.execute(select(publisher.tables['environment_state'])).mappings().one()
        changed = dict(state['snapshot'], config={'committed': 'intervening-source-policy'},
                       unrelated_setting='must-survive')
        connection.execute(update(publisher.tables['environment_state']).values(snapshot=changed))
    candidate, digest = ready(publisher, revision)
    response = publisher.approve('2', candidate, digest, 'restore-dependency-policy', acknowledge_database_unchanged=True)
    with publisher.engine.connect() as connection:
        restored = connection.scalar(select(publisher.tables['revision'].c.snapshot).where(
            publisher.tables['revision'].c.id == response['revision_id']))
        historical = connection.scalar(select(publisher.tables['revision'].c.snapshot).where(
            publisher.tables['revision'].c.id == revision))
    assert restored['config'] == historical['config']
    assert restored['unrelated_setting'] == 'must-survive'


def test_qualified_rollback_grant_change_requires_fresh_preview(publisher):
    revision = history(publisher)
    with publisher.engine.connect() as connection:
        baseline = connection.scalar(select(publisher.tables['environment_state'].c.snapshot))
    service = adapter.QualifiedPublication(publisher.engine, publisher.authorize, publisher.project, 'test_', baseline)
    candidate, digest = ready(service, revision)
    assert service.status('2', candidate)['approval_supported']
    with service.authority_write() as connection:
        connection.execute(text("INSERT INTO test_package_auth (package_id,user_id,authtype) SELECT id,2,'viewer' FROM test_package"))
    before = snapshot(service), intent(service)
    with pytest.raises(PreparationError, match='approval authority changed'):
        service.approve('2', candidate, digest, 'stale-grant', acknowledge_database_unchanged=True)
    assert (snapshot(service), intent(service)) == before


def test_rollback_failure_keeps_desired_historical_intent_and_failure_evidence(publisher):
    from docassemble_executor.installation import Installation
    revision = history(publisher)
    candidate, digest = ready(publisher, revision)
    response = publisher.approve('2', candidate, digest, 'failed-rollback', acknowledge_database_unchanged=True)
    installation = Installation(publisher.engine, lambda *_: False, 'test_')
    claim = installation.claim_installation('rollback-host')
    terminal = {'identity': claim.identity, 'outcome': 'failed-stopped', 'child_safe': True,
                'normal_verified': False, 'mutation_started': True,
                'failure': {'stage': 'sync', 'reason': 'sync-failed'}}
    assert installation.report_terminal(terminal) == 'reported'
    state = snapshot(publisher)['environment_state'][0]
    assert state['desired_revision_id'] == response['revision_id']
    assert state['snapshot']['schema_mode'] == 'unchanged'
    assert state['snapshot']['publication']['result']['rollback']['revision_id'] == revision
    assert state['snapshot']['runtime']['kind'] == 'unknown'
    status = publisher.publication_status('2', revision_id=response['revision_id'])
    assert status['failure'] == terminal['failure'] and status['installation'] == 'failed'
    assert installation.report_terminal(terminal) == 'replayed'


def test_same_image_rollback_boot_uses_unchanged_schema_mode_not_old_setup_journal(publisher):
    from docassemble_executor.boot_observation import BootObservation
    from docassemble_executor.installation import Installation
    from docassemble_executor.journal import Journal, Operation
    from docassemble_executor.state import AppliedState
    from test_image_bridge_postgres import normal_receipt
    with publisher.authority_write() as connection:
        table = publisher.tables['environment_state']
        current = connection.scalar(select(table.c.snapshot))
        connection.execute(update(table).values(snapshot=dict(current, image_baseline={'fixture': True})))
    revision = history(publisher)
    candidate, digest = ready(publisher, revision)
    publisher.approve('2', candidate, digest, 'rollback-boot', acknowledge_database_unchanged=True)
    installation = Installation(publisher.engine, lambda *_: False, 'test_')
    claim = installation.claim_installation('rollback-host')
    receipt = normal_receipt(claim)
    installation.report_terminal(receipt)
    journal = Journal(phase='verified', verified_revision=claim.revision_id,
        operation=Operation(claim.attempt_id, 'published-apply', claim.revision_id, binding=claim.identity),
        attempt_result=receipt)
    assert journal.schema is None  # no setup was started or invented for rollback
    applied = AppliedState(revision=claim.revision_id)
    boot = BootObservation(publisher.engine, lambda *_: False, 'test_')
    observation = boot.reconcile('rollback-host', journal, applied)
    resumable = boot.resumable(observation, journal, applied)
    assert resumable and resumable['schema_mode'] == 'unchanged'


@pytest.mark.parametrize('transport', ['json', 'form'])
def test_qualified_http_rollback_acknowledgement_and_assignment_replay(publisher, monkeypatch, transport):
    from flask import Flask
    from flask_login import LoginManager, UserMixin
    revision = history(publisher)
    with publisher.authority_write() as connection:
        connection.execute(text('DELETE FROM test_package_auth WHERE user_id=1'))
        connection.execute(update(publisher.tables['package_identity']).where(
            publisher.tables['package_identity'].c.normalized_name == 'example').values(owner_id=None))
        baseline = connection.scalar(select(publisher.tables['environment_state'].c.snapshot))
    service = adapter.QualifiedPublication(publisher.engine, publisher.authorize, publisher.project, 'test_', baseline)
    app = Flask(__name__)
    app.secret_key = 'disposable-rollback-test'
    manager = LoginManager(app)

    class User(UserMixin):
        def __init__(self, identity):
            self.id = identity

    manager.user_loader(lambda identity: User(identity))
    app.add_url_rule('/preview', view_func=adapter.preview_request, methods=['GET', 'POST'])
    monkeypatch.setattr(adapter, 'preview_service', lambda api=False: service)
    monkeypatch.setenv('DA_UV_MANAGED', 'true')
    client = app.test_client()

    def post(body):
        return client.post('/preview', **({'json': body} if transport == 'json' else {'data': body}))

    body = {'operation': 'rollback', 'rollback_revision': revision, 'idempotency_key': 'http-rollback',
            'assign_owners': ['example'] if transport == 'json' else 'example'}
    with client.session_transaction() as session:
        session['_user_id'] = '1'
    assert post(body).status_code == 403
    with client.session_transaction() as session:
        session['_user_id'] = '2'
    response = post(body)
    assert response.status_code == 202, response.json
    candidate = response.json['candidate_id']
    claim = service.claim(candidate)
    assert service.publish_ready(claim, result_for(claim.inputs))
    preview = client.get('/preview', query_string={'candidate_id': candidate}).json
    assert preview['approval_supported'] and preview['preview']['rollback']['database_unchanged']
    approval = {'action': 'approve', 'candidate_id': candidate, 'result_digest': preview['result_digest'],
                'idempotency_key': 'http-rollback-approve'}
    assert post(approval).status_code == 409
    approval['acknowledge_database_unchanged'] = True if transport == 'json' else 'true'
    approved = post(approval)
    assert approved.status_code == 202, approved.json
    replay = post(approval)
    assert replay.status_code == 202 and replay.json == approved.json
    assert intent(service)[1][0]['user_id'] == 2
    with publisher.engine.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM test_user')) == 2
