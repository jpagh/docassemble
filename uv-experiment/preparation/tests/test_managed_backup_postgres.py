"""Actual exported dump snapshot, retained references and concurrent publication."""
# ruff: noqa: F811
import copy
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import select, text, update

from test_preparation_postgres import protocol, result  # noqa: F401
from test_publication_writers_postgres import publisher, adapter  # noqa: F401
from docassemble.base.managed_preparation import digest
from docassemble_executor.bundles import hash_project
from docassemble_executor.managed_backup import (
    ManagedBackupError, ObjectCopy, create_backup, dumped_records, verify_backup)
from docassemble_executor.paths import ExecutorPaths


@pytest.fixture
def objects(tmp_path, monkeypatch):
    monkeypatch.setenv('DA_ROOT', str(tmp_path / 'root'))
    monkeypatch.setenv('DA_MANAGED_STATE_DIR', str(tmp_path / 'state'))
    paths = ExecutorPaths.from_env()
    paths.state_dir.mkdir()
    (paths.state_dir / 'lifecycle.json').write_text('{"phase":"verified"}')
    manifest = {'identity': {'core_baseline': 'r1'}, 'bundles': {}}
    for name in ('runtime-r1', 'core-r1'):
        directory = paths.bundles_dir / name
        directory.mkdir(parents=True)
        (directory / 'pyproject.toml').write_text('[project]\nname="fixture"\nversion="1"\n')
        (directory / 'uv.lock').write_text('version = 1\n')
        manifest['bundles'][name] = hash_project(directory)
    paths.manifest_file.write_text(json.dumps(manifest))
    baseline = tmp_path / 'baseline.json'
    baseline.write_text(json.dumps({'baseline': {'image_baseline': {'bundle': 'runtime-r1'}, 'core': {}}, 'wheels': {}}))
    store = tmp_path / 'retained'
    store.mkdir()
    staging = store / 'staging'
    staging.mkdir()
    (staging / 'pyproject.toml').write_text('[project]\nname="retained"\nversion="1"\n')
    (staging / 'uv.lock').write_text('version = 1\n')
    contents = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in staging.iterdir()}
    reference = digest(contents)
    (staging / 'manifest.json').write_text(json.dumps(contents))
    staging.rename(store / reference)
    artifact_root = tmp_path / 'artifacts'
    artifact_root.mkdir(mode=0o700)
    artifact = hashlib.sha256(b'retained ZIP').hexdigest()
    (artifact_root / artifact).write_bytes(b'retained ZIP')
    (artifact_root / ('e' * 64)).write_bytes(b'candidate-only')
    secret_root = tmp_path / 'secrets'
    secret_root.mkdir(mode=0o700)
    (secret_root / 'credential').write_text('https://fixture:PAT@example.test/repo')
    index_root = tmp_path / 'indexes'
    index_root.mkdir(mode=0o700)
    (index_root / 'private').write_text('{"password":"fixture-index"}')
    kwargs = dict(paths=paths, prefix='test_', store=store, baseline=baseline,
                  artifact_roots=[artifact_root], secret_root=secret_root, index_root=index_root)
    return kwargs, reference, artifact


def install_snapshot(service, objects):
    kwargs, bundle, artifact = objects
    with service.engine.begin() as connection:
        env = service._environment(connection)
        snapshot = dict(env['snapshot'], publication={'result': {'bundle': {'digest': bundle}}},
            contributions=[{'artifact_ref': artifact}],
            direct_sources={'direct': {'digest': artifact}},
            git_credentials={'git': 'credential'},
            config={'private_indexes': {'private': 'https://example.test/'}})
        connection.execute(service.tables['candidate_material'].insert().values(id='credential',
            path=str(kwargs['secret_root'] / 'credential'), usable=True, committed=True, shared=False, deleted=False))
        connection.execute(service.tables['revision'].insert().values(id='retained', parent_id='baseline', snapshot=snapshot))
        connection.execute(update(service.tables['environment_state']).values(snapshot=snapshot, desired_revision_id='retained'))


def test_dump_roundtrip_retains_history_auth_and_excludes_disposable(protocol, objects, tmp_path):
    install_snapshot(protocol, objects)
    kwargs, reference, artifact = objects
    dump = tmp_path / 'database'
    document = create_backup(protocol.engine, dump, **kwargs)
    directory = Path(str(dump) + '.managed')
    assert verify_backup(dump, directory, 'test_') == document
    assert document['managed_objects_complete'] and not document['interview_checkpoint_complete']
    assert {row['id'] for row in dumped_records(dump, 'test_')['revision']} == {'baseline', 'retained'}
    assert document['references']['bundles'] == [reference]
    assert document['references']['artifacts'] == [artifact]
    assert not any('e' * 64 in row['source'] for row in document['files'].values())
    assert any('PAT' in p.read_text() for p in (directory / 'objects/material').iterdir())
    # A later live publication cannot change what this archive certifies.
    with protocol.engine.begin() as connection:
        connection.execute(protocol.tables['revision'].insert().values(id='later', snapshot={'selected_baseline': {'core_bundle': {'digest': 'f' * 64}}}))
    assert verify_backup(dump, directory, 'test_') == document
    (directory / 'objects/retained' / reference / 'uv.lock').unlink()
    with pytest.raises(ManagedBackupError, match='incomplete'):
        verify_backup(dump, directory, 'test_')


@pytest.mark.parametrize('failure', ['missing-history', 'missing-auth', 'missing-artifact',
                                     'missing-index', 'missing-journal', 'missing-recovery', 'copy-interrupted'])
def test_missing_mandatory_objects_and_interrupted_copy_never_complete(protocol, objects, tmp_path, monkeypatch, failure):
    install_snapshot(protocol, objects)
    kwargs, reference, artifact = objects
    if failure == 'missing-history':
        # The desired baseline is intact: missing unrelated retained history is
        # still an incomplete backup, never permission to silently drop history.
        with protocol.engine.begin() as connection:
            baseline = connection.scalar(select(protocol.tables['revision'].c.snapshot).where(
                protocol.tables['revision'].c.id == 'baseline'))
            connection.execute(update(protocol.tables['environment_state']).values(
                desired_revision_id='baseline', snapshot=baseline))
        (kwargs['store'] / reference / 'uv.lock').unlink()
    elif failure == 'missing-auth':
        (kwargs['secret_root'] / 'credential').unlink()
    elif failure == 'missing-artifact':
        (kwargs['artifact_roots'][0] / artifact).unlink()
    elif failure == 'missing-index':
        (kwargs['index_root'] / 'private').unlink()
    elif failure == 'missing-journal':
        (kwargs['paths'].state_dir / 'lifecycle.json').unlink()
    elif failure == 'missing-recovery':
        (kwargs['paths'].bundles_dir / 'core-r1/uv.lock').unlink()
    else:
        original = ObjectCopy.file
        count = 0
        def interrupted(self, *args):
            nonlocal count
            original(self, *args)
            count += 1
            if count == 2:
                raise InterruptedError('fixture copy interruption')
        monkeypatch.setattr(ObjectCopy, 'file', interrupted)
    dump = tmp_path / 'incomplete'
    with pytest.raises((ManagedBackupError, OSError, RuntimeError)):
        create_backup(protocol.engine, dump, **kwargs)
    assert not Path(str(dump) + '.managed/manifest.json').exists()


def ready_add(service, bundle):
    candidate = service.prepare('1', 'Example>=2', 'concurrent')
    claim = service.claim(candidate)
    prepared = result(claim)
    prepared['bundle']['digest'] = bundle
    for field, before in [('desired_diff', claim.inputs['baseline']['desired_inventory']),
                          ('runtime_diff', claim.inputs['baseline']['runtime']['inventory'])]:
        inventory = prepared['bundle']['inventory']
        prepared[field] = {name: {'before': before.get(name), 'after': inventory.get(name)}
                          for name in sorted(set(before) | set(inventory)) if before.get(name) != inventory.get(name)}
    assert service.publish_ready(claim, prepared)
    return candidate, service.status('1', candidate)['result_digest']


def test_real_publication_waits_for_copy_and_dump_and_next_backup_includes_it(publisher, objects, tmp_path, monkeypatch):
    kwargs, bundle, _ = objects
    candidate, result_digest = ready_add(publisher, bundle)
    copying, release = threading.Event(), threading.Event()
    original = ObjectCopy.file
    def paused(self, *args):
        copying.set()
        assert release.wait(20)
        return original(self, *args)
    monkeypatch.setattr(ObjectCopy, 'file', paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        backup = pool.submit(create_backup, publisher.engine, tmp_path / 'before', **kwargs)
        assert copying.wait(20)
        publication = pool.submit(publisher.approve, '1', candidate, result_digest, 'concurrent-approve')
        try:
            deadline = time.monotonic() + 10
            waiting = False
            while time.monotonic() < deadline:
                with publisher.engine.connect() as connection:
                    waiting = connection.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE wait_event_type='Lock' AND query LIKE '%test_managed_environment_state%' "
                        "AND pid <> pg_backend_pid())"))
                if waiting:
                    break
                time.sleep(0.02)
            assert waiting, 'publication did not reach the retention/publication lock'
            assert not publication.done()
        finally:
            release.set()
        before = backup.result(timeout=30)
        published = publication.result(timeout=30)
    assert before['desired_revision_id'] == 'baseline'
    assert bundle not in before['references']['bundles']
    # This is the old sequential-copy/DB-dump gap: publication has now finished,
    # but a managed snapshot taken afterward MUST enumerate its new object.
    after = create_backup(publisher.engine, tmp_path / 'after', **kwargs)
    assert after['desired_revision_id'] == published['revision_id']
    assert bundle in after['references']['bundles']
    assert verify_backup(tmp_path / 'before', tmp_path / 'before.managed', 'test_') == before


def test_dump_snapshot_mismatch_cannot_be_certified(protocol, objects, tmp_path, monkeypatch):
    from docassemble_executor import managed_backup
    original = managed_backup.dumped_records
    def wrong(*args):
        records = copy.deepcopy(original(*args))
        records['revision'][0]['snapshot']['unexpected'] = True
        return records
    monkeypatch.setattr(managed_backup, 'dumped_records', wrong)
    with pytest.raises(ManagedBackupError, match='snapshot'):
        create_backup(protocol.engine, tmp_path / 'wrong', **objects[0])
    assert not (tmp_path / 'wrong.managed/manifest.json').exists()


@pytest.mark.parametrize('phase', ['creation', 'verification'])
def test_incomplete_baseline_wheel_map_is_never_certified(protocol, objects, tmp_path, phase):
    kwargs = objects[0]
    baseline = json.loads(kwargs['baseline'].read_text())
    baseline['baseline']['core']['missing-wheel'] = {'version': '1', 'source_digest': 'a' * 64}
    dump = tmp_path / 'wheel-map'
    if phase == 'creation':
        kwargs['baseline'].write_text(json.dumps(baseline))
        with pytest.raises(ManagedBackupError, match='cover core exactly'):
            create_backup(protocol.engine, dump, **kwargs)
        assert not (tmp_path / 'wheel-map.managed/manifest.json').exists()
    else:
        document = create_backup(protocol.engine, dump, **kwargs)
        directory = tmp_path / 'wheel-map.managed'
        descriptor = directory / 'objects/image-baseline/baseline.json'
        descriptor.write_text(json.dumps(baseline))
        # Rebind the outer file checksum to isolate descriptor semantic
        # completeness, rather than merely testing generic byte corruption.
        document['files']['image-baseline/baseline.json']['sha256'] = hashlib.sha256(descriptor.read_bytes()).hexdigest()
        (directory / 'manifest.json').write_text(json.dumps(document))
        with pytest.raises(ManagedBackupError, match='cover core exactly'):
            verify_backup(dump, directory, 'test_')
