"""Exact privilege view body with real Sessions; decorators/request auth not qualified."""
import ast
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from docassemble.base.managed_preparation import PreparationError
from test_preparation_postgres import protocol  # noqa: F401
from test_publication_postgres import wait_for_approval_lock
from test_publication_writers_postgres import SOURCE, publisher, ready, approve, intent, writers, snapshot  # noqa: F401
from test_publication_helper_writers_postgres import custom_role


def view(session):
    path = SOURCE / 'docassemble_webapp/docassemble/webapp/users/views.py'
    source = ast.parse(path.read_text())
    function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == 'delete_privilege')
    function.decorator_list = []
    messages = []
    namespace = {'db': SimpleNamespace(session=session), 'dbtableprefix': 'test_',
        'managed_installation': lambda: True, 'PreparationError': PreparationError,
        'delete_privilege_for_view': writers.delete_privilege_for_view,
        'setup_translation': lambda: None, 'word': lambda message: message,
        'flash': lambda message, category: messages.append((message, category)),
        'redirect': lambda destination: destination, 'url_for': lambda destination: destination}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['delete_privilege'], messages


def setup_role(publisher):  # noqa: F811
    custom_role(publisher)
    with publisher.engine.begin() as c:
        c.execute(text("INSERT INTO test_role VALUES (4,'user')"))


def memberships(publisher):  # noqa: F811
    with publisher.engine.connect() as c:
        return list(c.execute(text('SELECT user_id,role_id FROM test_user_roles ORDER BY user_id,role_id')))


def test_delete_privilege_view_race_atomic_default_role(publisher):  # noqa: F811
    setup_role(publisher)
    candidate = ready(publisher)
    before = snapshot(publisher)
    before_memberships = memberships(publisher)
    locked, release = Event(), Event()
    pids = []
    def revoke():
        with Session(publisher.engine) as session:
            def pause(session):
                pids.append(session.scalar(text('SELECT pg_backend_pid()')))
                locked.set()
                assert release.wait(15)
            event.listen(session, 'before_commit', pause)
            delete_role, messages = view(session)
            assert delete_role(3) == 'users.privilege_list'
            assert messages == [('The role installer was deleted.', 'success')]
    with ThreadPoolExecutor(2) as pool:
        mutation = pool.submit(revoke)
        assert locked.wait(10)
        approval = pool.submit(approve, publisher, candidate)
        try:
            wait_for_approval_lock(publisher.engine, pids[0])
            assert memberships(publisher) == before_memberships
            assert snapshot(publisher) == before
            assert intent(publisher) == ([], [])
        finally:
            release.set()
        mutation.result(timeout=10)
        with pytest.raises(PreparationError, match='forbidden'):
            approval.result(timeout=10)
    assert memberships(publisher) == [(1, 4), (2, 2)]
    assert snapshot(publisher) == before
    assert intent(publisher) == ([], [])
    with publisher.engine.connect() as c:
        assert c.scalar(text('SELECT count(*) FROM test_role WHERE id=3')) == 0


def test_delete_privilege_view_after_publication_preserves_revision_slot(publisher):  # noqa: F811
    setup_role(publisher)
    candidate = ready(publisher)
    response = approve(publisher, candidate)
    before = snapshot(publisher), intent(publisher)
    with Session(publisher.engine) as session:
        delete_role, _ = view(session)
        delete_role(3)
    with pytest.raises(PreparationError, match='forbidden'):
        approve(publisher, candidate)
    assert (snapshot(publisher), intent(publisher)) == before
    state = snapshot(publisher)
    assert len(state['installation_attempt']) == 1
    assert state['environment_state'][0]['active_attempt_id'] == response['attempt_id']


def test_delete_privilege_view_retains_other_memberships(publisher):  # noqa: F811
    setup_role(publisher)
    with publisher.engine.begin() as c:
        c.execute(text('INSERT INTO test_user_roles VALUES (2,3)'))
    with Session(publisher.engine) as session:
        delete_role, _ = view(session)
        delete_role(3)
    assert memberships(publisher) == [(1, 4), (2, 2)]


@pytest.mark.parametrize('role_id', [0, 1, 2, 4, 99, 5])
def test_delete_privilege_view_guard_no_mutation(publisher, role_id):  # noqa: F811
    setup_role(publisher)
    with publisher.engine.begin() as c:
        c.execute(text("INSERT INTO test_role VALUES (5,'trainer')"))
    before = memberships(publisher), snapshot(publisher)
    with Session(publisher.engine) as session:
        delete_role, messages = view(session)
        delete_role(role_id)
        assert messages == [('The role could not be deleted.', 'error')]
    assert (memberships(publisher), snapshot(publisher)) == before


def test_delete_privilege_view_failed_commit_rolls_back_composite(publisher):  # noqa: F811
    setup_role(publisher)
    before = memberships(publisher)
    with Session(publisher.engine) as session:
        def fail(session):
            raise PreparationError('commit denied')
        event.listen(session, 'before_commit', fail)
        delete_role, messages = view(session)
        delete_role(3)
        assert messages == [('The role could not be deleted.', 'error')]
    assert memberships(publisher) == before
    with publisher.engine.connect() as c:
        assert c.scalar(text('SELECT name FROM test_role WHERE id=3')) == 'installer'
