"""05b2a actual SQL writer/projection logic; NOT full HTTP/security-writer qualification."""
import importlib.util
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import Table, Column, Integer, Boolean, Text, text, update
from sqlalchemy.exc import DBAPIError

from docassemble.base.managed_publication import Publication
from docassemble.base.managed_preparation import PreparationError
from test_preparation_postgres import protocol, result, snapshot  # noqa: F401
from test_publication_postgres import wait_for_approval_lock

SOURCE = Path(__file__).resolve().parents[3]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, SOURCE / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adapter = load('publication_adapter', 'docassemble_webapp/docassemble/webapp/packages/managed.py')
writers = load('authority_writers', 'docassemble_webapp/docassemble/webapp/users/managed.py')


@pytest.fixture
def publisher(protocol):  # noqa: F811
    models = sys.modules['preparation_models']
    metadata = models.Package.metadata
    users = Table('test_user', metadata, Column('id', Integer, primary_key=True),
        Column('email', Text), Column('active', Boolean), Column('social_id', Text))
    uploads = Table('test_uploads', metadata, Column('indexno', Integer, primary_key=True))
    roles = Table('test_role', metadata, Column('id', Integer, primary_key=True), Column('name', Text))
    membership = Table('test_user_roles', metadata, Column('user_id', Integer), Column('role_id', Integer))
    with protocol.engine.begin() as c:
        metadata.create_all(c, tables=[users, uploads, roles, membership, models.Package.__table__, models.PackageAuth.__table__])
        c.execute(users.insert(), [{'id': 1, 'email': 'one', 'active': True, 'social_id': 'local$one'},
                                  {'id': 2, 'email': 'two', 'active': True, 'social_id': 'local$two'}])
        c.execute(roles.insert(), [{'id': 1, 'name': 'developer'}, {'id': 2, 'name': 'admin'}])
        c.execute(membership.insert(), [{'user_id': 1, 'role_id': 1}, {'user_id': 2, 'role_id': 2}])
    # SQL authorizer is actual code; external policy/Redis serialization is fenced.
    policy = {'allowed': {}, 'allow_updates': True, 'developer_can_install': True}
    return Publication(protocol.engine, adapter.PreviewAuthorizer('test_', lambda: policy),
                       adapter.PackageProjection('test_'), 'test_')


def ready(publisher, key='prepare'):
    identity = publisher.prepare('1', 'Example[extra]>=2; python_version >= "3.10"', key)
    claim = publisher.claim(identity)
    assert publisher.publish_ready(claim, result(claim))
    return identity, publisher.status('1', identity)['result_digest']


def approve(publisher, candidate, key='approve'):
    return publisher.approve('1', *candidate, key)


def intent(publisher):
    with publisher.engine.connect() as c:
        return [dict(row) for row in c.execute(text('SELECT * FROM test_package')).mappings()], [
            dict(row) for row in c.execute(text('SELECT * FROM test_package_auth')).mappings()]


def test_real_package_atomic_bundle_grant_and_duplicate(publisher):
    candidate = ready(publisher)
    preview = publisher.status('1', candidate[0])['preview']
    response = approve(publisher, candidate)
    packages, grants = intent(publisher)
    assert len(packages) == len(grants) == 1
    assert packages[0]['type'] == 'pip' and packages[0]['active'] and not packages[0]['dependency']
    assert packages[0]['limitation'] == '>=2'
    assert grants[0]['user_id'] == 1 and grants[0]['authtype'] == 'owner'
    state = snapshot(publisher)
    revision = next(row for row in state['revision'] if row['id'] == response['revision_id'])
    assert revision['snapshot']['publication']['result'] == preview
    assert revision['snapshot']['publication']['inputs']['requirement']['declaration'] == 'Example[extra]>=2; python_version >= "3.10"'
    assert len(state['installation_attempt']) == 1
    assert response['installation'] == 'pending'
    with publisher.engine.begin() as c:
        with pytest.raises(DBAPIError, match='immutable'):
            c.execute(update(publisher.tables['revision']).where(
                publisher.tables['revision'].c.id == response['revision_id']).values(snapshot={}))
    assert approve(publisher, candidate) == response
    with pytest.raises(PreparationError, match='body mismatch'):
        approve(publisher, (candidate[0], '0' * 64))


@pytest.mark.parametrize('writer', ['remove_role', 'deactivate'])
def test_actual_authority_writer_blocks_then_rejects(publisher, writer):
    candidate = ready(publisher)
    locked, release = Event(), Event()
    pids = []
    def revoke():
        with publisher.engine.begin() as c:
            if writer == 'remove_role':
                writers.change_privilege(c, 'test_', 1, 'developer', add=False)
            else:
                writers.deactivate_user(c, 'test_', 1)
            pids.append(c.scalar(text('SELECT pg_backend_pid()')))
            locked.set()
            assert release.wait(15)
    before = snapshot(publisher)
    with ThreadPoolExecutor(2) as pool:
        mutation = pool.submit(revoke)
        assert locked.wait(10)
        approval = pool.submit(approve, publisher, candidate)
        try:
            wait_for_approval_lock(publisher.engine, pids[0])
            assert not approval.done()
        finally:
            release.set()
        mutation.result(timeout=10)
        with pytest.raises(PreparationError, match='forbidden'):
            approval.result(timeout=10)
    assert intent(publisher) == ([], [])
    assert snapshot(publisher) == before


@pytest.mark.parametrize('same_candidate', [True, False])
def test_actual_projection_concurrent_approvals(publisher, same_candidate):
    first = ready(publisher)
    second = first if same_candidate else ready(publisher, 'second')
    def run(candidate, key):
        try:
            return approve(publisher, candidate, key)
        except PreparationError:
            return None
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(run, first, 'one'), pool.submit(run, second, 'two')]
        assert sum(f.result(timeout=10) is not None for f in futures) == 1
    assert len(intent(publisher)[0]) == len(intent(publisher)[1]) == 1
    assert len(snapshot(publisher)['installation_attempt']) == 1


@pytest.mark.parametrize('source', ['pip', 'github'])
def test_alias_source_never_implicitly_adopted(publisher, source):
    candidate = ready(publisher)
    with publisher.authority_write() as c:
        c.execute(text('INSERT INTO test_package (name,type,core) VALUES (:name,:type,false)'),
                  {'name': 'eXaMpLe', 'type': source})
        c.execute(text("INSERT INTO test_package_auth (package_id,user_id,authtype) SELECT id,1,'owner' FROM test_package"))
    before = snapshot(publisher), intent(publisher)
    with pytest.raises(PreparationError):
        approve(publisher, candidate)
    assert (snapshot(publisher), intent(publisher)) == before


def test_existing_managed_projection_preserves_owner(publisher):
    with publisher.authority_write() as c:
        package_id = c.scalar(text("INSERT INTO test_package (name,type,active,core) VALUES ('EXAMPLE','pip',false,false) RETURNING id"))
        c.execute(text("INSERT INTO test_package_auth (package_id,user_id,authtype) VALUES (:package,1,'owner')"), {'package': package_id})
        c.execute(publisher.tables['package_identity'].insert().values(id='existing',
            normalized_name='example', display_name='EXAMPLE', owner_id='1', legacy_package_id=package_id))
    candidate = ready(publisher)
    original_grants = intent(publisher)[1]
    approve(publisher, candidate)
    packages, grants = intent(publisher)
    assert len(packages) == 1 and packages[0]['id'] == package_id and packages[0]['active']
    assert grants == original_grants


def test_projection_rollback_no_partial_package_or_grant(publisher):
    candidate = ready(publisher)
    original = publisher.project
    def fail(c, actor, row):
        original(c, actor, row)
        raise PreparationError('after real projection')
    publisher.project = fail
    before = snapshot(publisher)
    with pytest.raises(PreparationError, match='after real projection'):
        approve(publisher, candidate)
    assert intent(publisher) == ([], []) and snapshot(publisher) == before


@pytest.mark.parametrize('generation', ['input_generation', 'runtime_generation', 'target_generation'])
def test_real_projection_stale_no_partial_intent(publisher, generation):
    candidate = ready(publisher)
    with publisher.authority_write() as c:
        c.execute(update(publisher.tables['environment_state']).values(**{generation: 2}))
    before = snapshot(publisher)
    with pytest.raises(PreparationError, match='stale'):
        approve(publisher, candidate)
    assert snapshot(publisher) == before and intent(publisher) == ([], [])


def test_real_projection_busy_heartbeat_never_releases_slot(publisher):
    candidate = ready(publisher)
    with publisher.authority_write() as c:
        c.execute(publisher.tables['installation_attempt'].insert().values(id='busy', revision_id='baseline', record={'heartbeat': 'expired'}))
        c.execute(update(publisher.tables['environment_state']).values(active_attempt_id='busy'))
    before = snapshot(publisher)
    with pytest.raises(PreparationError, match='busy'):
        approve(publisher, candidate)
    assert snapshot(publisher) == before and intent(publisher) == ([], [])
    assert publisher.status('1', candidate[0])['state'] == 'ready'


def test_actual_writer_revokes_lost_response_authority(publisher):
    candidate = ready(publisher)
    approve(publisher, candidate)
    before = snapshot(publisher), intent(publisher)
    with publisher.engine.begin() as c:
        writers.change_privilege(c, 'test_', 1, 'developer', add=False)
    with pytest.raises(PreparationError, match='forbidden'):
        approve(publisher, candidate)
    assert (snapshot(publisher), intent(publisher)) == before


def test_actual_add_remove_writer_and_transaction_rollback(publisher):
    with publisher.engine.begin() as c:
        writers.change_privilege(c, 'test_', 1, 'admin', add=True)
    with pytest.raises(RuntimeError):
        with publisher.engine.begin() as c:
            writers.change_privilege(c, 'test_', 1, 'admin', add=False)
            raise RuntimeError('rollback')
    with publisher.engine.connect() as c:
        assert c.scalar(text('SELECT count(*) FROM test_user_roles WHERE user_id=1 AND role_id=2')) == 1
