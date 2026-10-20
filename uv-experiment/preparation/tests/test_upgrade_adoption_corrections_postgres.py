"""Rollback, private Git idempotency and cloud adoption regressions."""
# ruff: noqa: F811 -- PostgreSQL fixtures injected by name
import copy
import json

import pytest
from sqlalchemy import select, update, text

from docassemble.base.managed_preparation import PreparationError
from test_publication_writers_postgres import publisher  # noqa: F401
from test_preparation_postgres import protocol, snapshot  # noqa: F401
from test_historical_rollback_postgres import history, ready
from test_git_install_postgres import (ready_git, approve, secret_root, CREDENTIAL_DECLARATION,
                                      ROTATED_DECLARATION, REDACTED_DECLARATION)
from test_legacy_adoption_postgres import adoption, observe, prepare, state, ready as adoption_ready  # noqa: F401


def test_rollback_restores_changed_config_declaration_not_interactive_rows(publisher):
    revision = history(publisher)
    with publisher.authority_write() as connection:
        table = publisher.tables['requirement_contribution']
        connection.execute(update(table).where(table.c.id == 'config-contribution').values(declaration='Separate>=9'))
    before = snapshot(publisher)
    candidate, digest = ready(publisher, revision)
    response = publisher.approve('2', candidate, digest, 'restore-config', acknowledge_database_unchanged=True)
    after = snapshot(publisher)
    historical = next(row['snapshot'] for row in after['revision'] if row['id'] == revision)
    restored = next(row['snapshot'] for row in after['revision'] if row['id'] == response['revision_id'])
    assert restored['contributions'] == historical['contributions']
    assert after['package_identity'] == before['package_identity']
    assert next(row for row in before['requirement_contribution'] if row['id'] == 'config-contribution')['declaration'] == 'Separate>=9'
    assert next(row for row in after['requirement_contribution'] if row['id'] == 'config-contribution')['declaration'] == 'Separate[extra]<2; os_name == "posix"'


def test_rollback_refuses_changed_interactive_declaration_atomically(publisher):
    revision = history(publisher)
    with publisher.authority_write() as connection:
        table = publisher.tables['requirement_contribution']
        connection.execute(update(table).where(table.c.origin == 'interactive').values(declaration='Example>=99'))
    candidate, digest = ready(publisher, revision)
    before = snapshot(publisher)
    with pytest.raises(PreparationError, match='historical contribution changed'):
        publisher.approve('2', candidate, digest, 'refuse-interactive', acknowledge_database_unchanged=True)
    assert snapshot(publisher) == before


@pytest.mark.parametrize('original,replay', [
    (CREDENTIAL_DECLARATION, ROTATED_DECLARATION),
    (CREDENTIAL_DECLARATION, REDACTED_DECLARATION),
    (REDACTED_DECLARATION, CREDENTIAL_DECLARATION),
])
def test_git_same_key_changed_credential_refuses_without_staging(publisher, monkeypatch, tmp_path, original, replay):
    root = secret_root(monkeypatch, tmp_path)
    candidate = publisher.prepare('1', original, 'credential-equality')
    assert publisher.prepare('1', original, 'credential-equality') == candidate
    before_files = {p.name: p.read_bytes() for p in root.iterdir()}
    with publisher.engine.connect() as connection:
        before = [dict(row) for row in connection.execute(select(publisher.tables['candidate'])).mappings()]
    with pytest.raises(PreparationError, match='idempotency key body mismatch'):
        publisher.prepare('1', replay, 'credential-equality')
    with publisher.engine.connect() as connection:
        assert [dict(row) for row in connection.execute(select(publisher.tables['candidate'])).mappings()] == before
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before_files


def test_git_private_binding_never_enters_public_digest_preview_or_revision(publisher, monkeypatch, tmp_path):
    root = secret_root(monkeypatch, tmp_path)
    prepared = ready_git(publisher, CREDENTIAL_DECLARATION, monkeypatch=monkeypatch, secrets=root)
    with publisher.engine.connect() as connection:
        row = connection.execute(select(publisher.tables['candidate'])).mappings().one()
    binding = row['credential_request_binding']
    assert binding and len(binding['key']) == 64 and len(binding['fingerprint']) == 64
    response = approve(publisher, prepared)
    with publisher.engine.connect() as connection:
        revision = connection.scalar(select(publisher.tables['revision'].c.snapshot).where(
            publisher.tables['revision'].c.id == response['revision_id']))
    public = json.dumps([revision, publisher.status('1', prepared[0]),
                         publisher.publication_status('1', revision_id=response['revision_id'])], default=str)
    assert 'credential_request_binding' not in public
    for value in binding.values():
        assert value not in public and value not in row['request_digest'] and value not in row['result_digest']


def test_git_legacy_missing_binding_refuses_credential_replay(publisher, monkeypatch, tmp_path):
    secret_root(monkeypatch, tmp_path)
    candidate = publisher.prepare('1', CREDENTIAL_DECLARATION, 'legacy-equality')
    with publisher.engine.begin() as connection:
        connection.execute(update(publisher.tables['candidate']).where(
            publisher.tables['candidate'].c.id == candidate).values(credential_request_binding=None))
    with pytest.raises(PreparationError, match='idempotency key body mismatch'):
        publisher.prepare('1', CREDENTIAL_DECLARATION, 'legacy-equality')


@pytest.mark.parametrize('replacement', ['artifact', 'git', 'direct'])
def test_replacement_preview_excludes_every_replaced_contribution(publisher, replacement):
    candidate = publisher.prepare('1', 'Example', 'preview')
    with publisher.engine.begin() as connection:
        row = connection.execute(select(publisher.tables['candidate'])).mappings().one()
        inputs = copy.deepcopy(row['inputs'])
        inputs['replace_' + replacement + '_contributions'] = ['config-contribution']
        connection.execute(update(publisher.tables['candidate']).where(
            publisher.tables['candidate'].c.id == candidate).values(inputs=inputs))
    retained = publisher.status('1', candidate)['intent_change']['retained_contributions']
    assert all(row['id'] != 'config-contribution' for row in retained)


@pytest.mark.parametrize('provider', ['s3', 'azure', 's3-environment'])
def test_adoption_captures_effective_cloud_before_secret_sanitization(adoption, tmp_path, provider):
    with adoption.engine.begin() as connection:
        connection.execute(text("UPDATE test_package SET type='zip',upload=7; INSERT INTO test_uploads VALUES(7,'original.zip')"))
    configuration = {'uploads': str(tmp_path)}
    environ = {}
    if provider == 's3':
        configuration['s3'] = {'bucket': 'bucket', 'secret access key': 'CLOUD_SECRET_SENTINEL'}
    elif provider == 'azure':
        configuration['azure'] = {'account name': 'account', 'account key': 'CLOUD_SECRET_SENTINEL'}
    else:
        environ = {'ENVIRONMENT_TAKES_PRECEDENCE': 'true', 'S3BUCKET': 'bucket',
                   'S3SECRETACCESSKEY': 'CLOUD_SECRET_SENTINEL'}
    adoption.observe(adoption.baseline, configuration, environ=environ)
    captured = state(adoption)['snapshot']['adoption']['configuration']
    assert 'CLOUD_SECRET_SENTINEL' not in json.dumps(captured)
    assert captured['azure' if provider == 'azure' else 's3'] == {'enable': True}
    def forbidden_copy(*args):
        raise AssertionError('cloud adoption must refuse before local ZIP retention')
    assert prepare(adoption, artifact_root=tmp_path, retain_upload=forbidden_copy) == 'failed'
    record = state(adoption)['snapshot']['adoption']
    assert record['state'] == 'failed'
    assert record['failure'] == 'restore required ZIP bytes to local uploads before adoption'
    assert state(adoption)['desired_revision_id'] is None


def test_symbolic_historical_git_adoption_then_real_management_update_and_remove(adoption, monkeypatch):
    from docassemble.base.managed_publication import Publication
    from test_publication_writers_postgres import adapter
    from test_registry_changes_postgres import finish
    from docassemble_executor import legacy_adoption
    with adoption.engine.begin() as connection:
        connection.execute(text("UPDATE test_package SET name='example',type='git',giturl='https://example.org/repo',gitbranch='deleted-branch'"))
        connection.execute(text("ALTER TABLE test_user ADD COLUMN active boolean DEFAULT true; "
                                "ALTER TABLE test_user ADD COLUMN social_id text DEFAULT 'local$user'; "
                                "INSERT INTO test_role VALUES (2,'developer'); "
                                "INSERT INTO test_user_roles VALUES (1,2)"))
    observe(adoption)
    monkeypatch.setattr(legacy_adoption, 'historical_git_sha', lambda *args: 'a' * 40)
    def forbidden(*args, **kwargs):
        raise AssertionError('historical adoption/update must never resolve deleted branch')
    monkeypatch.setattr(legacy_adoption, 'resolve_source', forbidden)
    assert adoption.prepare_observed(resolve_git=legacy_adoption.resolve_git) == 'queued'
    adoption_ready(adoption)
    assert adoption.advance() == 'published'
    adopted = state(adoption)['snapshot']
    assert adopted['git_sources']['example']['ref_kind'] == 'historical'
    assert adopted['git_sources']['example']['resolved_sha'] == 'a' * 40
    finish(adoption)
    policy = {'allowed': {}, 'allow_updates': True, 'developer_can_install': True}
    service = Publication(adoption.engine, adapter.PreviewAuthorizer('test_', lambda: policy),
                          adapter.PackageProjection('test_'), 'test_')
    updated = ready_git(service, 'example', 'historical-update', actor='2', operation='update', monkeypatch=monkeypatch)
    assert updated[3]['git_sources'] == adopted['git_sources']
    approve(service, updated, actor='2', key='historical-update')
    finish(service)
    contribution = next(row for row in adopted['contributions'] if row['declaration'].startswith('example @'))
    removed = ready_git(service, 'example', 'historical-remove', actor='1', operation='remove',
                        contribution_id=contribution['id'], monkeypatch=monkeypatch)
    assert removed[3]['git_sources'] == {}
    approve(service, removed, actor='1', key='historical-remove')
    assert state(adoption)['snapshot']['git_sources'] == {}


def test_concurrent_changed_git_credential_same_key_creates_only_one_private_candidate(publisher, monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    root = secret_root(monkeypatch, tmp_path)
    barrier = Barrier(2)
    def request(declaration):
        barrier.wait(timeout=10)
        try:
            return publisher.prepare('1', declaration, 'concurrent-credential-key')
        except PreparationError as error:
            assert str(error) == 'idempotency key body mismatch'
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(request, [CREDENTIAL_DECLARATION, ROTATED_DECLARATION]))
    assert sum(value is not None for value in outcomes) == 1
    with publisher.engine.connect() as connection:
        candidate = connection.execute(select(publisher.tables['candidate'])).mappings().one()
        assert candidate['id'] in outcomes
        assert candidate['credential_request_binding'] is not None
        assert len(connection.execute(select(publisher.tables['request_deduplication'])).all()) == 1
        assert len(connection.execute(select(publisher.tables['candidate_material'])).all()) == 1
    assert len(list(root.iterdir())) == 1


def test_crash_after_private_binding_commit_preserves_credential_equality_and_refuses_missing_material(publisher, monkeypatch, tmp_path):
    root = secret_root(monkeypatch, tmp_path)
    def interrupted_stage(*args):
        raise RuntimeError('simulated writer loss before secret staging')
    monkeypatch.setattr(publisher, '_stage_queued_secret', interrupted_stage)
    with pytest.raises(RuntimeError, match='simulated writer loss'):
        publisher.prepare('1', CREDENTIAL_DECLARATION, 'crash-before-stage')
    with publisher.engine.connect() as connection:
        candidate = connection.execute(select(publisher.tables['candidate'])).mappings().one()
    assert candidate['credential_request_binding'] is not None
    assert publisher.prepare('1', CREDENTIAL_DECLARATION, 'crash-before-stage') == candidate['id']
    with pytest.raises(PreparationError, match='idempotency key body mismatch'):
        publisher.prepare('1', ROTATED_DECLARATION, 'crash-before-stage')
    claim = publisher.claim(candidate['id'])
    reference = claim.inputs['git_credentials']['example']
    with pytest.raises(PreparationError):
        publisher.read_git_credential(claim, root, reference)
    assert list(root.iterdir()) == []
    assert snapshot(publisher)['installation_attempt'] == []
