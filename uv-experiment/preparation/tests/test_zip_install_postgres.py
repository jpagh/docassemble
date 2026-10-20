"""Durable uploaded ZIP identity, publication, reuse and cleanup (real PostgreSQL)."""
# ruff: noqa: F811 -- imported PostgreSQL fixtures are injected by name
from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
import zipfile

import pytest
from sqlalchemy import select, text, update

from docassemble.base.managed_artifacts import verify_artifact
from docassemble.base.managed_preparation import PreparationError
from test_preparation_postgres import protocol, result, snapshot  # noqa: F401
from test_publication_writers_postgres import publisher, intent  # noqa: F401
from test_registry_changes_postgres import finish

spec = importlib.util.spec_from_file_location('zip_fixture', Path(__file__).parents[1] / 'zip_fixture.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
distribution = fixture.distribution


@pytest.fixture
def store(tmp_path):
    root = tmp_path / 'artifacts'
    root.mkdir(mode=0o700)
    return root


def prepared(publisher, store, upload, key='prepare', **kwargs):
    candidate = publisher.prepare_zip('1', upload, key, artifact_root=store, **kwargs)
    claim = publisher.claim(candidate)
    return candidate, claim


def ready_zip(publisher, store, upload, key='prepare'):
    candidate, claim = prepared(publisher, store, upload, key)
    attestation = result(claim)
    artifact = claim.inputs['artifact']
    inventory = deepcopy(claim.inputs['baseline']['desired_inventory'])
    inventory[artifact['normalized_name']] = {'version': artifact['version'], 'source_digest': artifact['digest']}
    attestation['bundle']['inventory'] = inventory
    attestation['bundle']['artifacts'].append(artifact['digest'])
    def diff(before):
        return {name: {'before': before.get(name), 'after': inventory.get(name)}
                for name in sorted(set(before) | set(inventory)) if before.get(name) != inventory.get(name)}
    attestation['desired_diff'] = diff(claim.inputs['baseline']['desired_inventory'])
    runtime = claim.inputs['baseline']['runtime']
    attestation['runtime_diff'] = diff(runtime['inventory']) if runtime['kind'] == 'normal' else None
    attestation['warnings'] = publisher.ready_warnings(claim.inputs)
    assert publisher.publish_ready(claim, attestation)
    return candidate, publisher.status('1', candidate)['result_digest'], claim


@pytest.mark.parametrize('wheel', [True, False])
def test_ingestion_metadata_hash_restricted_atomic_store_and_no_early_intent(publisher, store, tmp_path, monkeypatch, wheel):
    upload = distribution(tmp_path / 'misleading-unrelated-name.zip', wheel=wheel)
    before = snapshot(publisher), intent(publisher)
    import docassemble.base.managed_artifacts as module
    rename, fsync = module.os.replace, module.os.fsync
    observed, synced = [], []
    def synchronize(fd):
        synced.append('file' if module.stat.S_ISREG(module.os.fstat(fd).st_mode) else 'directory')
        fsync(fd)
    def atomic(source, destination):
        assert synced == ['file']
        assert not destination.exists()
        assert hashlib.sha256(Path(source).read_bytes()).hexdigest() == destination.name
        observed.append(destination)
        rename(source, destination)
    monkeypatch.setattr(module.os, 'replace', atomic)
    monkeypatch.setattr(module.os, 'fsync', synchronize)
    candidate, claim = prepared(publisher, store, upload)
    artifact = claim.inputs['artifact']
    assert artifact['normalized_name'] == 'zip-example' and artifact['version'] == '1.0'
    assert artifact['digest'] == hashlib.sha256(upload.read_bytes()).hexdigest()
    retained = verify_artifact(store, artifact['digest'])
    assert retained.stat().st_mode & 0o777 == 0o600
    assert list(store.iterdir()) == observed == [retained]
    assert synced == ['file', 'directory']
    assert (snapshot(publisher), intent(publisher)) == before
    with publisher.engine.connect() as c:
        material = c.execute(select(publisher.tables['candidate_material'])).mappings().one()
    assert material['candidate_id'] == candidate and material['path'] == str(retained)
    assert not material['committed'] and not material['shared']


@pytest.mark.parametrize('failure', ['name', 'missing', 'version', 'ambiguous', 'wheel-tag'])
def test_rejected_archive_leaves_no_candidate_material_or_intent(publisher, store, tmp_path, failure):
    upload = distribution(tmp_path / 'upload.zip')
    declaration = 'another-name' if failure == 'name' else 'zip-example>=2' if failure == 'version' else None
    if failure in ('missing', 'ambiguous'):
        with zipfile.ZipFile(upload, 'w') as archive:
            archive.writestr('arbitrary/__init__.py', '')
            if failure == 'ambiguous':
                archive.writestr('one/PKG-INFO', 'Name: one\nVersion: 1\n')
                archive.writestr('two/PKG-INFO', 'Name: two\nVersion: 1\n')
    if failure == 'wheel-tag':
        with zipfile.ZipFile(upload) as archive:
            contents = {name: archive.read(name) for name in archive.namelist()}
        with zipfile.ZipFile(upload, 'w') as archive:
            for name, data in contents.items():
                archive.writestr(name, b'Wheel-Version: 1.0\nTag: py3-none-../../outside\n' if name.endswith('/WHEEL') else data)
    before = snapshot(publisher), intent(publisher)
    with pytest.raises(PreparationError):
        publisher.prepare_zip('1', upload, 'prepare', artifact_root=store, declaration=declaration)
    assert (snapshot(publisher), intent(publisher)) == before
    with publisher.engine.connect() as c:
        assert not c.scalar(select(publisher.tables['candidate'].c.id))
        assert not c.scalar(select(publisher.tables['candidate_material'].c.id))
    assert not list(store.iterdir())


def test_actual_name_authorization_is_rechecked_at_prepare_and_approval(publisher, store, tmp_path):
    upload = distribution(tmp_path / 'harmless.zip', name='docassemble_base')
    with pytest.raises(PreparationError, match='forbidden'):
        publisher.prepare_zip('1', upload, 'core-name', artifact_root=store)
    assert not list(store.iterdir())
    upload = distribution(upload)
    candidate, result_digest, claim = ready_zip(publisher, store, upload)
    with publisher.authority_write() as c:
        c.execute(text('DELETE FROM test_user_roles WHERE user_id=1'))
    before = snapshot(publisher), intent(publisher)
    with pytest.raises(PreparationError, match='forbidden'):
        publisher.approve('1', candidate, result_digest, 'approve')
    assert (snapshot(publisher), intent(publisher)) == before
    with publisher.engine.connect() as c:
        assert not c.scalar(select(publisher.tables['candidate_material'].c.committed))
    publisher.worker_exited(claim.worker_id)


def test_reupload_new_identity_old_revision_and_reuse_without_legacy_upload_or_cache(publisher, store, tmp_path):
    upload = distribution(tmp_path / 'legacy-upload.zip')
    candidate, result_digest, first = ready_zip(publisher, store, upload)
    published = publisher.approve('1', candidate, result_digest, 'approve')
    old = first.inputs['artifact']['digest']
    upload.unlink()
    cache = tmp_path / 'cache'
    cache.mkdir()
    (cache / 'disposable').write_bytes(b'not durable')
    import shutil
    shutil.rmtree(cache)
    assert verify_artifact(store, old).read_bytes()
    finish(publisher)
    distribution(upload, version='2.0')
    candidate, result_digest, second = ready_zip(publisher, store, upload, 'reupload')
    new = second.inputs['artifact']['digest']
    assert old != new
    publisher.approve('1', candidate, result_digest, 'approve-reupload')
    state = snapshot(publisher)
    old_revision = next(row for row in state['revision'] if row['id'] == published['revision_id'])
    assert old_revision['snapshot']['publication']['inputs']['artifact']['digest'] == old
    assert any(row['artifact_ref'] == old for row in old_revision['snapshot']['contributions'])
    active = [row for row in state['requirement_contribution'] if row['active'] and row['artifact_ref']]
    assert len(active) == 1 and active[0]['artifact_ref'] == new
    with publisher.engine.connect() as c:
        assert all(row['committed'] for row in c.execute(select(publisher.tables['candidate_material'])).mappings())
    assert publisher.reconcile_artifacts(store) == 0
    assert verify_artifact(store, old) and verify_artifact(store, new)
    finish(publisher)
    # An unrelated solve reuses the committed artifact after the upload is gone.
    upload.unlink()
    candidate = publisher.prepare('1', 'Another>=1', 'unrelated')
    claim = publisher.claim(candidate)
    from docassemble_executor.registry_builder import retained_artifacts
    assert retained_artifacts(claim.inputs, store)['zip-example'][0]['digest'] == new


@pytest.mark.parametrize('terminal', ['cancel', 'fail', 'expire'])
def test_cleanup_waits_for_worker_exit_preserves_committed_shared_and_cache(publisher, store, tmp_path, terminal):
    upload = distribution(tmp_path / 'upload.zip')
    candidate, result_digest, claim = ready_zip(publisher, store, upload)
    reference = claim.inputs['artifact']['digest']
    if terminal == 'expire':
        with publisher.engine.begin() as c:
            c.execute(text('ALTER TABLE test_managed_candidate DISABLE TRIGGER managed_ready_immutable'))
            c.execute(update(publisher.tables['candidate']).where(publisher.tables['candidate'].c.id == candidate).values(
                expires_at=text("clock_timestamp() - interval '1 second'")))
            c.execute(text('ALTER TABLE test_managed_candidate ENABLE TRIGGER managed_ready_immutable'))
    elif terminal == 'fail':
        with publisher.engine.begin() as c:
            c.execute(update(publisher.tables['candidate']).where(publisher.tables['candidate'].c.id == candidate).values(state='failed'))
            publisher._invalidate(c, candidate)
    else:
        publisher.cancel('1', candidate)
    assert publisher.reconcile_artifacts(store) == 0
    assert verify_artifact(store, reference)
    publisher.worker_exited(claim.worker_id)
    cache = store / 'cache'
    cache.mkdir()
    (cache / 'wheel').write_bytes(b'reusable')
    assert publisher.reconcile_artifacts(store) == 1
    assert not (store / reference).exists() and (cache / 'wheel').exists()
    # Shared rows are never unlinked by either candidate's cancellation.
    first, claim1 = prepared(publisher, store, upload, 'shared-one')
    second, claim2 = prepared(publisher, store, upload, 'shared-two')
    for candidate, worker in ((first, claim1), (second, claim2)):
        publisher.cancel('1', candidate)
        publisher.worker_exited(worker.worker_id)
    publisher.reconcile_artifacts(store)
    assert verify_artifact(store, reference)


@pytest.mark.parametrize('failure', ['unset', 'mode', 'symlink-root', 'symlink-upload', 'directory-upload', 'corrupt-object'])
def test_store_and_source_fail_closed(publisher, store, tmp_path, monkeypatch, failure):
    upload = distribution(tmp_path / 'upload.zip')
    configured = store
    if failure == 'unset':
        monkeypatch.delenv('DA_MANAGED_ARTIFACT_ROOT', raising=False)
        configured = None
    elif failure == 'mode':
        store.chmod(0o755)
    elif failure == 'symlink-root':
        configured = tmp_path / 'linked-store'
        configured.symlink_to(store, target_is_directory=True)
    elif failure == 'symlink-upload':
        linked = tmp_path / 'linked-upload.zip'
        linked.symlink_to(upload)
        upload = linked
    elif failure == 'directory-upload':
        upload = tmp_path
    else:
        (store / hashlib.sha256(upload.read_bytes()).hexdigest()).write_bytes(b'corrupt')
    with pytest.raises(PreparationError):
        publisher.prepare_zip('1', upload, 'prepare', artifact_root=configured)
    with publisher.engine.connect() as c:
        assert not c.scalar(select(publisher.tables['candidate'].c.id))


def test_orphan_scan_removes_only_unreferenced_atomic_staging(publisher, store, tmp_path):
    upload = distribution(tmp_path / 'upload.zip')
    candidate, claim = prepared(publisher, store, upload)
    (store / '.ingest-crash').write_bytes(b'partial')
    (store / ('a' * 64)).write_bytes(b'orphaned complete object')
    assert publisher.reconcile_artifacts(store) == 2
    assert verify_artifact(store, claim.inputs['artifact']['digest'])
    publisher.cancel('1', candidate)
    publisher.worker_exited(claim.worker_id)
    assert publisher.reconcile_artifacts(store) == 1


def test_zip_idempotency_binds_bytes_and_declaration(publisher, store, tmp_path):
    upload = distribution(tmp_path / 'upload.zip')
    first = publisher.prepare_zip('1', upload, 'same', artifact_root=store)
    assert publisher.prepare_zip('1', upload, 'same', artifact_root=store) == first
    distribution(upload, version='2.0')
    with pytest.raises(PreparationError, match='body mismatch'):
        publisher.prepare_zip('1', upload, 'same', artifact_root=store)
    assert len(list(store.iterdir())) == 1


def test_atomic_retention_failure_and_orphan_reconciliation_leave_no_intent(publisher, store, tmp_path, monkeypatch):
    upload = distribution(tmp_path / 'upload.zip')
    before = snapshot(publisher), intent(publisher)
    def rejected_registration(*args):
        raise PreparationError('injected registration failure')
    monkeypatch.setattr(publisher, '_register_material', rejected_registration)
    with pytest.raises(PreparationError, match='registration failure'):
        publisher.prepare_zip('1', upload, 'failed', artifact_root=store)
    assert (snapshot(publisher), intent(publisher)) == before
    assert len(list(store.iterdir())) == 1  # Complete but unreferenced after a DB rollback.
    assert not list(store.glob('.ingest-*'))
    assert publisher.reconcile_artifacts(store) == 1
    assert list(store.iterdir()) == []


def test_ingestion_rename_failure_never_exposes_partial_object(publisher, store, tmp_path, monkeypatch):
    import docassemble.base.managed_artifacts as module
    upload = distribution(tmp_path / 'upload.zip')
    def interrupted(*args):
        raise OSError('injected atomic rename failure')
    monkeypatch.setattr(module.os, 'replace', interrupted)
    with pytest.raises(PreparationError, match='retention failed'):
        publisher.prepare_zip('1', upload, 'failed', artifact_root=store)
    assert list(store.iterdir()) == []
    with publisher.engine.connect() as c:
        assert not c.scalar(select(publisher.tables['candidate'].c.id))
        assert not c.scalar(select(publisher.tables['candidate_material'].c.id))


def test_artifact_cleanup_does_not_touch_another_material_root(publisher, store, tmp_path):
    upload = distribution(tmp_path / 'upload.zip')
    candidate, claim = prepared(publisher, store, upload)
    secrets = tmp_path / 'secrets'
    secrets.mkdir(mode=0o700)
    secret = publisher.stage_candidate_secret(claim, secrets, b'candidate-only source material')
    publisher.cancel('1', candidate)
    publisher.worker_exited(claim.worker_id)
    assert publisher.reconcile_artifacts(store) == 1
    assert (secrets / secret).exists()
    assert publisher.delete_candidate_material(candidate, secrets) == 1
    assert not (secrets / secret).exists()
    from docassemble_executor.preparation_poller import PreparationPoller
    candidate, claim = prepared(publisher, store, upload, 'mixed-reconciliation')
    secret = publisher.stage_candidate_secret(claim, secrets, b'candidate-only material')
    publisher.cancel('1', candidate)
    publisher.worker_exited(claim.worker_id)
    PreparationPoller(publisher, object(), material_root=secrets, artifact_root=store)._reconcile()
    assert not (secrets / secret).exists() and not list(store.iterdir())


def test_same_committed_artifact_survives_reupload_cancellation(publisher, store, tmp_path):
    upload = distribution(tmp_path / 'upload.zip')
    candidate, result_digest, claim = ready_zip(publisher, store, upload)
    publisher.approve('1', candidate, result_digest, 'approve')
    finish(publisher)
    second, worker = prepared(publisher, store, upload, 'reuse')
    publisher.cancel('1', second)
    publisher.worker_exited(worker.worker_id)
    publisher.worker_exited(claim.worker_id)
    assert publisher.reconcile_artifacts(store) == 0
    assert verify_artifact(store, claim.inputs['artifact']['digest'])
    with publisher.engine.connect() as c:
        rows = list(c.execute(select(publisher.tables['candidate_material'])).mappings())
    assert len(rows) == 2 and any(row['committed'] for row in rows) and any(row['shared'] for row in rows)


def test_source_authorization_receives_authoritative_identity_and_rechecks_at_approval(publisher, store, tmp_path):
    upload = distribution(tmp_path / 'wrong-package-name.zip')
    original = publisher.authorize
    deny = False
    seen = []
    def authorize(connection, actor, operation, candidate):
        if operation in ('prepare', 'approve'):
            artifact = candidate.get('artifact') if operation == 'prepare' else candidate['inputs'].get('artifact')
            seen.append((operation, artifact['normalized_name'], artifact['digest']))
            if deny:
                return False
        return original(connection, actor, operation, candidate)
    publisher.authorize = authorize
    candidate, result_digest, claim = ready_zip(publisher, store, upload)
    deny = True
    before = snapshot(publisher), intent(publisher)
    with pytest.raises(PreparationError, match='forbidden'):
        publisher.approve('1', candidate, result_digest, 'approve')
    assert (snapshot(publisher), intent(publisher)) == before
    assert seen == [('prepare', 'zip-example', claim.inputs['artifact']['digest']),
                    ('approve', 'zip-example', claim.inputs['artifact']['digest'])]


def test_zip_ready_result_binds_metadata_version_source_and_retained_hash(publisher, store, tmp_path):
    upload = distribution(tmp_path / 'upload.zip')
    candidate, result_digest, claim = ready_zip(publisher, store, upload)
    with publisher.engine.connect() as c:
        attestation = c.scalar(select(publisher.tables['candidate'].c.result).where(publisher.tables['candidate'].c.id == candidate))
    for invalid in ('version', 'source_digest', 'absent', 'artifacts'):
        broken = deepcopy(attestation)
        if invalid == 'absent':
            del broken['bundle']['inventory']['zip-example']
        elif invalid == 'artifacts':
            broken['bundle']['artifacts'] = []
        else:
            broken['bundle']['inventory']['zip-example'][invalid] = '9' if invalid == 'version' else '9' * 64
        with pytest.raises(PreparationError, match='invalid ready result'):
            publisher._validate_result(claim.inputs, broken)


def test_artifact_store_rejects_another_writer_uid(publisher, store, tmp_path, monkeypatch):
    import os
    upload = distribution(tmp_path / 'upload.zip')
    monkeypatch.setattr(os, 'getuid', lambda: store.stat().st_uid + 1)
    with pytest.raises(PreparationError, match='operator-owned'):
        publisher.prepare_zip('1', upload, 'wrong-writer', artifact_root=store)
    assert not list(store.iterdir())


def test_artifact_store_rejects_ancestor_symlink(publisher, store, tmp_path):
    upload = distribution(tmp_path / 'upload.zip')
    real = tmp_path / 'real-store'
    (real / 'artifacts').mkdir(parents=True, mode=0o700)
    link = tmp_path / 'linked-parent'
    link.symlink_to(real, target_is_directory=True)
    for configured in (link / 'artifacts', link / '..' / 'real-store' / 'artifacts'):
        with pytest.raises(PreparationError, match='operator-owned'):
            publisher.prepare_zip('1', upload, 'ancestor-symlink', artifact_root=configured)
    with publisher.engine.connect() as c:
        assert not c.scalar(select(publisher.tables['candidate'].c.id))


def test_root_reader_requires_configured_writer_uid(publisher, store, tmp_path, monkeypatch):
    import docassemble.base.managed_artifacts as module
    upload = distribution(tmp_path / 'upload.zip')
    candidate, claim = prepared(publisher, store, upload)
    reference = claim.inputs['artifact']['digest']
    monkeypatch.setattr(module, '_euid', lambda: 0)
    monkeypatch.delenv('DA_MANAGED_ARTIFACT_UID', raising=False)
    with pytest.raises(PreparationError, match='owner must be configured'):
        verify_artifact(store, reference)
    monkeypatch.setenv('DA_MANAGED_ARTIFACT_UID', str(store.stat().st_uid))
    assert verify_artifact(store, reference) == store / reference
    monkeypatch.setenv('DA_MANAGED_ARTIFACT_UID', str(store.stat().st_uid + 1))
    with pytest.raises(PreparationError, match='operator-owned'):
        verify_artifact(store, reference)


def test_explicit_owner_zip_source_keeps_registry_and_config_constraints_and_rechecks_grants(publisher, store, tmp_path):
    from test_registry_changes_postgres import seed
    seed(publisher)
    upload = distribution(tmp_path / 'upload.zip', name='example_pkg', version='2.0')
    before = snapshot(publisher), intent(publisher)
    with pytest.raises(PreparationError):
        publisher.prepare_zip('2', upload, 'nonowner-source-replacement', artifact_root=store)
    assert (snapshot(publisher), intent(publisher)) == before
    candidate, result_digest, claim = ready_zip(publisher, store, upload)
    declarations = {row['id']: row['declaration'] for row in claim.inputs['contributions']}
    assert declarations['ui-one'] == 'Example_Pkg>=1'
    assert declarations['ui-two'] == 'example.pkg<4'
    assert declarations['config-one'] == 'EXAMPLE-pkg<5'
    assert claim.inputs['replace_artifact_contributions'] == []
    assert (snapshot(publisher), intent(publisher)) == before
    with publisher.authority_write() as c:
        c.execute(text('DELETE FROM test_package_auth WHERE user_id=1'))
    before = snapshot(publisher), intent(publisher)
    with pytest.raises(PreparationError):
        publisher.approve('1', candidate, result_digest, 'revoked-owner')
    assert (snapshot(publisher), intent(publisher)) == before
