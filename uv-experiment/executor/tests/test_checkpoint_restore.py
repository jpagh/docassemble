"""Operator restore authority and interruption fences (not package permission)."""
import json
import os
from pathlib import Path
import uuid

import pytest

from docassemble_executor.checkpoint_restore import checkpoint_authorization, require_restore_ready, WARNING
from docassemble_executor.cold_restore import ColdRestoreError
from docassemble_executor.managed_backup import _sha, ObjectCopy, ManagedBackupError
from docassemble_executor.paths import ExecutorPaths
from docassemble_executor.cloud_checkpoints import CloudCheckpoint
from test_cloud_checkpoints import cloud_fixture


@pytest.mark.parametrize('missing', ['none', 'selection', 'acknowledgement', 'action', 'manifest', 'exclude', 'dump'])
def test_full_restore_requires_independent_restricted_selected_data_loss_authority(tmp_path, missing):
    directory = tmp_path / 'checkpoint'
    directory.mkdir()
    (directory / 'database').write_bytes(b'dump')
    (directory / 'checkpoint.json').write_text('{}')
    identity = str(uuid.uuid4())
    auth_dir = tmp_path / 'operator'
    auth_dir.mkdir(mode=0o700)
    auth = auth_dir / 'restore.json'
    value = {'format': 1, 'action': 'full-checkpoint-restore', 'incarnation': str(uuid.uuid4()),
             'dump_sha256': _sha(directory / 'database'), 'checkpoint_id': identity,
             'checkpoint_sha256': _sha(directory / 'checkpoint.json'),
             'old_deployment_excluded': True, 'acknowledge_subsequent_data_loss': True}
    keys = {'selection': 'checkpoint_id', 'acknowledgement': 'acknowledge_subsequent_data_loss',
            'action': 'action', 'manifest': 'checkpoint_sha256', 'exclude': 'old_deployment_excluded', 'dump': 'dump_sha256'}
    if missing != 'none':
        value.pop(keys[missing])
    auth.write_text(json.dumps(value))
    auth.chmod(0o600)
    if missing == 'none' and os.geteuid() == 0:
        assert checkpoint_authorization(auth, directory, {'id': identity})['checkpoint_id'] == identity
    else:
        with pytest.raises(ColdRestoreError):
            checkpoint_authorization(auth, directory, {'id': identity})


@pytest.mark.parametrize('phase', ['preflight', 'preflight-failed', 'restoring', 'interrupted', 'invalid', 'inputs-restored'])
def test_every_incomplete_restore_blocks_boot_and_both_durable_workers(tmp_path, monkeypatch, phase):
    monkeypatch.setenv('DA_MANAGED_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('DA_ROOT', str(tmp_path / 'root'))
    monkeypatch.setenv('DA_RUN_DIR', str(tmp_path / 'run'))
    monkeypatch.setenv('UV_CACHE_DIR', str(tmp_path / 'cache'))
    paths = ExecutorPaths.from_env()
    (tmp_path / 'checkpoint-restore.json').write_text(json.dumps({'state': phase, 'phase': 'files-replace'}))
    if phase == 'inputs-restored':
        require_restore_ready(paths)
    else:
        with pytest.raises(ColdRestoreError, match='interrupted full checkpoint restore'):
            require_restore_ready(paths)
        from docassemble_executor.installation import InstallationPoller
        with pytest.raises(ColdRestoreError):
            InstallationPoller(None, paths, tmp_path, tmp_path).run_once()
        from docassemble_executor.preparation_poller import guarded_preparation_once
        with pytest.raises(ColdRestoreError):
            guarded_preparation_once(paths, [])


def test_full_restore_warning_distinguishes_data_and_external_effects():
    assert all(text in WARNING for text in ('grants', 'lost', 'email', 'not package-only rollback'))


@pytest.mark.parametrize('provider', ['s3', 'azure'])
@pytest.mark.parametrize('failure', ['none', 'auth', 'corrupt-upload'])
def test_cloud_restore_rewinds_exact_content_and_coverage_but_not_backups(tmp_path, provider, failure):
    inputs, bucket = cloud_fixture(tmp_path, provider)
    cloud = CloudCheckpoint(inputs)
    directory = tmp_path / 'capture'
    captured = cloud.capture(ObjectCopy(directory))
    (bucket / 'files/new').write_text('post-checkpoint object')
    (bucket / 'files/1/interview').write_text('post-checkpoint data')
    (bucket / 'checkpoints/retained').mkdir(parents=True)
    (bucket / 'checkpoints/retained/marker').write_text('keep historical material')
    if failure != 'none':
        cloud.env['CLOUD_FAKE_FAILURE'] = failure
        with pytest.raises(ManagedBackupError):
            cloud.restore(directory, captured)
        return
    cloud.restore(directory, captured)
    assert (bucket / 'files/1/interview').read_bytes() == b'pre-change interview bytes'
    assert not (bucket / 'files/new').exists()
    assert (bucket / 'backup/10-16/old').is_file()
    assert (bucket / 'checkpoints/retained/marker').is_file()
    assert set(cloud.live_listing()) == set(captured['files'])


def test_boot_checks_external_restore_journal_before_reading_runtime_configuration():
    source = (Path(__file__).parents[1] / 'src/docassemble_executor/controller.py').read_text().split('def boot(', 1)[1]
    assert source.index('require_restore_ready(paths)') < source.index('context = load_context(paths)')


@pytest.mark.parametrize('broker', [None, 'amqp://host', 'rediss://host', 'redis://host;redis://other'])
def test_unknown_broker_refused_before_any_destructive_work(broker):
    from docassemble_executor.checkpoint_restore import preflight_broker
    with pytest.raises(ColdRestoreError, match='qualified Redis broker'):
        preflight_broker({'rabbitmq': broker})


def test_qualified_broker_keys_are_only_transport_queues_and_unacked(monkeypatch):
    import docassemble_executor.checkpoint_restore as restore
    calls = []
    def command(broker, *args):
        calls.append(args)
        return 'PONG' if args == ('PING',) else 'appendonly\nno' if args[0] == 'CONFIG' else 'none'
    monkeypatch.setattr(restore, '_broker_command', command)
    assert restore.preflight_broker({'rabbitmq': 'redis://fixture/0'}) == 'redis://fixture/0'
    assert len(restore.broker_keys()) == 11
    assert 'celery\x06\x169' in restore.broker_keys()
    assert all(args[0] in ('PING', 'TYPE', 'CONFIG') for args in calls)
    assert ('CONFIG', 'GET', 'appendonly') in calls
    with pytest.raises(ColdRestoreError, match='qualified Redis broker'):
        restore.preflight_broker({'rabbitmq': 'redis://fixture/0', 'celery task routes': {'task': {'queue': 'unknown'}}})


@pytest.mark.parametrize('observed', ['captured-schema', 'changed-schema'])
def test_checkpoint_schema_is_nonmutating_and_must_match_capture(tmp_path, monkeypatch, observed):
    from types import SimpleNamespace
    import docassemble_executor.published as published
    from docassemble_executor.health import Check
    from docassemble_executor.journal import Journal
    from docassemble_executor.roles import RUNTIME_ROLES, INFRASTRUCTURE_ROLES
    from test_schema_journal import _prepare_paths, _publish_context, _publish_claim
    paths = _prepare_paths(tmp_path)
    claim = _publish_claim()
    claim.checkpoint_database_revision = 'captured-schema'
    monkeypatch.setattr(published, 'load_context', lambda *a, **k: _publish_context())
    monkeypatch.setattr(published, '_create_tables', lambda *_: pytest.fail('full checkpoint restore must not run setup'))
    monkeypatch.setattr(published, 'check_schema', lambda *a, **k: SimpleNamespace(usable=True, database_revision=observed))
    monkeypatch.setattr(published, 'probe_runtime', lambda *a, **k: Check('probe', True))
    monkeypatch.setattr(published, '_start_web', lambda *a, **k: True)
    monkeypatch.setattr(published, 'prepare_roles', lambda *a, **k: [Check(role, True) for role in RUNTIME_ROLES + INFRASTRUCTURE_ROLES])
    if observed == 'captured-schema':
        assert published.prepare_published(paths, claim)['schema_verified']
        assert Journal.load(paths.journal_file).schema_succeeded
    else:
        with pytest.raises(published.NormalQualificationError, match='schema'):
            published.prepare_published(paths, claim)
        assert Journal.load(paths.journal_file).schema_uncertain


def test_missing_historical_package_projection_is_refused_not_guessed():
    from sqlalchemy import create_engine, text
    from docassemble.base.managed_checkpoint import package_projection, restore_package_projection
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE package (id integer PRIMARY KEY, active boolean, limitation text)'))
        connection.execute(text("INSERT INTO package VALUES (1, true, '<2')"))
        historical = package_projection(connection, '')
        connection.execute(text("UPDATE package SET limitation='>=2'"))
        with pytest.raises(ValueError, match='lacks required Package projection'):
            restore_package_projection(connection, '', {})
        assert connection.scalar(text('SELECT limitation FROM package')) == '>=2'
        restore_package_projection(connection, '', {'package_projection': historical})
        assert connection.scalar(text('SELECT limitation FROM package')) == '<2'
    engine.dispose()


def test_package_projection_redacts_userinfo_and_query_credentials():
    from sqlalchemy import create_engine, text
    from docassemble.base.managed_checkpoint import package_projection
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE package (id integer PRIMARY KEY, active boolean, '
                                'giturl text, limitation text, gitbranch text)'))
        connection.execute(text("INSERT INTO package VALUES (1, true, "
            "'https://user:SECRET@example.org/repo.git', "
            "'@ https://user:SECRET@example.org/pkg-2.0.whl?token=SECRET&x=1', 'main')"))
        connection.execute(text("INSERT INTO package VALUES (2, false, "
            "'https://example.org/plain.git', '<2', NULL)"))
        rows = {row['id']: row for row in package_projection(connection, '')}
        assert rows[1]['giturl'] == 'https://example.org/repo.git'
        assert rows[1]['limitation'] == '@ https://example.org/pkg-2.0.whl?REDACTED'
        assert rows[1]['gitbranch'] == 'main'
        assert rows[2]['giturl'] == 'https://example.org/plain.git'
        assert rows[2]['limitation'] == '<2' and rows[2]['active'] is False
        assert 'SECRET' not in repr(rows)
    engine.dispose()


def test_package_projection_redaction_is_recursive_and_stable():
    from docassemble.base.managed_checkpoint import _redact
    value = {'url': ['https://user:SECRET@example.org/a.whl?token=SECRET'], 'n': 3}
    redacted = _redact(value)
    assert redacted == {'url': ['https://example.org/a.whl?REDACTED'], 'n': 3}
    assert _redact(redacted) == redacted
    assert _redact('name @ https://user:SECRET@example.org/a.whl?token=SECRET ; python_version > "3"') == \
        'name @ https://example.org/a.whl?REDACTED ; python_version > "3"'
    assert _redact('name @ https://example.org/a.whl?token=prefix;SECRET') == \
        'name @ https://example.org/a.whl?REDACTED'
    assert _redact('https://user:SECRET@example.org/a.whl?x=1;SECRET') == \
        'https://example.org/a.whl?REDACTED'
    assert _redact(_redact('https://example.org/a.whl?token=prefix;SECRET')) == \
        _redact('https://example.org/a.whl?token=prefix;SECRET')
    assert _redact('https://user;SECRET:pw@example.org/a.whl') == 'https://example.org/a.whl'
    assert _redact('https://user:SECRET@[2001:db8::1]:8443/repo.git?token=SECRET') == \
        'https://[2001:db8::1]:8443/repo.git?REDACTED'
    assert _redact(_redact('https://[2001:db8::1]/repo.git')) == _redact('https://[2001:db8::1]/repo.git')


@pytest.mark.parametrize('operator', ['sqlite:///postgres', 'postgresql://admin:SECRET@127.0.0.1:1/postgres',
                                     'postgresql://admin:SECRET@127.0.0.1:55432/wrong-db'])
def test_maintenance_credentials_cannot_select_another_cluster_or_database(operator):
    from sqlalchemy.engine import make_url
    from docassemble_executor.checkpoint_restore import maintenance_url
    with pytest.raises(ColdRestoreError, match='maintenance DB endpoint') as error:
        maintenance_url(make_url('postgresql://app@127.0.0.1:55432/application'), operator)
    assert 'SECRET' not in str(error.value)


def test_checkpoint_foreign_rehashed_lock_is_material_refusal_before_runtime_or_schema(tmp_path, monkeypatch):
    import pwd
    from types import SimpleNamespace
    from docassemble_executor import checkpoint_restore as restore, uvsync
    from docassemble_executor.bundles import sha256_file
    from docassemble.base.managed_python import python_target, python_requires
    paths = ExecutorPaths.from_env()
    directory = tmp_path / 'checkpoint'
    baseline = {'target': python_target(), 'tool': {'uv': '0.12.21'}, 'image_baseline': {}, 'core': {}}
    image = directory / 'database.managed/objects/image-baseline'
    image.mkdir(parents=True)
    (image / 'baseline.json').write_text(json.dumps({'baseline': baseline}))
    retained = directory / 'database.managed/objects/retained' / ('a' * 64)
    retained.mkdir(parents=True)
    (retained / 'pyproject.toml').write_text('[project]\nname="checkpoint-target"\nversion="0"\n'
        'requires-python=' + json.dumps(python_requires()) + '\ndependencies=[]\n[tool.uv]\npackage=false\n')
    (retained / 'uv.lock').write_text('version=1\nrevision=3\nrequires-python="==3.12.*"\n'
        '[[package]]\nname="checkpoint-target"\nversion="0"\nsource={virtual="."}\n')
    (retained / 'inventory.json').write_text('{}')
    bundle = {'digest': 'a' * 64, 'project_digest': sha256_file(retained / 'pyproject.toml'),
              'lock_digest': sha256_file(retained / 'uv.lock'), 'inventory': {}}
    revision = dict(baseline, publication={'result': {'bundle': bundle}})
    monkeypatch.setenv('DA_ENVIRONMENT_USER', 'fixture')
    monkeypatch.setattr(pwd, 'getpwnam', lambda _: SimpleNamespace(pw_dir=str(tmp_path), pw_uid=os.getuid(), pw_gid=os.getgid()))
    monkeypatch.setattr(restore.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=b'uv 0.12.21'))
    monkeypatch.setattr('docassemble_executor.inventory.verify_environment', lambda *a: pytest.fail('foreign sync cannot reach inventory/schema'))
    from unittest.mock import Mock
    inner_sync = Mock(side_effect=AssertionError('checkpoint target refusal must precede executable consumption'))
    monkeypatch.setattr(uvsync, '_sync_frozen', inner_sync)
    observed = []
    real_sync = uvsync.sync_frozen
    def observe_refusal(*args, **kwargs):
        result = real_sync(*args, **kwargs)
        observed.append(result)
        return result
    monkeypatch.setattr(uvsync, 'sync_frozen', observe_refusal)
    with pytest.raises(restore.RestoreMaterialError) as error:
        restore.preflight_environment(paths, directory, revision, None, 'schema')
    assert str(error.value) == ('checkpoint required frozen source/artifact unavailable or expired; no data replaced; '
                                'repair retained material/access, never solve a substitute')
    inner_sync.assert_not_called()
    assert len(observed) == 1 and observed[0].ok is False
    assert observed[0].output_tail == 'retained project and lock Python target mismatch'
    # Checkpoint creates outer scratch before this call; do not claim otherwise.
    assert not list(tmp_path.glob('checkpoint-preflight-*'))
