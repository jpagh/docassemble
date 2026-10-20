"""Actual private validation DB + destructive checkpoint DB/files round-trip."""
# ruff: noqa: F811
import json
import uuid

import pytest
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.engine import make_url

from test_preparation_postgres import protocol  # noqa: F401
from test_managed_backup_postgres import objects  # noqa: F401
from test_checkpoints_postgres import setup_checkpoint
from docassemble.base.managed_checkpoint import package_projection
from docassemble_executor import checkpoint_restore as restore
from docassemble_executor.checkpoints import capture
from docassemble_executor.cold_restore import ColdRestoreError
from docassemble_executor.installation import Installation
from docassemble_executor.managed_backup import _sha


@pytest.fixture
def checkpoint(protocol, objects, monkeypatch, tmp_path, request):
    kwargs, claim = setup_checkpoint(protocol, objects, monkeypatch)
    paths = kwargs['paths']
    if getattr(request, 'param', None) == 'migration':
        historical_id = migrate_with_reused_image_name(protocol, kwargs, monkeypatch)
        from docassemble_executor.managed_backup import create_backup, verify_backup
        dump = tmp_path / 'post-migration-backup'
        complete = create_backup(protocol.engine, dump, **kwargs)
        assert verify_backup(dump, tmp_path / 'post-migration-backup.managed', 'test_') == complete
        assert complete['revision_image_material'][historical_id]['image_bytes_archived'] is False
    name = 'da23_test_' + uuid.uuid4().hex
    url = make_url(str(protocol.engine.url.render_as_string(hide_password=False))).set(database=name,
        query={'options': '-csearch_path=preparation_test'})
    restore._admin(url, 'CREATE DATABASE ' + restore._quoted(name))
    target = create_engine(url)
    with target.begin() as connection:
        connection.execute(text('CREATE TABLE public.post_checkpoint (value text)'))
        connection.execute(text("INSERT INTO public.post_checkpoint VALUES ('subsequent data')"))
    target.dispose()
    paths.config_file.write_text('db:\n  name: ' + name + '\n  user: ' + url.username + '\n  host: ' + url.host +
        '\n  port: ' + str(url.port) + '\n  password: ' + (url.password or '') + '\n  table prefix: test_\n' +
        'uploads: ' + str(paths.da_root / 'files') + '\n')
    with protocol.engine.begin() as connection:
        connection.execute(text('CREATE TABLE test_package (id integer PRIMARY KEY, name text, active boolean, limitation text)'))
        connection.execute(text("INSERT INTO test_package VALUES (1, 'Example', true, '<2')"))
        connection.execute(text('CREATE TABLE restore_data (value text)'))
        connection.execute(text("INSERT INTO restore_data VALUES ('checkpoint progress')"))
        connection.execute(text('CREATE TABLE restore_grants (value text)'))
        connection.execute(text("INSERT INTO restore_grants VALUES ('checkpoint owner')"))
        connection.execute(text('CREATE TABLE alembic_version (version_num text)'))
        connection.execute(text("INSERT INTO alembic_version VALUES ('pre-schema')"))
        env = protocol._environment(connection)
        historical = dict(env['snapshot'], contributions=[dict(row) for row in connection.execute(
            select(protocol.tables['requirement_contribution'])).mappings()],
            package_projection=package_projection(connection, protocol.prefix))
        connection.execute(protocol.tables['revision'].insert().values(id='restore-applied', snapshot=historical))
        # Approval projection has advanced, but the running environment hasn't.
        connection.execute(text("UPDATE test_package SET limitation='>=2'"))
        connection.execute(protocol.tables['installation_attempt'].insert().values(id=claim.attempt_id,
            revision_id='advanced-desired', record={'phase': 'running', 'fence': 3}))
        connection.execute(update(protocol.tables['environment_state']).values(active_attempt_id=claim.attempt_id))
    claim.pre_change['runtime']['revision_id'] = 'restore-applied'
    claim.pre_change['target'] = historical['target']
    kwargs.pop('prefix')
    status = capture(protocol, claim=claim, inputs={}, **kwargs)
    directory = next(p.parent for p in (paths.da_root / 'backup/checkpoints').glob('*/checkpoint.json') if json.loads(p.read_text())['id'] == status['id'])
    auth_dir = tmp_path / 'authorization'
    auth_dir.mkdir(mode=0o700)
    auth = auth_dir / 'restore.json'
    auth.write_text(json.dumps({'format': 1, 'action': 'full-checkpoint-restore', 'incarnation': str(uuid.uuid4()),
        'dump_sha256': _sha(directory / 'database'), 'checkpoint_id': status['id'],
        'checkpoint_sha256': _sha(directory / 'checkpoint.json'), 'old_deployment_excluded': True,
        'acknowledge_subsequent_data_loss': True}))
    auth.chmod(0o600)
    (paths.da_root / 'files/interview').write_text('subsequent progress')
    (paths.da_root / 'files/created-after').write_text('must disappear')
    monkeypatch.setattr(restore, 'preflight_environment', lambda *_: None)
    monkeypatch.setattr(restore, 'preflight_broker', lambda _: 'redis://fixture/0')
    def broker_command(_broker, verb, *_):
        return {'EXISTS': '0', 'CONFIG': 'appendonly\nno', 'SAVE': 'OK', 'DEL': '1'}[verb]
    monkeypatch.setattr(restore, '_broker_command', broker_command)
    # Root-owned authorization itself is qualified by native act/container tests;
    # these PostgreSQL tests run as an unprivileged host user.
    monkeypatch.setattr(restore.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(restore, 'authorization', lambda path, dump: {
        'incarnation': json.loads(path.read_text())['incarnation'], 'dump_sha256': _sha(dump)})
    yield paths, directory, auth, url, claim
    restore._admin(url, 'DROP DATABASE IF EXISTS ' + restore._quoted(name) + ' WITH (FORCE)')
    journal = paths.state_dir / 'checkpoint-restore.json'
    if journal.exists():
        stage = json.loads(journal.read_text())['staging_database']
        restore._admin(url, 'DROP DATABASE IF EXISTS ' + restore._quoted(stage) + ' WITH (FORCE)')


def test_full_checkpoint_restore_rewinds_data_grants_files_applied_projection_not_pending_work(checkpoint):
    paths, directory, auth, url, claim = checkpoint
    result = restore.restore_checkpoint(paths, directory, auth, url.render_as_string(hide_password=False), 'test_')
    assert result['state'] == 'inputs-restored'
    assert (paths.da_root / 'files/interview').read_text() == 'pre-change progress'
    assert not (paths.da_root / 'files/created-after').exists()
    engine = create_engine(url)
    try:
        service = Installation(engine, lambda *_: True, 'test_')
        with engine.connect() as connection:
            env = connection.execute(select(service.tables['environment_state'])).mappings().one()
            assert env['desired_revision_id'] == 'restore-applied'
            assert env['active_attempt_id'] is None and env['snapshot']['runtime']['kind'] == 'unknown'
            assert connection.scalar(text('SELECT limitation FROM test_package')) == '<2'
            assert connection.scalar(text('SELECT value FROM restore_data')) == 'checkpoint progress'
            assert connection.scalar(text('SELECT value FROM restore_grants')) == 'checkpoint owner'
            assert connection.scalar(text('SELECT version_num FROM alembic_version')) == 'pre-schema'
            assert not connection.scalar(text("SELECT to_regclass('public.post_checkpoint')"))
            record = connection.scalar(select(service.tables['installation_attempt'].c.record).where(
                service.tables['installation_attempt'].c.id == claim.attempt_id))
            assert record['phase'] == 'blocked' and record['fence'] == 4
            assert env['snapshot']['cold_restore']['checkpoint_database_revision'] == 'pre-schema'
        assert service.claim_installation('copied-host') is None
        assert restore.Admission(paths.state_dir).state()['mode'] == 'closed'
        restore.require_restore_ready(paths)
        with pytest.raises(ColdRestoreError, match='fresh authorization'):
            restore.restore_checkpoint(paths, directory, auth, url.render_as_string(hide_password=False), 'test_')
    finally:
        engine.dispose()


@pytest.mark.parametrize('boundary', ['preflight', 'database-replace', 'files-replace', 'broker-work-discard', 'managed-authority-reset'])
def test_restore_crashes_leave_external_phase_evidence_and_no_normal_startup(checkpoint, monkeypatch, boundary):
    paths, directory, auth, url, _ = checkpoint
    if boundary == 'preflight':
        def unavailable(*_):
            raise ColdRestoreError('required frozen artifact expired')
        monkeypatch.setattr(restore, 'preflight_environment', unavailable)
    elif boundary == 'broker-work-discard':
        def failed(*_):
            raise InterruptedError('crash while discarding stale queued deliveries')
        monkeypatch.setattr(restore, '_broker_command', failed)
    elif boundary == 'database-replace':
        original = restore._admin
        def failed(url, statement):
            if statement.startswith('ALTER DATABASE'):
                raise InterruptedError('crash after dropping target')
            return original(url, statement)
        monkeypatch.setattr(restore, '_admin', failed)
    else:
        original = restore.restore_objects
        def failed(directory, document, report):
            if (directory.name == 'database.managed') == (boundary == 'managed-authority-reset'):
                raise InterruptedError('crash during file replacement')
            return original(directory, document, report)
        monkeypatch.setattr(restore, 'restore_objects', failed)
    with pytest.raises((ColdRestoreError, InterruptedError)):
        restore.restore_checkpoint(paths, directory, auth, url.render_as_string(hide_password=False), 'test_')
    journal = json.loads((paths.state_dir / 'checkpoint-restore.json').read_text())
    assert journal['state'] == ('preflight-failed' if boundary == 'preflight' else 'interrupted')
    assert journal['phase'] == ('database-validation' if boundary == 'preflight' else boundary)
    assert journal['staging_database'] and journal['action'].startswith('keep deployment excluded')
    with pytest.raises(ColdRestoreError, match='interrupted full checkpoint'):
        restore.require_restore_ready(paths)
    assert restore.Admission(paths.state_dir).state()['mode'] == 'closed'
    if boundary == 'preflight':
        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                assert connection.scalar(text('SELECT value FROM public.post_checkpoint')) == 'subsequent data'
            assert (paths.da_root / 'files/interview').read_text() == 'subsequent progress'
        finally:
            engine.dispose()


@pytest.mark.parametrize('damage', ['file', 'artifact', 'target', 'acknowledgement', 'projection'])
def test_preflight_failures_do_not_replace_any_live_data(checkpoint, monkeypatch, damage):
    paths, directory, auth, url, _ = checkpoint
    if damage == 'file':
        (directory / 'objects/files/interview').unlink()
    elif damage == 'artifact':
        next((directory / 'database.managed/objects/retained').glob('*/uv.lock')).unlink()
    elif damage == 'target':
        paths.manifest_file.write_text('{"identity":{"different":"image"}}')
    elif damage == 'acknowledgement':
        value = json.loads(auth.read_text())
        value['acknowledge_subsequent_data_loss'] = False
        auth.write_text(json.dumps(value))
    else:
        def unavailable(*_):
            raise ValueError('checkpoint applied revision lacks required Package projection; recapture required')
        monkeypatch.setattr(restore, 'restore_package_projection', unavailable)
    with pytest.raises((RuntimeError, ValueError, OSError)):
        restore.restore_checkpoint(paths, directory, auth, url.render_as_string(hide_password=False), 'test_')
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text('SELECT value FROM public.post_checkpoint')) == 'subsequent data'
    finally:
        engine.dispose()
    assert (paths.da_root / 'files/interview').read_text() == 'subsequent progress'


def migrate_with_reused_image_name(protocol, kwargs, monkeypatch):
    """Real system publication; obsolete image bytes are no longer present."""
    from docassemble.base.managed_migration import ImageMigration
    from docassemble_executor.bundles import hash_project
    from test_image_migration_postgres import core, queue
    from docassemble.base.managed_preparation import Preparation
    from docassemble.base.managed_publication import Publication
    paths = kwargs['paths']
    manifest = json.loads(paths.manifest_file.read_text())
    old_image = {'image_target': 'old-image', 'bundle': 'runtime-r1',
                 'bundle_digest': manifest['bundles']['runtime-r1']}
    with protocol.engine.begin() as connection:
        env = protocol._environment(connection)
        old = dict(env['snapshot'], core=core('2.0'), image_baseline=old_image)
        old.pop('direct_sources', None)
        old.pop('git_credentials', None)
        connection.execute(update(protocol.tables['environment_state']).values(
            desired_revision_id='retained', active_attempt_id=None, snapshot=old))
    publisher = Publication(protocol.engine, lambda *_: True,
                            lambda *_: {'owner_id': 'alice', 'legacy_package_id': None}, 'test_')
    candidate = publisher.prepare('alice', 'Example>=2', 'historical-image')
    historical_claim = publisher.claim(candidate)
    def prepared(claim):
        from docassemble.base.managed_preparation import digest
        inventory = dict(claim.inputs['baseline']['core'], example={'version': '2.0', 'source_digest': 'b' * 64})
        staging = kwargs['store'] / ('staging-' + claim.candidate_id)
        staging.mkdir()
        (staging / 'pyproject.toml').write_text('[project]\nname="migration-fixture"\nversion="1"\n')
        (staging / 'uv.lock').write_text('version = 1\n')
        (staging / 'inventory.json').write_text(json.dumps(inventory))
        contents = {path.name: _sha(path) for path in staging.iterdir()}
        reference = digest(contents)
        (staging / 'manifest.json').write_text(json.dumps(contents))
        retained = kwargs['store'] / reference
        if retained.exists():
            import shutil
            shutil.rmtree(staging)
        else:
            staging.rename(retained)
        before = claim.inputs['baseline']['desired_inventory']
        def diff(before):
            return {name: {'before': before.get(name), 'after': inventory.get(name)}
                    for name in sorted(set(before) | set(inventory)) if before.get(name) != inventory.get(name)}
        return {'bundle': {'digest': retained.name, 'project_digest': _sha(retained / 'pyproject.toml'),
            'lock_digest': _sha(retained / 'uv.lock'), 'inventory': inventory, 'artifacts': []},
            'desired_diff': diff(before), 'runtime_diff': diff(claim.inputs['baseline']['runtime']['inventory'])
                if claim.inputs['baseline']['runtime']['kind'] == 'normal' else None,
            'warnings': Preparation.ready_warnings(claim.inputs),
            'validation': {'core_unchanged': True, 'requirements_satisfied': True, 'durable_materialization_verified': True},
            'ownership_effects': claim.inputs['ownership_effects']}
    assert publisher.publish_ready(historical_claim, prepared(historical_claim))
    historical = publisher.approve('alice', candidate, publisher.status('alice', candidate)['result_digest'], 'historical-approve')
    installer = Installation(protocol.engine, lambda *_: True, 'test_')
    applied = installer.claim_installation('old-host')
    from docassemble_executor.roles import RUNTIME_ROLES, INFRASTRUCTURE_ROLES
    assert installer.report_terminal({'identity': applied.identity, 'outcome': 'applied-normal', 'normal_verified': True,
        'mutation_started': True, 'child_safe': True, 'failure': None,
        'inventory': {name: pin['version'] for name, pin in applied.snapshot['desired_inventory'].items()},
        'readiness': {'schema_verified': True, 'roles': sorted(RUNTIME_ROLES + INFRASTRUCTURE_ROLES)}}) == 'reported'
    # A replacement image deliberately reuses the bundle label with new bytes.
    (paths.bundles_dir / 'runtime-r1/pyproject.toml').write_text('[project]\nname="replacement"\nversion="3"\n')
    manifest['bundles']['runtime-r1'] = hash_project(paths.bundles_dir / 'runtime-r1')
    paths.manifest_file.write_text(json.dumps(manifest))
    new_image = dict(old_image, image_target='new-image', bundle_digest=manifest['bundles']['runtime-r1'])
    service = ImageMigration(protocol.engine, 'test_')
    service.target = dict(old, core=core('3.0'), image_baseline=new_image)
    monkeypatch.setenv('DA_UV_MANAGED', 'true')
    candidate = queue(service)
    claim = service.claim(candidate)
    assert service.publish_ready(claim, prepared(claim))
    assert service.advance() == 'published'
    with service.engine.connect() as connection:
        env = service._environment(connection)
        assert env['snapshot']['image_baseline'] == new_image
        assert env['desired_revision_id'] != historical['revision_id']
    return historical['revision_id']


@pytest.mark.parametrize('checkpoint', ['migration'], indirect=True)
def test_image_migration_complete_backup_checkpoint_restore_preserves_historical_reference(checkpoint):
    paths, directory, auth, url, _ = checkpoint
    managed = json.loads((directory / 'database.managed/manifest.json').read_text())
    historical_id, historical = next((identity, row) for identity, row in managed['revision_image_material'].items()
                                     if row['image_bytes_archived'] is False)
    assert historical['image_bytes_archived'] is False
    assert historical['restore_bundle_digests']
    assert historical['image_bundle_digest'] != json.loads(
        (directory / 'database.managed/objects/image/manifest.json').read_text())['bundles']['runtime-r1']
    result = restore.restore_checkpoint(paths, directory, auth, url.render_as_string(hide_password=False), 'test_')
    assert result['state'] == 'inputs-restored'
    engine = create_engine(url)
    try:
        service = Installation(engine, lambda *_: True, 'test_')
        with engine.connect() as connection:
            old = connection.scalar(select(service.tables['revision'].c.snapshot).where(
                service.tables['revision'].c.id == historical_id))
            assert old['image_baseline']['bundle_digest'] == historical['image_bundle_digest']
            assert old['publication']['result']['bundle']['digest'] in historical['restore_bundle_digests']
            assert connection.scalar(text('SELECT value FROM restore_data')) == 'checkpoint progress'
        assert (paths.da_root / 'files/interview').read_text() == 'pre-change progress'
    finally:
        engine.dispose()


@pytest.mark.parametrize('failure', ['error', 'crash'])
def test_broker_durability_failure_never_commits_inputs_restored(checkpoint, monkeypatch, failure):
    paths, directory, auth, url, _ = checkpoint
    original = restore._broker_command
    def command(broker, verb, *args):
        if verb == 'SAVE':
            if failure == 'crash':
                raise InterruptedError('crash during broker persistence barrier')
            return 'ERR persistence failed'
        return original(broker, verb, *args)
    monkeypatch.setattr(restore, '_broker_command', command)
    with pytest.raises((restore.RestoreBrokerError, InterruptedError)):
        restore.restore_checkpoint(paths, directory, auth, url.render_as_string(hide_password=False), 'test_')
    journal = json.loads((paths.state_dir / 'checkpoint-restore.json').read_text())
    assert journal['state'] == 'interrupted'
    assert journal['phase'] == 'broker-work-discard'
    assert not (paths.state_dir / 'checkpoint-broker.json').exists()
    assert restore.Admission(paths.state_dir).state()['mode'] == 'closed'
    with pytest.raises(ColdRestoreError, match='interrupted full checkpoint'):
        restore.require_restore_ready(paths)
