"""Actual helper source with real Sessions; hooks and authentication supplied.

This is SQL writer qualification, not full Flask/plugin/non-DB store integration.
"""
import ast
from functools import partial
import datetime
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import MetaData, Table, select, delete, update, text, event
from sqlalchemy.orm import Session, registry, relationship, joinedload

from docassemble.base.managed_records import preparation_tables
from docassemble.base.managed_preparation import PreparationError
from test_preparation_postgres import protocol, result  # noqa: F401
from test_publication_postgres import wait_for_approval_lock
from test_publication_writers_postgres import SOURCE, publisher, ready, approve, writers, intent  # noqa: F401


def source_functions(path, names, namespace):
    tree = ast.parse((SOURCE / path).read_text())
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(nodes) == len(names)
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE / path), 'exec'), namespace)
    return namespace


@pytest.fixture
def account_models(publisher):  # noqa: F811
    with publisher.engine.begin() as c:
        c.execute(text("SELECT setval(pg_get_serial_sequence('test_user','id'), 2)"))
        for field in ('first_name', 'last_name', 'nickname', 'country', 'subdivisionfirst', 'subdivisionsecond',
                      'subdivisionthird', 'organization', 'timezone', 'language', 'pypi_username', 'pypi_password',
                      'otp_secret', 'confirmed_at', 'last_login'):
            c.execute(text('ALTER TABLE test_user ADD COLUMN ' + field + ' text'))
        c.execute(text('CREATE TABLE test_user_auth (id serial PRIMARY KEY, user_id integer, password text, reset_password_token text)'))
        c.execute(text("INSERT INTO test_user_auth (user_id,password,reset_password_token) VALUES (1,'secret','token')"))
        for name in ('dict', 'dict_keys', 'uploads_user_auth'):
            c.execute(text('CREATE TABLE test_' + name + ' (id serial PRIMARY KEY, user_id integer)'))
            c.execute(text('INSERT INTO test_' + name + ' (user_id) VALUES (1)'))
    metadata = MetaData()
    mapping = registry()
    classes = {}
    for name, table_name in {'UserModel': 'user', 'UserAuthModel': 'user_auth', 'Role': 'role',
                            'UserDict': 'dict', 'UserDictKeys': 'dict_keys', 'UploadsUserAuth': 'uploads_user_auth',
                            'UserRoles': 'user_roles', 'PackageAuth': 'package_auth'}.items():
        cls = type(name, (), {})
        table = Table('test_' + table_name, metadata, autoload_with=publisher.engine)
        classes[name] = cls
        if name == 'UserModel':
            users = table
        elif name == 'UserRoles':
            membership = table
            mapping.map_imperatively(cls, table, primary_key=[table.c.user_id, table.c.role_id])
        else:
            mapping.map_imperatively(cls, table)
    mapping.map_imperatively(classes['UserModel'], users, properties={
        'roles': relationship(classes['Role'], secondary=membership,
            primaryjoin=users.c.id == membership.c.user_id,
            secondaryjoin=classes['Role'].id == membership.c.role_id),
        'user_auth': relationship(classes['UserAuthModel'],
            primaryjoin=users.c.id == classes['UserAuthModel'].user_id,
            foreign_keys=[classes['UserAuthModel'].user_id], uselist=False)})
    classes['UserModel'].has_role = lambda self, *names: any(role.name in names for role in self.roles)
    return classes


def deletion(session, models, managed=True, cleanup=None):
    callback = cleanup or (lambda *args, **kwargs: None)
    namespace = dict(models, db=SimpleNamespace(session=session), dbtableprefix='test_',
        select=select, delete=delete, update=update, preparation_tables=preparation_tables,
        lock_authority=writers.lock_authority, managed_installation=lambda: managed,
        DAException=PreparationError, manage_chat_logs=callback, manage_global_objects=callback,
        manage_email_server_objects=callback,
        SavedFile=lambda *args, **kwargs: SimpleNamespace(delete=callback), partial=partial,
        sql_hook=lambda name, mode, effects, **kwargs: effects.append(partial(callback, mode, **kwargs)),
        run_effects=lambda effects: [effect() for effect in effects])
    return source_functions('docassemble_webapp/docassemble/webapp/interview/helpers.py',
        {'delete_user_data', 'delete_managed_user_data', 'finish_managed_user_cleanup'}, namespace)['delete_user_data']


class Redis:
    def __init__(self):
        self.deleted = []
    def keys(self, pattern):
        return [pattern]
    def delete(self, key):
        self.deleted.append(key)


def cleanup_state(publisher):  # noqa: F811
    with publisher.engine.connect() as c:
        return [dict(row) for row in c.execute(select(publisher.tables['account_cleanup'])).mappings()]


def protocol_snapshot(publisher):  # noqa: F811
    with publisher.engine.connect() as c:
        return {name: [dict(row) for row in c.execute(select(table)).mappings()]
                for name, table in publisher.tables.items() if name != 'account_cleanup'}


def test_delete_user_data_actual_race(publisher, account_models):  # noqa: F811
    candidate = ready(publisher)
    before = protocol_snapshot(publisher)
    locked, release = Event(), Event()
    pids = []
    def remove():
        with Session(publisher.engine) as session:
            def pause(session):
                if not locked.is_set():
                    pids.append(session.scalar(text('SELECT pg_backend_pid()')))
                    locked.set()
                    assert release.wait(15)
            event.listen(session, 'before_commit', pause)
            deletion(session, account_models)(1, Redis(), Redis())
    with ThreadPoolExecutor(2) as pool:
        mutation = pool.submit(remove)
        assert locked.wait(10)
        approval = pool.submit(approve, publisher, candidate)
        try:
            wait_for_approval_lock(publisher.engine, pids[0])
            assert protocol_snapshot(publisher) == before
            assert cleanup_state(publisher) == []
        finally:
            release.set()
        mutation.result(timeout=10)
        with pytest.raises(PreparationError, match='forbidden'):
            approval.result(timeout=10)
    assert protocol_snapshot(publisher) == before
    assert intent(publisher) == ([], [])
    assert cleanup_state(publisher) == [{'user_id': 1, 'state': 'complete', 'error': None}]
    with publisher.engine.connect() as c:
        assert c.scalar(text('SELECT active FROM test_user WHERE id=1')) is False
        assert c.scalar(text('SELECT password FROM test_user_auth WHERE user_id=1')) == ''
        assert c.scalar(text('SELECT count(*) FROM test_user_roles WHERE user_id=1')) == 0


@pytest.mark.parametrize('failure', ['hook', 'file', 'redis'])
def test_delete_user_data_cleanup_failure_is_durable(publisher, account_models, failure):  # noqa: F811
    candidate = ready(publisher)
    approve(publisher, candidate)
    before = protocol_snapshot(publisher)
    def fail(*args, **kwargs):
        with publisher.engine.connect() as c:
            assert c.scalar(text('SELECT active FROM test_user WHERE id=1')) is False
        raise RuntimeError('credential=sensitive@example.invalid')
    with Session(publisher.engine) as session:
        remove = deletion(session, account_models, cleanup=fail if failure == 'hook' else None)
        if failure == 'file':
            remove.__globals__['SavedFile'] = lambda *a, **k: SimpleNamespace(delete=fail)
        redis = Redis()
        if failure == 'redis':
            redis.keys = fail
        with pytest.raises(PreparationError, match='Account deactivated; account cleanup failed') as error:
            remove(1, redis, Redis())
        assert 'sensitive' not in str(error.value)
    assert protocol_snapshot(publisher) == before
    assert cleanup_state(publisher) == [{'user_id': 1, 'state': 'failed', 'error': 'account cleanup failed'}]
    assert len(intent(publisher)[0]) == 1  # committed desired intent is never removed


def test_delete_user_data_duplicate_preserves_failed_cleanup(publisher, account_models):  # noqa: F811
    r, r_user = Redis(), Redis()
    def fail(*args, **kwargs):
        raise RuntimeError('failure before email-keyed Redis cleanup')
    with Session(publisher.engine) as session:
        with pytest.raises(PreparationError, match='Account deactivated; account cleanup failed'):
            deletion(session, account_models, cleanup=fail)(2, r, r_user)
    failed = cleanup_state(publisher)
    assert failed == [{'user_id': 2, 'state': 'failed', 'error': 'account cleanup failed'}]
    with publisher.engine.connect() as c:
        assert c.scalar(text('SELECT email FROM test_user WHERE id=2')) is None
    calls = []
    with Session(publisher.engine) as session:
        with pytest.raises(PreparationError, match='duplicate cleanup is not supported'):
            deletion(session, account_models, cleanup=lambda *a, **k: calls.append(1))(2, r, r_user)
    assert cleanup_state(publisher) == failed
    assert not calls and not r.deleted and not r_user.deleted


def test_delete_user_data_duplicate_rejects_during_cleanup_and_after_completion(publisher, account_models):  # noqa: F811
    pending, release = Event(), Event()
    calls = []
    def remove():
        def pause(*args, **kwargs):
            pending.set()
            assert release.wait(15)
        with Session(publisher.engine) as session:
            deletion(session, account_models, cleanup=pause)(2, Redis(), Redis())
    with ThreadPoolExecutor(1) as pool:
        first = pool.submit(remove)
        assert pending.wait(10)
        try:
            before = cleanup_state(publisher)
            assert before == [{'user_id': 2, 'state': 'pending', 'error': None}]
            with Session(publisher.engine) as session:
                with pytest.raises(PreparationError, match='duplicate cleanup is not supported'):
                    deletion(session, account_models, cleanup=lambda *a, **k: calls.append(1))(2, Redis(), Redis())
            assert cleanup_state(publisher) == before
        finally:
            release.set()
        first.result(timeout=10)
    complete = cleanup_state(publisher)
    assert complete == [{'user_id': 2, 'state': 'complete', 'error': None}]
    with Session(publisher.engine) as session:
        with pytest.raises(PreparationError, match='duplicate cleanup is not supported'):
            deletion(session, account_models, cleanup=lambda *a, **k: calls.append(1))(2, Redis(), Redis())
    assert cleanup_state(publisher) == complete
    assert not calls


@pytest.mark.parametrize('managed', [True, False])
def test_delete_user_data_preserves_reassignment_and_cleanup(publisher, account_models, managed):  # noqa: F811
    with publisher.engine.begin() as c:
        c.execute(text("INSERT INTO test_package (id,name,type,core) VALUES (77,'existing','pip',false)"))
        c.execute(text("INSERT INTO test_package_auth (package_id,user_id,authtype) VALUES (77,2,'owner')"))
        c.execute(publisher.tables['package_identity'].insert().values(id='existing', normalized_name='existing',
            display_name='Existing', legacy_package_id=77, owner_id='2'))
    r, r_user = Redis(), Redis()
    with Session(publisher.engine) as session:
        deletion(session, account_models, managed)(2, r, r_user)
    assert '*:user:two' in r_user.deleted
    assert '*userid:2' in r.deleted
    assert intent(publisher)[1][0]['user_id'] == 1
    with publisher.engine.connect() as c:
        assert c.scalar(select(publisher.tables['package_identity'].c.owner_id).where(
            publisher.tables['package_identity'].c.id == 'existing')) == ('1' if managed else '2')
    assert bool(cleanup_state(publisher)) == managed


def test_delete_user_data_db_failure_rolls_back(publisher, account_models):  # noqa: F811
    candidate = ready(publisher)
    before = protocol_snapshot(publisher)
    calls = []
    with Session(publisher.engine) as session:
        def fail(session):
            raise RuntimeError('SQL publication boundary')
        event.listen(session, 'before_commit', fail)
        with pytest.raises(RuntimeError):
            deletion(session, account_models, cleanup=lambda *a, **k: calls.append(1))(1, Redis(), Redis())
    assert not calls and not cleanup_state(publisher)
    assert protocol_snapshot(publisher) == before
    assert approve(publisher, candidate)['installation'] == 'pending'


def account_helper(name, session, models):
    current = SimpleNamespace(id=2, email='two', has_role_or_permission=lambda *a, **k: True,
        has_role=lambda *a: True, can_do=lambda *a: True, same_as=lambda identity: identity == 2)
    namespace = dict(models, db=SimpleNamespace(session=session, joinedload=joinedload), dbtableprefix='test_',
        select=select, lock_authority=writers.lock_authority, change_privilege=writers.change_privilege,
        managed_installation=lambda: True, DAException=PreparationError, current_user=current,
        run_effects=lambda effects: [effect() for effect in effects],
        current_app=SimpleNamespace(user_manager=SimpleNamespace(hash_password=lambda password: password,
            find_user_by_email=lambda email: (None, None))), datetime=datetime, re=re,
        random_alphanumeric=lambda n: 'unique', DAList=type('DAList', (), {}), DADict=type('DADict', (), {}))
    return source_functions('docassemble_webapp/docassemble/webapp/users/helpers.py', {name}, namespace)[name]


@pytest.mark.parametrize('change', [{'active': False}, {'privileges': ['user']}])
def test_set_user_info_actual_atomic_race(publisher, account_models, change):  # noqa: F811
    with publisher.engine.begin() as c:
        c.execute(text("INSERT INTO test_role VALUES (3,'user')"))
    candidate = ready(publisher)
    before = protocol_snapshot(publisher)
    locked, release = Event(), Event()
    pids = []
    def edit():
        with Session(publisher.engine) as session:
            def pause(session):
                pids.append(session.scalar(text('SELECT pg_backend_pid()')))
                locked.set()
                assert release.wait(15)
            event.listen(session, 'before_commit', pause)
            account_helper('set_user_info', session, account_models)(dict(change, user_id=1))
    with ThreadPoolExecutor(2) as pool:
        mutation = pool.submit(edit)
        assert locked.wait(10)
        approval = pool.submit(approve, publisher, candidate)
        try:
            wait_for_approval_lock(publisher.engine, pids[0])
            assert protocol_snapshot(publisher) == before
        finally:
            release.set()
        mutation.result(timeout=10)
        with pytest.raises(PreparationError, match='forbidden'):
            approval.result(timeout=10)
    assert protocol_snapshot(publisher) == before


def ownerless(publisher):  # noqa: F811
    with publisher.authority_write() as c:
        package_id = c.scalar(text("INSERT INTO test_package (name,type,core) VALUES ('Example','pip',false) RETURNING id"))
        c.execute(publisher.tables['package_identity'].insert().values(id='ownerless', normalized_name='example',
            display_name='Example', legacy_package_id=package_id, owner_id=None))
    return package_id


def assignment_ready(publisher):  # noqa: F811
    candidate = publisher.prepare('2', 'Example>=2', 'assign', assign_owner=True)
    claim = publisher.claim(candidate)
    assert publisher.publish_ready(claim, result(claim))
    status = publisher.status('2', candidate)
    return candidate, status['result_digest'], status['preview']['ownership_effects']


def test_explicit_ownerless_assignment_preview_and_publication(publisher):  # noqa: F811
    ownerless(publisher)
    with pytest.raises(PreparationError, match='forbidden'):
        publisher.prepare('1', 'Example>=2', 'nonadmin', assign_owner=True)
    with pytest.raises(PreparationError):
        publisher.prepare('2', 'Example>=2', 'implicit')
    candidate, digest, effects = assignment_ready(publisher)
    assert effects == {'normalized_name': 'example', 'owner_id': '2', 'existing': True,
                       'assignment': True, 'previous_owner_id': None}
    response = publisher.approve('2', candidate, digest, 'approval')
    assert intent(publisher)[1][0]['user_id'] == 2
    with publisher.engine.connect() as c:
        assert c.scalar(select(publisher.tables['package_identity'].c.owner_id).where(
            publisher.tables['package_identity'].c.id == 'ownerless')) == '2'
    assert publisher.approve('2', candidate, digest, 'approval') == response
    with pytest.raises(PreparationError, match='body mismatch'):
        publisher.prepare('2', 'Example>=2', 'assign')


def test_ownerless_assignment_authority_race(publisher):  # noqa: F811
    ownerless(publisher)
    candidate, digest, _ = assignment_ready(publisher)
    locked, release = Event(), Event()
    pids = []
    def revoke():
        with publisher.authority_write() as c:
            writers.change_privilege(c, 'test_', 2, 'admin', add=False)
            pids.append(c.scalar(text('SELECT pg_backend_pid()')))
            locked.set()
            assert release.wait(15)
    with ThreadPoolExecutor(2) as pool:
        mutation = pool.submit(revoke)
        assert locked.wait(10)
        approval = pool.submit(publisher.approve, '2', candidate, digest, 'approval')
        try:
            wait_for_approval_lock(publisher.engine, pids[0])
        finally:
            release.set()
        mutation.result(timeout=10)
        with pytest.raises(PreparationError, match='forbidden'):
            approval.result(timeout=10)
    assert not intent(publisher)[1]


def test_ownerless_assignment_changed_grants_rolls_back(publisher):  # noqa: F811
    package_id = ownerless(publisher)
    candidate, digest, _ = assignment_ready(publisher)
    with publisher.authority_write() as c:
        c.execute(text("INSERT INTO test_package_auth (package_id,user_id,authtype) VALUES (:id,1,'owner')"), {'id': package_id})
    before = protocol_snapshot(publisher), intent(publisher)
    with pytest.raises(PreparationError):
        publisher.approve('2', candidate, digest, 'approval')
    assert (protocol_snapshot(publisher), intent(publisher)) == before


def test_create_user_actual_environment_lock(publisher, account_models):  # noqa: F811
    candidate = ready(publisher)
    locked, release = Event(), Event()
    pids = []
    original = publisher.project
    def pause(connection, actor, candidate):
        pids.append(connection.scalar(text('SELECT pg_backend_pid()')))
        locked.set()
        assert release.wait(15)
        return original(connection, actor, candidate)
    publisher.project = pause
    def create():
        with Session(publisher.engine) as session:
            return account_helper('create_user', session, account_models)('new@example.invalid', 'password', ['developer'], {})
    with ThreadPoolExecutor(2) as pool:
        approval = pool.submit(approve, publisher, candidate)
        assert locked.wait(10)
        creation = pool.submit(create)
        try:
            wait_for_approval_lock(publisher.engine, pids[0])
            with publisher.engine.connect() as c:
                assert c.scalar(text("SELECT count(*) FROM test_user WHERE email='new@example.invalid'")) == 0
        finally:
            release.set()
        assert approval.result(timeout=10)['installation'] == 'pending'
        identity = creation.result(timeout=10)
    with publisher.engine.connect() as c:
        assert c.scalar(text('SELECT count(*) FROM test_user_roles WHERE user_id=:id'), {'id': identity}) == 1


def test_set_user_info_sql_failure_rolls_back_composite(publisher, account_models):  # noqa: F811
    with publisher.engine.begin() as c:
        c.execute(text("INSERT INTO test_role VALUES (3,'user')"))
    with Session(publisher.engine) as session:
        edit = account_helper('set_user_info', session, account_models)
        with pytest.raises(PreparationError, match='privilege does not exist'):
            edit({'user_id': 1, 'active': False, 'privileges': ['nonexistent']})
    with publisher.engine.connect() as c:
        assert c.scalar(text('SELECT active FROM test_user WHERE id=1')) is True
        assert c.scalar(text('SELECT role_id FROM test_user_roles WHERE user_id=1')) == 1


def test_delete_user_data_pending_is_committed_before_cleanup(publisher, account_models):  # noqa: F811
    calls = []
    def cleanup(*args, **kwargs):
        assert cleanup_state(publisher) == [{"user_id": 2, "state": "pending", "error": None}]
        with publisher.engine.connect() as c:
            assert c.scalar(text("SELECT active FROM test_user WHERE id=2")) is False
        calls.append(1)
    with Session(publisher.engine) as session:
        deletion(session, account_models, cleanup=cleanup)(2, Redis(), Redis())
    assert len(calls) == 9
    assert cleanup_state(publisher)[0]["state"] == "complete"


def test_set_user_info_success_is_one_composite_commit(publisher, account_models):  # noqa: F811
    with publisher.engine.begin() as c:
        c.execute(text("INSERT INTO test_role VALUES (3,'user')"))
    commits = []
    with Session(publisher.engine) as session:
        event.listen(session, "after_commit", lambda session: commits.append(1))
        account_helper("set_user_info", session, account_models)({"user_id": 1, "active": False, "privileges": ["user"]})
    assert commits == [1]
    with publisher.engine.connect() as c:
        assert c.scalar(text("SELECT active FROM test_user WHERE id=1")) is False
        assert list(c.scalars(text("SELECT role_id FROM test_user_roles WHERE user_id=1"))) == [3]
