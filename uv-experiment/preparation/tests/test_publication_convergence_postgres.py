"""Enclosing built-in ownership/deletion/secret writers on real PostgreSQL.

Production bodies and hook dispatch; Flask/forms/crypto/external effects supplied.
These tests do not qualify arbitrary extension SQL or historical image routes.
"""
import copy
import datetime
import email
import hashlib
import re
import zoneinfo
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from threading import Event
from types import SimpleNamespace, ModuleType

import pytest
from sqlalchemy import MetaData, Table, select, delete, update, and_, or_, text, event
from sqlalchemy.orm import Session, joinedload, registry
from werkzeug.exceptions import Forbidden

from docassemble.base.managed_preparation import PreparationError
from docassemble.base.managed_records import preparation_tables
from docassemble.base.plugin_manager import DocassemblePluginManager
from docassemble.base import hookspecs
import pluggy
from test_preparation_postgres import protocol  # noqa: F401
from test_publication_writers_postgres import SOURCE, publisher, writers, ready, approve, intent  # noqa: F401
from test_publication_postgres import wait_for_approval_lock
from test_publication_account_writers_postgres import account_models, source_functions, protocol_snapshot, Redis  # noqa: F401
from test_publication_profile_views_postgres import profile_models, Form  # noqa: F401
from test_publication_registration_postgres import registration_models  # noqa: F401


CHANGES = ['profile_delete', 'profile_delete_complete', 'manage_auth', 'manage_anon',
           'login_helper', 'login_signal', 'register_signal', 'custom_register',
           'oauth', 'phone', 'password_signal', 'fix_secret', 'substitute_secret', 'set_old_password']
REVOKES = {'profile_delete', 'profile_delete_complete', 'manage_auth', 'register_signal', 'set_old_password'}
HOOK_MODULES = {'manage_global_objects': 'docassemble.webapp.daglobal.hooks',
                'manage_chat_logs': 'docassemble.webapp.monitor.hooks',
                'manage_tts_objects': 'docassemble.webapp.tts.hooks',
                'manage_email_server_objects': 'docassemble.webapp.emailserver.helpers'}
TABLES = ['user', 'user_auth', 'user_roles', 'dict', 'dict_keys', 'uploads_user_auth',
          'uploads', 'global', 'speak', 'chat', 'shortener', 'email', 'email_attachment',
          'user_invite', 'managed_account_cleanup']


@pytest.fixture
def convergence_models(publisher, registration_models):  # noqa: F811
    with publisher.engine.begin() as c:
        for name in ('dict', 'dict_keys'):
            c.execute(text('ALTER TABLE test_' + name + ' ADD COLUMN filename text, ADD COLUMN key text, ADD COLUMN temp_user_id integer'))
        c.execute(text('ALTER TABLE test_dict ADD COLUMN encrypted boolean, ADD COLUMN dictionary text, ADD COLUMN indexno integer'))
        c.execute(text("UPDATE test_dict SET filename='interview',key='session',encrypted=true,dictionary='old',indexno=id"))
        c.execute(text("UPDATE test_dict_keys SET filename='interview',key='session'"))
        c.execute(text("INSERT INTO test_dict (user_id,filename,key,encrypted,dictionary,indexno) VALUES (2,'shared','shared',true,'old',2),(NULL,'temp-interview','temp-session',true,'old',3),(2,'temp-shared','temp-shared',true,'old',4)"))
        c.execute(text("INSERT INTO test_dict_keys (user_id,temp_user_id,filename,key) VALUES (1,NULL,'shared','shared'),(2,NULL,'shared','shared'),(NULL,9,'temp-interview','temp-session'),(2,NULL,'temp-shared','temp-shared'),(NULL,9,'temp-shared','temp-shared')"))
        c.execute(text('ALTER TABLE test_uploads_user_auth ADD COLUMN temp_user_id integer, ADD COLUMN uploads_indexno integer'))
        c.execute(text('INSERT INTO test_uploads_user_auth (temp_user_id,uploads_indexno) VALUES (9,1)'))
        c.execute(text('ALTER TABLE test_uploads ADD COLUMN key text, ADD COLUMN yamlfile text, ADD COLUMN persistent boolean'))
        c.execute(text("INSERT INTO test_uploads VALUES (1,'temp-session','temp-interview',false),(2,'session','interview',true)"))
        definitions = {
            'global': 'key text, user_id integer, temp_user_id integer, value text, encrypted boolean',
            'speak': 'key text, filename text, phrase text, encrypted boolean, upload integer',
            'chat': 'key text, filename text, message text, encrypted boolean, user_id integer, owner_id integer, temp_user_id integer, temp_owner_id integer',
            'shortener': 'short text, user_id integer, temp_user_id integer, uid text, filename text',
            'email': 'short text', 'email_attachment': 'email_id integer, upload integer'}
        for name, fields in definitions.items():
            c.execute(text('CREATE TABLE test_' + name + ' (id serial PRIMARY KEY,' + fields + ')'))
        c.execute(text("INSERT INTO test_global (key,user_id,temp_user_id,value,encrypted) VALUES ('da:uid:session:i:interview:object',NULL,NULL,'old',true),('da:userid:1:personal',1,NULL,'old',true),('da:userid:t9:personal',NULL,9,'old',true),('da:uid:temp-session:i:temp-interview:object',NULL,NULL,'old',true),('da:uid:shared:i:shared:object',NULL,NULL,'old',true)"))
        c.execute(text("INSERT INTO test_speak (key,filename,phrase,encrypted,upload) VALUES ('session','interview','old',true,2),('temp-session','temp-interview','old',true,1),('shared','shared','old',true,NULL)"))
        c.execute(text("INSERT INTO test_chat (key,filename,message,encrypted,user_id,owner_id,temp_user_id,temp_owner_id) VALUES ('session','interview','old',true,1,1,NULL,NULL),('temp-session','temp-interview','old',true,NULL,NULL,9,9)"))
        c.execute(text("INSERT INTO test_shortener (short,user_id,temp_user_id,uid,filename) VALUES ('own',1,NULL,'session','interview'),('temp',NULL,9,'temp-session','temp-interview')"))
        c.execute(text("INSERT INTO test_email (short) VALUES ('own'),('temp')"))
        c.execute(text('INSERT INTO test_email_attachment (email_id,upload) VALUES (1,2),(2,1)'))
    mapping = registry()
    models = dict(registration_models)
    for cls_name, name in [('UserDict', 'dict'), ('UserDictKeys', 'dict_keys'), ('UploadsUserAuth', 'uploads_user_auth'),
                           ('Uploads', 'uploads'), ('GlobalObjectStorage', 'global'), ('SpeakList', 'speak'),
                           ('ChatLog', 'chat'), ('Shortener', 'shortener'), ('Email', 'email'), ('EmailAttachment', 'email_attachment')]:
        cls = type(cls_name, (), {})
        mapping.map_imperatively(cls, Table('test_' + name, MetaData(), autoload_with=publisher.engine))
        models[cls_name] = cls
    models['UserModel'].same_as = lambda self, identity: self.id == identity
    return models


def snapshot(publisher):  # noqa: F811
    with publisher.engine.connect() as c:
        return {name: list(c.execute(text('SELECT * FROM test_' + name + ' ORDER BY 1,2'))) for name in TABLES}


class WebSession(dict):
    modified = False


def invoke(change, session, models, *, managed=True, effect=None, web=None, override=False, delete_shared=False):
    web = web if web is not None else WebSession(tempuser=9, sessions={
        'interview': {'uid': 'session'}, 'temp-interview': {'uid': 'temp-session'}, 'extra': {'uid': 'extra-session'}})
    current = session.get(models['UserModel'], 1 if change == 'manage_auth' else 2)
    user = session.get(models['UserModel'], 1)
    list(current.roles)
    list(user.roles)
    current.is_authenticated = change not in ('manage_anon', 'oauth', 'phone', 'custom_register')
    current.is_anonymous = not current.is_authenticated
    request = SimpleNamespace(method='POST', args={}, values={}, form={'password': 'pw'}, cookies={'secret': 'old'})
    def external(*args, **kwargs):
        if managed:
            assert session.info.get('committed', 0) > 0, 'external effect before SQL commit'
        if effect:
            effect(*args, **kwargs)
    event.listen(session, 'after_commit', lambda session: session.info.__setitem__('committed', session.info.get('committed', 0) + 1))
    def unlocked(*args):
        raise AssertionError('flag-off acquired managed environment lock')
    def denied(*args):
        raise Forbidden()
    manager = SimpleNamespace(hash_password=lambda value: value, get_password=lambda user: user.user_auth.password,
        verify_password=lambda value, user: value == user.user_auth.password,
        find_user_by_email=lambda value: (None, None), after_login_endpoint='after', after_register_endpoint='after',
        after_confirm_endpoint='after', auto_login_at_login=False)
    namespace = dict(models, db=SimpleNamespace(session=session, joinedload=joinedload), dbtableprefix='test_',
        preparation_tables=preparation_tables, select=select, delete=delete, update=update, and_=and_, or_=or_,
        lock_authority=writers.lock_authority if managed else unlocked, change_privilege=writers.change_privilege,
        managed_installation=lambda: managed, partial=partial, copy=copy, re=re, datetime=datetime, zoneinfo=zoneinfo,
        current_user=current, session=web, request=request, daconfig={'delete account deletes shared': delete_shared}, this_thread=SimpleNamespace(),
        current_app=SimpleNamespace(user_manager=manager, _get_current_object=lambda: None,
            config={'USE_PHONE_LOGIN': True, 'USER_PROFILE_FIELDS': [], 'USE_MFA': False,
                    'SESSION_COOKIE_SECURE': True, 'SESSION_COOKIE_SAMESITE': 'Lax'}),
        DAException=PreparationError, DAError=PreparationError, random_string=lambda n: 'device',
        random_alphanumeric=lambda n: 'unique', current_info=lambda **kwargs: {},
        MD5Hash=lambda data: hashlib.md5(data.encode()), pad_to_16=lambda value: value,
        encrypt_dictionary=lambda data, secret: 'new', decrypt_dictionary=lambda data, secret: {'_internal': {'user_local': {'t9': {}}}},
        pack_dictionary=lambda data: 'new', unpack_dictionary=lambda data: {'_internal': {'user_local': {'t9': {}}}},
        encrypt_object=lambda data, secret: 'new', decrypt_object=lambda data, secret: {},
        encrypt_phrase=lambda data, secret: 'new', decrypt_phrase=lambda data, secret: 'phrase',
        logmessage=lambda *args: None, setup_translation=lambda: None, flash=lambda *args: None, word=lambda value: value,
        url_for=lambda endpoint, **kwargs: endpoint, _endpoint_url=lambda value: value, abort=denied,
        redirect=lambda value: SimpleNamespace(location=value, set_cookie=external),
        get_default_timezone=lambda: 'UTC', get_person=lambda *args: user, manual_checkout=external,
        SavedFile=lambda *a, **kw: SimpleNamespace(delete=external), r=Redis(), r_user=Redis(),
        get_server_redis=Redis, get_server_redis_user=Redis, logout_user=external, login_user=external,
        delete_session_info=external, exit_page='exit', get_requester_ip=lambda *args: 'local', email=email)
    pm = DocassemblePluginManager('docassemble')
    pm.add_hookspecs(hookspecs)
    modules = {}
    for name, path in HOOK_MODULES.items():
        function = source_functions('docassemble_webapp/' + path.replace('.', '/') + '.py', {name}, dict(namespace))[name]
        module = ModuleType(path)
        setattr(module, name, pluggy.HookimplMarker('docassemble')(function))
        pm.register(module)
        modules[path] = module
    plugin = ModuleType('test_additive_plugin')
    for name in HOOK_MODULES:
        def callback(mode, kwargs):
            external(mode, **kwargs)
            return 'override' if override else None
        setattr(plugin, name, pluggy.HookimplMarker('docassemble')(tryfirst=True)(callback))
    pm.register(plugin)
    namespace.update(pm=pm, import_module=lambda name: modules[name], BUILTIN_SQL_HOOKS=HOOK_MODULES)
    source_functions('docassemble_webapp/docassemble/webapp/users/managed_effects.py', {'sql_hook', 'run_plugin_hook', 'run_effects'}, namespace)
    for name in HOOK_MODULES:
        namespace[name] = lambda mode, _name=name, **kwargs: getattr(pm.hook, _name)(mode=mode, kwargs=kwargs)
    source_functions('docassemble_webapp/docassemble/webapp/sessions.py', {'get_session', 'update_session', 'delete_obsolete'}, namespace)
    source_functions('docassemble_webapp/docassemble/webapp/interview/helpers.py', {
        'sub_temp_user_dict_key', 'sub_temp_other', 'save_user_dict_key', 'substitute_secret', 'fix_secret',
        'reset_user_dict', 'delete_temp_user_data', 'clear_temp_user_redis', 'delete_user_data', 'delete_managed_user_data',
        'finish_managed_user_cleanup', 'delete_managed_account', 'user_interviews'}, namespace)
    source_functions('docassemble_webapp/docassemble/webapp/users/helpers.py', {
        'login_or_register', 'assign_registration_role', 'update_last_login', 'set_user_info'}, namespace)
    source_functions('docassemble_webapp/docassemble/webapp/users/signals.py', {
        'on_user_login', 'on_password_change', 'on_register_hook'}, namespace)
    if change.startswith('profile_delete'):
        request.method = 'GET'
        request.args['delete_account_complete' if change.endswith('complete') else 'delete_account'] = '1'
        namespace['get_server_redis'] = Redis
        return source_functions('docassemble_webapp/docassemble/webapp/users/views.py', {'edit_user_profile_page'}, namespace)['edit_user_profile_page'](1)
    if change.startswith('manage_'):
        namespace['ManageAccountForm'] = lambda *args: Form(change)
        return source_functions('docassemble_webapp/docassemble/webapp/users/views.py', {'manage_account'}, namespace)['manage_account']()
    if change in ('login_helper', 'login_signal', 'register_signal', 'password_signal'):
        name = {'login_helper': 'login_or_register', 'login_signal': 'on_user_login',
                'register_signal': 'on_register_hook', 'password_signal': 'on_password_change'}[change]
        return namespace[name](None, user, *(['login'] if change == 'login_helper' else []))
    if change == 'fix_secret':
        return namespace['fix_secret'](user=user)
    if change == 'substitute_secret':
        return namespace['substitute_secret']('old', 'new', user=user)
    if change == 'set_old_password':
        return namespace['set_user_info']({'user_id': 1, 'password': 'newpass', 'old_password': 'secret', 'active': False})
    if change == 'oauth':
        request.method = 'GET'
        namespace.update(OAuthSignIn=SimpleNamespace(get_provider=lambda *args: SimpleNamespace(
            enabled=lambda: True, callback=lambda: ('oauth$new', 'new', 'new@example.invalid', {}))), verify_email=lambda value: True)
        return source_functions('docassemble_webapp/docassemble/webapp/auth/views.py', {'oauth_callback'}, namespace)['oauth_callback']('google')
    if change == 'phone':
        web['phone_number'] = '123'
        namespace['PhoneLoginVerifyForm'] = lambda *args: Form(change, submit=True, phone_number='123')
        namespace['r'] = SimpleNamespace(delete=external)
        return source_functions('docassemble_webapp/docassemble/webapp/phonelogin/views.py', {'phone_login_verify'}, namespace)['phone_login_verify']()
    assert change == 'custom_register'
    form = Form(change, timezone='UTC', next='', reg_next='', country='', subdivisionfirst='', subdivisionsecond='',
        subdivisionthird='', organization='', language='', invite_token='')
    form.data = {'email': 'new@example.invalid', 'password': 'pw'}
    manager.login_form = lambda: None
    manager.register_form = lambda *args: form
    manager.require_invitation = False
    manager.send_registered_email = True
    manager.enable_confirm_email = False
    manager.auto_login_after_register = True
    namespace.update(_get_safe_next_param=lambda *args: 'after', _call_or_get=lambda value: value,
        as_int=int, urllibquote=lambda value: value)
    def add_object(cls, **kwargs):
        obj = cls(**kwargs)
        session.add(obj)
        return obj
    manager.db_adapter = SimpleNamespace(UserClass=models['UserModel'], UserAuthClass=models['UserAuthModel'],
        UserEmailClass=None, UserProfileClass=None, UserInvitationClass=models['MyUserInvitation'],
        add_object=add_object, commit=session.commit, delete_object=session.delete,
        find_first_object=lambda cls, **kwargs: session.execute(select(cls).filter_by(**kwargs)).scalar(),
        update_object=lambda obj, **kwargs: [setattr(obj, name, value) for name, value in kwargs.items()])
    def signal(sender, **kwargs):
        external()
        namespace['on_register_hook'](sender, **kwargs)
    def auto_login(user, *args):
        external()
        namespace['on_user_login'](None, user)
        return 'logged-in'
    namespace['docassemble_flask_user'] = SimpleNamespace(views=SimpleNamespace(_send_registered_email=external, _do_login_user=auto_login),
        signals=SimpleNamespace(user_registered=SimpleNamespace(send=signal)))
    return source_functions('docassemble_webapp/docassemble/webapp/users/views.py', {'custom_register'}, namespace)['custom_register']()


def assert_effect(publisher, change, managed=True):  # noqa: F811
    with publisher.engine.connect() as c:
        if change.startswith('profile_delete') or change == 'manage_auth':
            assert c.scalar(text('SELECT active FROM test_user WHERE id=1')) is False
            assert c.scalar(text('SELECT count(*) FROM test_user_roles WHERE user_id=1')) == 0
            assert c.scalar(text('SELECT count(*) FROM test_chat WHERE user_id=1 OR owner_id=1')) == 0
            assert c.scalar(text('SELECT count(*) FROM test_shortener WHERE user_id=1')) == 0
            assert c.scalar(text('SELECT count(*) FROM test_global WHERE user_id=1')) == 0
            assert c.scalar(text("SELECT state FROM test_managed_account_cleanup WHERE user_id=1")) == 'complete'
            assert c.scalar(text("SELECT count(*) FROM test_dict_keys WHERE key='shared'")) == (0 if change.endswith('complete') else 1)
            assert c.scalar(text("SELECT count(*) FROM test_dict WHERE key='shared'")) == (0 if change.endswith('complete') else 1)
        elif change == 'manage_anon':
            assert c.scalar(text('SELECT count(*) FROM test_dict_keys WHERE temp_user_id=9')) == 0
            assert c.scalar(text('SELECT count(*) FROM test_chat WHERE temp_user_id=9 OR temp_owner_id=9')) == 0
            assert c.scalar(text('SELECT count(*) FROM test_global WHERE temp_user_id=9')) == 0
            assert c.scalar(text('SELECT count(*) FROM test_shortener WHERE temp_user_id=9')) == 0
        else:
            assert c.scalar(text("SELECT dictionary FROM test_dict WHERE key='session'")) == 'new'
            assert c.scalar(text("SELECT value FROM test_global WHERE key='da:userid:1:personal'")) == ('old' if change in ('custom_register', 'oauth', 'phone') else 'new')
            assert c.scalar(text("SELECT phrase FROM test_speak WHERE key='session'")) == 'new'
            assert c.scalar(text("SELECT message FROM test_chat WHERE key='session'")) == 'new'
            if change in ('login_helper', 'login_signal', 'register_signal', 'custom_register', 'oauth', 'phone'):
                owner = 3 if change in ('custom_register', 'oauth', 'phone') else 1
                assert c.scalar(text('SELECT count(*) FROM test_dict_keys WHERE temp_user_id=9')) == 0
                if not managed and change == 'phone':
                    assert c.scalar(text('SELECT temp_user_id FROM test_uploads_user_auth WHERE uploads_indexno=1')) == 9
                    assert c.scalar(text("SELECT temp_user_id FROM test_chat WHERE key='temp-session'")) == 9
                    return
                assert c.scalar(text('SELECT user_id FROM test_uploads_user_auth WHERE uploads_indexno=1')) == owner
                assert c.scalar(text("SELECT user_id FROM test_chat WHERE key='temp-session'")) == owner
                assert c.scalar(text("SELECT owner_id FROM test_chat WHERE key='temp-session'")) == owner
                assert c.scalar(text("SELECT user_id FROM test_global WHERE key=:key"), {'key': 'da:userid:' + str(owner) + ':personal'}) == owner
            if change == 'register_signal':
                assert c.scalar(text('SELECT role_id FROM test_user_roles WHERE user_id=1')) == 3
            if change == 'set_old_password':
                assert c.scalar(text('SELECT password FROM test_user_auth WHERE user_id=1')) == 'newpass'
                assert c.scalar(text('SELECT active FROM test_user WHERE id=1')) is False


@pytest.mark.parametrize('change', CHANGES)
@pytest.mark.parametrize('first', ['writer', 'publication'])
def test_enclosing_builtin_actual_race(publisher, convergence_models, change, first):  # noqa: F811
    candidate = ready(publisher)
    before = snapshot(publisher), protocol_snapshot(publisher), intent(publisher)
    locked, release = Event(), Event()
    pids = []
    project = publisher.project
    if first == 'publication':
        def pause(c, *args):
            result = project(c, *args)
            pids.append(c.scalar(text('SELECT pg_backend_pid()')))
            locked.set()
            assert release.wait(20)
            return result
        publisher.project = pause
    def write():
        with Session(publisher.engine) as session:
            def pause(session):
                if not locked.is_set():
                    session.flush()
                    pids.append(session.scalar(text('SELECT pg_backend_pid()')))
                    locked.set()
                    assert release.wait(20)
            if first == 'writer':
                event.listen(session, 'before_commit', pause)
            invoke(change, session, convergence_models)
    with ThreadPoolExecutor(2) as pool:
        leading = pool.submit(write if first == 'writer' else approve, *(() if first == 'writer' else (publisher, candidate)))
        if not locked.wait(10):
            leading.result(timeout=1)
            pytest.fail('writer never reached its commit')
        trailing = pool.submit(approve if first == 'writer' else write, *((publisher, candidate) if first == 'writer' else ()))
        try:
            wait_for_approval_lock(publisher.engine, pids[0])
            assert (snapshot(publisher), protocol_snapshot(publisher), intent(publisher)) == before
        finally:
            release.set()
        leading.result(timeout=10)
        if first == 'writer' and change in REVOKES:
            with pytest.raises(PreparationError, match='forbidden'):
                trailing.result(timeout=10)
            assert (protocol_snapshot(publisher), intent(publisher)) == before[1:]
        else:
            trailing.result(timeout=10)
            assert len(intent(publisher)[0]) == len(intent(publisher)[1]) == 1
            state = protocol_snapshot(publisher)
            assert len(state['revision']) == 2 and len(state['installation_attempt']) == 1
    assert_effect(publisher, change)


@pytest.mark.parametrize('change', CHANGES)
def test_enclosing_builtin_precommit_failure_rolls_back_every_sql_and_session_effect(publisher, convergence_models, change):  # noqa: F811
    before = snapshot(publisher), protocol_snapshot(publisher), intent(publisher)
    web = WebSession(tempuser=9, sessions={'interview': {'uid': 'session'}, 'temp-interview': {'uid': 'temp-session'}})
    if change == 'phone':
        web['phone_number'] = '123'
    old_web = copy.deepcopy(web)
    effects = []
    with Session(publisher.engine) as session:
        def fail(session):
            session.flush()
            raise RuntimeError('precommit failure')
        event.listen(session, 'before_commit', fail)
        with pytest.raises(RuntimeError, match='precommit failure'):
            invoke(change, session, convergence_models, effect=lambda *a, **kw: effects.append(1), web=web)
    assert (snapshot(publisher), protocol_snapshot(publisher), intent(publisher)) == before
    assert not effects
    assert web == old_web


@pytest.mark.parametrize('change', CHANGES)
def test_enclosing_builtin_flag_off_legacy_without_environment_lock(publisher, convergence_models, change):  # noqa: F811
    before = protocol_snapshot(publisher)
    with Session(publisher.engine) as session:
        invoke(change, session, convergence_models, managed=False)
    # Legacy secret substitution leaves the outer transaction uncommitted only
    # in managed caller-owned mode; flag-off still uses its old per-hook commits.
    if not change.startswith('profile_delete') and change != 'manage_auth':
        assert_effect(publisher, change, managed=False)
    else:
        with publisher.engine.connect() as c:
            assert c.scalar(text('SELECT active FROM test_user WHERE id=1')) is False
    assert protocol_snapshot(publisher) == before


@pytest.mark.parametrize('change', CHANGES)
def test_enclosing_builtin_missing_baseline_rejects_without_effects(publisher, convergence_models, change):  # noqa: F811
    with publisher.engine.begin() as c:
        c.execute(publisher.tables['environment_state'].update().values(managed=False))
    before = snapshot(publisher)
    effects = []
    with Session(publisher.engine) as session:
        with pytest.raises(PreparationError, match='no explicitly managed baseline'):
            invoke(change, session, convergence_models, effect=lambda *a, **kw: effects.append(1))
    assert snapshot(publisher) == before
    assert not effects


@pytest.mark.parametrize('change', ['login_helper', 'oauth', 'phone', 'custom_register', 'set_old_password'])
def test_managed_builtin_sql_cannot_be_overridden_by_firstresult_plugin(publisher, convergence_models, change):  # noqa: F811
    calls = []
    commits = []
    with Session(publisher.engine) as session:
        event.listen(session, 'after_commit', lambda session: commits.append(1))
        invoke(change, session, convergence_models, override=True, effect=lambda *a, **kw: calls.append(1))
    assert commits == [1]
    assert calls
    assert_effect(publisher, change)


@pytest.mark.parametrize('change', CHANGES)
def test_enclosing_builtin_explicit_commit_count(publisher, convergence_models, change):  # noqa: F811
    commits = []
    with Session(publisher.engine) as session:
        event.listen(session, 'after_commit', lambda session: commits.append(1))
        invoke(change, session, convergence_models)
    assert len(commits) == (2 if change.startswith('profile_delete') or change == 'manage_auth' else 1)
    assert_effect(publisher, change)


def test_additive_plugin_failure_is_postcommit_not_a_sql_rollback(publisher, convergence_models):  # noqa: F811
    def fail(*args, **kwargs):
        raise RuntimeError('external plugin failure')
    with Session(publisher.engine) as session:
        with pytest.raises(RuntimeError, match='external plugin failure'):
            invoke('login_helper', session, convergence_models, effect=fail, override=True)
    assert_effect(publisher, 'login_helper')


def test_enclosing_deletion_plugin_failure_is_durable_and_redacted(publisher, convergence_models):  # noqa: F811
    def fail(*args, **kwargs):
        raise RuntimeError('credential=sensitive@example.invalid')
    with Session(publisher.engine) as session:
        with pytest.raises(PreparationError, match='Account deactivated; account cleanup failed') as error:
            invoke('profile_delete', session, convergence_models, effect=fail)
        assert 'sensitive' not in str(error.value)
    with publisher.engine.connect() as c:
        assert c.scalar(text('SELECT active FROM test_user WHERE id=1')) is False
        assert list(c.execute(text("SELECT state,error FROM test_managed_account_cleanup WHERE user_id=1"))) == [('failed', 'account cleanup failed')]
        assert c.scalar(text('SELECT count(*) FROM test_global WHERE user_id=1')) == 0
    calls = []
    with Session(publisher.engine) as session:
        with pytest.raises(PreparationError, match='duplicate cleanup is not supported'):
            invoke('manage_auth', session, convergence_models, effect=lambda *a, **kw: calls.append(1))
    assert not calls


def test_registration_mail_compensation_cleans_migrated_ownership_without_orphans(publisher, convergence_models):  # noqa: F811
    calls = []
    def fail_mail_once(*args, **kwargs):
        if len(args) == 3 and hasattr(args[0], 'social_id') and not calls:
            calls.append(1)
            raise RuntimeError('uncertain mail delivery')
    with Session(publisher.engine) as session:
        with pytest.raises(RuntimeError, match='uncertain mail delivery'):
            invoke('custom_register', session, convergence_models, effect=fail_mail_once)
    with publisher.engine.connect() as c:
        assert c.scalar(text('SELECT active FROM test_user WHERE id=3')) is False
        assert c.scalar(text("SELECT state FROM test_managed_account_cleanup WHERE user_id=3")) == 'complete'
        for name in ('dict_keys', 'uploads_user_auth', 'global', 'chat'):
            assert c.scalar(text('SELECT count(*) FROM test_' + name + ' WHERE user_id=3')) == 0
        assert c.scalar(text('SELECT count(*) FROM test_chat WHERE owner_id=3')) == 0
        assert c.scalar(text("SELECT count(*) FROM test_dict WHERE key='temp-session'")) == 0
    assert calls == [1]


def test_flag_off_firstresult_plugin_still_substitutes_for_builtin_hooks(publisher, convergence_models):  # noqa: F811
    calls = []
    with Session(publisher.engine) as session:
        invoke('login_helper', session, convergence_models, managed=False, override=True, effect=lambda *a, **kw: calls.append(1))
    assert calls
    with publisher.engine.connect() as c:
        assert c.scalar(text("SELECT dictionary FROM test_dict WHERE key='session'")) == 'new'
        assert c.scalar(text("SELECT value FROM test_global WHERE key='da:userid:1:personal'")) == 'old'
        assert c.scalar(text("SELECT phrase FROM test_speak WHERE key='session'")) == 'old'
        assert c.scalar(text("SELECT temp_user_id FROM test_chat WHERE key='temp-session'")) == 9


def test_legacy_cookie_session_is_captured_without_precommit_mutation(publisher, convergence_models):  # noqa: F811
    web = WebSession(tempuser=9, i='interview', uid='session')
    commits = []
    with Session(publisher.engine) as session:
        def before_commit(session):
            assert web == {'tempuser': 9, 'i': 'interview', 'uid': 'session'}
            commits.append(1)
        event.listen(session, 'before_commit', before_commit)
        invoke('login_helper', session, convergence_models, web=web)
    assert commits == [1]
    assert 'i' not in web and 'uid' not in web and 'tempuser' not in web
    assert web['sessions']['interview']['key_logged'] is True
    assert_effect(publisher, 'login_helper')


@pytest.mark.parametrize('delete_shared', [False, True])
def test_anonymous_deletion_captures_shared_metadata_and_preserves_selected_semantics(publisher, convergence_models, delete_shared):  # noqa: F811
    with Session(publisher.engine) as session:
        invoke('manage_anon', session, convergence_models, delete_shared=delete_shared)
    with publisher.engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM test_dict WHERE key='temp-shared'")) == (0 if delete_shared else 1)
        assert c.scalar(text("SELECT count(*) FROM test_dict_keys WHERE key='temp-shared'")) == (0 if delete_shared else 1)
        assert c.scalar(text('SELECT count(*) FROM test_dict_keys WHERE temp_user_id=9')) == 0
