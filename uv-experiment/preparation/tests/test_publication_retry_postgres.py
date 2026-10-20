"""05b2b3 actual SQL retry/status and cooperating helper writer races.

Free slots simulate TEST-ONLY qualified terminal reporting, not 05c proof.
Flask authentication and external config/Redis writers remain fenced.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import flask_login
import pytest
from flask import Flask
from sqlalchemy import event, select, text, update
from sqlalchemy.orm import Session

from docassemble.base.managed_preparation import Preparation, PreparationError
from test_preparation_postgres import protocol, result  # noqa: F401
from test_publication_helper_writers_postgres import helper
from test_publication_postgres import wait_for_approval_lock
from test_publication_writers_postgres import publisher, ready, approve, intent, adapter, writers  # noqa: F401


def snapshot(publisher):  # noqa: F811
    with publisher.engine.connect() as c:
        return {name: [dict(row) for row in c.execute(select(table)).mappings()]
                for name, table in publisher.tables.items()}


def published(publisher):  # noqa: F811
    response = approve(publisher, ready(publisher))
    # TEST ONLY: production terminal evidence and slot release belong to 05c.
    with publisher.authority_write() as c:
        attempts = publisher.tables['installation_attempt']
        row = c.execute(select(attempts).where(attempts.c.id == response['attempt_id'])).mappings().one()
        c.execute(update(attempts).where(attempts.c.id == response['attempt_id']).values(
            record={**row['record'], 'phase': 'failed'}))
        c.execute(update(publisher.tables['environment_state']).values(active_attempt_id=None))
    return response


def test_retry_exact_revision_no_solve_or_intent_change(publisher):  # noqa: F811
    original = published(publisher)
    before = snapshot(publisher), intent(publisher)
    response = publisher.retry('1', original['revision_id'], 'retry')
    after = snapshot(publisher)
    assert response['installation'] == 'pending'
    assert response['revision_id'] == original['revision_id']
    assert response['attempt_id'] != original['attempt_id']
    assert len(after['installation_attempt']) == 2 and len(after['retry_result']) == 1
    for name in ('revision', 'candidate', 'requirement_contribution', 'package_identity', 'publication_result'):
        assert after[name] == before[0][name]
    assert after['environment_state'] == [dict(before[0]['environment_state'][0], active_attempt_id=response['attempt_id'])]
    assert intent(publisher) == before[1]
    assert publisher.retry('1', original['revision_id'], 'retry') == response
    with pytest.raises(PreparationError, match='body mismatch'):
        publisher.retry('1', 'baseline', 'retry')


@pytest.mark.parametrize('same_key', [True, False])
def test_retry_concurrent_one_slot(publisher, same_key):  # noqa: F811
    original = published(publisher)
    def run(key):
        try:
            return publisher.retry('1', original['revision_id'], key)
        except PreparationError as error:
            return str(error)
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(run, 'one'), pool.submit(run, 'one' if same_key else 'two')]
        responses = [future.result(timeout=10) for future in futures]
    if same_key:
        assert responses[0] == responses[1]
    else:
        assert sum(isinstance(value, dict) for value in responses) == 1
        assert 'installation busy' in responses
    assert len(snapshot(publisher)['installation_attempt']) == 2


def test_retry_competes_with_approval_for_same_slot(publisher):  # noqa: F811
    original = published(publisher)
    candidate_id = publisher.prepare('1', 'Example>=2', 'another-prepare')
    claim = publisher.claim(candidate_id)
    preview = result(claim)
    inventory = preview['bundle']['inventory']
    desired = claim.inputs['baseline']['desired_inventory']
    preview['desired_diff'] = {name: {'before': desired.get(name), 'after': inventory.get(name)}
        for name in sorted(set(desired) | set(inventory)) if desired.get(name) != inventory.get(name)}
    assert publisher.publish_ready(claim, preview)
    candidate = candidate_id, publisher.status('1', candidate_id)['result_digest']
    def run(action):
        try:
            return action()
        except PreparationError as error:
            return str(error)
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(run, lambda: publisher.retry('1', original['revision_id'], 'retry')),
                   pool.submit(run, lambda: approve(publisher, candidate, 'another-approve'))]
        responses = [future.result(timeout=10) for future in futures]
    assert sum(isinstance(value, dict) for value in responses) == 1
    assert len(snapshot(publisher)['installation_attempt']) == 2


@pytest.mark.parametrize('writer', ['deactivate_helper', 'remove_role'])
def test_retry_waits_for_actual_writer_then_denied(publisher, writer):  # noqa: F811
    original = published(publisher)
    before = snapshot(publisher), intent(publisher)
    locked, release = Event(), Event()
    pids = []
    def revoke():
        with Session(publisher.engine) as session:
            def pause(session):
                pids.append(session.scalar(text('SELECT pg_backend_pid()')))
                locked.set()
                assert release.wait(15)
            event.listen(session, 'before_commit', pause)
            if writer == 'deactivate_helper':
                helper('make_user_inactive', session)(1, None)
            else:
                writers.change_privilege(session.connection(), 'test_', 1, 'developer', add=False)
                session.commit()
    with ThreadPoolExecutor(2) as pool:
        mutation = pool.submit(revoke)
        assert locked.wait(10)
        retry = pool.submit(publisher.retry, '1', original['revision_id'], 'retry')
        try:
            wait_for_approval_lock(publisher.engine, pids[0])
            assert (snapshot(publisher), intent(publisher)) == before
        finally:
            release.set()
        mutation.result(timeout=10)
        with pytest.raises(PreparationError, match='forbidden'):
            retry.result(timeout=10)
    assert (snapshot(publisher), intent(publisher)) == before


def test_retry_admin_but_not_another_developer(publisher):  # noqa: F811
    original = published(publisher)
    with publisher.engine.begin() as c:
        c.execute(text('UPDATE test_user_roles SET role_id=1 WHERE user_id=2'))
    with pytest.raises(PreparationError, match='forbidden'):
        publisher.retry('2', original['revision_id'], 'other')
    with publisher.engine.begin() as c:
        writers.change_privilege(c, 'test_', 2, 'admin', add=True)
        writers.deactivate_user(c, 'test_', 1)
    assert publisher.retry('2', original['revision_id'], 'admin')['installation'] == 'pending'


def test_retry_replay_revoked_original_approver(publisher):  # noqa: F811
    original = published(publisher)
    publisher.retry('1', original['revision_id'], 'retry')
    before = snapshot(publisher), intent(publisher)
    with Session(publisher.engine) as session:
        helper('make_user_inactive', session)(1, None)
    with pytest.raises(PreparationError, match='forbidden'):
        publisher.retry('1', original['revision_id'], 'retry')
    assert (snapshot(publisher), intent(publisher)) == before


@pytest.mark.parametrize('case', ['historical', 'target', 'policy', 'grants', 'source', 'heartbeat'])
def test_retry_rejections_do_not_change_intent(publisher, case):  # noqa: F811
    original = published(publisher)
    with publisher.authority_write() as c:
        env = publisher.tables['environment_state']
        if case == 'historical':
            c.execute(update(env).values(desired_revision_id='baseline'))
        elif case == 'target':
            state = c.execute(select(env)).mappings().one()
            c.execute(update(env).values(snapshot={**state['snapshot'], 'target': {'changed': True}}))
        elif case == 'policy':
            publisher.authorize.policy = lambda: {'allowed': {}, 'allow_updates': False, 'developer_can_install': True}
        elif case == 'grants':
            c.execute(text('UPDATE test_package_auth SET user_id=2'))
        elif case == 'source':
            c.execute(text("UPDATE test_package SET type='github'"))
        else:
            c.execute(update(env).values(active_attempt_id=original['attempt_id']))
            c.execute(update(publisher.tables['installation_attempt']).values(record={'heartbeat': 'expired'}))
    before = snapshot(publisher), intent(publisher)
    with pytest.raises(PreparationError):
        publisher.retry('1', original['revision_id'], 'retry')
    assert (snapshot(publisher), intent(publisher)) == before


@pytest.mark.parametrize('rollback', [False, True])
def test_retry_atomic_snapshot_or_rollback(publisher, rollback):  # noqa: F811
    original = published(publisher)
    before = snapshot(publisher), intent(publisher)
    inserted, release = Event(), Event()
    def pause(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith('INSERT INTO test_managed_retry_result'):
            if rollback:
                raise RuntimeError('test failure')
            inserted.set()
            assert release.wait(15)
    event.listen(publisher.engine, 'before_cursor_execute', pause)
    try:
        if rollback:
            with pytest.raises(RuntimeError, match='test failure'):
                publisher.retry('1', original['revision_id'], 'retry')
            assert (snapshot(publisher), intent(publisher)) == before
        else:
            with ThreadPoolExecutor(1) as pool:
                future = pool.submit(publisher.retry, '1', original['revision_id'], 'retry')
                assert inserted.wait(10)
                try:
                    assert (snapshot(publisher), intent(publisher)) == before
                finally:
                    release.set()
                future.result(timeout=10)
    finally:
        event.remove(publisher.engine, 'before_cursor_execute', pause)


@pytest.mark.parametrize('identity', ['revision_id', 'attempt_id', 'candidate_id'])
def test_public_read_adapter_truthful_and_mutations_fenced(publisher, monkeypatch, identity):  # noqa: F811
    original = published(publisher)
    service = Preparation(publisher.engine, publisher.authorize, 'test_')
    monkeypatch.setattr(adapter, 'preview_service', lambda api=False: service)
    monkeypatch.setattr(adapter, 'managed_installation', lambda: True)
    monkeypatch.setattr(flask_login, 'current_user', SimpleNamespace(id=1, is_authenticated=True))
    app = Flask(__name__)
    with app.test_request_context('/?' + identity + '=' + original[identity]):
        status = adapter.preview_request().get_json()
        assert status['publication'] == 'published' and status['attempt_phase'] == 'failed'
        assert status['installation'] == 'failed' and status['is_desired']
        assert not status['approval_supported'] and not status['retry_supported']
        assert 'bundle' not in status and 'preview' not in status
    for action in ('approve', 'retry'):
        with app.test_request_context('/', method='POST', json={'action': action}):
            response, code = adapter.preview_request()
            assert code == 409 and 'unsupported' in response.get_json()['error']


def test_status_pending_redaction_and_current_authority(publisher):  # noqa: F811
    original = published(publisher)
    response = publisher.retry('1', original['revision_id'], 'retry')
    status = publisher.publication_status('1', attempt_id=response['attempt_id'])
    assert status['installation'] == 'pending' and status['active']
    with publisher.authority_write() as c:
        c.execute(update(publisher.tables['installation_attempt']).where(
            publisher.tables['installation_attempt'].c.id == response['attempt_id']).values(
                record={'phase': 'succeeded', 'error': 'secret-token', 'bundle': 'secret'}))
    status = publisher.publication_status('1', attempt_id=response['attempt_id'])
    assert status['installation'] == 'in-progress' and status['attempt_phase'] == 'reported-terminal'
    assert 'secret' not in str(status) and 'succeeded' not in str(status)
    with publisher.engine.begin() as c:
        writers.deactivate_user(c, 'test_', 1)
    with pytest.raises(PreparationError, match='forbidden'):
        publisher.publication_status('1', revision_id=original['revision_id'])
    assert publisher.publication_status('2', revision_id=original['revision_id'])['publication'] == 'published'
