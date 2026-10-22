"""Real PostgreSQL protocol tests. Required URL; no fallback/skip to in-memory DB.

Run separately from the native act gate using DA_PREPARATION_TEST_URL.
Only explicit fixture baseline intent is seeded. No live venv is touched.
"""
from docassemble.base.managed_python import python_target
import copy
import importlib.util
import os
from pathlib import Path
import sys
import types
from concurrent.futures import ThreadPoolExecutor

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, select, update, text
from sqlalchemy.exc import DBAPIError

from docassemble_executor.preparation import Preparation, PreparationError

SOURCE = Path(__file__).resolve().parents[3]
PIN = {'version': '1.0', 'source_digest': 'a' * 64}


@pytest.fixture(params=['upgrade', 'fresh'])
def protocol(monkeypatch, request):
    engine = create_engine(os.environ['DA_PREPARATION_TEST_URL'])
    assert engine.dialect.name == 'postgresql'
    # Isolate each test in a schema; also exercise non-empty table prefixes.
    with engine.begin() as connection:
        connection.execute(text('DROP SCHEMA IF EXISTS preparation_test CASCADE'))
        connection.execute(text('CREATE SCHEMA preparation_test'))
    engine.dispose()
    engine = create_engine(os.environ['DA_PREPARATION_TEST_URL'],
                           connect_args={'options': '-csearch_path=preparation_test'})
    database = types.ModuleType('docassemble.webapp.database')
    database.dbtableprefix = 'test_'
    monkeypatch.setitem(sys.modules, 'docassemble.webapp.database', database)
    # Load actual ORM mappings without importing the Flask blueprint/runtime.
    base_spec = importlib.util.spec_from_file_location('docassemble.webapp.db_base',
        SOURCE / 'docassemble_webapp/docassemble/webapp/db_base.py')
    base_module = importlib.util.module_from_spec(base_spec)
    monkeypatch.setitem(sys.modules, base_spec.name, base_module)
    base_spec.loader.exec_module(base_module)
    model_spec = importlib.util.spec_from_file_location('preparation_models',
        SOURCE / 'docassemble_webapp/docassemble/webapp/packages/models.py')
    model_module = importlib.util.module_from_spec(model_spec)
    monkeypatch.setitem(sys.modules, model_spec.name, model_module)
    model_spec.loader.exec_module(model_module)
    assert model_module.Candidate.__table__.name == 'test_managed_candidate'
    assert model_module.EnvironmentState.__table__.name == 'test_managed_environment_state'
    path = SOURCE / 'docassemble_webapp/docassemble/webapp/alembic/versions/05a100000001_managed_preparation.py'
    spec = importlib.util.spec_from_file_location('preparation_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        if request.param == 'upgrade':
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                publication_spec = importlib.util.spec_from_file_location('publication_migration',
                    SOURCE / 'docassemble_webapp/docassemble/webapp/alembic/versions/05b100000001_managed_publication.py')
                publication_migration = importlib.util.module_from_spec(publication_spec)
                publication_spec.loader.exec_module(publication_migration)
                publication_migration.upgrade()
                retry_spec = importlib.util.spec_from_file_location('retry_migration',
                    SOURCE / 'docassemble_webapp/docassemble/webapp/alembic/versions/05b300000001_managed_retry.py')
                retry_migration = importlib.util.module_from_spec(retry_spec)
                retry_spec.loader.exec_module(retry_migration)
                retry_migration.upgrade()
                cleanup_spec = importlib.util.spec_from_file_location('cleanup_migration',
                    SOURCE / 'docassemble_webapp/docassemble/webapp/alembic/versions/05b400000001_account_cleanup.py')
                cleanup_migration = importlib.util.module_from_spec(cleanup_spec)
                cleanup_spec.loader.exec_module(cleanup_migration)
                cleanup_migration.upgrade()
                binding_spec = importlib.util.spec_from_file_location('binding_migration',
                    SOURCE / 'docassemble_webapp/docassemble/webapp/alembic/versions/05b500000001_git_credential_request_binding.py')
                binding_migration = importlib.util.module_from_spec(binding_spec)
                binding_spec.loader.exec_module(binding_migration)
                probe = connection.begin_nested()
                connection.execute(text("INSERT INTO test_managed_candidate "
                    "(id,requester_id,operation,request_digest,publication_generation,input_generation,"
                    "target_generation,runtime_generation,inputs,state,fence) "
                    "VALUES ('pre-binding-row','alice','add','old-digest',1,1,1,1,'{}','queued',0)"))
                binding_migration.upgrade()
                assert connection.scalar(text("SELECT credential_request_binding IS NULL FROM test_managed_candidate "
                                              "WHERE id='pre-binding-row'"))
                assert connection.scalar(text("SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_schema='preparation_test' AND table_name='test_managed_candidate' "
                    "AND column_name='credential_request_binding'")) == 'YES'
                # Retained rows cannot be deleted. Roll back the probe, then
                # apply the same migration to the fixture's actual empty schema.
                probe.rollback()
                binding_migration.upgrade()
        else:
            # Fresh create_tables.py stamps head, then creates ORM metadata.
            # Select the additive tables to avoid unrelated legacy FK targets.
            tables = list(model_module._managed_tables.values())
            base_module.Base.metadata.create_all(connection, tables=tables)
        # The normal post-upgrade create_all must not reinstall existing guards.
        base_module.Base.metadata.create_all(connection, tables=list(model_module._managed_tables.values()))
    def authorize(connection, actor, operation, candidate):
        if actor not in ('alice', 'admin'):
            return False
        return candidate is None or candidate['requester_id'] == actor or actor == 'admin'
    module = Preparation(engine, authorize, 'test_')
    tables = module.tables
    baseline = {'core': {'core': PIN}, 'target': 'fixture-target', 'tool': 'fixture-uv',
                'config': {'committed': 'fixture'}, 'desired_inventory': {'core': PIN},
                'runtime': {'kind': 'normal', 'inventory': {'core': PIN}}}
    with engine.begin() as connection:
        # Migration is additive only; it seeds nothing, including managed state.
        assert connection.scalar(select(tables['environment_state'].c.id)) is None
        connection.execute(tables['revision'].insert().values(id='baseline', snapshot=baseline))
        connection.execute(tables['environment_state'].insert().values(id=1, managed=True,
            desired_revision_id='baseline', publication_generation=1, input_generation=1,
            target_generation=1, runtime_generation=1, snapshot=baseline))
        connection.execute(tables['package_identity'].insert().values(id='config-package',
            normalized_name='separate', display_name='Separate', owner_id='alice'))
        connection.execute(tables['requirement_contribution'].insert().values(id='config-contribution',
            identity_id='config-package', origin='config', declaration='Separate[extra]<2; os_name == "posix"', active=True))
    yield module
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            connection.execute(text('DROP TABLE test_managed_account_cleanup'))
            connection.execute(text('DROP TABLE test_managed_retry_result'))
            connection.execute(text('DROP TABLE test_managed_publication_result'))
            migration.downgrade()
    engine.dispose()


def result(claim):
    inventory = {'core': PIN, 'separate': {'version': '1.5', 'source_digest': 'b' * 64},
                 'example': {'version': '2.0', 'source_digest': 'c' * 64}}
    diff = {name: {'before': None, 'after': value} for name, value in inventory.items() if name != 'core'}
    return {'bundle': {'digest': 'd' * 64, 'project_digest': 'e' * 64, 'lock_digest': 'f' * 64,
                       'inventory': inventory, 'artifacts': ['b' * 64, 'c' * 64]},
            'desired_diff': diff, 'runtime_diff': diff, 'warnings': [],
            'validation': {'core_unchanged': True, 'requirements_satisfied': True,
                           'durable_materialization_verified': True},
            'ownership_effects': claim.inputs['ownership_effects']}


def snapshot(protocol):
    with protocol.engine.connect() as connection:
        return {name: [dict(row) for row in connection.execute(select(protocol.tables[name])).mappings()]
                for name in ('environment_state', 'revision', 'package_identity', 'requirement_contribution',
                             'host_environment', 'installation_attempt')}


def expire_claim(protocol, candidate_id):
    with protocol.engine.begin() as connection:
        table = protocol.tables['candidate']
        connection.execute(update(table).where(table.c.id == candidate_id).values(
            claim_until=text("clock_timestamp() - interval '1 second'")))


def material(protocol, candidate_id, name, *, shared=False, committed=False):
    with protocol.engine.begin() as connection:
        connection.execute(protocol.tables['candidate_material'].insert().values(
            id=name, candidate_id=candidate_id, path='/restricted/staging/' + name,
            usable=True, shared=shared, committed=committed, deleted=False))


@pytest.mark.parametrize('terminal', ['cancel', 'failure', 'expiry'])
def test_candidate_secret_staging_lifecycle(protocol, tmp_path, terminal):
    root = tmp_path / 'secrets'
    root.mkdir(mode=0o700)
    cache = tmp_path / 'cache'
    cache.write_bytes(b'reusable cache')
    candidate = protocol.prepare('alice', 'Example>=2', 'secret-key')
    claim = protocol.claim(candidate)
    secrets = [b'https://user:DISPOSABLE_SECRET_TOKEN@example.invalid/repo',
               b'https://DISPOSABLE_SECRET_TOKEN@example.invalid/repo',
               b'https://example.invalid/repo?token=DISPOSABLE_SECRET_TOKEN']
    ids = [protocol.stage_candidate_secret(claim, root, value) for value in secrets]
    for identity, value in zip(ids, secrets):
        assert protocol.read_candidate_secret(claim, root, identity) == value
        assert (root / identity).stat().st_mode & 0o777 == 0o600
    other = protocol.claim(protocol.prepare('alice', 'Example>=2', 'other-key'))
    with pytest.raises(PreparationError, match='use denied'):
        protocol.read_candidate_secret(other, root, ids[0])
    for value in secrets:
        with pytest.raises(PreparationError) as error:
            protocol.inject_source_credentials(claim, value)
        assert 'DISPOSABLE_SECRET_TOKEN' not in str(error.value)
        assert 'scoped HTTPS Git declaration' in str(error.value)
    # Fixture-only committed/shared files are never promoted by staging.
    for flags in ({'committed': True}, {'shared': True}):
        identity = protocol.stage_candidate_secret(claim, root, b'preserved fixture')
        table = protocol.tables['candidate_material']
        with protocol.engine.begin() as connection:
            connection.execute(update(table).where(table.c.id == identity).values(**flags))
    if terminal == 'cancel':
        protocol.cancel('alice', candidate)
    elif terminal == 'failure':
        assert protocol.fail(claim)
    else:
        assert protocol.publish_ready(claim, result(claim))
        assert 'DISPOSABLE_SECRET_TOKEN' not in repr(protocol.status('alice', candidate))
        with protocol.engine.begin() as connection:
            connection.execute(text('ALTER TABLE test_managed_candidate DISABLE TRIGGER managed_ready_immutable'))
            table = protocol.tables['candidate']
            connection.execute(update(table).where(table.c.id == candidate).values(
                expires_at=text("clock_timestamp() - interval '1 second'")))
            connection.execute(text('ALTER TABLE test_managed_candidate ENABLE TRIGGER managed_ready_immutable'))
    status = protocol.status('alice', candidate)
    assert 'DISPOSABLE_SECRET_TOKEN' not in repr(status)
    assert status['preview'] is None
    for identity in ids:
        assert not protocol.material_authorized(claim, identity)
        with pytest.raises(PreparationError, match='use denied'):
            protocol.read_candidate_secret(claim, root, identity)
        assert (root / identity).exists()
    assert protocol.reconcile_material(root) == 0
    # A new protocol instance represents recovery after a staging-process crash.
    recovered = Preparation(protocol.engine, protocol.authorize, 'test_')
    recovered.worker_exited(claim.worker_id)  # Fixture launches no OS descendants.
    assert recovered.reconcile_material(root) == 3
    assert recovered.reconcile_material(root) == 0
    assert not any((root / identity).exists() for identity in ids)
    assert len(list(root.iterdir())) == 2
    assert cache.read_bytes() == b'reusable cache'
    assert snapshot(protocol)['installation_attempt'] == []


def test_candidate_secret_cancel_between_registration_and_write(protocol, tmp_path, monkeypatch):
    root = tmp_path / 'secrets'
    root.mkdir(mode=0o700)
    candidate = protocol.prepare('alice', 'Example>=2', 'secret-key')
    claim = protocol.claim(candidate)
    register = protocol.register_material

    def cancel_after_register(*args):
        register(*args)
        protocol.cancel('alice', candidate)

    monkeypatch.setattr(protocol, 'register_material', cancel_after_register)
    with pytest.raises(PreparationError, match='no longer authorized'):
        protocol.stage_candidate_secret(claim, root, b'DISPOSABLE_SECRET_TOKEN')
    assert list(root.iterdir()) == []
    assert protocol.reconcile_material(root) == 0
    protocol.worker_exited(claim.worker_id)
    assert protocol.reconcile_material(root) == 1  # Missing-file crash reference is retryable.
    assert protocol.reconcile_material(root) == 0


def test_candidate_secret_reclaim_and_storage_restrictions(protocol, tmp_path):
    root = tmp_path / 'secrets'
    root.mkdir(mode=0o700)
    candidate = protocol.prepare('alice', 'Example>=2', 'secret-key')
    old = protocol.claim(candidate)
    identity = protocol.stage_candidate_secret(old, root, b'DISPOSABLE_SECRET_TOKEN')
    expire_claim(protocol, candidate)
    current = protocol.claim(candidate)
    with pytest.raises(PreparationError, match='use denied'):
        protocol.read_candidate_secret(old, root, identity)
    assert protocol.read_candidate_secret(current, root, identity) == b'DISPOSABLE_SECRET_TOKEN'
    root.chmod(0o755)
    with pytest.raises(PreparationError, match='restricted'):
        protocol.read_candidate_secret(current, root, identity)
    root.chmod(0o700)
    path = root / identity
    path.unlink()
    path.symlink_to('/etc/passwd')
    with pytest.raises(PreparationError, match='use denied'):
        protocol.read_candidate_secret(current, root, identity)
    path.unlink()
    path.write_bytes(b'DISPOSABLE_SECRET_TOKEN')
    path.chmod(0o600)
    protocol.cancel('alice', candidate)
    protocol.worker_exited(current.worker_id)
    assert protocol.reconcile_material(root) == 0  # ALL workers, not just current.
    protocol.worker_exited(old.worker_id)
    assert protocol.reconcile_material(root) == 1


def test_initialization_guards_present_and_revision_immutable(protocol):
    with protocol.engine.connect() as connection:
        triggers = connection.execute(text('''SELECT c.relname, t.tgname FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'preparation_test' AND NOT t.tgisinternal''')).all()
    assert set(triggers) == {('test_managed_candidate', 'managed_ready_immutable'),
                             ('test_managed_revision', 'managed_revision_immutable')}
    revision = protocol.tables['revision']
    for statement in (revision.update().values(snapshot={}), revision.delete()):
        with pytest.raises(DBAPIError, match='revision is immutable'):
            with protocol.engine.begin() as connection:
                connection.execute(statement)


def test_dedup_concurrent_body_mismatch(protocol):
    before = snapshot(protocol)
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: protocol.prepare('alice', 'Example[extra]>=2,<3; python_version >= "3.10"', 'key'), range(2)))
    assert ids[0] == ids[1]
    with pytest.raises(PreparationError, match='body mismatch'):
        protocol.prepare('alice', 'Example>=3', 'key')
    assert snapshot(protocol) == before
    with protocol.engine.connect() as connection:
        assert len(connection.execute(select(protocol.tables['candidate'])).all()) == 1
        candidate = connection.execute(select(protocol.tables['candidate'])).mappings().one()
        assert candidate['inputs']['requirement']['declaration'] == 'Example[extra]>=2,<3; python_version >= "3.10"'
        assert candidate['inputs']['contributions'][0]['declaration'] == 'Separate[extra]<2; os_name == "posix"'
        assert candidate['inputs']['baseline']['core'] == {'core': PIN}


def test_cancel_immediate_invalidation_delayed_cleanup_and_late_worker(protocol):
    before = snapshot(protocol)
    candidate = protocol.prepare('alice', 'Example>=2', 'key')
    claim = protocol.claim(candidate)
    protocol.register_material(claim, 'candidate-secret', '/restricted/staging/candidate-secret')
    assert protocol.material_authorized(claim, 'candidate-secret')
    material(protocol, candidate, 'committed-secret', committed=True)
    material(protocol, candidate, 'shared-secret', shared=True)
    protocol.cancel('admin', candidate)
    with protocol.engine.connect() as connection:
        rows = {row.id: row.usable for row in connection.execute(select(protocol.tables['candidate_material']))}
    assert rows == {'candidate-secret': False, 'committed-secret': True, 'shared-secret': True}
    assert protocol.cleanup_plan(candidate) == []
    assert not protocol.material_authorized(claim, 'candidate-secret')
    with pytest.raises(PreparationError, match='no longer authorized'):
        protocol.register_material(claim, 'late-secret', '/restricted/staging/late-secret')
    assert not protocol.publish_ready(claim, result(claim))
    assert not protocol.renew(claim)
    protocol.worker_exited(claim.worker_id)
    assert protocol.cleanup_plan(candidate) == [{'id': 'candidate-secret', 'path': '/restricted/staging/candidate-secret'}]
    assert snapshot(protocol) == before
    assert protocol.status('alice', candidate)['preview'] is None


def test_reclaim_fences_late_worker_and_orphan_cleanup(protocol):
    candidate = protocol.prepare('alice', 'Example>=2', 'key')
    first = protocol.claim(candidate)
    with pytest.raises(PreparationError, match='busy'):
        protocol.claim(candidate)
    assert protocol.renew(first)
    expire_claim(protocol, candidate)
    second = protocol.claim(candidate)
    assert second.fence == first.fence + 1
    assert not protocol.material_authorized(first, 'orphan')
    assert not protocol.publish_ready(first, result(first))
    assert not protocol.fail(first)
    material(protocol, candidate, 'orphan')
    assert protocol.fail(second)
    assert protocol.cleanup_plan(candidate) == []
    protocol.worker_exited(second.worker_id)
    # Even after reclaim, the original worker must be proven gone.
    assert protocol.cleanup_plan(candidate) == []
    protocol.worker_exited(first.worker_id)  # trusted OS reconciliation, not lease inference
    assert len(protocol.cleanup_plan(candidate)) == 1


def test_readiness_db_time_expiry_immutable_and_dedup(protocol):
    before = snapshot(protocol)
    candidate = protocol.prepare('alice', 'Example>=2', 'key')
    claim = protocol.claim(candidate)
    with protocol.engine.connect() as connection:
        start = connection.scalar(text('select clock_timestamp()'))
    assert protocol.publish_ready(claim, result(claim))
    status = protocol.status('alice', candidate)
    assert status['ready_at'] >= start
    assert (status['expires_at'] - status['ready_at']).total_seconds() == 300
    assert not status['approval_supported'] and not status['installation_requested']
    assert status['preview']['desired_diff'] == status['preview']['runtime_diff']
    assert not protocol.publish_ready(claim, result(claim))
    assert protocol.prepare('alice', 'Example>=2', 'key') == candidate
    assert protocol.status('alice', candidate) == status
    table = protocol.tables['candidate']
    with pytest.raises(DBAPIError, match='immutable'):
        with protocol.engine.begin() as connection:
            connection.execute(update(table).where(table.c.id == candidate).values(result_digest='0' * 64))
    # Test DB-time expiry without modifying immutable timestamps: publish with
    # DB clock shifted backwards by a temporary test-only trigger.
    with protocol.engine.begin() as connection:
        connection.execute(text('''CREATE FUNCTION test_expired() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN NEW.ready_at := clock_timestamp() - interval '301 seconds';
            NEW.expires_at := NEW.ready_at + interval '5 minutes'; RETURN NEW; END $$'''))
        connection.execute(text('''CREATE TRIGGER aaa_expired BEFORE UPDATE ON test_managed_candidate
            FOR EACH ROW WHEN (OLD.result_digest IS NULL AND NEW.result_digest IS NOT NULL)
            EXECUTE FUNCTION test_expired()'''))
    expired = protocol.prepare('alice', 'Example>=2', 'expired-key')
    expired_claim = protocol.claim(expired)
    material(protocol, expired, 'expired-secret')
    assert protocol.publish_ready(expired_claim, result(expired_claim))
    assert protocol.status('alice', expired)['state'] == 'expired'
    assert protocol.status('alice', expired)['preview'] is None
    assert protocol.prepare('alice', 'Example>=2', 'expired-key') == expired
    assert protocol.cleanup_plan(expired) == []
    protocol.worker_exited(expired_claim.worker_id)
    assert len(protocol.cleanup_plan(expired)) == 1
    assert snapshot(protocol) == before


@pytest.mark.parametrize('generation', ['publication_generation', 'input_generation', 'target_generation', 'runtime_generation'])
def test_stale_preview_and_completion(protocol, generation):
    candidate = protocol.prepare('alice', 'Example>=2', 'key')
    claim = protocol.claim(candidate)
    assert protocol.publish_ready(claim, result(claim))
    queued = protocol.prepare('alice', 'Example>=2', 'second-key')
    stale_claim = protocol.claim(queued)
    with protocol.engine.begin() as connection:
        table = protocol.tables['environment_state']
        connection.execute(update(table).values({generation: table.c[generation] + 1}))
    assert protocol.status('alice', candidate)['stale']
    assert protocol.status('alice', candidate)['preview'] is None
    assert not protocol.publish_ready(stale_claim, result(stale_claim))
    assert protocol.status('alice', queued)['state'] == 'failed'


def test_invalid_result_core_conflict_and_failed_solve_preserve_intent(protocol):
    before = snapshot(protocol)
    # Contradictory contributions must remain intact for the later uv builder;
    # 05a1 does not claim to solve or relax them.
    candidate = protocol.prepare('alice', 'Separate>=3', 'conflict-key')
    claim = protocol.claim(candidate)
    assert claim.inputs['contributions'][0]['declaration'].startswith('Separate[extra]<2')
    invalid = result(claim)
    invalid['bundle']['inventory']['core'] = dict(PIN, version='2.0')
    with pytest.raises(PreparationError, match='invalid ready result'):
        protocol.publish_ready(claim, invalid)
    assert protocol.fail(claim)  # explicit failed builder attestation, no solve in this slice
    assert snapshot(protocol) == before
    assert protocol.status('alice', candidate)['preview'] is None


def test_authorization_unsupported_and_no_adoption(protocol):
    with pytest.raises(PreparationError, match='forbidden'):
        protocol.prepare('outsider', 'Example', 'key')
    candidate = protocol.prepare('alice', 'Example', 'key')
    for operation in ('status', 'cancel'):
        with pytest.raises(PreparationError, match='forbidden'):
            getattr(protocol, operation)('outsider', candidate)
    for operation in ('import', 'adopt'):
        with pytest.raises(PreparationError, match='unsupported'):
            protocol.prepare('alice', 'Example', 'key', operation=operation)
    with pytest.raises(PreparationError, match='unsupported'):
        protocol.prepare('alice', 'Example', 'key', restart=False)
    with protocol.engine.begin() as connection:
        connection.execute(update(protocol.tables['package_identity']).values(owner_id=None))
    with pytest.raises(PreparationError, match='ownerless'):
        protocol.prepare('alice', 'Separate', 'ownerless-key')
    with protocol.engine.begin() as connection:
        connection.execute(update(protocol.tables['environment_state']).values(managed=False))
    with pytest.raises(PreparationError, match='baseline'):
        protocol.prepare('alice', 'Example', 'off-key')


def test_unknown_runtime_warning_and_complete_diff(protocol):
    with protocol.engine.begin() as connection:
        table = protocol.tables['environment_state']
        row = connection.execute(select(table)).mappings().one()
        baseline = copy.deepcopy(row['snapshot'])
        baseline['runtime'] = {'kind': 'core-only', 'inventory': {'core': PIN}}
        connection.execute(update(table).values(snapshot=baseline))
    candidate = protocol.prepare('alice', 'Example', 'key')
    claim = protocol.claim(candidate)
    ready = result(claim)
    ready['runtime_diff'] = None
    ready['warnings'] = ['runtime-not-normally-verified']
    assert protocol.publish_ready(claim, ready)
    assert protocol.status('alice', candidate)['preview']['runtime_diff'] is None


def test_real_builder_retained_attestation_completion(protocol):
    import json
    from docassemble_executor.registry_builder import verify_retained
    evidence = SOURCE / 'uv-experiment/05a2/evidence/accepted'
    ready = json.loads((evidence / 'attestation.json').read_text())
    verify_retained(evidence / 'retained' / ready['bundle']['digest'], ready['bundle']['digest'])
    core = {'packaging': ready['bundle']['inventory']['packaging']}
    baseline = {'core': core, 'target': python_target(), 'tool': {'uv': '0.12.21'},
                'config': {'index_url': 'https://pypi.org/simple'}, 'desired_inventory': core,
                'runtime': {'kind': 'normal', 'inventory': core}}
    with protocol.engine.begin() as connection:
        connection.execute(update(protocol.tables['environment_state']).values(snapshot=baseline))
        connection.execute(protocol.tables['requirement_contribution'].delete())
    candidate = protocol.prepare('alice', 'six==1.17.0', 'real-builder')
    from docassemble_executor.preparation_poller import PreparationPoller
    class RetainedBuilder:
        def build(self, inputs):
            assert inputs['ownership_effects'] == ready['ownership_effects']
            verify_retained(evidence / 'retained' / ready['bundle']['digest'], ready['bundle']['digest'])
            return ready
    assert PreparationPoller(protocol, RetainedBuilder()).run_once() == 'ready'
    assert protocol.status('alice', candidate)['preview'] == ready
    assert protocol.status('alice', candidate)['installation_requested'] is False


def test_preparation_poller_concurrent_discovery_and_reclaim(protocol):
    candidate = protocol.prepare('alice', 'Example', 'poll')
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: protocol.claim_next(), range(2)))
    first = next(claim for claim in claims if claim)
    assert claims.count(None) == 1
    assert first.candidate_id == candidate
    expire_claim(protocol, candidate)
    second = protocol.claim_next()
    assert second.fence == first.fence + 1
    assert not protocol.publish_ready(first, result(first))
    assert not protocol.renew(first)
    assert protocol.claim_next() is None
    with protocol.engine.connect() as connection:
        assert [row.exited for row in connection.execute(select(protocol.tables['preparation_worker']))] == [False, False]


def test_preparation_poller_renews_during_build_without_transaction(protocol):
    import time
    from docassemble_executor.preparation_poller import PreparationPoller
    before = snapshot(protocol)
    candidate = protocol.prepare('alice', 'Example', 'poll')
    class SlowBuilder:
        def build(self, inputs):
            # Runs longer than the initial lease; a competing poller stays idle.
            time.sleep(1.4)
            assert protocol.claim_next() is None
            with protocol.engine.connect() as connection:
                row = connection.execute(select(protocol.tables['candidate'])).mappings().one()
            claim = types.SimpleNamespace(inputs=inputs)
            assert row['state'] == 'preparing'
            return result(claim)
    poller = PreparationPoller(protocol, SlowBuilder(), lease_seconds=1)
    assert poller.run_once() == 'ready'
    assert poller.run_once() == 'idle'
    assert protocol.status('alice', candidate)['state'] == 'ready'
    assert snapshot(protocol) == before


def test_preparation_poller_cancel_fences_completion_and_failure_is_redacted(protocol):
    from docassemble_executor.preparation_poller import PreparationPoller
    candidate = protocol.prepare('alice', 'Example', 'cancel-poll')
    class CancelBuilder:
        def build(self, inputs):
            protocol.cancel('alice', candidate)
            return result(types.SimpleNamespace(inputs=inputs))
    assert PreparationPoller(protocol, CancelBuilder()).run_once() == 'fenced'
    failed = protocol.prepare('alice', 'Example', 'failure-poll')
    class FailedBuilder:
        def build(self, inputs):
            raise RuntimeError('https://user:secret@example.invalid/?token=secret')
    assert PreparationPoller(protocol, FailedBuilder()).run_once() == 'failed'
    assert protocol.status('alice', failed)['state'] == 'failed'
    with protocol.engine.connect() as connection:
        assert all(not row.exited for row in connection.execute(select(protocol.tables['preparation_worker'])))


def test_preparation_poller_independent_process_no_runtime_or_dispatch_service(protocol, tmp_path):
    import subprocess
    candidate = protocol.prepare('alice', 'Example', 'process-poll')
    url = protocol.engine.url.render_as_string(hide_password=False)
    # Connect with the isolated schema and host mutation state; no
    # Flask/Redis/Celery process is running. Production startup provisions this
    # state/lock for the non-root preparation account.
    state = tmp_path / 'state'
    state.mkdir()
    env = dict(os.environ, DA_PREPARATION_DATABASE_URL=url,
               DA_MANAGED_STATE_DIR=str(state), PGOPTIONS='-csearch_path=preparation_test')
    command = [sys.executable, '-m', 'docassemble_executor.preparation_poller',
               '--store', str(tmp_path), '--prefix', 'test_', '--once']
    completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=20)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == 'failed'  # fixture target fails closed in real builder
    assert protocol.status('alice', candidate)['state'] == 'failed'
    completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=20)
    assert completed.returncode == 0
    assert completed.stdout.strip() == 'idle'


def test_preparation_poller_lost_renewal_reclaims_without_exit_inference(protocol):
    import time
    from docassemble_executor.preparation_poller import PreparationPoller
    candidate = protocol.prepare('alice', 'Example', 'lost-poll')
    class LostBuilder:
        def build(self, inputs):
            expire_claim(protocol, candidate)
            time.sleep(0.5)  # renewal observes expired fence; no resurrection
            return result(types.SimpleNamespace(inputs=inputs))
    assert PreparationPoller(protocol, LostBuilder(), lease_seconds=1).run_once() == 'fenced'
    class NextBuilder:
        def build(self, inputs):
            return result(types.SimpleNamespace(inputs=inputs))
    assert PreparationPoller(protocol, NextBuilder()).run_once() == 'ready'
    with protocol.engine.connect() as connection:
        workers = connection.execute(select(protocol.tables['preparation_worker'])).mappings().all()
    assert len(workers) == 2
    assert {worker['fence'] for worker in workers} == {1, 2}
    assert not any(worker['exited'] for worker in workers)


@pytest.mark.parametrize('build_failure', [False, True], ids=['ready', 'failed'])
def test_preparation_poller_inflight_renewal_uncertainty_fences_terminal_write(
        protocol, monkeypatch, build_failure):
    import threading
    from docassemble_executor.preparation_poller import PreparationPoller

    candidate = protocol.prepare('alice', 'Example', 'uncertain-poll')
    renewing, joining, release = threading.Event(), threading.Event(), threading.Event()
    original_join = threading.Thread.join

    def observe_join(thread, *args, **kwargs):
        if thread.name == 'preparation-renewal':
            joining.set()
        return original_join(thread, *args, **kwargs)

    def uncertain_renew(claim, **kwargs):
        renewing.set()
        assert release.wait(5)
        raise RuntimeError('uncertain database response')

    class FinishedBuilder:
        def build(self, inputs):
            assert renewing.wait(5)
            if build_failure:
                raise RuntimeError('redacted build failure')
            return result(types.SimpleNamespace(inputs=inputs))

    monkeypatch.setattr(threading.Thread, 'join', observe_join)
    monkeypatch.setattr(protocol, 'renew', uncertain_renew)
    with ThreadPoolExecutor(max_workers=1) as pool:
        completion = pool.submit(
            PreparationPoller(protocol, FinishedBuilder(), lease_seconds=3).run_once)
        try:
            assert joining.wait(5)
            # Builder has finished and renewal is still in flight. Neither ready
            # nor failed may be written, even with an unexpired original lease.
            with protocol.engine.connect() as connection:
                row = connection.execute(select(protocol.tables['candidate'])).mappings().one()
                assert row['state'] == 'preparing'
                assert row['claim_until'] > connection.scalar(select(text('clock_timestamp()')))
        finally:
            release.set()
        assert completion.result(timeout=5) == 'fenced'
    status = protocol.status('alice', candidate)
    assert status['state'] == 'preparing'
    assert status['preview'] is None


def test_physical_material_cleanup_all_workers_gate(protocol, tmp_path):
    import uuid
    module = protocol
    candidate = module.prepare('alice', 'example', 'physical-cleanup')
    first = module.claim(candidate, lease_seconds=1)
    # Reclaim without waiting for wall clock; timeout is not exit evidence.
    with module.engine.begin() as connection:
        connection.execute(update(module.tables['candidate']).where(
            module.tables['candidate'].c.id == candidate).values(claim_until=text("clock_timestamp() - interval '1 second'")))
    second = module.claim(candidate)
    ids = [str(uuid.uuid4()) for _ in range(3)]
    for material_id in ids:
        path = tmp_path / material_id
        path.write_text('DISPOSABLE_SECRET_TOKEN')
        module.register_material(second, material_id, str(path))
    with module.engine.begin() as connection:
        material = module.tables['candidate_material']
        connection.execute(update(material).where(material.c.id == ids[1]).values(committed=True))
        connection.execute(update(material).where(material.c.id == ids[2]).values(shared=True))
    cache = tmp_path / 'cache'
    cache.mkdir()
    (cache / 'entry').write_text('cache')
    module.cancel('alice', candidate)
    assert not module.material_authorized(second, ids[0])
    assert 'DISPOSABLE_SECRET_TOKEN' not in str(module.status('alice', candidate))
    assert module.delete_candidate_material(candidate, tmp_path) == 0
    module.worker_exited(second.worker_id)
    assert module.delete_candidate_material(candidate, tmp_path) == 0
    assert (tmp_path / ids[0]).exists()
    module.worker_exited(first.worker_id)
    assert module.reconcile_material(tmp_path) == 1
    assert module.reconcile_material(tmp_path) == 0
    assert not (tmp_path / ids[0]).exists()
    assert all((tmp_path / value).exists() for value in ids[1:])
    assert (cache / 'entry').read_text() == 'cache'


def test_physical_cleanup_rejects_escape_and_symlink(protocol, tmp_path):
    import uuid
    module = protocol
    candidate = module.prepare('alice', 'example', 'escape-cleanup')
    claim = module.claim(candidate)
    material_id = str(uuid.uuid4())
    outside = tmp_path / 'outside'
    outside.write_text('DISPOSABLE_SECRET_TOKEN')
    path = tmp_path / material_id
    path.symlink_to(outside)
    module.register_material(claim, material_id, str(path))
    module.cancel('alice', candidate)
    module.worker_exited(claim.worker_id)
    with pytest.raises(PreparationError, match='storage type'):
        module.delete_candidate_material(candidate, tmp_path)
    path.unlink()
    with module.engine.begin() as connection:
        table = module.tables['candidate_material']
        connection.execute(update(table).where(table.c.id == material_id).values(path=str(outside)))
    with pytest.raises(PreparationError, match='outside restricted'):
        module.delete_candidate_material(candidate, tmp_path)
    assert outside.read_text() == 'DISPOSABLE_SECRET_TOKEN'


def test_preparation_container_receipt_lock_spans_renewal_drain_and_readiness(
        protocol, monkeypatch, tmp_path):
    import threading
    from docassemble_executor.preparation_containers import ContainerWorkers
    from docassemble_executor.preparation_poller import PreparationPoller

    protocol.prepare('alice', 'Example', 'container-publication-race')
    authority = ContainerWorkers(protocol, tmp_path / 'receipts', tmp_path,
                                 'trusted-image', tmp_path / 'store')
    # This protocol fixture uses synthetic inputs/results, not a real uv build.
    monkeypatch.setattr('docassemble_executor.preparation_containers.validate_public_policy',
                        lambda inputs: None)
    joining, publishing = threading.Event(), threading.Event()
    drain, publish = threading.Event(), threading.Event()
    original_join = threading.Thread.join
    original_publish = protocol.publish_ready
    original_exit = protocol.worker_exited
    order = []
    claimed = []

    def build(claim):
        claimed.append(claim)
        authority._write(authority.receipts / claim.worker_id / 'receipt.json', {
            'worker': claim.worker_id, 'candidate': claim.candidate_id,
            'container': 'c' * 64, 'daemon': 'daemon'})
        order.append('built')
        return result(claim)

    def join(thread, *args, **kwargs):
        if thread.name == 'preparation-renewal':
            joining.set()
            assert drain.wait(5)
        return original_join(thread, *args, **kwargs)

    def ready(claim, value):
        publishing.set()
        assert publish.wait(5)
        accepted = original_publish(claim, value)
        assert accepted
        order.append('ready')
        return accepted

    def exited(worker):
        assert order == ['built', 'ready']
        assert protocol.status('alice', claimed[0].candidate_id)['state'] == 'ready'
        order.append('exited')
        return original_exit(worker)

    info = {'Config': {'Labels': {}, 'User': '1000:1000'},
            'HostConfig': {'PidMode': '', 'Privileged': False,
                           'RestartPolicy': {'Name': 'no'}, 'CapDrop': ['ALL']},
            'Mounts': [], 'State': {'Status': 'exited'}}
    docker_calls = []

    def docker(*args):
        docker_calls.append(args)
        if args[0] == 'info':
            return 'daemon'
        if args[0] == 'ps':
            return 'c' * 64
        if args[0] == 'inspect':
            import json
            info['Config']['Labels']['da.preparation.worker'] = claimed[0].worker_id
            return json.dumps([info])
        assert args == ('rm', 'c' * 64)
        return ''

    monkeypatch.setattr(authority, 'build_claim', build)
    monkeypatch.setattr(authority, '_docker', docker)
    monkeypatch.setattr(threading.Thread, 'join', join)
    monkeypatch.setattr(protocol, 'publish_ready', ready)
    monkeypatch.setattr(protocol, 'worker_exited', exited)
    with ThreadPoolExecutor(max_workers=1) as pool:
        completion = pool.submit(PreparationPoller(protocol, types.SimpleNamespace(), containers=authority).run_once)
        try:
            assert joining.wait(5)  # Build has returned; renewal drain is not done.
            assert authority.reconcile() == 0
            assert docker_calls == []
            drain.set()
            assert publishing.wait(5)  # Drain is done; terminal DB write is not.
            assert authority.reconcile() == 0
            assert docker_calls == []
            assert order == ['built']
            publish.set()
            assert completion.result(timeout=5) == 'ready'
        finally:
            drain.set()
            publish.set()
    assert order == ['built', 'ready', 'exited']
    assert docker_calls[-1] == ('rm', 'c' * 64)
