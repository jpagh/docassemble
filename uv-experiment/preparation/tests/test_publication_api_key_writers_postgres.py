"""Actual Redis writer source + PostgreSQL lock, not authenticated Flask requests.

Set DA_API_WRITER_REDIS_URL to a disposable Redis DB (it is flushed per test).
Without it these external-store integration cases explicitly skip.
"""
import ast
import codecs
import hashlib
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import text, select, update

from docassemble.base.managed_preparation import PreparationError
from test_preparation_postgres import protocol, result, snapshot  # noqa: F401
from test_publication_postgres import wait_for_approval_lock
from test_publication_writers_postgres import SOURCE, publisher, ready, approve, writers, load, adapter, intent  # noqa: F401

managed = load('api_writer_managed', 'docassemble_webapp/docassemble/webapp/utils/managed_api_keys.py')


def functions(path, names, namespace):
    nodes = [node for node in ast.parse((SOURCE / path).read_text()).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(nodes) == len(names)
    for node in nodes:
        node.decorator_list = [d for d in node.decorator_list
                               if isinstance(d, ast.Name) and d.id == 'serialized_api_key_writer']
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE / path), 'exec'), namespace)
    return namespace


class RedisProxy:
    def __init__(self, client):
        self.client = client
        self.after_write = lambda: None
    def keys(self, pattern):
        return self.client.keys(pattern)
    def get(self, key):
        return self.client.get(key)
    def set(self, key, value):
        value = self.client.set(key, value)
        self.after_write()
        return value
    def delete(self, *keys):
        value = self.client.delete(*keys)
        self.after_write()
        return value


@pytest.fixture
def api_writers(publisher, monkeypatch):  # noqa: F811
    url = os.environ.get('DA_API_WRITER_REDIS_URL')
    if not url:
        pytest.skip('disposable Redis URL required for actual API writer integration')
    import redis
    client = redis.Redis.from_url(url)
    client.flushdb()
    r = RedisProxy(client)
    monkeypatch.setattr(managed, 'managed_installation', lambda: True)
    monkeypatch.setitem(sys.modules, 'docassemble.webapp.extensions', SimpleNamespace(db=SimpleNamespace(engine=publisher.engine)))
    monkeypatch.setitem(sys.modules, 'docassemble.webapp.database', SimpleNamespace(dbtableprefix='test_'))
    monkeypatch.setitem(sys.modules, 'docassemble.webapp.users.managed', writers)
    namespace = dict(json=json, re=re, hashlib=hashlib, codecs=codecs, r=r,
        current_app=SimpleNamespace(secret_key='fixture'), random_alphanumeric=lambda n: 'n' * n,
        serialized_api_key_writer=managed.serialized_api_key_writer)
    functions('docassemble_webapp/docassemble/webapp/utils/api_key.py',
              {'encrypt_api_key', 'add_specific_api_key'}, namespace)
    functions('docassemble_webapp/docassemble/webapp/api/helpers.py',
              {'add_api_key', 'update_api_key', 'delete_api_key', 'fix_api_key', 'fix_api_keys'}, namespace)
    user = SimpleNamespace(id=1, has_role=lambda *roles: True)
    fields = {name: SimpleNamespace(data=value) for name, value in
              {'action': 'edit', 'security': '[]', 'permissions': ['read_packages'], 'name': 'fixture',
               'method': 'ip', 'key': namespace['encrypt_api_key']('k' * 32, 'fixture'), 'delete': False}.items()}
    form = SimpleNamespace(**fields, validate=lambda: True)
    namespace.update(current_user=user, request=SimpleNamespace(method='POST', form={}, args={}),
        setup_translation=lambda: None, APIKey=lambda data: form, daconfig={},
        word=lambda value: value, DEFER='', NOTIFICATION_CONTAINER='', NOTIFICATION_MESSAGE='',
        PERMISSIONS_LIST=[], Markup=lambda value: value, url_for=lambda *a, **kw: '/',
        redis_script=lambda data: '', get_requester_ip=lambda request: '127.0.0.1',
        flash=lambda *args: None, render_template=lambda *a, **kw: kw,
        urllibquoteplus=lambda value: value, logmessage=lambda value: None)
    functions('docassemble_webapp/docassemble/webapp/api/views.py', {'manage_api'}, namespace)
    key = 'da:apikey:userid:1:key:' + fields['key'].data + ':info'
    info = dict(name='fixture', method='none', constraints=[], last_four='kkkk', permissions=['manage_packages', 'read_packages'])
    client.set(key, json.dumps(info))
    policy = {'allowed': {}, 'allow_updates': True, 'developer_can_install': True}
    def permission(actor, permission, admin):
        value = r.get(key)
        if not value:
            return False
        info = json.loads(value)
        # Request constraint dependency supplied, not a complete api_verify request.
        return (permission in info['permissions'] and
                (not info['constraints'] or '127.0.0.1' in info['constraints']))
    publisher.authorize = adapter.PreviewAuthorizer('test_', lambda: policy, permission)
    yield SimpleNamespace(ns=namespace, r=r, key=key, info=info, form=form, client=client)
    client.flushdb()


def mutate(api, operation):
    ns = api.ns
    if operation == 'add':
        return ns['add_api_key'](1, 'new', 'none', [], ['read_packages'])
    if operation == 'specific':
        return ns['add_specific_api_key']('new', 's' * 32, 1, 'fixture')
    if operation == 'update':
        return ns['update_api_key'](1, 'k' * 32, None, 'ip', ['192.0.2.1'], None, None, ['read_packages'], None, None)
    if operation == 'constraint':
        return ns['update_api_key'](1, 'k' * 32, None, 'ip', ['192.0.2.1'], None, None, None, None, None)
    if operation == 'delete':
        return ns['delete_api_key'](1, 'k' * 32)
    if operation == 'fix':
        info = dict(api.info, permissions=['read_packages'])
        api.client.set('da:api:userid:1:key:' + 'k' * 32 + ':info', json.dumps(info))
        return ns['fix_api_keys']()
    if operation == 'view_new':
        api.form.action.data = 'new'
    if operation == 'view_delete':
        api.form.delete.data = True
    return ns['manage_api']()


OPERATIONS = ['add', 'specific', 'update', 'constraint', 'delete', 'fix', 'view_new', 'view_edit', 'view_delete']


@pytest.mark.parametrize('operation', OPERATIONS)
def test_actual_redis_writer_blocks_publication(publisher, api_writers, operation, monkeypatch):  # noqa: F811
    candidate = ready(publisher)
    before = snapshot(publisher)
    locked, release = Event(), Event()
    pids = []
    original = writers.lock_authority
    def lock(connection, prefix):
        original(connection, prefix)
        pids.append(connection.scalar(text('SELECT pg_backend_pid()')))
    monkeypatch.setattr(writers, 'lock_authority', lock)
    def pause():
        if not locked.is_set():
            locked.set()
            assert release.wait(15)
    api_writers.r.after_write = pause
    with ThreadPoolExecutor(2) as pool:
        mutation = pool.submit(mutate, api_writers, operation)
        assert locked.wait(10)
        approval = pool.submit(approve, publisher, candidate)
        try:
            wait_for_approval_lock(publisher.engine, pids[0])
            assert snapshot(publisher) == before
        finally:
            release.set()
        mutation.result(timeout=10)
        if operation in ('add', 'specific', 'view_new'):
            assert approval.result(timeout=10)['installation'] == 'pending'
            assert len(intent(publisher)[0]) == 1
        else:
            with pytest.raises(PreparationError, match='forbidden'):
                approval.result(timeout=10)
            assert snapshot(publisher) == before
            assert intent(publisher) == ([], [])


@pytest.mark.parametrize('operation', OPERATIONS)
def test_actual_redis_writer_waits_for_publication(publisher, api_writers, operation):  # noqa: F811
    candidate = ready(publisher)
    locked, release = Event(), Event()
    pids = []
    authorize = publisher.authorize
    def pause(connection, actor, action, record):
        if action == 'approve':
            pids.append(connection.scalar(text('SELECT pg_backend_pid()')))
            locked.set()
            assert release.wait(15)
        return authorize(connection, actor, action, record)
    publisher.authorize = pause
    initial = api_writers.client.get(api_writers.key)
    with ThreadPoolExecutor(2) as pool:
        approval = pool.submit(approve, publisher, candidate)
        assert locked.wait(10)
        mutation = pool.submit(mutate, api_writers, operation)
        try:
            wait_for_approval_lock(publisher.engine, pids[0])
            assert api_writers.client.get(api_writers.key) == initial
            assert not mutation.done()
        finally:
            release.set()
        response = approval.result(timeout=10)
        mutation.result(timeout=10)
    assert len(intent(publisher)[0]) == 1
    with publisher.engine.connect() as c:
        assert c.scalar(select(publisher.tables['environment_state'].c.desired_revision_id)) == response['revision_id']


@pytest.mark.parametrize('managed_flag', [True, False])
def test_redis_error_releases_lock_and_flag_off(publisher, api_writers, monkeypatch, managed_flag):  # noqa: F811
    monkeypatch.setattr(managed, 'managed_installation', lambda: managed_flag)
    calls = []
    original = writers.lock_authority
    def lock(connection, prefix):
        calls.append(1)
        original(connection, prefix)
    monkeypatch.setattr(writers, 'lock_authority', lock)
    def failure():
        raise RuntimeError('lost Redis acknowledgement')
    api_writers.r.after_write = failure
    with pytest.raises(RuntimeError, match='lost Redis'):
        mutate(api_writers, 'delete')
    assert calls == ([1] if managed_flag else [])
    assert api_writers.client.get(api_writers.key) is None  # SQL rollback cannot undo Redis
    with publisher.engine.begin() as c:
        c.execute(text("SET LOCAL lock_timeout = '1s'"))
        original(c, 'test_')


def test_startup_fix_api_keys_rejects_absent_managed_singleton(publisher, api_writers):  # noqa: F811
    startup = ast.parse((SOURCE / 'docassemble_webapp/docassemble/webapp/startup.py').read_text())
    initialize = next(node for node in startup.body if isinstance(node, ast.FunctionDef) and node.name == 'initialize')
    assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and
               node.func.id == 'fix_api_keys' for node in ast.walk(initialize))
    before = {key: api_writers.client.get(key) for key in api_writers.client.keys('*')}
    with publisher.engine.begin() as connection:
        connection.execute(publisher.tables['environment_state'].delete())
    with pytest.raises(PreparationError, match='no explicitly managed baseline'):
        api_writers.ns['fix_api_keys']()
    assert {key: api_writers.client.get(key) for key in api_writers.client.keys('*')} == before


def test_missing_baseline_fails_before_redis(publisher, api_writers):  # noqa: F811
    before = api_writers.client.get(api_writers.key)
    with publisher.engine.begin() as c:
        c.execute(update(publisher.tables['environment_state']).values(managed=False))
    with pytest.raises(PreparationError, match='no explicitly managed baseline'):
        mutate(api_writers, 'delete')
    assert api_writers.client.get(api_writers.key) == before
