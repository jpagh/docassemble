"""Actual SQL view/helper bodies and PostgreSQL locks, not route/plugin auth proof."""
import datetime
import re
import zoneinfo
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import select, and_, text, event
from sqlalchemy.orm import Session, joinedload
from werkzeug.exceptions import Forbidden

from docassemble.base.managed_preparation import PreparationError
from test_preparation_postgres import protocol  # noqa: F401
from test_publication_postgres import wait_for_approval_lock
from test_publication_writers_postgres import publisher, ready, approve, writers, intent  # noqa: F401
from test_publication_account_writers_postgres import account_models, source_functions, protocol_snapshot  # noqa: F401


WRITERS = ['active', 'roles', 'profile', 'mfa', 'email', 'self', 'user_add', 'role_view', 'role_helper']


@pytest.fixture
def profile_models(publisher, account_models):  # noqa: F811
    with publisher.engine.begin() as c:
        c.execute(text("INSERT INTO test_role VALUES (3,'user')"))
        c.execute(text("SELECT setval(pg_get_serial_sequence('test_role','id'), 3)"))
        c.execute(text("UPDATE test_user SET timezone='UTC',otp_secret='mfa',confirmed_at='confirmed',first_name='before'"))
    user = account_models['UserModel']
    user.has_roles = lambda self, *names: self.has_role(*(names[0] if isinstance(names[0], list) else names))
    user.has_role_or_permission = lambda self, *names, **kwargs: self.has_role(*names)
    user.can_do = lambda self, task: self.has_role('admin')
    return account_models


class Field:
    def __init__(self, data=None):
        self.data = data
    def process_data(self, value):
        self.data = value


class Form:
    def __init__(self, change, **kwargs):
        values = dict(cancel=False, active=True, role_id=[1], timezone='UTC', uses_mfa=True,
                      name='new-role', email='new@example.invalid', password='password',
                      first_name='after', last_name='name')
        values.update(kwargs)
        if change == 'roles':
            values['role_id'] = [3]
        if change == 'active':
            values['active'] = False
        for key, value in values.items():
            setattr(self, key, Field(value))
        self.change = change
    def validate(self, *args):
        return True
    def populate_obj(self, user):
        user.first_name = 'after'
        if self.change == 'active':
            user.active = False


def invoke(change, session, models, *, managed=True):
    current = session.get(models['UserModel'], 1 if change == 'self' else 2)
    # Deliberately preload actor and target SQL state before the environment lock.
    list(current.roles)
    target = session.get(models['UserModel'], 1)
    list(target.roles)
    args = {'disable_mfa': '1'} if change == 'mfa' else {'reset_email_confirmation': '1'} if change == 'email' else {}
    def denied(code):
        raise Forbidden()
    def unlocked(*args):
        raise AssertionError('flag-off writer acquired managed lock')
    namespace = dict(models, db=SimpleNamespace(session=session, joinedload=joinedload),
        dbtableprefix='test_', select=select, and_=and_, managed_installation=lambda: managed,
        lock_authority=writers.lock_authority if managed else unlocked,
        setup_translation=lambda: None, current_user=current, abort=denied,
        request=SimpleNamespace(method='GET' if args else 'POST', args=args, form={}),
        current_app=SimpleNamespace(config={'SHOW_PROFILE': True, 'USER_PROFILE_FIELDS': []},
            user_manager=SimpleNamespace(hash_password=lambda value: value, find_user_by_email=lambda value: (None, None))),
        daconfig={}, get_default_timezone=lambda: 'UTC', zoneinfo=zoneinfo,
        flash=lambda *args: None, word=lambda value: value, redirect=lambda value: value,
        url_for=lambda endpoint, **kwargs: endpoint, re=re, datetime=datetime,
        random_alphanumeric=lambda n: 'unique',
        EditUserProfileForm=lambda *a, **kw: Form(change),
        UserProfileForm=lambda *a, **kw: Form(change),
        PhoneUserProfileForm=lambda *a, **kw: Form(change),
        UserAddForm=lambda *a, **kw: Form(change, role_id=[1]),
        NewPrivilegeForm=lambda *a, **kw: Form(change), DAException=PreparationError)
    if change == 'role_helper':
        functions = source_functions('docassemble_webapp/docassemble/webapp/users/helpers.py',
            {'add_privilege', 'get_privileges_list'}, namespace)
        return functions['add_privilege']('new-role')
    name = {'self': 'user_profile_page', 'user_add': 'user_add', 'role_view': 'add_privilege'}.get(change, 'edit_user_profile_page')
    function = source_functions('docassemble_webapp/docassemble/webapp/users/views.py', {name}, namespace)[name]
    return function(1) if name == 'edit_user_profile_page' else function()


def authority(publisher):  # noqa: F811
    with publisher.engine.connect() as c:
        return {name: list(c.execute(text('SELECT * FROM test_' + name + ' ORDER BY 1,2')))
                for name in ('user', 'user_auth', 'role', 'user_roles')}


def assert_effect(publisher, change):  # noqa: F811
    with publisher.engine.connect() as c:
        if change == 'active':
            assert c.scalar(text('SELECT active FROM test_user WHERE id=1')) is False
        elif change == 'roles':
            assert list(c.execute(text('SELECT role_id FROM test_user_roles WHERE user_id=1'))) == [(3,)]
        elif change in ('profile', 'self'):
            assert c.scalar(text('SELECT first_name FROM test_user WHERE id=1')) == 'after'
        elif change in ('mfa', 'email'):
            field = 'otp_secret' if change == 'mfa' else 'confirmed_at'
            assert c.scalar(text('SELECT ' + field + ' FROM test_user WHERE id=1')) is None
        elif change == 'user_add':
            assert c.scalar(text('SELECT count(*) FROM test_user WHERE id>2')) == 1
            assert c.scalar(text('SELECT role_id FROM test_user_roles WHERE user_id=3')) == 1
            assert c.scalar(text('SELECT password FROM test_user_auth WHERE user_id=3')) == 'password'
        else:
            assert c.scalar(text("SELECT count(*) FROM test_role WHERE name='new-role'")) == 1


@pytest.mark.parametrize('change', WRITERS)
def test_profile_actual_writer_first_race(publisher, profile_models, change):  # noqa: F811
    candidate = ready(publisher)
    before = protocol_snapshot(publisher), intent(publisher), authority(publisher)
    locked, release = Event(), Event()
    pids = []
    def write():
        with Session(publisher.engine) as session:
            def pause(session):
                pids.append(session.scalar(text('SELECT pg_backend_pid()')))
                locked.set()
                assert release.wait(15)
            event.listen(session, 'before_commit', pause)
            invoke(change, session, profile_models)
    with ThreadPoolExecutor(2) as pool:
        mutation = pool.submit(write)
        assert locked.wait(10)
        approval = pool.submit(approve, publisher, candidate)
        try:
            wait_for_approval_lock(publisher.engine, pids[0])
            assert (protocol_snapshot(publisher), intent(publisher), authority(publisher)) == before
        finally:
            release.set()
        mutation.result(timeout=10)
        if change in ('active', 'roles'):
            with pytest.raises(PreparationError, match='forbidden'):
                approval.result(timeout=10)
            assert (protocol_snapshot(publisher), intent(publisher)) == before[:2]
        else:
            assert approval.result(timeout=10)['installation'] == 'pending'
            assert len(intent(publisher)[0]) == 1
    assert_effect(publisher, change)


@pytest.mark.parametrize('change', WRITERS)
def test_profile_actual_publication_first_race(publisher, profile_models, change):  # noqa: F811
    candidate = ready(publisher)
    locked, release = Event(), Event()
    pids = []
    project = publisher.project
    def pause(connection, *args):
        result = project(connection, *args)
        pids.append(connection.scalar(text('SELECT pg_backend_pid()')))
        locked.set()
        assert release.wait(15)
        return result
    publisher.project = pause
    before = authority(publisher)
    def write():
        with Session(publisher.engine) as session:
            invoke(change, session, profile_models)
    with ThreadPoolExecutor(2) as pool:
        approval = pool.submit(approve, publisher, candidate)
        assert locked.wait(10)
        mutation = pool.submit(write)
        try:
            wait_for_approval_lock(publisher.engine, pids[0])
            assert authority(publisher) == before
        finally:
            release.set()
        response = approval.result(timeout=10)
        mutation.result(timeout=10)
    assert_effect(publisher, change)
    state = protocol_snapshot(publisher)
    assert len(state['revision']) == 2 and len(state['installation_attempt']) == 1
    assert state['environment_state'][0]['active_attempt_id'] == response['attempt_id']
    assert len(intent(publisher)[0]) == len(intent(publisher)[1]) == 1


@pytest.mark.parametrize('change', WRITERS)
def test_profile_failed_commit_rolls_back_sql(publisher, profile_models, change):  # noqa: F811
    before = protocol_snapshot(publisher), intent(publisher), authority(publisher)
    with Session(publisher.engine) as session:
        def fail(session):
            session.flush()
            raise RuntimeError('commit failed')
        event.listen(session, 'before_commit', fail)
        with pytest.raises(RuntimeError, match='commit failed'):
            invoke(change, session, profile_models)
    assert (protocol_snapshot(publisher), intent(publisher), authority(publisher)) == before


@pytest.mark.parametrize('change', WRITERS)
def test_profile_flag_off_preserves_legacy_writer(publisher, profile_models, change):  # noqa: F811
    before = protocol_snapshot(publisher)
    with Session(publisher.engine) as session:
        invoke(change, session, profile_models, managed=False)
    assert_effect(publisher, change)
    assert protocol_snapshot(publisher) == before


@pytest.mark.parametrize('change', WRITERS)
def test_profile_missing_managed_baseline_rejects_before_write(publisher, profile_models, change):  # noqa: F811
    with publisher.engine.begin() as c:
        c.execute(publisher.tables['environment_state'].update().values(managed=False))
    before = authority(publisher)
    with Session(publisher.engine) as session:
        with pytest.raises(PreparationError, match='no explicitly managed baseline'):
            invoke(change, session, profile_models)
    assert authority(publisher) == before


@pytest.mark.parametrize('change', ['active', 'user_add', 'role_view', 'role_helper'])
def test_profile_rechecks_preloaded_actor_after_lock(publisher, profile_models, change):  # noqa: F811
    started = Event()
    def write():
        with Session(publisher.engine) as session:
            def observe(connection, cursor, statement, parameters, context, many):
                if 'environment_state' in statement and 'FOR UPDATE' in statement:
                    started.set()
            event.listen(session.connection(), 'before_cursor_execute', observe)
            with pytest.raises(Forbidden if change != 'role_helper' else PreparationError):
                invoke(change, session, profile_models)
    before = authority(publisher)
    with ThreadPoolExecutor(1) as pool:
        with publisher.authority_write() as connection:
            connection.execute(text('DELETE FROM test_user_roles WHERE user_id=2'))
            pid = connection.scalar(text('SELECT pg_backend_pid()'))
            mutation = pool.submit(write)
            assert started.wait(10)
            wait_for_approval_lock(publisher.engine, pid)
            assert authority(publisher) == before
        mutation.result(timeout=10)
    after = authority(publisher)
    assert after['user'] == before['user'] and after['user_auth'] == before['user_auth']
    assert after['role'] == before['role']
    assert after['user_roles'] == [(1, 1)]
