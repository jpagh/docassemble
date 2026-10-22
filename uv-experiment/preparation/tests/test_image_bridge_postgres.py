"""Qualified public adapter/publication and normal observation, real PostgreSQL."""
# ruff: noqa: F811 -- explicit pytest fixture registration.
from copy import deepcopy

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user
from sqlalchemy import select, update, text

from test_preparation_postgres import protocol, result, snapshot  # noqa: F401
from test_publication_writers_postgres import publisher, adapter, intent  # noqa: F401
from docassemble_executor.installation import Installation
from docassemble_executor.preparation import PreparationError
from docassemble_executor.roles import RUNTIME_ROLES, INFRASTRUCTURE_ROLES


@pytest.fixture
def bridge(publisher, monkeypatch):
    environment = publisher.tables['environment_state']
    with publisher.engine.begin() as connection:
        baseline = dict(connection.scalar(select(environment.c.snapshot)))
        baseline['image_baseline'] = {'image_target': 'fixture-image', 'bundle': 'runtime-r1', 'bundle_digest': 'a' * 64}
        connection.execute(update(environment).values(snapshot=baseline))
    service = adapter.QualifiedPublication(publisher.engine, publisher.authorize,
        publisher.project, 'test_', baseline)
    app = Flask(__name__)
    app.secret_key = 'disposable'
    manager = LoginManager(app)
    class User(UserMixin):
        id = '2'
    manager.user_loader(lambda _: User())
    @app.route('/login')
    def login():
        login_user(User())
        return 'ok'
    app.add_url_rule('/preview', view_func=adapter.preview_request, methods=['GET', 'POST'])
    monkeypatch.setattr(adapter, 'preview_service', lambda api=False: service)
    monkeypatch.setenv('DA_UV_MANAGED', 'true')
    client = app.test_client()
    client.get('/login')
    return client, service, baseline


def ready_bridge(bridge):
    client, service, _ = bridge
    response = client.post('/preview', json={'requirement': 'Example>=2', 'idempotency_key': 'prepare'})
    assert response.status_code == 202, response.json
    candidate = response.json['id']
    claim = service.claim(candidate)
    assert service.publish_ready(claim, result(claim))
    preview = client.get('/preview', query_string={'candidate_id': candidate}).json
    assert preview['approval_supported']
    return {'action': 'approve', 'candidate_id': candidate, 'result_digest': preview['result_digest'],
            'idempotency_key': 'approve'}


def test_authenticated_adapter_publication_is_pending_not_success(bridge):
    client, service, _ = bridge
    request = ready_bridge(bridge)
    response = client.post('/preview', json=request)
    assert response.status_code == 202
    assert response.json['installation'] == 'pending'
    assert client.post('/preview', json=request).json == response.json
    status = client.get('/preview', query_string={'attempt_id': response.json['attempt_id']}).json
    assert status['installation'] == 'pending' and not status['retry_supported']
    assert len(intent(service)[0]) == len(intent(service)[1]) == 1


@pytest.mark.parametrize('change', ['account', 'other-role', 'other-grant', 'target', 'core'])
def test_changed_authority_or_baseline_rejects_public_approval_without_intent(bridge, change):
    client, service, baseline = bridge
    request = ready_bridge(bridge)
    with service.engine.begin() as connection:
        if change == 'account':
            connection.execute(text("UPDATE test_user SET active=false WHERE id=1"))
        elif change == 'other-role':
            connection.execute(text("INSERT INTO test_user_roles VALUES (1,2)"))
        elif change == 'other-grant':
            connection.execute(text("INSERT INTO test_package (id,name,type,core) VALUES (99,'unrelated','pip',false)"))
            connection.execute(text("INSERT INTO test_package_auth (package_id,user_id,authtype) VALUES (99,1,'owner')"))
        else:
            service.baseline = deepcopy(baseline)
            service.baseline[change] = 'changed'
    before = snapshot(service), intent(service)
    assert client.post('/preview', json=request).status_code == 409
    assert (snapshot(service), intent(service)) == before


def test_qualified_upgrade_preview_prepare_and_approve_guard(bridge):
    # Regression: the qualified prepare and GET-status/approve paths must handle an
    # upgrade candidate, which has no single requirement.
    client, service, _ = bridge
    response = client.post('/preview', json={'operation': 'upgrade', 'release_mode': 'exact',
        'release_version': '2.0.0', 'idempotency_key': 'upgrade'})
    assert response.status_code == 202, response.json
    candidate = response.json['id']
    assert response.json['intent_change']['operation'] == 'upgrade'
    preview = client.get('/preview', query_string={'candidate_id': candidate})
    assert preview.status_code == 200, preview.json
    assert preview.json['intent_change']['operation'] == 'upgrade'


@pytest.mark.parametrize('case', ['non-admin', 'core', 'ownerless'])
def test_unqualified_prepare_remains_rejected(bridge, case):
    _, service, _ = bridge
    actor, requirement, options = ('1' if case == 'non-admin' else '2'), 'Example>=2', {}
    if case == 'core':
        requirement = 'core==1.0'
    if case == 'ownerless':
        options['assign_owner'] = True
    before = snapshot(service), intent(service)
    with pytest.raises(PreparationError):
        service.prepare(actor, requirement, 'reject', **options)
    assert (snapshot(service), intent(service)) == before


def normal_receipt(claim):
    return {'identity': claim.identity, 'outcome': 'applied-normal', 'child_safe': True,
            'mutation_started': True, 'normal_verified': True, 'failure': None,
            'readiness': {'schema_verified': True, 'roles': sorted(RUNTIME_ROLES + INFRASTRUCTURE_ROLES)},
            'inventory': {name: value['version'] for name, value in claim.snapshot['publication']['result']['bundle']['inventory'].items()}}


def test_normal_report_advances_verified_not_desired_and_health_invalidation(bridge):
    client, service, _ = bridge
    response = client.post('/preview', json=ready_bridge(bridge)).json
    installation = Installation(service.engine, lambda *_: False, 'test_')
    claim = installation.claim_installation('host')
    receipt = normal_receipt(claim)
    assert installation.report_terminal(receipt) == 'reported'
    assert installation.report_terminal(receipt) == 'replayed'
    status = service.publication_status('2', attempt_id=response['attempt_id'])
    assert status['installation'] == 'verified-running'
    assert status['verified_running_revision_id'] == response['revision_id']
    assert not status['retry_supported']
    installation.invalidate_normal(claim.identity)
    status = service.publication_status('2', attempt_id=response['attempt_id'])
    assert status['installation'] == 'not-verified'
    assert status['desired_revision_id'] == response['revision_id']
    assert status['verified_running_revision_id'] is None
    assert status['last_normally_verified_revision_id'] == response['revision_id']


def test_only_confirmed_pre_sync_abort_allows_retry(bridge):
    client, service, _ = bridge
    response = client.post('/preview', json=ready_bridge(bridge)).json
    installation = Installation(service.engine, lambda *_: False, 'test_')
    claim = installation.claim_installation('host')
    retry = {'action': 'retry', 'revision_id': response['revision_id'], 'idempotency_key': 'retry'}
    assert client.post('/preview', json=retry).status_code == 409
    receipt = {'identity': claim.identity, 'outcome': 'aborted-before-sync', 'mutation_started': False,
               'child_safe': False, 'normal_verified': False, 'failure': {'stage': 'drain', 'reason': 'drain-aborted'}}
    installation.report_terminal(receipt)
    assert service.publication_status('2', attempt_id=claim.attempt_id)['retry_supported']
    retried = client.post('/preview', json=retry)
    assert retried.status_code == 202, retried.json
    assert client.post('/preview', json=retry).json == retried.json
    retry_claim = installation.claim_installation('host')
    assert retry_claim.attempt_id == retried.json['attempt_id']
    installation.report_terminal(dict(receipt, identity=retry_claim.identity, outcome='failed-stopped',
        mutation_started=True, child_safe=True, failure={'stage': 'sync', 'reason': 'sync-failed'}))
    assert not service.publication_status('2', attempt_id=retry_claim.attempt_id)['retry_supported']
    assert client.post('/preview', json=dict(retry, idempotency_key='uncertain')).status_code == 409


@pytest.mark.parametrize('missing', ['roles', 'schema', 'child', 'inventory', 'generation'])
def test_incomplete_normal_report_cannot_release_slot(bridge, missing):
    client, service, _ = bridge
    client.post('/preview', json=ready_bridge(bridge))
    installation = Installation(service.engine, lambda *_: False, 'test_')
    claim = installation.claim_installation('host')
    receipt = normal_receipt(claim)
    if missing == 'roles':
        receipt['readiness']['roles'].remove('celery')
    elif missing == 'schema':
        receipt['readiness']['schema_verified'] = False
    elif missing == 'child':
        receipt['child_safe'] = False
    elif missing == 'inventory':
        receipt['inventory']['example'] = '999'
    else:
        with service.engine.begin() as connection:
            connection.execute(update(service.tables['environment_state']).values(runtime_generation=999))
    before = snapshot(service)
    with pytest.raises(PreparationError):
        installation.report_terminal(receipt)
    assert snapshot(service) == before


def test_configured_old_target_refuses_publication_without_approval_or_apply(bridge, tmp_path, monkeypatch):
    import json
    import sys
    from docassemble.base.managed_qualification import configured_baseline
    client, service, baseline = bridge
    monkeypatch.setattr(sys, 'version_info', (3, 14, 4))
    document = {'format': 1, 'baseline': dict(baseline, target={'python': '3.12'})}
    path = tmp_path / 'baseline.json'
    path.write_text(json.dumps(document))
    monkeypatch.setenv('DA_QUALIFIED_IMAGE_BASELINE', str(path))
    monkeypatch.delenv('CONTAINERROLE', raising=False)
    before = snapshot(service), intent(service)
    def boundary(api=False):
        configured_baseline()
        pytest.fail('foreign configured baseline must not create publication service')
    monkeypatch.setattr(adapter, 'preview_service', boundary)
    response = client.post('/preview', json={'requirement': 'Example>=2', 'idempotency_key': 'old-target'})
    assert response.status_code == 409
    assert (snapshot(service), intent(service)) == before
