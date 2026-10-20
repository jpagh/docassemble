"""Actual old-password/secret SQL bodies; external hooks and crypto supplied.

No authenticated Flask or non-SQL hook atomicity qualification is claimed.
"""
import hashlib
from functools import partial
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import MetaData, Table, and_, or_, event, select, text
from sqlalchemy.orm import Session, registry

from docassemble.base.managed_preparation import PreparationError
from test_preparation_postgres import protocol  # noqa: F401
from test_publication_writers_postgres import publisher, writers, ready, approve, intent  # noqa: F401
from test_publication_postgres import wait_for_approval_lock
from test_publication_account_writers_postgres import (account_models, account_helper,  # noqa: F401
    source_functions, protocol_snapshot)  # noqa: F401


@pytest.fixture
def secret_models(publisher, account_models):  # noqa: F811
    with publisher.engine.begin() as c:
        for name in ('dict', 'dict_keys'):
            c.execute(text('ALTER TABLE test_' + name + ' ADD COLUMN filename text, ADD COLUMN key text'))
        c.execute(text('ALTER TABLE test_dict ADD COLUMN encrypted boolean, ADD COLUMN dictionary text, ADD COLUMN indexno integer'))
        c.execute(text("UPDATE test_dict SET filename='interview', key='session', encrypted=true, dictionary='old', indexno=1"))
        c.execute(text("UPDATE test_dict_keys SET filename='interview', key='session'"))
        c.execute(text("INSERT INTO test_role VALUES (3,'user')"))
        for name, fields in [('global', 'key text, user_id integer, value text'),
                             ('speak', 'key text, filename text, phrase text'),
                             ('chat', 'key text, filename text, message text')]:
            c.execute(text('CREATE TABLE test_' + name + ' (id serial PRIMARY KEY, encrypted boolean, ' + fields + ')'))
        c.execute(text("INSERT INTO test_global (key,user_id,value,encrypted) VALUES ('da:uid:session:i:interview:object',NULL,'old',true), ('personal',1,'old',true)"))
        c.execute(text("INSERT INTO test_speak (key,filename,phrase,encrypted) VALUES ('session','interview','old',true)"))
        c.execute(text("INSERT INTO test_chat (key,filename,message,encrypted) VALUES ('session','interview','old',true)"))
    mapping = registry()
    models = dict(account_models)
    for cls_name, table_name in [('UserDict', 'dict'), ('UserDictKeys', 'dict_keys')]:
        cls = type(cls_name, (), {})
        mapping.map_imperatively(cls, Table('test_' + table_name, MetaData(), autoload_with=publisher.engine))
        models[cls_name] = cls
    for cls_name, table_name in [('GlobalObjectStorage', 'global'), ('SpeakList', 'speak'), ('ChatLog', 'chat')]:
        cls = type(cls_name, (), {})
        mapping.map_imperatively(cls, Table('test_' + table_name, MetaData(), autoload_with=publisher.engine))
        models[cls_name] = cls
    return models


def actual_hooks(session, models):
    namespace = dict(models, db=SimpleNamespace(session=session), select=select, and_=and_, or_=or_,
        encrypt_object=lambda data, secret: 'new', decrypt_object=lambda data, secret: {},
        encrypt_phrase=lambda data, secret: 'new', decrypt_phrase=lambda data, secret: 'phrase',
        logmessage=lambda message: None)
    callbacks = {}
    for path, name in [('daglobal/hooks.py', 'manage_global_objects'),
                       ('tts/hooks.py', 'manage_tts_objects'), ('monitor/hooks.py', 'manage_chat_logs')]:
        function = source_functions('docassemble_webapp/docassemble/webapp/' + path, {name}, dict(namespace))[name]
        callbacks[name] = lambda mode, _function=function, **kwargs: _function(mode, kwargs)
    return callbacks


def hook_snapshot(publisher):  # noqa: F811
    with publisher.engine.connect() as c:
        return [list(c.scalars(text('SELECT ' + field + ' FROM test_' + name + ' ORDER BY id')))
                for name, field in [('global', 'value'), ('speak', 'phrase'), ('chat', 'message')]]


def secret_writer(session, models, *, managed=True, temp=False, hook=None):
    callback = hook or (lambda *a, **k: None)
    namespace = dict(models, db=SimpleNamespace(session=session), dbtableprefix='test_',
        select=select, and_=and_, managed_installation=lambda: managed,
        lock_authority=writers.lock_authority, current_user=SimpleNamespace(id=1),
        request=SimpleNamespace(cookies={}), session={'tempuser': 9} if temp else {},
        random_string=lambda n: 'device', current_info=lambda **k: {}, this_thread=SimpleNamespace(),
        manage_global_objects=callback, manage_tts_objects=callback, manage_chat_logs=callback,
        decrypt_dictionary=lambda data, secret: {'_internal': {'user_local': {'t9': {}}}},
        encrypt_dictionary=lambda data, secret: 'new', unpack_dictionary=lambda data: {},
        pack_dictionary=lambda data: 'new', logmessage=lambda message: None, partial=partial,
        run_effects=lambda effects: [effect() for effect in effects])
    if hook is None:
        namespace.update(actual_hooks(session, models))
    namespace['sql_hook'] = lambda name, mode, effects, **kwargs: namespace[name](mode, transaction_owned=True, **kwargs)
    return source_functions('docassemble_webapp/docassemble/webapp/interview/helpers.py',
        {'substitute_secret'}, namespace)['substitute_secret']


def edit_writer(session, models, **kwargs):
    edit = account_helper('set_user_info', session, models)
    edit.__globals__.update(substitute_secret=secret_writer(session, models, **kwargs),
        pad_to_16=lambda value: value, MD5Hash=lambda data: hashlib.md5(data.encode()))
    manager = edit.__globals__['current_app'].user_manager
    manager.get_password = lambda user: user.user_auth.password
    manager.verify_password = lambda password, user: password == user.user_auth.password
    return edit


def snapshot(publisher):  # noqa: F811
    with publisher.engine.connect() as c:
        return (c.scalar(text('SELECT password FROM test_user_auth WHERE user_id=1')),
            c.scalar(text('SELECT active FROM test_user WHERE id=1')),
            list(c.scalars(text('SELECT role_id FROM test_user_roles WHERE user_id=1'))),
            list(c.scalars(text('SELECT dictionary FROM test_dict ORDER BY id'))))


@pytest.mark.parametrize('change', [{'active': False}, {'privileges': ['user']}, {}])
@pytest.mark.parametrize('first', ['writer', 'publication'])
def test_old_password_actual_writer_race(publisher, secret_models, change, first):  # noqa: F811
    candidate = ready(publisher)
    before = protocol_snapshot(publisher)
    locked, release = Event(), Event()
    pids, commits = [], []
    original = publisher.project
    if first == 'publication':
        def project(c, actor, candidate):
            pids.append(c.scalar(text('SELECT pg_backend_pid()')))
            locked.set()
            assert release.wait(15)
            return original(c, actor, candidate)
        publisher.project = project
    def edit():
        with Session(publisher.engine) as session:
            if first == 'writer':
                def pause(session):
                    pids.append(session.scalar(text('SELECT pg_backend_pid()')))
                    locked.set()
                    assert release.wait(15)
                event.listen(session, 'before_commit', pause)
            event.listen(session, 'after_commit', lambda session: commits.append(1))
            edit_writer(session, secret_models)({'user_id': 1, 'password': 'newpass', 'old_password': 'secret', **change})
    with ThreadPoolExecutor(2) as pool:
        leading = pool.submit(edit if first == 'writer' else approve, *(() if first == 'writer' else (publisher, candidate)))
        assert locked.wait(10)
        trailing = pool.submit(approve if first == 'writer' else edit, *((publisher, candidate) if first == 'writer' else ()))
        try:
            wait_for_approval_lock(publisher.engine, pids[0])
            assert snapshot(publisher) == ('secret', True, [1], ['old'])
            assert hook_snapshot(publisher) == [['old', 'old'], ['old'], ['old']]
            assert protocol_snapshot(publisher) == before
        finally:
            release.set()
        leading.result(timeout=10)
        if first == 'writer' and change:
            with pytest.raises(PreparationError, match='forbidden'):
                trailing.result(timeout=10)
            assert protocol_snapshot(publisher) == before
            assert intent(publisher) == ([], [])
        else:
            trailing.result(timeout=10)
            assert len(intent(publisher)[0]) == 1
    assert commits == [1]  # includes actual daglobal 6/7, tts 4 and monitor 6 hooks
    assert hook_snapshot(publisher) == [['new', 'new'], ['new'], ['new']]
    assert snapshot(publisher) == ('newpass', not bool(change.get('active') is False),
        [3] if 'privileges' in change else [1], ['new'])


@pytest.mark.parametrize('failure', ['password', 'role', 'hook', 'commit'])
def test_old_password_actual_rollback(publisher, secret_models, failure):  # noqa: F811
    before = snapshot(publisher), hook_snapshot(publisher), protocol_snapshot(publisher)
    calls = []
    def hook(*a, **k):
        calls.append(1)
        raise RuntimeError('external failure')
    with Session(publisher.engine) as session:
        edit = edit_writer(session, secret_models, hook=hook if failure == 'hook' else None)
        if failure == 'commit':
            def fail(session):
                raise RuntimeError('SQL failure')
            event.listen(session, 'before_commit', fail)
        with pytest.raises((PreparationError, RuntimeError)):
            edit({'user_id': 1, 'password': 'newpass',
                'old_password': 'wrong' if failure == 'password' else 'secret', 'active': False,
                'privileges': ['missing'] if failure == 'role' else ['user']})
        session.rollback()
    assert (snapshot(publisher), hook_snapshot(publisher), protocol_snapshot(publisher)) == before
    assert bool(calls) == (failure == 'hook')  # external effects are NOT rolled back


@pytest.mark.parametrize('managed', [True, False])
@pytest.mark.parametrize('temp', [True, False])
def test_substitute_secret_commit_ownership(publisher, secret_models, managed, temp):  # noqa: F811
    commits = []
    with Session(publisher.engine) as session:
        event.listen(session, 'after_commit', lambda session: commits.append(1))
        assert secret_writer(session, secret_models, managed=managed, temp=temp,
            hook=lambda *a, **k: None)('old', 'new', **({'effects': []} if managed else {})) == 'new'
        assert len(commits) == (0 if managed else (2 if temp else 1))
        if managed:
            assert snapshot(publisher)[3] == ['old']
            session.rollback()
    assert snapshot(publisher)[3] == (['old'] if managed else ['new'])


def test_old_password_missing_baseline_no_hook(publisher, secret_models):  # noqa: F811
    with publisher.engine.begin() as c:
        c.execute(publisher.tables['environment_state'].update().values(managed=False))
    calls = []
    with Session(publisher.engine) as session:
        with pytest.raises(PreparationError, match='no explicitly managed baseline'):
            edit_writer(session, secret_models, hook=lambda *a, **k: calls.append(1))(
                {'user_id': 1, 'password': 'newpass', 'old_password': 'secret'})
        session.rollback()
    assert not calls
    assert snapshot(publisher) == ('secret', True, [1], ['old'])


@pytest.mark.parametrize('temp', [True, False])
def test_substitute_secret_actual_hooks_flag_off(publisher, secret_models, temp):  # noqa: F811
    commits = []
    with Session(publisher.engine) as session:
        event.listen(session, 'after_commit', lambda session: commits.append(1))
        substitute = secret_writer(session, secret_models, managed=False, temp=temp)
        def forbidden_lock(*args):
            raise AssertionError('flag-off acquired environment lock')
        substitute.__globals__['lock_authority'] = forbidden_lock
        substitute('old', 'new')
    assert len(commits) == (7 if temp else 5)
    assert hook_snapshot(publisher) == [['new', 'new'], ['new'], ['new']]
