"""Authenticated HTTP preview adapter + fresh SQL policy, real PostgreSQL only."""
import importlib.util
from pathlib import Path
import sys

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user
from sqlalchemy import select, text, update

from test_preparation_postgres import protocol, result, snapshot  # noqa: F401 -- imported pytest fixture
from docassemble.base.managed_preparation import Preparation, PreparationError

SOURCE = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('preview_adapter', SOURCE /
    'docassemble_webapp/docassemble/webapp/packages/managed.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


@pytest.fixture
def http(protocol, monkeypatch):  # noqa: F811 -- pytest fixture injection
    with protocol.engine.begin() as c:
        for sql in (
            'CREATE TABLE test_user (id integer PRIMARY KEY, active boolean, social_id text)',
            'CREATE TABLE test_role (id integer PRIMARY KEY, name text)',
            'CREATE TABLE test_user_roles (user_id integer, role_id integer)',
            'CREATE TABLE test_package (id integer PRIMARY KEY, name text, type text, core boolean)',
            'CREATE TABLE test_package_auth (package_id integer, user_id integer, authtype text)',
            "INSERT INTO test_user VALUES (1,true,'one'),(2,true,'two'),(3,true,'three')",
            "INSERT INTO test_role VALUES (1,'developer'),(2,'admin'),(3,'custom')",
            'INSERT INTO test_user_roles VALUES (1,1),(2,2),(3,3)',
        ):
            c.execute(text(sql))
    policy = {'allowed': {'custom': ['manage_packages', 'read_packages']},
              'allow_updates': True, 'developer_can_install': True}
    key = {'valid': True, 'permissions': ['manage_packages', 'read_packages']}
    auth = adapter.PreviewAuthorizer('test_', lambda: policy,
        lambda actor, permission, admin: key['valid'] and permission in key['permissions'])
    service = Preparation(protocol.engine, auth, 'test_')
    app = Flask(__name__)
    app.secret_key = 'disposable'
    manager = LoginManager(app)

    class User(UserMixin):
        def __init__(self, identity):
            self.id = identity
    manager.user_loader(lambda identity: User(identity))
    # Only identity is session-authenticated; role authority is SQL above.
    @app.route('/login/<identity>')
    def login(identity):
        login_user(User(identity))
        return 'ok'
    app.add_url_rule('/preview', view_func=adapter.preview_request, methods=['GET', 'POST'])
    monkeypatch.setattr(adapter, 'preview_service', lambda api=False: service)
    monkeypatch.setenv('DA_UV_MANAGED', 'true')
    client = app.test_client()
    client.get('/login/1')
    yield client, service, policy, key


def prepare(client, **values):
    return client.post('/preview', json={'requirement': 'Example>=2',
        'idempotency_key': 'http-key', **values})


def test_authenticated_adapter_no_intent_or_runtime_mutation(http):
    client, service, policy, key = http
    before = snapshot(service)
    reply = prepare(client)
    assert reply.status_code == 202
    candidate = reply.json['id']
    assert reply.json['preview'] is None
    claim = service.claim(candidate)
    assert service.publish_ready(claim, result(claim))
    preview = client.get('/preview', query_string={'candidate_id': candidate}).json
    assert preview['preview']['desired_diff'] == result(claim)['desired_diff']
    assert preview['preview']['runtime_diff'] == result(claim)['runtime_diff']
    assert not preview['approval_supported'] and not preview['installation_requested']
    for action in ('approve', 'retry', 'update', 'remove'):
        assert client.post('/preview', json={'action': action, 'candidate_id': candidate}).status_code == 409
    assert prepare(client, restart=False).status_code == 409
    assert snapshot(service) == before
    assert client.post('/preview', json={'action': 'cancel', 'candidate_id': candidate}).json['state'] == 'cancelled'
    assert client.get('/preview', query_string={'candidate_id': candidate}).json['preview'] is None


@pytest.mark.parametrize('revocation', ['role', 'account', 'permission', 'key', 'key_permission'])
def test_fresh_revocation_denies_prepare_cancel_status(http, revocation):
    client, service, policy, key = http
    if revocation == 'permission':
        client.get('/login/3')
    candidate = prepare(client).json['id']
    if revocation == 'role':
        with service.engine.begin() as c:
            c.execute(text('DELETE FROM test_user_roles WHERE user_id=1'))
    elif revocation == 'account':
        with service.engine.begin() as c:
            c.execute(text('UPDATE test_user SET active=false WHERE id=1'))
    elif revocation == 'permission':
        policy['allowed']['custom'] = []
    elif revocation == 'key':
        key['valid'] = False
    else:
        key['permissions'] = []
    assert prepare(client, idempotency_key='fresh-key').status_code == 403
    assert client.get('/preview', query_string={'candidate_id': candidate}).status_code == 403
    assert client.post('/preview', json={'action': 'cancel', 'candidate_id': candidate}).status_code == 403


def test_owner_grant_alias_and_ownerless_fences(http):
    client, service, policy, key = http
    with service.engine.begin() as c:
        c.execute(text("INSERT INTO test_package VALUES (10,'ExAmPle','pip',false)"))
        c.execute(text("INSERT INTO test_package_auth VALUES (10,1,'owner')"))
    assert prepare(client).status_code == 202
    with service.engine.begin() as c:
        c.execute(text('DELETE FROM test_package_auth'))
    assert prepare(client, idempotency_key='revoked').status_code == 403
    client.get('/login/2')
    assert prepare(client, idempotency_key='ownerless-admin').status_code == 403
    with service.engine.begin() as c:
        c.execute(text("INSERT INTO test_package_auth VALUES (10,2,'owner')"))
    assert prepare(client, idempotency_key='admin-owned').status_code == 202
    identities = service.tables['package_identity']
    with service.engine.begin() as c:
        c.execute(identities.insert().values(id='ownerless', normalized_name='other', display_name='Other'))
    assert prepare(client, requirement='Other', idempotency_key='ownerless').status_code == 409


def test_status_requester_or_current_admin_only(http):
    client, service, policy, key = http
    candidate = prepare(client).json['id']
    client.get('/login/3')
    assert client.get('/preview', query_string={'candidate_id': candidate}).status_code == 403
    assert client.post('/preview', json={'action': 'cancel', 'candidate_id': candidate}).status_code == 403
    client.get('/login/2')
    assert client.get('/preview', query_string={'candidate_id': candidate}).status_code == 200
    assert client.post('/preview', json={'action': 'cancel', 'candidate_id': candidate}).json['state'] == 'cancelled'


@pytest.mark.parametrize('kind', ['unknown', 'core-only', 'development-override', 'normal'])
def test_runtime_warning_and_stale_status_truthfulness(http, kind):
    client, service, policy, key = http
    env = service.tables['environment_state']
    with service.engine.begin() as c:
        baseline = c.execute(text('SELECT snapshot FROM test_managed_environment_state')).scalar()
        baseline['runtime']['kind'] = kind
        c.execute(update(env).values(snapshot=baseline))
    candidate = prepare(client).json['id']
    claim = service.claim(candidate)
    ready = result(claim)
    if kind != 'normal':
        ready['runtime_diff'] = None
        ready['warnings'] = ['runtime-not-normally-verified']
    assert service.publish_ready(claim, ready)
    reply = client.get('/preview', query_string={'candidate_id': candidate}).json
    assert reply['runtime']['kind'] == kind
    assert reply['preview']['runtime_diff'] == ready['runtime_diff']
    assert reply['warnings'] == ready['warnings']
    with service.engine.begin() as c:
        c.execute(update(env).values(runtime_generation=2))
    reply = client.get('/preview', query_string={'candidate_id': candidate}).json
    assert reply['stale'] and reply['preview'] is None
    assert 'stale-preview' in reply['warnings']


def test_flag_off_and_anonymous(http, monkeypatch, tmp_path):
    client, service, policy, key = http
    monkeypatch.setenv('DA_UV_MANAGED', 'false')
    monkeypatch.setenv('DA_MANAGED_STATE_DIR', str(tmp_path))
    assert prepare(client).status_code == 404
    monkeypatch.setenv('DA_UV_MANAGED', 'true')
    with client.session_transaction() as session:
        session.clear()
    assert prepare(client).status_code == 403


def test_single_semantic_implementation_without_executor_dependency():
    from docassemble.base.managed_preparation import Preparation as shared
    from docassemble_executor.preparation import Preparation as independent
    assert adapter.Preparation is shared is independent
    source = (SOURCE / 'docassemble_base/docassemble/base/managed_preparation.py').read_text()
    assert 'docassemble_executor' not in source


def test_desired_and_runtime_relative_diff_are_distinct_and_expire(http):
    client, service, policy, key = http
    env = service.tables['environment_state']
    with service.engine.begin() as c:
        baseline = c.execute(text('SELECT snapshot FROM test_managed_environment_state')).scalar()
        baseline['runtime']['inventory']['old-runtime-only'] = {'version': '1', 'source_digest': 'a' * 64}
        c.execute(update(env).values(snapshot=baseline))
    candidate = prepare(client).json['id']
    claim = service.claim(candidate)
    ready = result(claim)
    ready['runtime_diff'] = dict(ready['desired_diff'], **{'old-runtime-only': {
        'before': claim.inputs['baseline']['runtime']['inventory']['old-runtime-only'], 'after': None}})
    assert service.publish_ready(claim, ready)
    reply = client.get('/preview', query_string={'candidate_id': candidate}).json
    assert reply['preview']['runtime_diff'] != reply['preview']['desired_diff']
    with service.engine.begin() as c:
        # Fixture-only timestamp aging, as in the existing protocol suite.
        c.execute(text('ALTER TABLE test_managed_candidate DISABLE TRIGGER USER'))
        c.execute(text("UPDATE test_managed_candidate SET expires_at=clock_timestamp()-interval '1 second'"))
        c.execute(text('ALTER TABLE test_managed_candidate ENABLE TRIGGER USER'))
    reply = client.get('/preview', query_string={'candidate_id': candidate}).json
    assert reply['state'] == 'expired' and reply['preview'] is None
    assert 'expired-preview' in reply['warnings']


def test_production_api_permission_callback_revalidates_and_fails_closed(http, monkeypatch):
    import types
    client, service, policy, key = http
    modules = {
        'docassemble.webapp.extensions': types.SimpleNamespace(db=types.SimpleNamespace(engine=service.engine)),
        'docassemble.webapp.database': types.SimpleNamespace(dbtableprefix='test_'),
        'docassemble.webapp.config': types.SimpleNamespace(allowed=policy['allowed']),
    }
    api = types.SimpleNamespace(api_verify=lambda: key['valid'],
        get_api_info=lambda **kw: {'permissions': key['permissions']}, get_api_key=lambda: 'disposable')
    modules['docassemble.webapp.api.helpers'] = api
    webapp = types.ModuleType('docassemble.webapp')
    webapp.config = modules['docassemble.webapp.config']
    modules['docassemble.webapp'] = webapp
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    from flask_login import login_user, UserMixin
    class User(UserMixin):
        id = '2'
    with client.application.test_request_context():
        login_user(User())
        client.application.config.update(ALLOW_UPDATES=True, DEVELOPER_CAN_INSTALL=True)
        production = adapter.preview_service(api=True)
        candidate = production.prepare('2', 'Example', 'production-api')
        assert production.status('2', candidate)['state'] == 'queued'
        key['permissions'] = ['read_packages']
        with pytest.raises(PreparationError, match='forbidden'):
            production.cancel('2', candidate)
        assert production.status('2', candidate)['state'] == 'queued'
        key['valid'] = False
        with pytest.raises(PreparationError, match='forbidden'):
            production.status('2', candidate)
        def outage():
            raise ConnectionError('disposable Redis outage')
        api.api_verify = outage
        with pytest.raises(PreparationError, match='forbidden'):
            production.prepare('2', 'Example', 'outage')


def test_web_import_with_executor_imports_blocked():
    import subprocess
    code = '''
import importlib.abc, sys
class NoExecutor(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.startswith('docassemble_executor'):
            raise ImportError('executor intentionally unavailable')
sys.meta_path.insert(0, NoExecutor())
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from docassemble.webapp.packages.managed import Preparation
from docassemble.base.managed_preparation import Preparation as shared
assert Preparation is shared
print('PASS web semantic import without executor package')
'''
    subprocess.run([sys.executable, '-I', '-c', code, str(SOURCE / 'docassemble_base'),
                    str(SOURCE / 'docassemble_webapp')], check=True)


def test_upgrade_prepare_is_admin_only_and_previews_without_mutation(http):
    client, service, policy, key = http
    body = {'operation': 'upgrade', 'release_mode': 'exact', 'release_version': '2.0.0',
            'idempotency_key': 'upgrade-key'}
    # Developer authority is preserved for ordinary operations but not upgrades.
    assert client.post('/preview', json=body).status_code == 403
    client.get('/login/2')
    reply = client.post('/preview', json=body)
    assert reply.status_code == 202, reply.json
    candidate = reply.json['id']
    with service.engine.begin() as connection:
        desired = connection.scalar(select(service.tables['environment_state'].c.desired_revision_id))
    preview = client.get('/preview', query_string={'candidate_id': candidate}).json
    assert preview['intent_change']['operation'] == 'upgrade'
    assert preview['intent_change']['release_selection'] == {'mode': 'exact', 'version': '2.0.0'}
    assert preview['intent_change']['requirement'] is None
    assert preview['preview'] is None and preview['approval_supported'] is False
    with service.engine.begin() as connection:
        assert connection.scalar(select(service.tables['environment_state'].c.desired_revision_id)) == desired
        assert connection.execute(select(service.tables['installation_attempt'])).first() is None
