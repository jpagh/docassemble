"""Automatic adoption, with actual PostgreSQL CAS and preserved legacy evidence."""
# ruff: noqa: F811 -- explicit fixture registration.
import hashlib
import io
import json
import os
import zipfile

import pytest
from sqlalchemy import select, text, update

from test_preparation_postgres import protocol, PIN  # noqa: F401
from docassemble.base.managed_adoption import (LegacyAdoption, SYSTEM_ACTOR, POLICY,
    legacy_evidence, classify, configuration_intent, upload_path, adoption_status)
from docassemble.base.managed_preparation import Preparation, PreparationError, digest


@pytest.fixture
def adoption(protocol, monkeypatch):
    monkeypatch.setenv('DA_UV_MANAGED', 'true')
    with protocol.engine.begin() as connection:
        connection.execute(text('ALTER TABLE test_managed_revision DISABLE TRIGGER managed_revision_immutable'))
        for name in ('requirement_contribution', 'package_identity', 'environment_state', 'revision'):
            connection.execute(protocol.tables[name].delete())
        connection.execute(text('ALTER TABLE test_managed_revision ENABLE TRIGGER managed_revision_immutable'))
        connection.execute(text('''CREATE TABLE test_package (id serial PRIMARY KEY, name text, type text,
            giturl text, gitsubdir text, upload integer, version integer DEFAULT 1,
            packageversion text, limitation text, dependency boolean DEFAULT false,
            core boolean DEFAULT false, active boolean DEFAULT true, gitbranch text);
            CREATE TABLE test_package_auth (id integer PRIMARY KEY, package_id integer, user_id integer, authtype text);
            CREATE TABLE test_install (id integer PRIMARY KEY, package_id integer, hostname text, packageversion text);
            CREATE TABLE test_uploads (indexno integer PRIMARY KEY, filename text);
            CREATE TABLE test_user (id integer PRIMARY KEY);
            CREATE TABLE test_role (id integer PRIMARY KEY, name text);
            CREATE TABLE test_user_roles (user_id integer, role_id integer);
            INSERT INTO test_user VALUES (1), (2);
            INSERT INTO test_role VALUES (1, 'admin');
            INSERT INTO test_user_roles VALUES (2, 1);
            INSERT INTO test_package (id,name,type,limitation,dependency) VALUES (1,'Example','pip','>=2',true);
            SELECT setval('test_package_id_seq', 1);
            INSERT INTO test_package_auth VALUES (1,1,1,'owner');
            INSERT INTO test_install VALUES (1,1,'old-host','0.1');'''))
    service = LegacyAdoption(protocol.engine, 'test_')
    service.baseline = {'core': {'core': PIN}, 'target': {'python': '3.12'}, 'tool': {'uv': '0.12.21'},
        'config': {'index_url': 'https://pypi.org/simple'}}
    return service


def evidence(service):
    with service.engine.connect() as connection:
        return legacy_evidence(connection, service.prefix)


def state(service):
    with service.engine.connect() as connection:
        return dict(connection.execute(select(service.tables['environment_state'])).mappings().one())


def observe(service, configuration=None):
    return service.observe(service.baseline, configuration or {}, environ={})


def prepare(service, **kwargs):
    return service.prepare_observed(resolve_git=lambda name, source, payload: dict(source,
        ref_kind='commit' if source['declared_ref'] == 'a' * 40 else 'branch', resolved_sha='a' * 40), **kwargs)


def ready(service):
    candidate_id = state(service)['snapshot']['adoption']['candidate_id']
    claim = service.claim(candidate_id)
    inventory = dict(claim.inputs['baseline']['core'])
    for row in claim.inputs['contributions']:
        from packaging.requirements import Requirement
        from packaging.utils import canonicalize_name
        name = canonicalize_name(Requirement(row['declaration']).name)
        inventory[name] = {'version': '2.0', 'source_digest': row['artifact_ref'] or 'b' * 64}
    for name, source in claim.inputs['baseline'].get('git_sources', {}).items():
        inventory[name]['source_digest'] = digest(source)
    result = {'bundle': {'digest': 'd' * 64, 'project_digest': 'e' * 64, 'lock_digest': 'f' * 64,
        'inventory': inventory, 'artifacts': [row['artifact_ref'] for row in claim.inputs['contributions'] if row['artifact_ref']]},
        'desired_diff': {name: {'before': None, 'after': pin} for name, pin in inventory.items() if name != 'core'},
        'runtime_diff': None, 'warnings': Preparation.ready_warnings(claim.inputs),
        'validation': {'core_unchanged': True, 'requirements_satisfied': True, 'durable_materialization_verified': True},
        'ownership_effects': {}}
    if claim.inputs['baseline'].get('git_sources'):
        result['git_sources'] = claim.inputs['baseline']['git_sources']
    assert service.publish_ready(claim, result)
    return claim, result


def test_registry_config_preserve_original_intent_provenance_grants_and_system_publication(adoption):
    before = evidence(adoption)
    observe(adoption, {'python packages': ['Example[extra]<3', 'Separate>=1']})
    assert prepare(adoption) == 'queued'
    assert evidence(adoption) == before
    assert not state(adoption)['desired_revision_id']
    claim, _ = ready(adoption)
    with pytest.raises(PreparationError):
        adoption.approve(SYSTEM_ACTOR, claim.candidate_id, 'd' * 64, 'interactive')
    assert adoption.advance() == 'published'
    assert adoption.advance() == 'managed'
    current = state(adoption)
    snapshot = current['snapshot']
    assert current['publication_generation'] == 1 and current['active_attempt_id']
    assert 'approver_id' not in snapshot['publication']
    assert snapshot['publication']['system_actor'] == SYSTEM_ACTOR
    assert snapshot['publication']['enabling_policy'] == POLICY
    assert snapshot['adoption']['state'] == 'published'
    rows = snapshot['contributions']
    assert sorted(row['declaration'] for row in rows) == sorted(['Example', 'Example[extra]<3', 'Example>=2', 'Separate>=1'])
    assert sorted(row['origin'] for row in rows) == ['config', 'config', 'interactive', 'interactive']
    with adoption.engine.connect() as connection:
        identities = list(connection.execute(select(adoption.tables['package_identity'])).mappings())
        attempt = connection.scalar(select(adoption.tables['installation_attempt'].c.record))
    assert next(row for row in identities if row['normalized_name'] == 'example')['owner_id'] == '1'
    assert next(row for row in identities if row['normalized_name'] == 'separate')['owner_id'] is None
    assert attempt['actor_id'] == SYSTEM_ACTOR and attempt['reason'] == 'automatic-adoption'
    after = evidence(adoption)
    assert all(row in after['package'] for row in before['package'])
    assert {key: value for key, value in after.items() if key != 'package'} == {key: value for key, value in before.items() if key != 'package'}
    projected = [row for row in after['package'] if row not in before['package']]
    assert len(projected) == 1 and projected[0]['name'] == 'Separate' and projected[0]['active'] is False


@pytest.mark.parametrize('dependency', [True, False])
def test_inventory_and_dependency_flags_alone_never_establish_direct_intent(adoption, dependency):
    with adoption.engine.begin() as connection:
        connection.execute(text('DELETE FROM test_package_auth; UPDATE test_package SET limitation=NULL, dependency=:flag'), {'flag': dependency})
    original = evidence(adoption)
    observe(adoption)
    assert prepare(adoption) == 'failed'
    assert 'classification' in state(adoption)['snapshot']['adoption']['failure']
    assert evidence(adoption) == original
    assert not state(adoption)['desired_revision_id']


@pytest.mark.parametrize('case', ['deleted-owner', 'no-owner', 'dependency-true'])
def test_clearly_identified_ownerless_intent_remains_flagged_and_ownerless(adoption, case):
    with adoption.engine.begin() as connection:
        if case == 'deleted-owner':
            connection.execute(text('DELETE FROM test_user WHERE id=1'))
        elif case == 'no-owner':
            connection.execute(text('DELETE FROM test_package_auth'))
    original = evidence(adoption)
    observe(adoption)
    assert prepare(adoption) == 'queued'
    ready(adoption)
    assert adoption.advance() == 'published'
    snapshot = state(adoption)['snapshot']
    assert snapshot['adoption']['ownerless'] == ([] if case == 'dependency-true' else ['example'])
    assert evidence(adoption) == original


@pytest.mark.parametrize('case', ['duplicate', 'multiple-owners', 'source-fields', 'unknown-type', 'bad-name', 'missing-upload'])
def test_ambiguous_or_missing_sources_block_the_whole_adoption(adoption, case):
    statements = {
        'duplicate': "INSERT INTO test_package (id,name,type,limitation) VALUES(2,'example','pip','<3')",
        'multiple-owners': "INSERT INTO test_package_auth VALUES(2,1,2,'owner')",
        'source-fields': "UPDATE test_package SET giturl='https://example.org/repo'",
        'unknown-type': "UPDATE test_package SET type='unknown'",
        'bad-name': "UPDATE test_package SET name='not a distribution name'",
        'missing-upload': "UPDATE test_package SET type='zip',upload=7"}
    with adoption.engine.begin() as connection:
        connection.execute(text(statements[case]))
    original = evidence(adoption)
    observe(adoption)
    assert prepare(adoption) == 'failed'
    assert evidence(adoption) == original
    assert state(adoption)['desired_revision_id'] is None
    with adoption.engine.connect() as connection:
        assert connection.scalar(select(adoption.tables['package_identity'].c.id)) is None


def test_inactive_and_core_rows_remain_evidence_not_requests(adoption):
    with adoption.engine.begin() as connection:
        connection.execute(text("INSERT INTO test_package(id,name,type,active) VALUES(2,'Unknown','pip',false); INSERT INTO test_package(id,name,type,core) VALUES(3,'core','pip',true)"))
    original = evidence(adoption)
    observe(adoption)
    assert prepare(adoption) == 'queued'
    ready(adoption)
    assert adoption.advance() == 'published'
    assert {row['declaration'] for row in state(adoption)['snapshot']['contributions']} == {'Example', 'Example>=2'}
    assert evidence(adoption) == original


def test_failed_generation_suppresses_restart_loops_and_admin_retry_is_fresh(adoption):
    observe(adoption)
    assert prepare(adoption) == 'queued'
    old_id = state(adoption)['snapshot']['adoption']['candidate_id']
    claim = adoption.claim(old_id)
    assert adoption.fail(claim)
    assert adoption.advance() == 'failed'
    for _ in range(3):
        assert observe(adoption)['state'] == 'failed'
        assert prepare(adoption) == 'failed'
    with pytest.raises(PreparationError, match='forbidden'):
        adoption.request_retry('1')
    adoption.request_retry('2')
    assert prepare(adoption) == 'queued'
    new_id = state(adoption)['snapshot']['adoption']['candidate_id']
    assert new_id != old_id
    with adoption.engine.connect() as connection:
        assert connection.scalar(select(adoption.tables['candidate'].c.state).where(adoption.tables['candidate'].c.id == old_id)) == 'failed'


def test_changed_config_and_target_are_new_generations_not_restart_retry(adoption):
    observe(adoption)
    prepare(adoption)
    claim = adoption.claim(state(adoption)['snapshot']['adoption']['candidate_id'])
    adoption.fail(claim)
    adoption.advance()
    original = state(adoption)
    assert observe(adoption, {'python packages': ['Separate>=1']})['state'] == 'observed'
    assert state(adoption)['input_generation'] > original['input_generation']
    assert prepare(adoption) == 'queued'
    ready(adoption)
    assert adoption.advance() == 'published'


@pytest.mark.parametrize('change', ['limitation', 'grant', 'runtime', 'config'])
def test_stale_inputs_and_authority_cannot_publish(adoption, change):
    observe(adoption)
    prepare(adoption)
    ready(adoption)
    with adoption.engine.begin() as connection:
        if change == 'limitation':
            connection.execute(text("UPDATE test_package SET limitation='<1'"))
        elif change == 'grant':
            connection.execute(text('DELETE FROM test_package_auth'))
        elif change == 'runtime':
            table = adoption.tables['environment_state']
            connection.execute(update(table).values(runtime_generation=table.c.runtime_generation + 1))
    if change == 'config':
        observe(adoption, {'python packages': ['Separate>=1']})
    else:
        assert adoption.advance() == 'failed'
    assert state(adoption)['desired_revision_id'] is None


def test_busy_slot_never_publishes_a_second_installation(adoption):
    observe(adoption)
    prepare(adoption)
    ready(adoption)
    with adoption.engine.begin() as connection:
        connection.execute(adoption.tables['revision'].insert().values(id='other', snapshot={}))
        connection.execute(adoption.tables['installation_attempt'].insert().values(id='busy', revision_id='other', record={}))
        connection.execute(update(adoption.tables['environment_state']).values(active_attempt_id='busy'))
    assert adoption.advance() == 'busy'
    assert state(adoption)['publication_generation'] == 0
    assert state(adoption)['desired_revision_id'] is None


@pytest.mark.parametrize('ref', ['main', 'a' * 40])
def test_git_pat_sha_selected_once_retained_and_disclosed(adoption, tmp_path, ref):
    secret = tmp_path / 'secrets'
    secret.mkdir(mode=0o700)
    with adoption.engine.begin() as connection:
        connection.execute(text("UPDATE test_package SET type='git',giturl='https://user:DISPOSABLE_PAT@example.org/repo',gitbranch=:ref"), {'ref': ref})
    calls = []
    def resolver(name, source, payload):
        assert b'DISPOSABLE_PAT' not in payload  # Basic authorization is separated.
        assert b'authorization' in payload
        calls.append(source)
        return dict(source, ref_kind='commit' if ref == 'a' * 40 else 'branch', resolved_sha='a' * 40)
    original = evidence(adoption)
    observed = observe(adoption)
    assert adoption.prepare_observed(resolve_git=resolver, secret_root=secret) == 'queued'
    candidate_id = state(adoption)['snapshot']['adoption']['candidate_id']
    claim = adoption.claim(candidate_id)
    assert 'DISPOSABLE_PAT' not in repr(claim.inputs)
    assert claim.inputs['adoption']['fingerprint'] == observed['fingerprint']
    assert claim.inputs['adoption']['evidence_digest'] == digest(original)
    assert claim.inputs['adoption']['identities'][0]['legacy_package_id'] == 1
    assert claim.inputs['adoption']['identities'][0]['owner_id'] == '1'
    adoption.fail(claim)
    adoption.advance()
    assert observe(adoption)['fingerprint'] == observed['fingerprint']
    adoption.request_retry('2')
    assert adoption.prepare_observed(resolve_git=resolver, secret_root=secret) == 'queued'
    claim, _ = ready(adoption)
    assert claim.candidate_id != candidate_id
    assert claim.inputs['adoption']['fingerprint'] == observed['fingerprint']
    assert claim.inputs['adoption']['evidence_digest'] == digest(original)
    assert len(calls) == 1
    assert 'adopted-git:example:' + 'a' * 40 in Preparation.ready_warnings(claim.inputs)
    assert adoption.advance() == 'published'
    assert state(adoption)['snapshot']['git_sources']['example']['resolved_sha'] == 'a' * 40
    assert evidence(adoption) == original
    assert 'DISPOSABLE_PAT' not in repr(state(adoption)['snapshot'])
    with adoption.engine.connect() as connection:
        rows = list(connection.execute(select(adoption.tables['candidate_material'])).mappings())
    assert sum(row['committed'] for row in rows) == 1


def archive(name='Example'):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as output:
        output.writestr('Example/PKG-INFO', 'Metadata-Version: 2.1\nName: ' + name + '\nVersion: 2.0\n')
    return buffer.getvalue()


@pytest.mark.parametrize('case', ['valid', 'missing-bytes', 'mismatch', 'cloud'])
def test_zip_retained_before_publication_and_original_uploads_preserved(adoption, tmp_path, monkeypatch, case):
    root = tmp_path / 'artifacts'
    root.mkdir(mode=0o700)
    monkeypatch.setenv('DA_MANAGED_ARTIFACT_UID', str(os.getuid()))
    config = {'uploads': str(tmp_path / 'uploads')}
    if case == 'cloud':
        config['s3'] = {'enable': True}
    with adoption.engine.begin() as connection:
        connection.execute(text("UPDATE test_package SET type='zip',upload=7; INSERT INTO test_uploads VALUES(7,'original.zip')"))
    path = upload_path({'uploads': config['uploads']}, 7)
    path.parent.mkdir(parents=True)
    data = archive('Other' if case == 'mismatch' else 'Example')
    if case != 'missing-bytes':
        path.write_bytes(data)
    original = evidence(adoption)
    observe(adoption, config)
    assert prepare(adoption, artifact_root=root) == ('queued' if case == 'valid' else 'failed')
    if case == 'valid':
        retained = root / hashlib.sha256(data).hexdigest()
        assert retained.read_bytes() == data
        ready(adoption)
        assert adoption.advance() == 'published'
        assert retained.read_bytes() == data and path.read_bytes() == data
    assert evidence(adoption) == original


def test_configuration_git_direct_and_private_bindings_preserved_structurally(adoption):
    policy, observed, rows = configuration_intent({'python packages': [
        'GitProject @ git+https://example.org/repo@main',
        'DirectProject @ https://user:TOKEN@example.org/direct.whl?signature=SECRET']}, {}, adoption.baseline)
    identities, contributions, secrets, direct, uploads = classify(evidence(adoption), rows, adoption.baseline['core'])
    assert direct['directproject']['url'].endswith('?signature=SECRET')
    assert 'TOKEN' not in repr(contributions) and 'SECRET' not in repr(contributions)
    assert len(identities) == 3 and not uploads and not secrets
    private = {'private indexes': {'private': 'https://example.org/simple/'},
        'private bindings': {'example': 'private', 'transitive': 'private'}}
    observe(adoption, private)
    adoption.baseline['config'] = {'private_indexes': private['private indexes'], 'private_bindings': private['private bindings']}
    # Matching operator-qualified private policy, with no public substitution.
    observe(adoption, private)
    assert prepare(adoption) == 'queued'
    claim = adoption.claim(state(adoption)['snapshot']['adoption']['candidate_id'])
    assert claim.inputs['baseline']['config']['private_bindings']['transitive'] == 'private'


def test_failure_status_is_redacted_and_original_evidence_remains_restricted(adoption):
    with adoption.engine.begin() as connection:
        connection.execute(text("UPDATE test_package SET giturl='https://secret@example.org/repo'"))
    observe(adoption)
    prepare(adoption)
    public = json.dumps(adoption_status(state(adoption)['snapshot']))
    assert 'secret' not in public and 'https://' not in public
    assert evidence(adoption)['package'][0]['giturl'] == 'https://secret@example.org/repo'


def test_cleanup_scopes_materials_to_the_actual_store(adoption, tmp_path):
    observe(adoption)
    prepare(adoption)
    candidate_id = state(adoption)['snapshot']['adoption']['candidate_id']
    claim = adoption.claim(candidate_id)
    roots = [tmp_path / 'one', tmp_path / 'two']
    for root in roots:
        root.mkdir(mode=0o700)
        adoption.stage_candidate_secret(claim, root, b'secret')
    adoption.fail(claim)
    adoption.worker_exited(claim.worker_id)
    assert adoption.reconcile_material(roots[0]) == 1
    assert len(list(roots[1].iterdir())) == 1
    assert adoption.reconcile_material(roots[1]) == 1


@pytest.mark.parametrize('mode', ['false', 'invalid'])
def test_automatic_adoption_obeys_startup_convergence_flags_without_trusting_legacy_inventory(adoption, mode):
    adoption.observe(adoption.baseline, {}, environ={'DAUPDATEONSTART': mode})
    assert prepare(adoption) == 'failed'
    assert 'DAUPDATEONSTART' in state(adoption)['snapshot']['adoption']['failure']
    assert state(adoption)['desired_revision_id'] is None


def test_legacy_direct_limitation_preserves_the_request_and_separates_public_declaration(adoption):
    with adoption.engine.begin() as connection:
        connection.execute(text("UPDATE test_package SET limitation='@ https://user:TOKEN@example.org/Example.whl?signature=SECRET'"))
    observe(adoption)
    assert prepare(adoption) == 'queued'
    claim = adoption.claim(state(adoption)['snapshot']['adoption']['candidate_id'])
    assert claim.inputs['adoption_direct_sources']['example']['url'].endswith('?signature=SECRET')
    assert all('TOKEN' not in row['declaration'] and 'SECRET' not in row['declaration'] for row in claim.inputs['contributions'])


def test_adopted_git_remains_repairable_with_original_package_and_current_grants(adoption, tmp_path):
    from test_publication_writers_postgres import adapter
    secrets = tmp_path / 'secrets'
    secrets.mkdir(mode=0o700)
    with adoption.engine.begin() as connection:
        connection.execute(text("UPDATE test_package SET type='git',giturl='https://user:PAT_SENTINEL@example.org/repo',gitbranch='main'"))
    observe(adoption)
    assert prepare(adoption, secret_root=secrets) == 'queued'
    ready(adoption)
    assert adoption.advance() == 'published'
    snapshot = state(adoption)['snapshot']
    assert 'PAT_SENTINEL' not in repr(snapshot)
    candidate = {'operation': 'repair', 'inputs': {'baseline': snapshot, 'contributions': snapshot['contributions']}}
    with adoption.engine.begin() as connection:
        assert adapter.PackageProjection('test_')(connection, '2', candidate) == {'repaired': True}


def test_normal_installation_claim_accepts_only_the_exact_system_published_adoption(adoption):
    from docassemble_executor.installation import Installation
    observe(adoption)
    prepare(adoption)
    ready(adoption)
    assert adoption.advance() == 'published'
    installation = Installation(adoption.engine, lambda *_: False, 'test_')
    claim = installation.claim_installation('fresh-host')
    assert claim.snapshot['publication']['system_actor'] == SYSTEM_ACTOR
    assert claim.revision_id == state(adoption)['desired_revision_id']
    assert state(adoption)['active_attempt_id'] == claim.attempt_id


def test_admin_exact_retry_of_published_adoption_is_not_fresh_preparation(adoption):
    from test_publication_writers_postgres import adapter
    from docassemble_executor.installation import Installation
    observe(adoption)
    prepare(adoption)
    ready(adoption)
    assert adoption.advance() == 'published'
    revision_id = state(adoption)['desired_revision_id']
    installation = Installation(adoption.engine, lambda *_: False, 'test_')
    assert installation.pending_first_adoption()
    claim = installation.claim_installation('host')
    assert not installation.pending_first_adoption()
    assert installation.report_terminal({'identity': claim.identity, 'outcome': 'aborted-before-sync',
        'mutation_started': False, 'child_safe': False, 'normal_verified': False,
        'failure': {'stage': 'drain', 'reason': 'drain-aborted'}}) == 'reported'
    publisher = adapter.QualifiedPublication(adoption.engine, lambda *_: True,
        adapter.PackageProjection('test_'), 'test_', adoption.baseline)
    retried = publisher.retry('2', revision_id, 'exact-adoption')
    assert retried['revision_id'] == revision_id and retried['attempt_id'] != claim.attempt_id
    with pytest.raises(PreparationError, match='failed unpublished preparation'):
        adoption.request_retry('2')
    with adoption.engine.connect() as connection:
        assert len(list(connection.execute(select(adoption.tables['candidate'])))) == 1


def test_historical_git_commit_does_not_require_a_live_ref(monkeypatch):
    # A recorded, fetchable historical commit must be preserved even when the
    # declared branch has since been deleted: do not re-resolve the live ref.
    from docassemble_executor import legacy_adoption
    monkeypatch.setattr(legacy_adoption, 'historical_git_sha', lambda *a, **k: 'a' * 40)
    def explode(*args, **kwargs):
        raise AssertionError('live ref resolution must not run when a historical SHA is recorded')
    monkeypatch.setattr(legacy_adoption, 'resolve_source', explode)
    source = {'repository': 'https://example.org/repo.git', 'subdirectory': None, 'declared_ref': 'legacy'}
    selected = legacy_adoption.resolve_git('example', source, None)
    # 16.1: symbolic declarations are historical pins, not explicit SHA requests.
    assert selected['ref_kind'] == 'historical' and selected['resolved_sha'] == 'a' * 40
    assert selected['declared_ref'] == 'legacy'
