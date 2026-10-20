"""05b1 real PostgreSQL internal publication; production writers NOT qualified."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep

import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import DBAPIError

from docassemble.base.managed_publication import Publication
from docassemble.base.managed_preparation import PreparationError
from test_preparation_postgres import protocol, result, snapshot  # noqa: F401


@pytest.fixture
def publisher(protocol):  # noqa: F811 - imported pytest fixture registration
    with protocol.engine.begin() as connection:
        connection.execute(text('CREATE TABLE fixture_authority (actor text PRIMARY KEY, allowed boolean NOT NULL)'))
        connection.execute(text("INSERT INTO fixture_authority VALUES ('alice', true), ('admin', true)"))
        connection.execute(text('CREATE TABLE fixture_package (id serial PRIMARY KEY, name text UNIQUE, declaration text, owner text)'))
    def authorize(connection, actor, operation, candidate):
        allowed = connection.scalar(text('SELECT allowed FROM fixture_authority WHERE actor=:actor'), {'actor': actor})
        return bool(allowed and (not candidate or candidate['requester_id'] == actor or actor == 'admin'))
    def project(connection, actor, candidate):
        requirement = candidate['inputs']['requirement']
        identity = connection.scalar(text('INSERT INTO fixture_package (name, declaration, owner) VALUES (:name,:declaration,:owner) RETURNING id'),
            {'name': requirement['normalized_name'], 'declaration': requirement['declaration'], 'owner': actor})
        return {'legacy_package_id': identity, 'owner_id': actor}
    return Publication(protocol.engine, authorize, project, 'test_')


def ready(publisher, key='prepare'):
    candidate = publisher.prepare('alice', 'Example>=2', key)
    claim = publisher.claim(candidate)
    assert publisher.publish_ready(claim, result(claim))
    return candidate, publisher.status('alice', candidate)['result_digest']


def approve(publisher, candidate, key='approve'):
    return publisher.approve('alice', *candidate, key)


def packages(publisher):
    with publisher.engine.connect() as connection:
        return list(connection.execute(text('SELECT * FROM fixture_package')))


def test_atomic_publication_exact_bundle_and_lost_response(publisher):
    candidate = ready(publisher)
    before = snapshot(publisher)
    approved_result = publisher.status('alice', candidate[0])['preview']
    response = approve(publisher, candidate)
    after = snapshot(publisher)
    assert response['installation'] == 'pending'
    assert len(packages(publisher)) == 1
    assert len(after['installation_attempt']) == 1
    assert after['environment_state'][0]['active_attempt_id'] == response['attempt_id']
    assert after['environment_state'][0]['publication_generation'] == 2
    revision = next(row for row in after['revision'] if row['id'] == response['revision_id'])
    assert revision['snapshot']['publication']['result'] == approved_result
    assert revision['snapshot']['publication']['result_digest'] == candidate[1]
    assert before['requirement_contribution'][0] in after['requirement_contribution']
    with publisher.authority_write() as connection:
        table = publisher.tables['environment_state']
        connection.execute(update(table).values(publication_generation=99, runtime_generation=99))
    assert approve(publisher, candidate) == response
    with pytest.raises(PreparationError, match='body mismatch'):
        approve(publisher, (candidate[0], '0' * 64))
    with pytest.raises(PreparationError, match='cannot be cancelled'):
        publisher.cancel('alice', candidate[0])
    with publisher.engine.begin() as connection:
        with pytest.raises(DBAPIError, match='immutable'):
            connection.execute(update(publisher.tables['revision']).where(
                publisher.tables['revision'].c.id == response['revision_id']).values(snapshot={}))


@pytest.mark.parametrize('same_candidate', [True, False])
def test_concurrent_approvals_one_parent_one_slot(publisher, same_candidate):
    first = ready(publisher)
    second = first if same_candidate else ready(publisher, 'second')
    def run(candidate, key):
        try:
            return approve(publisher, candidate, key)
        except PreparationError as error:
            return str(error)
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(run, first, 'one'), pool.submit(run, second, 'two')]
        responses = [future.result(timeout=10) for future in futures]
    assert sum(isinstance(response, dict) for response in responses) == 1
    assert len(packages(publisher)) == len(snapshot(publisher)['installation_attempt']) == 1


@pytest.mark.parametrize('generation', ['input_generation', 'runtime_generation', 'target_generation'])
def test_stale_generation_no_partial_intent(publisher, generation):
    candidate = ready(publisher)
    with publisher.authority_write() as connection:
        connection.execute(update(publisher.tables['environment_state']).values(**{generation: 2}))
    before = snapshot(publisher)
    with pytest.raises(PreparationError, match='stale'):
        approve(publisher, candidate)
    assert snapshot(publisher) == before
    assert not packages(publisher)


def test_busy_leaves_ready_and_never_releases_slot(publisher):
    candidate = ready(publisher)
    with publisher.authority_write() as connection:
        connection.execute(publisher.tables['installation_attempt'].insert().values(
            id='busy', revision_id='baseline', record={'heartbeat': 'expired'}))
        connection.execute(update(publisher.tables['environment_state']).values(active_attempt_id='busy'))
    before = snapshot(publisher)
    with pytest.raises(PreparationError, match='busy'):
        approve(publisher, candidate)
    assert snapshot(publisher) == before
    assert publisher.status('alice', candidate[0])['state'] == 'ready'
    assert not packages(publisher)


def test_expiry_db_time(publisher):
    # Set timestamps consistently before first readiness; immutable after that.
    candidate = publisher.prepare('alice', 'Example>=2', 'expire')
    claim = publisher.claim(candidate)
    import docassemble.base.managed_preparation as preparation
    original = publisher._now
    from datetime import timedelta
    publisher._now = lambda connection: original(connection) - timedelta(minutes=6)
    # Claim ownership checks need current lease, which remains later than past now.
    assert publisher.publish_ready(claim, result(claim))
    publisher._now = original
    with pytest.raises(preparation.PreparationError, match='expired'):
        publisher.approve('alice', candidate, publisher.status('alice', candidate)['result_digest'], 'approve')
    assert not packages(publisher)


def wait_for_approval_lock(engine, writer_pid):
    """Independent backend must observe approval blocked by the retained writer."""
    deadline = monotonic() + 10
    # pg_stat_activity snapshots are transaction-cached; each poll must refresh.
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as observer:
        while monotonic() < deadline:
            blocked = observer.execute(text("SELECT pid FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
                "AND :writer = ANY(pg_blocking_pids(pid)) AND query LIKE '%managed_environment_state%FOR UPDATE%'"),
                {'writer': writer_pid}).scalars().all()
            if blocked:
                assert observer.scalar(text('SELECT pg_backend_pid()')) not in blocked
                return blocked
            sleep(0.01)
    pytest.fail('approval backend was not observed waiting for writer lock')


@pytest.mark.parametrize('delete', [False, True])
def test_cooperating_authority_writer_race(publisher, delete):
    candidate = ready(publisher)
    locked, release = Event(), Event()
    writer_pid = []
    def revoke():
        with publisher.authority_write() as connection:
            writer_pid.append(connection.scalar(text('SELECT pg_backend_pid()')))
            connection.execute(text('DELETE FROM fixture_authority WHERE actor=\'alice\'' if delete else
                                    'UPDATE fixture_authority SET allowed=false WHERE actor=\'alice\''))
            locked.set()
            assert release.wait(10)
    with ThreadPoolExecutor(2) as pool:
        writer = pool.submit(revoke)
        assert locked.wait(10)
        reader = pool.submit(approve, publisher, candidate)
        try:
            wait_for_approval_lock(publisher.engine, writer_pid[0])
            assert not reader.done()
        finally:
            release.set()
        writer.result(timeout=10)
        with pytest.raises(PreparationError, match='forbidden'):
            reader.result(timeout=10)
    assert not packages(publisher)
    assert not snapshot(publisher)['installation_attempt']


def test_projection_failure_rolls_back_all_intent(publisher):
    candidate = ready(publisher)
    project = publisher.project
    def fail(connection, actor, row):
        project(connection, actor, row)
        raise PreparationError('projection failure')
    publisher.project = fail
    before = snapshot(publisher)
    with pytest.raises(PreparationError, match='projection failure'):
        approve(publisher, candidate)
    assert not packages(publisher)
    assert snapshot(publisher) == before
    assert publisher.status('alice', candidate[0])['state'] == 'ready'


@pytest.mark.parametrize('case', ['digest', 'requester', 'ownerless', 'ownership', 'contribution'])
def test_rechecks_reject_without_partial_intent(publisher, case):
    candidate = ready(publisher)
    if case in ('ownerless', 'ownership'):
        with publisher.authority_write() as connection:
            connection.execute(publisher.tables['package_identity'].insert().values(
                id='changed', normalized_name='example', display_name='Example',
                owner_id=None if case == 'ownerless' else 'admin'))
    elif case == 'contribution':
        with publisher.authority_write() as connection:
            connection.execute(update(publisher.tables['requirement_contribution']).values(declaration='Separate==9'))
    before = snapshot(publisher)
    with pytest.raises(PreparationError):
        publisher.approve('admin' if case == 'requester' else 'alice', candidate[0],
                          '0' * 64 if case == 'digest' else candidate[1], 'approve')
    assert snapshot(publisher) == before
    assert not packages(publisher)


def test_concurrent_duplicate_same_key_returns_same_result(publisher):
    candidate = ready(publisher)
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(approve, publisher, candidate) for _ in range(2)]
        assert futures[0].result(timeout=10) == futures[1].result(timeout=10)
    assert len(packages(publisher)) == len(snapshot(publisher)['installation_attempt']) == 1


def test_uncommitted_projection_not_visible_and_revoked_duplicate_forbidden(publisher):
    candidate = ready(publisher)
    projected, release = Event(), Event()
    original = publisher.project
    def pause(connection, actor, row):
        projection = original(connection, actor, row)
        projected.set()
        assert release.wait(10)
        return projection
    publisher.project = pause
    before = snapshot(publisher)
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(approve, publisher, candidate)
        assert projected.wait(10)
        assert not packages(publisher)
        assert snapshot(publisher) == before
        release.set()
        pending.result(timeout=10)
    with publisher.authority_write() as connection:
        connection.execute(text("UPDATE fixture_authority SET allowed=false WHERE actor='alice'"))
    with pytest.raises(PreparationError, match='forbidden'):
        approve(publisher, candidate)
