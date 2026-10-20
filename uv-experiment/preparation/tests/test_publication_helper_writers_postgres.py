"""Execute named helper source bodies with real SQL sessions, not mock writers.

Application import dependencies/permission context are supplied explicitly; this
is not full Flask request qualification. SQL mutation and commits are real.
"""
import ast
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session, registry

from docassemble.base.managed_preparation import PreparationError
from test_preparation_postgres import protocol  # noqa: F401
from test_publication_postgres import wait_for_approval_lock
from test_publication_writers_postgres import SOURCE, publisher, ready, approve, intent, writers, snapshot  # noqa: F401


def helper(name, session, managed=True):
    source = ast.parse((SOURCE / 'docassemble_webapp/docassemble/webapp/users/helpers.py').read_text())
    function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == name)
    function.decorator_list = []
    mapping = registry()
    class User:
        pass
    users = publisher_user_table(session)
    mapping.map_imperatively(User, users)
    namespace = {'current_user': SimpleNamespace(has_role_or_permission=lambda *args, **kwargs: True),
        'db': SimpleNamespace(session=session), 'dbtableprefix': 'test_',
        'managed_installation': lambda: managed, 'DAException': PreparationError,
        'PreparationError': PreparationError, 'deactivate_user': writers.deactivate_user,
        'delete_privilege': writers.delete_privilege, 'select': select, 'UserModel': User}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE / 'docassemble_webapp/docassemble/webapp/users/helpers.py'), 'exec'), namespace)
    return namespace[name]


def publisher_user_table(session):
    from sqlalchemy import Table, MetaData
    return Table('test_user', MetaData(), autoload_with=session.get_bind())


@pytest.mark.parametrize('managed', [True, False])
def test_make_user_inactive_whitespace_email(publisher, managed):  # noqa: F811
    with Session(publisher.engine) as session:
        helper('make_user_inactive', session, managed)(None, ' \t one \n ')
    with publisher.engine.connect() as c:
        assert c.scalar(text('SELECT active FROM test_user WHERE id=1')) is False


@pytest.mark.parametrize('managed', [True, False])
def test_make_user_inactive_email_type_validation(publisher, managed):  # noqa: F811
    with Session(publisher.engine) as session:
        with pytest.raises(AssertionError):
            helper('make_user_inactive', session, managed)(None, 123)
    with publisher.engine.connect() as c:
        assert c.scalar(text('SELECT active FROM test_user WHERE id=1')) is True


def custom_role(publisher):  # noqa: F811
    with publisher.engine.begin() as c:
        c.execute(text("INSERT INTO test_role VALUES (3,'installer')"))
        c.execute(text('UPDATE test_user_roles SET role_id=3 WHERE user_id=1'))
    publisher.authorize.policy = lambda: {'allowed': {'installer': ['manage_packages']},
        'allow_updates': True, 'developer_can_install': True}


def test_remove_privilege_helper_race_no_partial_intent(publisher):  # noqa: F811
    custom_role(publisher)
    candidate = ready(publisher)
    before = snapshot(publisher)
    locked, release = Event(), Event()
    pids = []
    def revoke():
        with Session(publisher.engine) as session:
            def pause(session):
                pids.append(session.scalar(text('SELECT pg_backend_pid()')))
                locked.set()
                assert release.wait(15)
            event.listen(session, 'before_commit', pause)
            helper('remove_privilege', session)('installer')
    with ThreadPoolExecutor(2) as pool:
        mutation = pool.submit(revoke)
        assert locked.wait(10)
        approval = pool.submit(approve, publisher, candidate)
        try:
            wait_for_approval_lock(publisher.engine, pids[0])
            assert intent(publisher) == ([], [])
            assert snapshot(publisher) == before
        finally:
            release.set()
        mutation.result(timeout=10)
        with pytest.raises(PreparationError, match='forbidden'):
            approval.result(timeout=10)
    assert snapshot(publisher) == before
    assert intent(publisher) == ([], [])
    with publisher.engine.connect() as c:
        assert c.scalar(text('SELECT count(*) FROM test_role WHERE id=3')) == 0
        assert c.scalar(text('SELECT count(*) FROM test_user_roles WHERE role_id=3')) == 0


def test_remove_privilege_helper_lost_response_revocation(publisher):  # noqa: F811
    custom_role(publisher)
    candidate = ready(publisher)
    approve(publisher, candidate)
    before = snapshot(publisher), intent(publisher)
    with Session(publisher.engine) as session:
        helper('remove_privilege', session)('installer')
    with pytest.raises(PreparationError, match='forbidden'):
        approve(publisher, candidate)
    assert (snapshot(publisher), intent(publisher)) == before


def test_delete_privilege_rollback_and_builtin_guard(publisher):  # noqa: F811
    custom_role(publisher)
    with pytest.raises(RuntimeError):
        with publisher.engine.begin() as c:
            writers.delete_privilege(c, 'test_', 'installer')
            raise RuntimeError('rollback')
    with publisher.engine.connect() as c:
        assert c.scalar(text('SELECT count(*) FROM test_user_roles WHERE role_id=3')) == 1
    with Session(publisher.engine) as session:
        with pytest.raises(PreparationError, match='built-in'):
            helper('remove_privilege', session)('admin')
