"""Managed API transports: real PostgreSQL, Redis and current helper source bodies.

The minimal Flask app excludes unrelated runtime plugins. Authentication executes
api_verify/get_api_info/get_api_key unchanged, with real ORM users/roles and Redis;
only login telemetry, encryption and the request-info logger are fixture seams.
"""
from contextlib import contextmanager
import datetime
import importlib.util
import json
import os
import re
import sys
from types import SimpleNamespace
import uuid

import pytest
from flask import Flask, current_app, g, render_template, request
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user
from sqlalchemy import MetaData, Table, select, text, update
from sqlalchemy.orm import Session, joinedload, registry, relationship

from docassemble.base.managed import ManagedOperationError, reject_legacy_operation
from docassemble.base.managed_preparation import Preparation, PreparationError
from test_preparation_postgres import protocol, result  # noqa: F401 -- pytest fixture
from test_publication_writers_postgres import publisher, adapter  # noqa: F401 -- pytest fixture
from test_publication_api_key_writers_postgres import functions


@pytest.fixture
def api_http(publisher, monkeypatch):  # noqa: F811
    import redis
    redis_client = redis.Redis.from_url(os.environ['DA_API_WRITER_REDIS_URL'])
    redis_client.flushdb()
    with publisher.engine.begin() as c:
        c.execute(text("INSERT INTO test_role VALUES (3,'custom')"))
        c.execute(text("INSERT INTO test_user VALUES (3,'three',true,'local$three')"))
        c.execute(text('INSERT INTO test_user_roles VALUES (3,3)'))
    metadata = MetaData()
    users, roles, membership = [Table('test_' + name, metadata, autoload_with=publisher.engine)
                                for name in ('user', 'role', 'user_roles')]
    class User(UserMixin):
        limited_api = False
        limits = []
        def has_role(self, name):
            return any(role.name == name for role in self.roles)
        def can_do(self, permission):
            return self.has_role('admin')
    class Role:
        pass
    mappings = registry()
    mappings.map_imperatively(Role, roles)
    mappings.map_imperatively(User, users, properties={'roles': relationship(Role, secondary=membership,
        primaryjoin=users.c.id == membership.c.user_id, secondaryjoin=roles.c.id == membership.c.role_id)})
    session = Session(publisher.engine)
    app = Flask(__name__)
    app.secret_key = 'disposable'
    app.config.update(ALLOW_UPDATES=True, DEVELOPER_CAN_INSTALL=True)
    manager = LoginManager(app)
    manager.user_loader(lambda identity: session.get(User, int(identity)))
    @app.route('/login/<int:identity>')
    def login(identity):
        login_user(session.get(User, identity))
        return 'ok'
    policy = {'custom': ['read_packages', 'manage_packages']}
    namespace = dict(json=json, re=re, request=request, current_app=current_app, g=g,
        current_user=current_user, login_user=login_user, select=select,
        UserModel=User, db=SimpleNamespace(session=session, joinedload=joinedload),
        r=redis_client, encrypt_api_key=lambda value, secret: value,
        logmessage=lambda *args: None, get_requester_ip=lambda req: req.remote_addr,
        update_last_login=lambda user: None, this_thread=SimpleNamespace(),
        current_info=lambda **kwargs: {})
    functions('docassemble_webapp/docassemble/webapp/api/helpers.py',
              {'get_api_key', 'get_api_info', 'api_verify'}, namespace)
    helper = SimpleNamespace(**{name: namespace[name] for name in ('get_api_key', 'get_api_info', 'api_verify')})
    modules = {
        'docassemble.webapp.extensions': SimpleNamespace(db=SimpleNamespace(engine=publisher.engine)),
        'docassemble.webapp.database': SimpleNamespace(dbtableprefix='test_'),
        'docassemble.webapp.config': SimpleNamespace(allowed=policy),
        'docassemble.webapp.api.helpers': helper,
        'docassemble.webapp.packages.managed': adapter,
        'docassemble.base.config': SimpleNamespace(daconfig={}),
    }
    import types
    webapp = types.ModuleType('docassemble.webapp')
    webapp.config = modules['docassemble.webapp.config']
    modules['docassemble.webapp'] = webapp
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    # Exercise production service construction, including its API permission callback.
    with publisher.engine.connect() as c:
        baseline = c.scalar(select(publisher.tables['environment_state'].c.snapshot))
    monkeypatch.setenv('DA_UV_MANAGED', 'true')
    monkeypatch.setenv('DA_QUALIFIED_IMAGE_BASELINE', '/fixture/explicit-baseline')
    monkeypatch.setattr(adapter, 'configured_baseline', lambda: baseline)
    routes = {'__name__': 'docassemble.webapp.packages.api',
              '__package__': 'docassemble.webapp.packages', 'managed_installation': lambda: True}
    functions('docassemble_webapp/docassemble/webapp/packages/api.py',
              {'api_package', 'api_package_update_status', 'api_package_preview'}, routes)
    # Real packages Blueprint, deployed server-side backend and lifecycle hook.
    # Unrelated lifecycle language/global helpers are inert fixture seams.
    from docassemblekvsession import KVSessionExtension
    from simplekv.memory import DictStore
    from flask_wtf import CSRFProtect
    from pathlib import Path
    source = Path(__file__).resolve().parents[3]

    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, source / path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module

    blueprint = load('docassemble.webapp.packages.blueprint',
        'docassemble_webapp/docassemble/webapp/packages/blueprint.py').packages_bp
    api_sessions = load('docassemble.webapp.api.session',
        'docassemble_webapp/docassemble/webapp/api/session.py')
    monkeypatch.setitem(sys.modules, 'docassemble.base.functions', SimpleNamespace(parse_accept_language=lambda _: []))
    monkeypatch.setitem(sys.modules, 'docassemble.base.language.control', SimpleNamespace(set_language=lambda _: None))
    monkeypatch.setitem(sys.modules, 'docassemble.base.language.words', SimpleNamespace(word=lambda value: value))
    monkeypatch.setitem(sys.modules, 'docassemble.base.thread_context', SimpleNamespace(
        empty_globals=lambda: {}, global_context=lambda _: contextmanager(lambda: iter([None]))()))
    monkeypatch.setitem(sys.modules, 'docassemble.webapp.interview.helpers', SimpleNamespace(get_part=lambda: None))
    modules['docassemble.webapp.config'].DEFAULT_LANGUAGE = 'en'
    modules['docassemble.webapp.config'].DEBUG = False
    modules['docassemble.webapp.config'].daconfig = {}
    lifecycle = load('managed_api_lifecycle', 'docassemble_webapp/docassemble/webapp/lifecycle.py')
    store = DictStore()
    KVSessionExtension().init_app(app, session_kvstore=store)
    lifecycle.init_app(app)
    assert isinstance(app.session_interface, api_sessions.APISessionInterface)
    csrf = CSRFProtect(app)
    app.jinja_loader = __import__('jinja2').FileSystemLoader(str(source /
        'docassemble_webapp/docassemble/webapp/packages/templates'))
    for path, name, methods in (
            ('/api/package', 'api_package', ['GET', 'POST', 'DELETE']),
            ('/api/package_update_status', 'api_package_update_status', ['GET']),
            ('/api/package_preview', 'api_package_preview', ['GET', 'POST'])):
        blueprint.add_url_rule(path, view_func=csrf.exempt(routes[name]), methods=methods)
    browser = {'__name__': 'docassemble.webapp.packages.views',
        '__package__': 'docassemble.webapp.packages', 'request': request,
        'managed_installation': lambda: True, 'render_template': render_template}
    functions('docassemble_webapp/docassemble/webapp/packages/views.py', {'package_preview'}, browser)
    blueprint.add_url_rule('/package_preview', view_func=login_required(browser['package_preview']), methods=['GET', 'POST'])
    app.register_blueprint(blueprint)
    # Existing transport tests focus semantic bodies, not CSRF. Enable it in
    # the session-scope regression below; never disable it in production.
    app.config['WTF_CSRF_ENABLED'] = False
    key = 'disposable-api-token'
    redis_key = 'da:apikey:userid:2:key:' + key + ':info'
    info = dict(permissions=['manage_packages', 'read_packages'], constraints=[], method='none', last_four='oken')
    redis_client.set(redis_key, json.dumps(info))
    client = app.test_client()
    client.get('/login/2')
    yield SimpleNamespace(client=client, service=publisher, key=key, redis_key=redis_key,
        redis=redis_client, info=info, policy=policy, helper=helper, namespace=namespace,
        modules=modules, session=session, store=store)
    session.close()
    redis_client.flushdb()
    mappings.dispose()


def call(http, path='/api/package_preview', body=None, method=None, **kwargs):
    return http.client.open(path, method=method or ('POST' if body is not None else 'GET'),
        json=body, headers={'X-API-Key': http.key}, **kwargs)


def prepare(http, **values):
    return call(http, body={'requirement': 'Example>=2', 'idempotency_key': 'prepare', **values})


def ready(http):
    reply = prepare(http)
    assert reply.status_code == 202, reply.json
    candidate = reply.json['candidate_id']
    claim = http.service.claim(candidate)
    assert http.service.publish_ready(claim, result(claim))
    return call(http, '/api/package_preview?candidate_id=' + candidate).json


def approval(preview, **values):
    return {'action': 'approve', 'candidate_id': preview['candidate_id'], 'result_digest': preview['result_digest'],
            'idempotency_key': 'approve', **values}


def snapshot(http):
    with http.service.engine.connect() as c:
        return {name: list(c.execute(text('SELECT * FROM test_' + name + ' ORDER BY 1')))
                for name in ('managed_environment_state', 'managed_candidate', 'managed_request_deduplication',
                    'managed_revision', 'managed_package_identity', 'managed_requirement_contribution',
                    'managed_installation_attempt', 'managed_publication_result', 'managed_retry_result',
                    'package', 'package_auth')}


def denied(reply, code='forbidden'):
    assert reply.status_code == (403 if code == 'forbidden' else 409), reply.data
    assert set(reply.json) == {'error', 'code'}
    assert reply.json['code'] == code


@pytest.mark.parametrize('revocation', ['role', 'account', 'permission', 'key', 'key_permission'])
def test_current_authority_rechecked_on_every_api_call(api_http, revocation):
    h = api_http
    if revocation == 'permission':
        h.redis_key = h.redis_key.replace('userid:2:', 'userid:3:')
        h.redis.delete(h.redis_key.replace('userid:3:', 'userid:2:'))
        h.redis.set(h.redis_key, json.dumps(h.info))
        # Custom roles have read authority but remain outside admin-only publication.
        with h.service.engine.begin() as c:
            c.execute(text('UPDATE test_user_roles SET role_id=2 WHERE user_id=3'))
    preview = ready(h)
    if revocation == 'permission':
        with h.service.engine.begin() as c:
            c.execute(text('UPDATE test_user_roles SET role_id=3 WHERE user_id=3'))
        # Isolate config-policy revocation from the role change: this custom
        # requester still has read/cancel permission before policy removal.
        assert call(h, '/api/package_preview?candidate_id=' + preview['candidate_id']).status_code == 200
    if revocation in ('role', 'account'):
        with h.service.engine.begin() as c:
            if revocation == 'account':
                c.execute(text('UPDATE test_user SET active=false WHERE id=2'))
            else:
                c.execute(text('DELETE FROM test_user_roles WHERE user_id=2'))
    elif revocation == 'permission':
        h.policy['custom'] = []
    elif revocation == 'key':
        h.redis.delete(h.redis_key)
    else:
        h.info['permissions'] = ['read_packages']
        h.redis.set(h.redis_key, json.dumps(h.info))
    before = snapshot(h)
    denied(call(h, body=approval(preview)))
    denied(prepare(h, idempotency_key='new'))
    denied(call(h, body={'action': 'cancel', 'candidate_id': preview['candidate_id']}))
    status = call(h, '/api/package_update_status?candidate_id=' + preview['candidate_id'])
    if revocation == 'key_permission':
        assert status.status_code == 200
    else:
        denied(status)
    assert snapshot(h) == before


def test_lost_response_idempotency_and_read_only_scope(api_http):
    h = api_http
    first = prepare(h)
    assert prepare(h).json['candidate_id'] == first.json['candidate_id']
    denied(prepare(h, requirement='Example>=3'), 'idempotency-conflict')
    preview = ready(h)
    approved = call(h, body=approval(preview))
    assert approved.status_code == 202, approved.json
    after = snapshot(h)
    assert call(h, body=approval(preview)).json == approved.json
    denied(call(h, body=approval(preview, result_digest='0' * 64)), 'idempotency-conflict')
    h.info['permissions'] = ['read_packages']
    h.redis.set(h.redis_key, json.dumps(h.info))
    for body in (approval(preview), approval(preview, result_digest='0' * 64),
                 {'action': 'cancel', 'candidate_id': preview['candidate_id']},
                 {'requirement': 'Example>=2', 'idempotency_key': 'other'},
                 {'action': 'retry', 'revision_id': approved.json['revision_id'], 'idempotency_key': 'retry'}):
        denied(call(h, body=body))
    for field in ('candidate_id', 'revision_id', 'attempt_id'):
        response = call(h, '/api/package_update_status?' + field + '=' + approved.json[field] + '&task_id=old-task')
        assert response.status_code == 200, response.json
        assert response.json['revision_id'] == approved.json['revision_id']
        assert response.json['attempt_id'] == approved.json['attempt_id']
    assert snapshot(h) == after


@pytest.mark.parametrize('constraint', ['ip', 'referer', 'redis', 'redis-after-auth'])
def test_constraints_and_redis_loss_fail_closed(api_http, constraint, monkeypatch):
    h = api_http
    preview = ready(h)
    if constraint.startswith('redis'):
        verify = h.helper.api_verify
        calls = []
        def outage():
            calls.append(True)
            if constraint == 'redis-after-auth' and len(calls) == 1:
                return verify()
            raise ConnectionError('SQL secret-token /restricted/bundle Traceback')
        monkeypatch.setattr(h.helper, 'api_verify', outage)
    else:
        h.info.update(method=constraint, constraints=['192.0.2.1' if constraint == 'ip' else 'trusted.invalid'])
        h.redis.set(h.redis_key, json.dumps(h.info))
    before = snapshot(h)
    denied(call(h, body=approval(preview)))
    denied(call(h, '/api/package_update_status?candidate_id=' + preview['candidate_id']))
    assert snapshot(h) == before


@pytest.mark.parametrize('fault', ['stale', 'expired', 'busy'])
def test_distinguishable_publication_conflicts(api_http, fault):
    h = api_http
    preview = ready(h)
    with h.service.engine.begin() as c:
        if fault == 'stale':
            c.execute(update(h.service.tables['environment_state']).values(runtime_generation=2))
        elif fault == 'expired':
            c.execute(text('ALTER TABLE test_managed_candidate DISABLE TRIGGER USER'))
            c.execute(text("UPDATE test_managed_candidate SET expires_at=clock_timestamp()-interval '1 second'"))
            c.execute(text('ALTER TABLE test_managed_candidate ENABLE TRIGGER USER'))
        else:
            c.execute(h.service.tables['installation_attempt'].insert().values(id='occupied', revision_id='baseline', record={}))
            c.execute(update(h.service.tables['environment_state']).values(active_attempt_id='occupied'))
    before = snapshot(h)
    denied(call(h, body=approval(preview)), fault)
    assert snapshot(h) == before


@pytest.mark.parametrize('path,method,body', [
    ('/api/package', 'POST', {'pip': 'Example>=2'}),
    ('/api/package', 'DELETE', {'package': 'Example', 'contribution_id': 'explicit'}),
    ('/api/package_preview', 'POST', {'action': 'prepare', 'requirement': 'Example>=2'}),
    ('/api/package_preview', 'POST', {'action': 'approve'}),
    ('/api/package_preview', 'POST', {'action': 'retry'}),
    ('/package_preview', 'POST', {'action': 'prepare', 'requirement': 'Example>=2'}),
    ('/package_preview', 'POST', {'action': 'approve'}),
    ('/package_preview', 'POST', {'action': 'retry'}),
])
@pytest.mark.parametrize('encoding', ['json', 'form'])
def test_restart_false_never_creates_candidate_or_intent(api_http, path, method, body, encoding):
    h = api_http
    before = snapshot(h)
    values = dict(body, restart=False if encoding == 'json' else 'false', idempotency_key='restart')
    reply = h.client.open(path, method=method, headers={'X-API-Key': h.key},
        **({'json': values} if encoding == 'json' else {'data': values}))
    denied(reply, 'restart-required')
    assert 'prepare' in reply.json['error'] and 'separate explicit approval' in reply.json['error']
    assert snapshot(h) == before


def test_legacy_prepare_shim_add_update_remove_without_early_intent(api_http):
    h = api_http
    before = snapshot(h)
    reply = call(h, '/api/package', {'pip': 'Example>=2', 'idempotency_key': 'shim'})
    assert reply.status_code == 202, reply.json
    assert reply.json['candidate_id'] == call(h, '/api/package', {'pip': 'Example>=2', 'idempotency_key': 'shim'}).json['candidate_id']
    after = snapshot(h)
    assert {k: v for k, v in before.items() if k not in ('managed_candidate', 'managed_request_deduplication')} == {
        k: v for k, v in after.items() if k not in ('managed_candidate', 'managed_request_deduplication')}
    preview = ready(h)
    assert call(h, body=approval(preview)).status_code == 202
    with h.service.engine.connect() as c:
        contribution = c.scalar(select(h.service.tables['requirement_contribution'].c.id).where(
            h.service.tables['requirement_contribution'].c.origin == 'interactive'))
    before = snapshot(h)
    assert call(h, '/api/package', {'update': 'Example', 'idempotency_key': 'update'}).status_code == 202
    assert call(h, '/api/package', method='DELETE', query_string={'package': 'Example',
        'contribution_id': contribution, 'idempotency_key': 'remove'}).status_code == 202
    denied(call(h, '/api/package', method='DELETE', query_string={'package': 'Example', 'idempotency_key': 'missing'}), 'rejected')
    after = snapshot(h)
    assert {k: v for k, v in before.items() if k not in ('managed_candidate', 'managed_request_deduplication')} == {
        k: v for k, v in after.items() if k not in ('managed_candidate', 'managed_request_deduplication')}


@pytest.mark.parametrize('fault', ['invalid', 'missing', 'foreign', 'github', 'zip', 'unknown', 'service', 'sql'])
def test_errors_are_redacted(api_http, fault, monkeypatch):
    h = api_http
    secret = 'secret-token SQL Traceback /restricted/bundle Example>=2'
    before = snapshot(h)
    if fault in ('invalid', 'missing'):
        reply = call(h, '/api/package_update_status?candidate_id=' + (secret if fault == 'invalid' else str(uuid.uuid4())))
    elif fault == 'foreign':
        candidate = h.service.prepare('1', 'Example>=2', 'foreign')
        # Actor 2 is a current admin; requester-only behavior needs a non-admin reader.
        with h.service.engine.begin() as c:
            c.execute(text('UPDATE test_user_roles SET role_id=1 WHERE user_id=2'))
        before = snapshot(h)
        reply = call(h, '/api/package_preview?candidate_id=' + candidate)
    elif fault in ('github', 'zip', 'unknown'):
        reply = call(h, '/api/package', {dict(github='github_url', zip='zip', unknown='unknown')[fault]: secret,
                                        'idempotency_key': 'redacted'})
    elif fault == 'service':
        # Fixed service diagnostics are the actionable transport contract.
        def failing(*args, **kwargs):
            raise PreparationError('unsupported registry requirement syntax')
        monkeypatch.setattr(adapter.QualifiedPublication, 'prepare', failing)
        reply = prepare(h)
    else:
        def failing(*args, **kwargs):
            raise RuntimeError(secret)
        monkeypatch.setattr(adapter.QualifiedPublication, 'prepare', failing)
        reply = prepare(h)
    denied(reply, 'forbidden' if fault == 'foreign' else 'rejected')
    if fault == 'service':
        assert reply.json['error'] == 'unsupported registry requirement syntax'
    assert all(value not in reply.get_data(as_text=True) for value in (secret, h.key, 'Example', 'SQL', 'Traceback', '/restricted/'))
    assert snapshot(h) == before


def test_legacy_task_payloads_rejected_and_task_status_never_consulted(api_http):
    h = api_http
    namespace = {'reject_legacy_operation': reject_legacy_operation}
    functions('docassemble_webapp/docassemble/webapp/tasks/tasks.py', {'reset_server', 'update_packages'}, namespace)
    for name, args, kwargs in (('reset_server', ({'ok': True},), {'run_create': True}),
                               ('update_packages', (), {'restart': False})):
        with pytest.raises(ManagedOperationError):
            namespace[name](*args, **kwargs)
    before = snapshot(h)
    # Legacy globals are deliberately absent from the extracted real route body:
    # consulting Celery or Redis task metadata would fail instead of returning this code.
    denied(call(h, '/api/package_update_status?task_id=old-queued-task'), 'rejected')
    assert snapshot(h) == before


def test_browser_and_api_have_identical_durable_publication(api_http, monkeypatch):
    h = api_http
    original_engine = h.service.engine
    fixed_time = datetime.datetime(2026, 10, 15, tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(Preparation, '_now', staticmethod(lambda connection: fixed_time))
    outcomes = []
    with original_engine.connect() as connection:
        class TransactionEngine:
            dialect = original_engine.dialect
            @contextmanager
            def begin(self):
                with connection.begin_nested():
                    yield connection
            @contextmanager
            def connect(self):
                yield connection
        proxy = TransactionEngine()
        monkeypatch.setattr(h.modules['docassemble.webapp.extensions'].db, 'engine', proxy)
        h.service.engine = proxy
        for path in ('/package_preview', '/api/package_preview'):
            transaction = connection.begin()
            counter = iter(range(1, 30))
            monkeypatch.setattr(uuid, 'uuid4', lambda: uuid.UUID(int=next(counter)))
            response = call(h, path, {'requirement': 'Example>=2', 'idempotency_key': 'identical'})
            assert response.status_code == 202, response.json
            candidate = response.json['candidate_id']
            claim = h.service.claim(candidate)
            assert h.service.publish_ready(claim, result(claim))
            preview = call(h, path + '?candidate_id=' + candidate).json
            response = call(h, path, approval(preview))
            assert response.status_code == 202, response.json
            outcomes.append((response.json,
                connection.scalar(select(h.service.tables['revision'].c.snapshot).where(
                    h.service.tables['revision'].c.id == response.json['revision_id'])),
                connection.scalar(select(h.service.tables['installation_attempt'].c.record)),
                list(connection.execute(text('SELECT name,type,active,limitation FROM test_package'))),
                list(connection.execute(text('SELECT package_id,user_id,authtype FROM test_package_auth')))))
            transaction.rollback()
            # PostgreSQL sequences are not transactional; reset only this disposable fixture.
            connection.execute(text('ALTER SEQUENCE test_package_id_seq RESTART WITH 1'))
            connection.commit()
    h.service.engine = original_engine
    assert outcomes[0] == outcomes[1]
    assert outcomes[0][0]['installation'] == 'pending'
    assert outcomes[0][1]['publication']['result']['bundle']['digest'] == 'd' * 64


@pytest.mark.parametrize('role', ['admin', 'developer'])
@pytest.mark.parametrize('scope', [[], ['manage_packages'], ['read_packages']])
def test_empty_legacy_scope_and_nonempty_scope_for_every_actor(api_http, role, scope):
    h = api_http
    preview = ready(h)
    if role == 'developer':
        with h.service.engine.begin() as c:
            c.execute(text('UPDATE test_user_roles SET role_id=1 WHERE user_id=2'))
    h.info['permissions'] = scope
    h.redis.set(h.redis_key, json.dumps(h.info))
    assert call(h, '/api/package_update_status?candidate_id=' + preview['candidate_id']).status_code == 200
    before = snapshot(h)
    response = call(h, body={'action': 'cancel', 'candidate_id': preview['candidate_id']})
    if scope == ['read_packages']:
        denied(response)
        assert snapshot(h) == before
    else:
        assert response.status_code == 200 and response.json['state'] == 'cancelled'
    if role == 'admin':
        response = prepare(h, idempotency_key='new-scope')
        if scope == ['read_packages']:
            denied(response)
        else:
            assert response.status_code == 202, response.json


def test_api_key_required_even_with_authenticated_browser_session(api_http):
    h = api_http
    before = snapshot(h)
    for path in ('/api/package', '/api/package_preview', '/api/package_update_status'):
        denied(h.client.get(path))
    assert snapshot(h) == before


def test_uploaded_sources_explicitly_rejected(api_http):
    import io
    h = api_http
    before = snapshot(h)
    response = h.client.post('/api/package', headers={'X-API-Key': h.key}, data={
        'idempotency_key': 'upload', 'pip': 'Example', 'file': (io.BytesIO(b'secret-token'), 'source.zip')})
    denied(response, 'rejected')
    assert snapshot(h) == before


def test_opaque_publication_identities_never_grant_authority(api_http):
    h = api_http
    preview = ready(h)
    approved = call(h, body=approval(preview)).json
    h.redis.delete(h.redis_key)
    h.redis_key = h.redis_key.replace('userid:2:', 'userid:1:')
    h.redis.set(h.redis_key, json.dumps(h.info))
    before = snapshot(h)
    for field in ('candidate_id', 'revision_id', 'attempt_id'):
        denied(call(h, '/api/package_update_status?' + field + '=' + approved[field]))
    for body in (approval(preview), {'action': 'cancel', 'candidate_id': preview['candidate_id']},
                 {'action': 'retry', 'revision_id': approved['revision_id'], 'idempotency_key': 'foreign-retry'}):
        denied(call(h, body=body))
    assert snapshot(h) == before


def test_safe_retry_replay_reauthorizes_without_new_attempt_or_solve(api_http):
    h = api_http
    preview = ready(h)
    approved = call(h, body=approval(preview)).json
    # TEST ONLY terminal drain-abort/slot release, matching the protocol harness.
    # The existing image flow proves actual admitted-consumer drain reporting.
    attempts = h.service.tables['installation_attempt']
    with h.service.engine.begin() as c:
        record = c.scalar(select(attempts.c.record).where(attempts.c.id == approved['attempt_id']))
        record.update(phase='failed', terminal={'outcome': 'aborted-before-sync', 'mutation_started': False,
            'child_safe': False, 'normal_verified': False,
            'failure': {'stage': 'drain', 'reason': 'drain-aborted'}})
        c.execute(update(attempts).where(attempts.c.id == approved['attempt_id']).values(record=record))
        c.execute(update(h.service.tables['environment_state']).values(active_attempt_id=None))
    body = {'action': 'retry', 'revision_id': approved['revision_id'], 'idempotency_key': 'safe-retry'}
    response = call(h, body=body)
    assert response.status_code == 202, response.json
    assert response.json['revision_id'] == approved['revision_id']
    assert response.json['attempt_id'] != approved['attempt_id']
    after = snapshot(h)
    assert call(h, body=body).json == response.json
    denied(call(h, body=dict(body, revision_id=str(uuid.uuid4()))), 'idempotency-conflict')
    h.info['permissions'] = ['read_packages']
    h.redis.set(h.redis_key, json.dumps(h.info))
    denied(call(h, body=body))
    denied(call(h, body=dict(body, revision_id=str(uuid.uuid4()))))
    assert snapshot(h) == after


def test_scoped_api_key_never_mints_or_accepts_browser_session_with_csrf(api_http):
    h = api_http
    app = h.client.application
    app.config['WTF_CSRF_ENABLED'] = True
    h.info['permissions'] = ['read_packages']
    h.redis.set(h.redis_key, json.dumps(h.info))
    anonymous = app.test_client()
    before = snapshot(h)
    stored = set(h.store.iter_keys())
    # Authentication runs even though no status identity was supplied.
    response = anonymous.get('/api/package_update_status', headers={'X-API-Key': h.key})
    assert response.status_code == 409
    assert 'Set-Cookie' not in response.headers
    assert set(h.store.iter_keys()) == stored
    with anonymous.session_transaction() as session:
        assert '_user_id' not in session
    assert anonymous.get('/package_preview').status_code == 401
    # A real CSRF token is not authentication. Borrow one from the independently
    # logged-in client, then prove cookie-only anonymous mutation remains denied.
    page = h.client.get('/package_preview')
    assert page.status_code == 200
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    with h.client.session_transaction() as session:
        csrf_seed = session['csrf_token']
    with anonymous.session_transaction() as session:
        session['csrf_token'] = csrf_seed
    for revoked in (False, True):
        if revoked:
            h.redis.delete(h.redis_key)
        response = anonymous.post('/package_preview', data={'csrf_token': token,
            'requirement': 'Example>=2', 'idempotency_key': 'scope-escape'})
        assert response.status_code == 401
        assert snapshot(h) == before
    # The API fence neither consumes nor overwrites an independent browser login,
    # and rejects cookie / X-User-Auth / remember-token authority on API requests.
    cookie = h.client.get_cookie(app.config['SESSION_COOKIE_NAME']).value
    assert h.client.get('/api/package_preview', headers={'X-User-Auth': cookie}).status_code == 403
    assert h.client.get('/package_preview').status_code == 200
    h.redis.set(h.redis_key, json.dumps(h.info))
    denied(h.client.post('/api/package_preview', json={'requirement': 'Example',
        'idempotency_key': 'read-only'}, headers={'X-API-Key': h.key}))
    assert snapshot(h) == before
    response = h.client.post('/package_preview', data={'csrf_token': token,
        'requirement': 'Example>=2', 'idempotency_key': 'real-browser'})
    assert response.status_code == 202, response.json
