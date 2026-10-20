"""Actual account/invitation/bootstrap SQL bodies, not Flask/plugin migration proof."""
import ast
from functools import partial
import datetime
import email.utils
import re
import zoneinfo
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import MetaData, Table, select, text, event, and_
from sqlalchemy.orm import Session, joinedload, registry
from werkzeug.exceptions import Forbidden

from docassemble.base.managed_preparation import PreparationError
from test_preparation_postgres import protocol  # noqa: F401
from test_publication_postgres import wait_for_approval_lock
from test_publication_writers_postgres import SOURCE, publisher, ready, approve, writers, intent  # noqa: F401
from test_publication_account_writers_postgres import account_models, source_functions, protocol_snapshot, deletion, Redis  # noqa: F401
from test_publication_profile_views_postgres import profile_models, Form  # noqa: F401


WRITERS = ['invite_helper', 'invite_view', 'registration_role', 'register_signal',
           'bootstrap_role', 'bootstrap_user', 'bootstrap_populate',
           'ldap', 'oauth', 'phone', 'custom_register', 'invited_register']
REVOKES = {'registration_role', 'register_signal'}


@pytest.fixture
def registration_models(publisher, profile_models, request):  # noqa: F811
    with publisher.engine.begin() as c:
        c.execute(text('CREATE TABLE test_user_invite (id serial PRIMARY KEY, email text, role_id integer, invited_by_user_id integer, token text)'))
        c.execute(text("UPDATE test_user SET nickname=CASE id WHEN 1 THEN 'admin' ELSE 'cron' END"))
        if request.node.callspec.params.get('change') == 'invited_register':
            c.execute(text("INSERT INTO test_user_invite (email,role_id,invited_by_user_id,token) VALUES ('new@example.invalid',1,2,'existing-token')"))
        if request.node.callspec.params.get('change') == 'bootstrap_populate':
            c.execute(text('DELETE FROM test_user_roles WHERE user_id=2'))
            c.execute(text('UPDATE test_user SET confirmed_at=NULL'))
    model = type('MyUserInvitation', (), {})
    registry().map_imperatively(model, Table('test_user_invite', MetaData(), autoload_with=publisher.engine))
    return dict(profile_models, MyUserInvitation=model)


def snapshot(publisher):  # noqa: F811
    with publisher.engine.connect() as c:
        return {name: list(c.execute(text('SELECT * FROM test_' + name + ' ORDER BY 1')))
                for name in ('user', 'user_auth', 'role', 'user_roles', 'user_invite')}


def invoke(change, session, models, *, managed=True, effect=None, recipients='new@example.invalid'):
    current = session.get(models['UserModel'], 2)
    current.is_authenticated = True
    list(current.roles)
    user = session.get(models['UserModel'], 1)
    list(user.roles)
    def denied(*args):
        raise Forbidden()
    def unlocked(*args):
        raise AssertionError('flag-off acquired managed lock')
    def side_effect(*args, **kwargs):
        # Inspect from a genuinely independent transaction, not this Session.
        if managed:
            with session.bind.connect() as c:
                if change in ('invite_helper', 'invite_view') and '\n' not in recipients:
                    assert c.scalar(text('SELECT count(*) FROM test_user_invite WHERE token IS NOT NULL')) == 1
                elif change in REVOKES:
                    assert c.scalar(text('SELECT role_id FROM test_user_roles WHERE user_id=1')) == 3
                elif change in ('custom_register', 'invited_register', 'oauth', 'phone'):
                    assert c.scalar(text('SELECT count(*) FROM test_user WHERE id>2 AND last_login IS NOT NULL')) == 1
                    if change in ('custom_register', 'invited_register'):
                        assert c.scalar(text('SELECT role_id FROM test_user_roles WHERE user_id>2')) == (1 if change == 'invited_register' else 3)
        if effect:
            effect(*args, **kwargs)
    manager = SimpleNamespace(find_user_by_email=lambda value: (None, None),
        generate_token=lambda value: 'signed-' + str(value), hash_password=lambda value: value,
        after_invite_endpoint='after', after_login_endpoint='after', after_register_endpoint='after',
        after_confirm_endpoint='after', auto_login_at_login=False)
    web_session = {}
    request = SimpleNamespace(method='POST', args={}, form={}, values={}, cookies={})
    namespace = dict(models, db=SimpleNamespace(session=session, joinedload=joinedload),
        dbtableprefix='test_', select=select, and_=and_, joinedload=joinedload, managed_installation=lambda: managed,
        lock_authority=writers.lock_authority if managed else unlocked,
        current_user=current, current_app=SimpleNamespace(user_manager=manager, _get_current_object=lambda: None,
            config={'USER_PROFILE_FIELDS': [], 'USE_PHONE_LOGIN': True, 'SESSION_COOKIE_SECURE': True,
                    'SESSION_COOKIE_SAMESITE': 'Lax'}),
        session=web_session, request=request, daconfig={}, re=re, email=email,
        datetime=datetime, zoneinfo=zoneinfo, abort=denied,
        setup_translation=lambda: None, word=lambda value: value, flash=lambda *a: None,
        logmessage=lambda *a: None, url_for=lambda endpoint, **kw: endpoint,
        redirect=lambda value: SimpleNamespace(location=value, set_cookie=lambda *a, **kw: None), _endpoint_url=lambda value: value,
        random_alphanumeric=lambda value: 'unique', DAError=PreparationError,
        emails=SimpleNamespace(send_invite_email=side_effect),
        docassemble_flask_user=SimpleNamespace(emails=SimpleNamespace(send_invite_email=side_effect)),
        login_or_register=lambda *a, effects=None, **kw: effects.append(partial(side_effect)) if effects is not None and change == 'register_signal' else None,
        run_effects=lambda effects: [effect() for effect in effects], login_user=side_effect,
        delete_managed_account=lambda user_id, temp_user_id, shared, r, r_user: deletion(session, models, managed=managed)(user_id, r, r_user),
        get_server_redis=Redis, get_server_redis_user=Redis,
        get_default_timezone=lambda: 'UTC', add_specific_api_key=side_effect,
        get_flask_app=lambda: SimpleNamespace(user_manager=manager), os=__import__('os'),
        time=__import__('time'))
    helpers = source_functions('docassemble_webapp/docassemble/webapp/users/helpers.py',
        {'assign_registration_role', 'update_last_login'}, namespace)
    if change == 'invite_helper':
        return source_functions('docassemble_webapp/docassemble/webapp/users/helpers.py',
            {'invite_user'}, namespace)['invite_user']('new@example.invalid', 'user', True)
    if change == 'invite_view':
        namespace['MyInviteForm'] = lambda *a: Form(change, email=recipients, role_id='3')
        return source_functions('docassemble_webapp/docassemble/webapp/users/views.py',
            {'invite'}, namespace)['invite']()
    if change == 'registration_role':
        if managed:
            helpers['assign_registration_role'](user)
        else:
            user.roles = [session.get(models['Role'], 3)]
        session.commit()
        side_effect()
        return
    if change == 'register_signal':
        return source_functions('docassemble_webapp/docassemble/webapp/users/signals.py',
            {'on_register_hook'}, namespace)['on_register_hook'](None, user)
    if change == 'last_login':
        return helpers['update_last_login'](user)
    if change.startswith('bootstrap'):
        @contextmanager
        def scope():
            yield session
            session.commit()
        namespace['session_scope'] = scope
        functions = source_functions('docassemble_webapp/docassemble/webapp/create_tables.py',
            {'get_role', 'get_user', 'populate_tables'}, namespace)
        if change == 'bootstrap_role':
            functions['get_role'](session, 'new-role')
            session.commit()
        elif change == 'bootstrap_user':
            functions['get_user'](session, session.get(models['Role'], 3),
                {'nickname': 'new', 'email': 'new@example.invalid'})
            session.commit()
        else:
            namespace['daconfig'] = {'fix user roles': True}
            namespace['add_dependencies'] = lambda *a, **kw: None
            namespace['Package'] = Table('test_package', MetaData(), autoload_with=session.bind)
            functions['populate_tables']()
        return
    if change == 'ldap':
        # Exact method body, with the external LDAP verifier supplied.
        tree = ast.parse((SOURCE / 'docassemble_webapp/docassemble/webapp/users/forms.py').read_text())
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'MySignInForm')
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == 'validate')
        namespace.update(BAN_IP_ADDRESSES=False, daconfig={'ldap login': {'enable': True}},
            ldap=SimpleNamespace(initialize=lambda *a: SimpleNamespace(set_option=lambda *a: None,
                simple_bind_s=lambda *a: None, whoami_s=lambda: 'verified', unbind_s=lambda: None),
                OPT_REFERRALS=1, LDAPError=ValueError, INVALID_CREDENTIALS=ValueError))
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'users/forms.py', 'exec'), namespace)
        form = SimpleNamespace(email=SimpleNamespace(data='new@example.invalid'), password=SimpleNamespace(data='pw'))
        namespace.update(r=SimpleNamespace(get=lambda *a: None, delete=lambda *a: None), get_requester_ip=lambda *a: 'local')
        return namespace['validate'](form)
    if change in ('oauth', 'phone'):
        namespace.update(get_session=lambda *a: None, sub_temp_user_dict_key=lambda *a: [],
            save_user_dict_key=lambda *a, **kw: None, update_session=lambda *a, **kw: None,
            substitute_secret=lambda *a, **kw: 'new', sub_temp_other=lambda *a: None,
            MD5Hash=lambda **kw: SimpleNamespace(hexdigest=lambda: 'hash'), pad_to_16=lambda value: value,
            r=SimpleNamespace(delete=lambda *a: None), get_requester_ip=lambda *a: 'local')
        if change == 'oauth':
            current.is_anonymous = True
            namespace.update(OAuthSignIn=SimpleNamespace(get_provider=lambda *a: SimpleNamespace(
                enabled=lambda: True, callback=lambda: ('oauth$new', 'new', 'new@example.invalid', {}))),
                verify_email=lambda value: True)
            request.method = 'GET'
            return source_functions('docassemble_webapp/docassemble/webapp/auth/views.py',
                {'oauth_callback'}, namespace)['oauth_callback']('google')
        web_session['phone_number'] = '123'
        namespace['PhoneLoginVerifyForm'] = lambda *a: Form(change, submit=True, phone_number='123')
        return source_functions('docassemble_webapp/docassemble/webapp/phonelogin/views.py',
            {'phone_login_verify'}, namespace)['phone_login_verify']()
    assert change in ('custom_register', 'invited_register')
    if change == 'invited_register':
        request.values['token'] = 'existing-token'
    current.is_authenticated = False
    form = Form(change, timezone='UTC', next='', reg_next='', country='', subdivisionfirst='',
        subdivisionsecond='', subdivisionthird='', organization='', language='', invite_token='')
    form.data = {'email': 'new@example.invalid', 'password': 'pw'}
    manager.login_form = lambda: None
    manager.register_form = lambda *a: form
    manager.require_invitation = False
    manager.send_registered_email = True
    manager.enable_confirm_email = False
    manager.auto_login_after_register = False
    namespace.update(_get_safe_next_param=lambda *a: 'after', _call_or_get=lambda value: value,
        as_int=int, urllibquote=lambda value: value)
    def add_object(cls, **kwargs):
        obj = cls(**kwargs)
        session.add(obj)
        return obj
    manager.db_adapter = SimpleNamespace(UserClass=models['UserModel'], UserAuthClass=models['UserAuthModel'],
        UserEmailClass=None, UserProfileClass=None, UserInvitationClass=models['MyUserInvitation'],
        add_object=add_object, commit=session.commit, delete_object=session.delete,
        find_first_object=lambda cls, **kw: session.execute(select(cls).filter_by(**kw)).scalar(),
        update_object=lambda obj, **kw: [setattr(obj, key, value) for key, value in kw.items()])
    def signal(*args, **kw):
        side_effect()
        source_functions('docassemble_webapp/docassemble/webapp/users/signals.py',
            {'on_register_hook'}, namespace)['on_register_hook'](None, **kw)
    namespace['docassemble_flask_user'] = SimpleNamespace(
        views=SimpleNamespace(_send_registered_email=side_effect),
        signals=SimpleNamespace(user_registered=SimpleNamespace(send=signal)))
    return source_functions('docassemble_webapp/docassemble/webapp/users/views.py',
        {'custom_register'}, namespace)['custom_register']()


def assert_effect(publisher, change):  # noqa: F811
    with publisher.engine.connect() as c:
        if change.startswith('invite'):
            assert c.scalar(text('SELECT count(*) FROM test_user_invite WHERE token IS NOT NULL')) == 1
        elif change in REVOKES:
            assert list(c.execute(text('SELECT role_id FROM test_user_roles WHERE user_id=1'))) == [(3,)]
        elif change == 'last_login':
            assert c.scalar(text('SELECT last_login FROM test_user WHERE id=1')) is not None
        elif change == 'bootstrap_role':
            assert c.scalar(text("SELECT count(*) FROM test_role WHERE name='new-role'")) == 1
        elif change == 'bootstrap_populate':
            assert c.scalar(text("SELECT count(*) FROM test_role WHERE name='trainer'")) == 1
            assert c.scalar(text('SELECT role_id FROM test_user_roles WHERE user_id=2')) == 3
            assert c.scalar(text('SELECT count(*) FROM test_user WHERE confirmed_at IS NULL')) == 0
        else:
            assert c.scalar(text('SELECT count(*) FROM test_user WHERE id>2')) == 1
            if change in ('bootstrap_user', 'ldap', 'custom_register', 'invited_register'):
                assert c.scalar(text('SELECT role_id FROM test_user_roles WHERE user_id>2')) == (1 if change == 'invited_register' else 3)


@pytest.mark.parametrize('change', WRITERS)
def test_registration_actual_writer_first_race(publisher, registration_models, change):  # noqa: F811
    candidate = ready(publisher)
    before = protocol_snapshot(publisher), intent(publisher), snapshot(publisher)
    locked, release = Event(), Event()
    pids = []
    def write():
        with Session(publisher.engine) as session:
            def pause(session):
                if not locked.is_set():
                    pids.append(session.scalar(text('SELECT pg_backend_pid()')))
                    locked.set()
                    assert release.wait(15)
            event.listen(session, 'before_commit', pause)
            invoke(change, session, registration_models)
    with ThreadPoolExecutor(2) as pool:
        mutation = pool.submit(write)
        if not locked.wait(10):
            mutation.result(timeout=1)
            pytest.fail('writer never reached commit')
        approval = pool.submit(approve, publisher, candidate)
        try:
            wait_for_approval_lock(publisher.engine, pids[0])
            assert (protocol_snapshot(publisher), intent(publisher), snapshot(publisher)) == before
        finally:
            release.set()
        mutation.result(timeout=10)
        if change in REVOKES:
            with pytest.raises(PreparationError, match='forbidden'):
                approval.result(timeout=10)
            assert (protocol_snapshot(publisher), intent(publisher)) == before[:2]
        else:
            assert approval.result(timeout=10)['installation'] == 'pending'
    assert_effect(publisher, change)


@pytest.mark.parametrize('change', WRITERS)
def test_registration_actual_publication_first_race(publisher, registration_models, change):  # noqa: F811
    candidate = ready(publisher)
    locked, release = Event(), Event()
    pids = []
    project = publisher.project
    def pause(connection, *args):
        response = project(connection, *args)
        pids.append(connection.scalar(text('SELECT pg_backend_pid()')))
        locked.set()
        assert release.wait(15)
        return response
    publisher.project = pause
    before = snapshot(publisher)
    def write():
        with Session(publisher.engine) as session:
            invoke(change, session, registration_models)
    with ThreadPoolExecutor(2) as pool:
        approval = pool.submit(approve, publisher, candidate)
        assert locked.wait(10)
        mutation = pool.submit(write)
        try:
            wait_for_approval_lock(publisher.engine, pids[0])
            assert snapshot(publisher) == before
        finally:
            release.set()
        result = approval.result(timeout=10)
        mutation.result(timeout=10)
    assert_effect(publisher, change)
    state = protocol_snapshot(publisher)
    assert len(state['revision']) == 2 and len(state['installation_attempt']) == 1
    assert state['environment_state'][0]['active_attempt_id'] == result['attempt_id']
    assert len(intent(publisher)[0]) == len(intent(publisher)[1]) == 1


@pytest.mark.parametrize('change', WRITERS)
def test_registration_failed_commit_rolls_back_without_effects(publisher, registration_models, change):  # noqa: F811
    before = protocol_snapshot(publisher), intent(publisher), snapshot(publisher)
    effects = []
    with Session(publisher.engine) as session:
        def fail(session):
            session.flush()
            raise RuntimeError('commit failed')
        event.listen(session, 'before_commit', fail)
        with pytest.raises(RuntimeError, match='commit failed'):
            invoke(change, session, registration_models, effect=lambda *a, **kw: effects.append(1))
    assert (protocol_snapshot(publisher), intent(publisher), snapshot(publisher)) == before
    assert not effects


@pytest.mark.parametrize('change', [value for value in WRITERS if value != 'registration_role'])
def test_registration_flag_off_legacy(publisher, registration_models, change):  # noqa: F811
    before = protocol_snapshot(publisher)
    with Session(publisher.engine) as session:
        invoke(change, session, registration_models, managed=False)
    assert_effect(publisher, change)
    assert protocol_snapshot(publisher) == before


@pytest.mark.parametrize('change', WRITERS)
def test_registration_missing_baseline_rejects(publisher, registration_models, change):  # noqa: F811
    with publisher.engine.begin() as c:
        c.execute(publisher.tables['environment_state'].update().values(managed=False))
    before = protocol_snapshot(publisher), intent(publisher), snapshot(publisher)
    effects = []
    with Session(publisher.engine) as session:
        with pytest.raises(PreparationError, match='no explicitly managed baseline'):
            invoke(change, session, registration_models, effect=lambda *a, **kw: effects.append(1))
    assert (protocol_snapshot(publisher), intent(publisher), snapshot(publisher)) == before
    assert not effects


@pytest.mark.parametrize('change', ['bootstrap_populate'])
def test_production_populate_tables_absent_singleton_rejects(publisher, registration_models, change):  # noqa: F811
    with publisher.engine.begin() as c:
        c.execute(publisher.tables['environment_state'].delete())
    before = protocol_snapshot(publisher), intent(publisher), snapshot(publisher)
    with Session(publisher.engine) as session:
        with pytest.raises(PreparationError, match='no explicitly managed baseline'):
            invoke(change, session, registration_models)
    assert (protocol_snapshot(publisher), intent(publisher), snapshot(publisher)) == before


@pytest.mark.parametrize('change', ['invite_helper', 'invite_view', 'custom_register'])
def test_registration_mail_failure_revokes_under_new_transaction(publisher, registration_models, change):  # noqa: F811
    before = protocol_snapshot(publisher), intent(publisher)
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise RuntimeError('mail failed')
    with Session(publisher.engine) as session:
        if change == 'invite_view':
            invoke(change, session, registration_models, effect=fail)
        else:
            with pytest.raises((PreparationError, RuntimeError)):
                invoke(change, session, registration_models, effect=fail)
    assert calls == [1]
    with publisher.engine.connect() as c:
        if change == 'custom_register':
            assert c.scalar(text('SELECT count(*) FROM test_user WHERE id>2 AND active')) == 0
            assert c.scalar(text('SELECT count(*) FROM test_user WHERE id>2')) == 1
            assert c.scalar(text("SELECT state FROM test_managed_account_cleanup WHERE user_id>2")) == 'complete'
        else:
            assert c.scalar(text('SELECT count(*) FROM test_user_invite')) == 0
    assert (protocol_snapshot(publisher), intent(publisher)) == before


@pytest.mark.parametrize("change", WRITERS)
def test_registration_managed_success_commits_once(publisher, registration_models, change):  # noqa: F811
    commits = []
    with Session(publisher.engine) as session:
        event.listen(session, "before_commit", lambda session: commits.append(1))
        invoke(change, session, registration_models)
    assert commits == [1]
    assert_effect(publisher, change)


@pytest.mark.parametrize("change", ["invite_helper", "invite_view"])
def test_invitation_rechecks_preloaded_actor_after_lock(publisher, registration_models, change):  # noqa: F811
    started = Event()
    before = snapshot(publisher)
    def write():
        with Session(publisher.engine) as session:
            def observe(connection, cursor, statement, parameters, context, many):
                if "environment_state" in statement and "FOR UPDATE" in statement:
                    started.set()
            event.listen(session.connection(), "before_cursor_execute", observe)
            with pytest.raises(PreparationError if change == "invite_helper" else Forbidden):
                invoke(change, session, registration_models)
    with ThreadPoolExecutor(1) as pool:
        with publisher.authority_write() as connection:
            connection.execute(text("DELETE FROM test_user_roles WHERE user_id=2"))
            pid = connection.scalar(text("SELECT pg_backend_pid()"))
            mutation = pool.submit(write)
            assert started.wait(10)
            wait_for_approval_lock(publisher.engine, pid)
            assert snapshot(publisher) == before
        mutation.result(timeout=10)
    after = snapshot(publisher)
    assert after["user_invite"] == []
    assert after["user"] == before["user"]
    assert after["user_roles"] == [(1, 1)]


@pytest.mark.parametrize("change", ["invite_helper", "invite_view", "custom_register"])
def test_registration_mail_revocation_blocks_behind_publication(publisher, registration_models, change):  # noqa: F811
    candidate = ready(publisher)
    sent, release = Event(), Event()
    def fail(*args, **kwargs):
        sent.set()
        assert release.wait(15)
        raise RuntimeError("mail failed")
    def write():
        with Session(publisher.engine) as session:
            if change == "invite_view":
                invoke(change, session, registration_models, effect=fail)
            else:
                with pytest.raises((RuntimeError, PreparationError)):
                    invoke(change, session, registration_models, effect=fail)
    publishing, finish = Event(), Event()
    pids = []
    project = publisher.project
    def pause(connection, *args):
        response = project(connection, *args)
        pids.append(connection.scalar(text('SELECT pg_backend_pid()')))
        publishing.set()
        assert finish.wait(15)
        return response
    publisher.project = pause
    with ThreadPoolExecutor(2) as pool:
        mutation = pool.submit(write)
        assert sent.wait(10)
        publication = pool.submit(approve, publisher, candidate)
        assert publishing.wait(10)
        try:
            release.set()
            wait_for_approval_lock(publisher.engine, pids[0])
            before = snapshot(publisher)
            if change == 'custom_register':
                assert len(before['user']) == 3
            else:
                assert len(before['user_invite']) == 1
        finally:
            release.set()
            finish.set()
        assert publication.result(timeout=10)['installation'] == 'pending'
        mutation.result(timeout=10)
    assert len(intent(publisher)[0]) == 1


@pytest.mark.parametrize("change", ["last_login"])
def test_last_login_does_not_reenter_publication_environment_lock(publisher, registration_models, change):  # noqa: F811
    # api_verify runs on a separate Session inside the publication authorizer.
    # Timestamp-only writes are not authority mutations and MUST NOT reacquire
    # that already-held environment lock on the separate connection.
    def write():
        with Session(publisher.engine) as session:
            invoke(change, session, registration_models)
    with ThreadPoolExecutor(1) as pool:
        with publisher.authority_write():
            pool.submit(write).result(timeout=3)
    assert_effect(publisher, change)
