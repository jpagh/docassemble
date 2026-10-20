"""Repair re-prepares committed desired intent against recovery; retry stays separate.

A repair is a true re-solve of every active contribution while the runtime is
unverified recovery state, producing a NEW revision/bundle. Recovery never
rewrites desired intent and a changed runtime observation invalidates approval.
"""
# ruff: noqa: F811 -- explicit pytest fixture registration.
import pytest

from test_preparation_postgres import protocol, result, snapshot  # noqa: F401
from test_publication_writers_postgres import publisher, adapter, intent  # noqa: F401
from test_image_bridge_postgres import bridge, ready_bridge  # noqa: F401
from docassemble.base.managed_preparation import Preparation
from docassemble_executor.installation import Installation
from docassemble_executor.preparation import PreparationError


def recovery_receipt(claim, result='succeeded'):
    return {'identity': claim.identity, 'outcome': 'failed-stopped', 'mutation_started': True,
        'child_safe': True, 'normal_verified': False, 'failure': {'stage': 'sync', 'reason': 'sync-failed'},
        'recovery': {'id': claim.attempt_id + ':core:1', 'attempt': 1, 'result': result,
            'core_baseline': {'image_target': 'fixture-image', 'bundle': 'core-r1', 'bundle_digest': 'b' * 64},
            'inventory': {'core': '1.0'}}}


def failed_recovery(bridge):
    """Publish Example, then report a real failed apply with stopped core recovery."""
    client, service, _ = bridge
    response = client.post('/preview', json=ready_bridge(bridge)).json
    installation = Installation(service.engine, lambda *_: False, 'test_')
    claim = installation.claim_installation('host')
    assert installation.report_terminal(recovery_receipt(claim)) == 'reported'
    return response, claim


def repair_result(claim):
    base = result(claim)
    inputs = claim.inputs
    inventory = base['bundle']['inventory']

    def diff(before):
        return {name: {'before': before.get(name), 'after': inventory.get(name)}
                for name in sorted(set(before) | set(inventory)) if before.get(name) != inventory.get(name)}

    base['desired_diff'] = diff(inputs['baseline']['desired_inventory'])
    base['runtime_diff'] = (diff(inputs['baseline']['runtime']['inventory'])
                            if inputs['baseline']['runtime']['kind'] == 'normal' else None)
    base['warnings'] = Preparation.ready_warnings(inputs)
    base['ownership_effects'] = inputs['ownership_effects']
    return base


def contributions(snapshot_doc):
    return snapshot_doc['environment_state'][0]['snapshot']['contributions']


def test_repair_republishes_desired_intent_as_new_revision_and_bundle(bridge):
    client, service, _ = bridge
    response, _ = failed_recovery(bridge)
    before = snapshot(service)
    prepared = client.post('/preview', json={'operation': 'repair', 'idempotency_key': 'repair'})
    assert prepared.status_code == 202, prepared.json
    candidate = prepared.json['id']
    status = client.get('/preview', query_string={'candidate_id': candidate}).json
    assert status['state'] == 'queued' and status['intent_change']['operation'] == 'repair'
    assert status['intent_change']['requirement'] is None
    claim = service.claim(candidate)
    assert service.publish_ready(claim, repair_result(claim))
    preview = client.get('/preview', query_string={'candidate_id': candidate}).json
    assert preview['approval_supported'] and not preview['stale']
    approved = client.post('/preview', json={'action': 'approve', 'candidate_id': candidate,
        'result_digest': preview['result_digest'], 'idempotency_key': 'approve-repair'})
    assert approved.status_code == 202, approved.json
    after = snapshot(service)
    environment = after['environment_state'][0]
    assert environment['desired_revision_id'] == approved.json['revision_id']
    # A new revision/bundle, same desired intent, and prior failure evidence retained.
    assert approved.json['revision_id'] != response['revision_id']
    assert contributions(after) == contributions(before)
    assert len(after['installation_attempt']) == 2
    assert after['installation_attempt'][0]['record']['terminal']['outcome'] == 'failed-stopped'
    status = service.publication_status('2', attempt_id=approved.json['attempt_id'])
    assert status['installation'] == 'pending' and not status['retry_supported']


def test_repair_requires_admin_and_unverified_runtime(bridge):
    client, service, _ = bridge
    client.post('/preview', json=ready_bridge(bridge)).json
    # A verified-normal runtime has nothing to repair; non-admins cannot repair at all.
    with pytest.raises(PreparationError, match='repair requires an unverified recovery runtime'):
        service.prepare('2', None, 'repair-normal', operation='repair')
    before = snapshot(service)
    with pytest.raises(PreparationError, match='forbidden'):
        service.prepare('1', None, 'repair-non-admin', operation='repair')
    assert snapshot(service) == before
    installation = Installation(service.engine, lambda *_: False, 'test_')
    claim = installation.claim_installation('host')
    assert installation.report_terminal(recovery_receipt(claim)) == 'reported'
    allowed = client.post('/preview', json={'operation': 'repair', 'idempotency_key': 'repair-unknown'})
    assert allowed.status_code == 202, allowed.json


def test_changed_runtime_generation_invalidates_repair_approval(bridge):
    client, service, _ = bridge
    failed_recovery(bridge)
    candidate = client.post('/preview', json={'operation': 'repair', 'idempotency_key': 'repair'}).json['id']
    claim = service.claim(candidate)
    assert service.publish_ready(claim, repair_result(claim))
    preview = client.get('/preview', query_string={'candidate_id': candidate}).json
    with service.engine.begin() as connection:
        environment = service.tables['environment_state']
        connection.execute(environment.update().values(runtime_generation=environment.c.runtime_generation + 1))
    before = snapshot(service)
    rejected = client.post('/preview', json={'action': 'approve', 'candidate_id': candidate,
        'result_digest': preview['result_digest'], 'idempotency_key': 'approve-repair'})
    assert rejected.status_code == 409
    assert snapshot(service) == before


def test_failed_recovery_records_failed_attempt_and_does_not_rewrite_desired(bridge):
    client, service, _ = bridge
    response = client.post('/preview', json=ready_bridge(bridge)).json
    installation = Installation(service.engine, lambda *_: False, 'test_')
    claim = installation.claim_installation('host')
    receipt = recovery_receipt(claim, result='failed')
    assert installation.report_terminal(receipt) == 'reported'
    after = snapshot(service)
    environment = after['environment_state'][0]
    assert environment['desired_revision_id'] == response['revision_id']
    assert environment['snapshot']['runtime']['kind'] == 'unknown'
    status = service.publication_status('2', attempt_id=claim.attempt_id)
    assert status['installation'] == 'failed' and status['last_recovery_result'] == 'failed'
    assert not status['retry_supported']
    # A failed recovery cannot authorize a no-solve retry of uncertain work.
    assert client.post('/preview', json={'action': 'retry', 'revision_id': response['revision_id'],
        'idempotency_key': 'uncertain'}).status_code == 409


@pytest.fixture
def recovery_http(bridge, monkeypatch, tmp_path):
    """Serve real recovery routes with fixture-only authentication/CSRF wiring."""
    import importlib.util
    from pathlib import Path
    import sys
    import types

    from flask import Flask
    from flask_login import LoginManager, UserMixin, login_required, login_user

    source = Path(__file__).resolve().parents[3]

    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, source / path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module

    webapp = types.ModuleType('docassemble.webapp')
    webapp.recovery_status = load('docassemble.webapp.recovery_status',
        'docassemble_webapp/docassemble/webapp/recovery_status.py')
    monkeypatch.setitem(sys.modules, 'docassemble.webapp', webapp)
    csrf = types.ModuleType('flask_wtf.csrf')
    csrf.generate_csrf = lambda: 'fixture-csrf'
    monkeypatch.setitem(sys.modules, 'flask_wtf.csrf', csrf)
    auth = types.ModuleType('docassemble_flask_user')
    auth.login_required = login_required
    auth.roles_required = lambda roles: login_required
    monkeypatch.setitem(sys.modules, 'docassemble_flask_user', auth)
    monkeypatch.setitem(sys.modules, 'docassemble.webapp.packages.managed', adapter)
    service = bridge[1]
    monkeypatch.setattr(adapter, 'internal_publication_service', lambda: service)
    monkeypatch.setenv('DA_RECOVERY_RUN_DIR', str(tmp_path / 'recovery'))
    recovery = load('repair_recovery', 'docassemble_webapp/docassemble/webapp/recovery.py')
    recovery.STATUS_FILE.parent.mkdir(parents=True)
    recovery.STATUS_FILE.write_text('{"publication": null}')
    app = Flask(__name__)
    app.secret_key = 'disposable'
    manager = LoginManager(app)

    class User(UserMixin):
        id = '2'
        email = 'admin@example.invalid'

    manager.user_loader(lambda _: User())
    app.register_blueprint(recovery.recovery_bp)
    app.add_url_rule('/config', endpoint='admin.config_page', view_func=lambda: '')
    app.add_url_rule('/logs', endpoint='logs.logs', view_func=lambda: '')
    client = app.test_client()
    with app.test_request_context():
        login_user(User())
        from flask import session
        with client.session_transaction() as saved:
            saved.update(session)
    return client, service


@pytest.fixture
def recovery_module(recovery_http):
    import sys

    return sys.modules['repair_recovery']


@pytest.mark.parametrize('qualified', [False, True])
def test_recovery_repair_published_csrf_only_prepares_candidate_without_legacy_request(
        bridge, recovery_http, recovery_module, monkeypatch, qualified):
    import json

    client, service = recovery_http
    failed_recovery(bridge)
    if qualified:
        monkeypatch.setenv('DA_QUALIFIED_IMAGE_BASELINE', '/fixture/baseline.json')
    else:
        monkeypatch.delenv('DA_QUALIFIED_IMAGE_BASELINE', raising=False)
    recovery_module.STATUS_FILE.write_text(json.dumps({'publication': {'desired_revision_id': 'published'}}))
    before = snapshot(service)
    response = client.post('/recovery/repair', data={'csrf_token': 'fixture-csrf'})
    assert response.status_code in (200, 302)
    with client.session_transaction() as session:
        candidate = session['recovery_repair_candidate']['candidate_id']
    status = service.status('2', candidate)
    assert status['state'] == 'queued'
    assert status['intent_change']['operation'] == 'repair'
    assert snapshot(service)['environment_state'] == before['environment_state']
    assert not recovery_module.REPAIR_REQUEST_FILE.exists()
    page = client.get('/recovery')
    assert 'Repair preview prepared' in page.text
    assert 'name="action" value="core-restore"' not in page.text


@pytest.mark.parametrize('projection', [
    {'publication': {'desired_revision_id': 'published'}}, {},
])
def test_recovery_repair_explicit_legacy_action_is_refused_for_published_or_unknown_work(
        recovery_http, recovery_module, monkeypatch, projection):
    import json

    client, service = recovery_http
    monkeypatch.setenv('DA_QUALIFIED_IMAGE_BASELINE', '/fixture/baseline.json')
    recovery_module.STATUS_FILE.write_text(json.dumps(projection))
    before = snapshot(service)
    response = client.post('/recovery/repair', data={'csrf_token': 'fixture-csrf', 'action': 'core-restore'})
    assert response.status_code == 409
    assert 'Published or unknown work requires managed retry or repair.' in response.text
    assert snapshot(service) == before
    assert not recovery_module.REPAIR_REQUEST_FILE.exists()
    assert 'name="action" value="core-restore"' not in client.get('/recovery').text


@pytest.mark.parametrize('projection', ['missing', 'unreadable', 'pre-publication'])
def test_recovery_repair_unknown_projection_never_enqueues_legacy_restore(
        bridge, recovery_http, recovery_module, projection):
    client, service = recovery_http
    failed_recovery(bridge)
    if projection == 'missing':
        recovery_module.STATUS_FILE.unlink()
    elif projection == 'unreadable':
        recovery_module.STATUS_FILE.write_text('not json')
    else:
        recovery_module.STATUS_FILE.write_text('{"desired_revision": "r2"}')
    assert recovery_module._published_work(recovery_module.read_status())
    assert 'name="action" value="core-restore"' not in client.get('/recovery').text
    assert client.post('/recovery/repair', data={'action': 'core-restore'}).status_code == 409
    assert client.post('/recovery/repair', data={'csrf_token': 'fixture-csrf'}).status_code == 302
    with client.session_transaction() as session:
        assert service.status('2', session['recovery_repair_candidate']['candidate_id'])['state'] == 'queued'
    assert not recovery_module.REPAIR_REQUEST_FILE.exists()


@pytest.mark.parametrize('qualified', [False, True])
@pytest.mark.parametrize('action', [None, 'core-restore'])
def test_recovery_repair_explicitly_unpublished_preserves_legacy_request(
        recovery_http, recovery_module, monkeypatch, qualified, action):
    import json

    client, service = recovery_http
    if qualified:
        monkeypatch.setenv('DA_QUALIFIED_IMAGE_BASELINE', '/fixture/baseline.json')
    else:
        monkeypatch.delenv('DA_QUALIFIED_IMAGE_BASELINE', raising=False)
    recovery_module.STATUS_FILE.write_text(json.dumps({
        'publication': None, 'desired_revision': 'r2', 'desired_revision_id': 'r2', 'applied_revision': 'r1'}))
    before = snapshot(service)
    page = client.get('/recovery')
    assert 'name="action" value="core-restore"' in page.text
    data = {'csrf_token': 'fixture-csrf'}
    if action is not None:
        data['action'] = action
    response = client.post('/recovery/repair', data=data)
    assert response.status_code in (200, 302)
    payload = json.loads(recovery_module.REPAIR_REQUEST_FILE.read_text())
    assert payload['requested_by'] == 'admin@example.invalid'
    assert payload['requested_at']
    assert snapshot(service) == before
    with client.session_transaction() as session:
        assert 'recovery_repair_candidate' not in session


def pending_repair(bridge, client):
    failed_recovery(bridge)
    candidate = bridge[1].prepare('2', None, 'repair', operation='repair')
    with client.session_transaction() as session:
        session['recovery_repair_candidate'] = {'candidate_id': candidate}
    return candidate


def test_recovery_page_renders_authorized_readiness_preview_before_approval(bridge, recovery_http):
    from html import unescape

    client, service = recovery_http
    candidate = pending_repair(bridge, client)
    queued = client.get('/recovery')
    assert queued.status_code == 200
    assert 'queued' in queued.text and 'Readiness' in queued.text
    assert 'name="action" value="approve"' not in queued.text
    claim = service.claim(candidate)
    assert service.publish_ready(claim, repair_result(claim))
    status = service.status('2', candidate)
    response = client.get('/recovery')
    page = unescape(response.text)
    assert response.status_code == 200
    assert 'no-store' in response.headers['Cache-Control']
    for value in ('Readiness', 'ready', 'Requirement and source-policy diff', 'requirements',
                  'before', 'after', 'Example>=2', 'source_policy', '"changed": false',
                  'Warnings', 'runtime-not-normally-verified', 'Desired-vs-actual-recovery comparison',
                  status['desired_revision_id'], 'core-only', 'Readiness digest', status['result_digest']):
        assert value in page, value
    diff = status['intent_change']['repair_diff']
    assert diff['requirements']['before'] == diff['requirements']['after']
    assert len(diff['requirements']['before']) == 2  # config and interactive intent
    assert 'name="action" value="approve"' in page
    # A changed runtime suppresses the ready preview and control on the next GET.
    with service.engine.begin() as connection:
        environment = service.tables['environment_state']
        connection.execute(environment.update().values(runtime_generation=environment.c.runtime_generation + 1))
    stale = client.get('/recovery')
    assert 'stale-preview' in stale.text
    assert 'name="action" value="approve"' not in stale.text


@pytest.mark.parametrize('failure', ['unauthorized', 'unavailable'])
def test_recovery_page_fails_closed_without_exposing_status_errors(bridge, recovery_http, monkeypatch, failure):
    from sqlalchemy import text

    client, service = recovery_http
    pending_repair(bridge, client)
    if failure == 'unauthorized':
        with service.engine.begin() as connection:
            connection.execute(text('DELETE FROM test_user_roles WHERE user_id=2'))
    else:
        def unavailable(*args):
            raise RuntimeError('SECRET_DATABASE_URL')
        monkeypatch.setattr(service, 'status', unavailable)
    response = client.get('/recovery')
    assert response.status_code == 200
    assert 'Repair preview unavailable or unauthorized. Approval is disabled.' in response.text
    assert 'SECRET_DATABASE_URL' not in response.text
    assert 'name="action" value="approve"' not in response.text


def test_other_administrator_can_retry_repair_but_cannot_approve_it(bridge):
    from sqlalchemy import text

    _, service, _ = bridge
    failed_recovery(bridge)
    with service.engine.begin() as connection:
        connection.execute(text('INSERT INTO test_user_roles VALUES (1,2)'))
    candidate = service.prepare('2', None, 'repair', operation='repair')
    claim = service.claim(candidate)
    assert service.publish_ready(claim, repair_result(claim))
    status = service.status('2', candidate)
    assert status['approval_supported']
    assert not service.status('1', candidate)['approval_supported']
    before = snapshot(service)
    with pytest.raises(PreparationError, match='forbidden'):
        service.approve('1', candidate, status['result_digest'], 'other-admin-approve')
    assert snapshot(service) == before
    published = service.approve('2', candidate, status['result_digest'], 'requester-approve')
    installation = Installation(service.engine, lambda *_: False, 'test_')
    attempt = installation.claim_installation('host')
    assert installation.report_terminal({'identity': attempt.identity, 'outcome': 'aborted-before-sync',
        'mutation_started': False, 'child_safe': False, 'normal_verified': False,
        'failure': {'stage': 'drain', 'reason': 'drain-aborted'}}) == 'reported'
    assert service.publication_status('1', revision_id=published['revision_id'])['retry_supported']
    retried = service.retry('1', published['revision_id'], 'other-admin-retry')
    assert retried['revision_id'] == published['revision_id']
    assert retried['attempt_id'] != published['attempt_id']
    assert retried['installation'] == 'pending'


@pytest.mark.parametrize('missing', ['digest', 'preview', 'approval'])
def test_recovery_page_requires_complete_approvable_preview(bridge, recovery_http, monkeypatch, missing):
    client, service = recovery_http
    candidate = pending_repair(bridge, client)
    claim = service.claim(candidate)
    assert service.publish_ready(claim, repair_result(claim))
    status = service.status('2', candidate)
    status[{'digest': 'result_digest', 'preview': 'preview', 'approval': 'approval_supported'}[missing]] = None
    monkeypatch.setattr(service, 'status', lambda *args: status)
    response = client.get('/recovery')
    assert response.status_code == 200
    assert 'Readiness' in response.text
    assert 'name="action" value="approve"' not in response.text


def test_repair_diff_preserves_all_requirements_and_redacts_source_policy():
    contributions = [{'origin': 'config', 'declaration': 'Config>=1'},
                     {'origin': 'interactive', 'declaration': 'Interactive>=2'}]
    diff = Preparation.config_diff({'change_operation': 'repair',
        'committed_contributions': contributions, 'contributions': contributions,
        'baseline': {'config': {'private_indexes': {'private': 'https://secret@example.invalid'},
            'private_bindings': {'config': 'private'}, 'private_tls_exceptions': ['private']}}})
    assert diff['requirements'] == {'before': ['Config>=1', 'Interactive>=2'],
                                    'after': ['Config>=1', 'Interactive>=2']}
    assert diff['source_policy'] == {'changed': False, 'supported_public_registry': False,
        'private_index_order': ['private'], 'private_bindings': {'config': 'private'},
        'cross_index_newest_version': False, 'insecure_tls_exceptions': ['private']}
    assert 'secret' not in str(diff) and 'https://' not in str(diff)
