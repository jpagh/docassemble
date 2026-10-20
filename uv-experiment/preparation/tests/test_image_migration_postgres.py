"""Replacement image system publication through real PostgreSQL CAS/slot/fences."""
# ruff: noqa: F811 -- imported pytest fixtures
import copy

import pytest
from sqlalchemy import select, text, update

from test_preparation_postgres import protocol  # noqa: F401
from test_legacy_adoption_postgres import adoption, evidence, state  # noqa: F401
from docassemble.base.managed_migration import ImageMigration, SYSTEM_ACTOR, POLICY
from docassemble.base.managed_preparation import Preparation, PreparationError
from docassemble_executor.installation import Installation
from docassemble_executor.roles import RUNTIME_ROLES, INFRASTRUCTURE_ROLES


def core(version):
    return {'docassemble-' + name: {'version': version, 'source_digest': str(index) * 64}
            for index, name in enumerate(('base', 'webapp', 'demo'), 1)}


def ready(service, candidate_id):
    claim = service.claim(candidate_id)
    inventory = dict(claim.inputs['baseline']['core'], example={'version': '2.0', 'source_digest': 'b' * 64})
    from docassemble.base.managed_preparation import digest
    for name, source in claim.inputs['baseline'].get('git_sources', {}).items():
        inventory[name] = {'version': '2.0', 'source_digest': digest(source)}
    def diff(before):
        return {name: {'before': before.get(name), 'after': inventory.get(name)}
                for name in sorted(set(before) | set(inventory)) if before.get(name) != inventory.get(name)}
    result = {'bundle': {'digest': 'd' * 64, 'project_digest': 'e' * 64, 'lock_digest': 'f' * 64,
        'inventory': inventory, 'artifacts': ['b' * 64]},
        'desired_diff': diff(claim.inputs['baseline']['desired_inventory']),
        'runtime_diff': diff(claim.inputs['baseline']['runtime']['inventory']) if claim.inputs['baseline']['runtime']['kind'] == 'normal' else None,
        'warnings': Preparation.ready_warnings(claim.inputs),
        'validation': {'core_unchanged': True, 'requirements_satisfied': True, 'durable_materialization_verified': True},
        'ownership_effects': {}}
    if claim.inputs['baseline'].get('git_sources'):
        result['git_sources'] = claim.inputs['baseline']['git_sources']
    assert service.publish_ready(claim, result)
    return claim, result


@pytest.fixture
def migration(adoption):
    adoption.baseline.update(core=core('2.0'), image_baseline={'image_target': 'old-image'})
    adoption.observe(adoption.baseline, {}, environ={})
    assert adoption.prepare_observed(resolve_git=None) == 'queued'
    ready(adoption, state(adoption)['snapshot']['adoption']['candidate_id'])
    assert adoption.advance() == 'published'
    installer = Installation(adoption.engine, lambda *_: True, 'test_')
    claim = installer.claim_installation('host')
    receipt = {'identity': claim.identity, 'outcome': 'applied-normal', 'normal_verified': True,
        'mutation_started': True, 'child_safe': True, 'failure': None,
        'inventory': {name: pin['version'] for name, pin in claim.snapshot['desired_inventory'].items()},
        'readiness': {'schema_verified': True, 'roles': sorted(RUNTIME_ROLES + INFRASTRUCTURE_ROLES)}}
    assert installer.report_terminal(receipt) == 'reported'
    service = ImageMigration(adoption.engine, 'test_')
    service.target = dict(adoption.baseline, core=core('3.0'), image_baseline={'image_target': 'new-image'})
    return service


def queue(service):
    service.observe(service.target)
    assert service.prepare_observed() == 'queued'
    return state(service)['snapshot']['migration']['candidate_id']


@pytest.mark.parametrize('version,mode', [('3.0', 'upgrade'), ('1.0', 'unchanged'), ('2.0', 'unchanged')])
def test_newer_older_equal_replacement_is_system_cas_without_intent_or_grant_changes(migration, version, mode):
    migration.target['core'] = core(version)
    before = state(migration)
    original = evidence(migration)
    candidate = queue(migration)
    captured = state(migration)
    assert captured['desired_revision_id'] == before['desired_revision_id']
    assert captured['publication_generation'] == before['publication_generation']
    claim, result = ready(migration, candidate)
    assert claim.inputs['baseline']['core'] == core(version)
    assert claim.inputs['contributions'] == before['snapshot']['contributions']
    assert claim.inputs['baseline']['publication'] == before['snapshot']['publication']  # seed preferences only
    with pytest.raises(PreparationError, match='system publication'):
        migration.approve(SYSTEM_ACTOR, candidate, 'd' * 64, 'human')
    assert migration.advance() == 'published'
    current = state(migration)
    snapshot = current['snapshot']
    assert snapshot['schema_mode'] == mode
    assert snapshot['core'] == core(version) and snapshot['selected_baseline'] is None
    assert snapshot['image_baseline'] == migration.target['image_baseline']
    assert snapshot['recovery_baseline'] == {'image_baseline': migration.target['image_baseline'], 'core': core(version)}
    assert snapshot['publication']['system_actor'] == SYSTEM_ACTOR
    assert snapshot['publication']['enabling_policy'] == POLICY
    assert 'approver_id' not in snapshot['publication']
    assert snapshot['publication']['result'] == result
    assert current['publication_generation'] == before['publication_generation'] + 1
    assert snapshot['contributions'] == before['snapshot']['contributions']
    assert evidence(migration) == original
    installer = Installation(migration.engine, lambda *_: True, 'test_')
    attempt = installer.claim_installation('replacement')
    assert attempt.snapshot['core'] == core(version)
    assert attempt.snapshot['recovery_baseline']['image_baseline'] == migration.target['image_baseline']
    with pytest.raises(PreparationError, match='active|uncertain'):
        migration.target['image_baseline'] = {'image_target': 'third'}
        migration.observe(migration.target)


def test_failed_preparation_preserves_prior_intent_and_suppresses_restart_until_explicit_admin_retry(migration):
    before = state(migration)
    candidate = queue(migration)
    claim = migration.claim(candidate)
    assert migration.fail(claim)
    assert migration.advance() == 'failed'
    for _ in range(3):
        assert migration.observe(migration.target)['state'] == 'failed'
        assert migration.prepare_observed() == 'failed'
    current = state(migration)
    assert current['desired_revision_id'] == before['desired_revision_id']
    assert current['snapshot']['core'] == before['snapshot']['core']
    assert current['snapshot']['contributions'] == before['snapshot']['contributions']
    assert current['publication_generation'] == before['publication_generation']
    with pytest.raises(PreparationError, match='forbidden'):
        migration.request_retry('1')
    migration.request_retry('2')
    assert migration.prepare_observed() == 'queued'
    assert state(migration)['snapshot']['migration']['candidate_id'] != candidate


@pytest.mark.parametrize('variable,value', [('DAUPDATEONSTART', 'false'), ('DAUPDATEONSTART', 'invalid'),
    ('DAALLOWUPDATES', 'false'), ('DAREADONLYFILESYSTEM', 'true'), ('CONTAINERROLE', ':web:')])
def test_replacement_respects_startup_and_update_gates(migration, monkeypatch, variable, value):
    before = state(migration)
    migration.observe(migration.target)
    monkeypatch.setenv(variable, value)
    assert migration.prepare_observed() == 'failed'
    assert state(migration)['desired_revision_id'] == before['desired_revision_id']
    assert state(migration)['publication_generation'] == before['publication_generation']


@pytest.mark.parametrize('change', ['runtime_generation', 'input_generation', 'target_generation', 'publication_generation'])
def test_late_ready_workers_and_stale_results_cannot_publish(migration, change):
    candidate = queue(migration)
    ready(migration, candidate)
    before = state(migration)
    with migration.engine.begin() as connection:
        table = migration.tables['environment_state']
        connection.execute(update(table).values(**{change: table.c[change] + 1}))
    assert migration.advance() == 'failed'
    assert state(migration)['desired_revision_id'] == before['desired_revision_id']


def test_different_target_cancels_live_preparation_claim_and_cannot_publish_old_result(migration):
    first = queue(migration)
    claim = migration.claim(first)
    migration.target['image_baseline'] = {'image_target': 'different'}
    migration.observe(migration.target)
    assert migration.prepare_observed() == 'queued'
    assert not migration.renew(claim)
    assert not migration.fail(claim)
    with migration.engine.connect() as connection:
        row = connection.execute(select(migration.tables['candidate']).where(migration.tables['candidate'].c.id == first)).mappings().one()
    assert row['state'] == 'cancelled' and row['fence'] > claim.fence


def test_application_failure_preserves_new_desired_and_explicit_retry_is_exact(migration):
    candidate = queue(migration)
    ready(migration, candidate)
    assert migration.advance() == 'published'
    installer = Installation(migration.engine, lambda *_: True, 'test_')
    claim = installer.claim_installation('replacement')
    receipt = {'identity': claim.identity, 'outcome': 'failed-stopped', 'mutation_started': True,
        'normal_verified': False, 'child_safe': True, 'failure': {'stage': 'sync', 'reason': 'sync-failed'},
        # 13.2: retry after a failed apply requires the completed core recovery that
        # made the runtime child-safe, not just the terminal outcome.
        'recovery': {'id': claim.attempt_id + ':core:1', 'attempt': 1, 'result': 'succeeded',
            'core_baseline': {'image_target': 'new-image', 'bundle': 'core-r1', 'bundle_digest': 'b' * 64},
            'inventory': {'core': '1.0'}}}
    assert installer.report_terminal(receipt) == 'reported'
    published = state(migration)
    for _ in range(3):
        assert migration.observe(migration.target)['state'] == 'published'
        assert migration.prepare_observed() == 'published'
    assert state(migration) == published
    # Published system retry is administrator-authorized by the normal adapter,
    # and reuses the immutable revision rather than solving another candidate.
    from docassemble.base.managed_publication import Publication
    retry = Publication(migration.engine, lambda *_: True, lambda *_: {}, 'test_').retry('2', published['desired_revision_id'], 'explicit-retry')
    assert retry['revision_id'] == claim.revision_id
    assert retry['attempt_id'] != claim.attempt_id
    # 13.2: the completed child-safe recovery is exactly what makes this retry
    # claimable, so the retried attempt is claimed instead of refused. The
    # unrecovered claim/rollover refusals are independently covered by
    # test_unrecovered_migration_failure_cannot_claim_or_roll_over_explicit_retry.
    retried = installer.claim_installation('replacement')
    assert retried.attempt_id == retry['attempt_id']


@pytest.mark.parametrize('recovery', [None, 'failed'])
def test_unrecovered_migration_failure_cannot_claim_or_roll_over_explicit_retry(migration, recovery):
    from docassemble.base.managed_publication import Publication
    from test_preparation_postgres import snapshot
    candidate = queue(migration)
    ready(migration, candidate)
    assert migration.advance() == 'published'
    installer = Installation(migration.engine, lambda *_: True, 'test_')
    claim = installer.claim_installation('replacement')
    receipt = {'identity': claim.identity, 'outcome': 'failed-stopped', 'mutation_started': True,
        'normal_verified': False, 'child_safe': True, 'failure': {'stage': 'sync', 'reason': 'sync-failed'}}
    if recovery is not None:
        receipt['recovery'] = {'id': claim.attempt_id + ':core:1', 'attempt': 1, 'result': recovery,
            'core_baseline': {'image_target': 'new-image', 'bundle': 'core-r1', 'bundle_digest': 'b' * 64},
            'inventory': {'core': '1.0'}}
    assert installer.report_terminal(receipt) == 'reported'
    # Internal publication can queue a retry without the web evidence guard;
    # executor claim/rollover must independently refuse unrecovered mutation.
    retry = Publication(migration.engine, lambda *_: True, lambda *_: {}, 'test_').retry(
        '2', claim.revision_id, 'unrecovered-retry')
    before = snapshot(installer)
    assert before['environment_state'][0]['active_attempt_id'] == retry['attempt_id']
    assert not installer.next_after_terminal(receipt)
    with pytest.raises(PreparationError, match='eligible durable terminal'):
        installer.claim_installation('replacement')
    assert snapshot(installer) == before


def test_schema_high_water_survives_downgrade_and_intermediate_forward_replacement(migration):
    high = {'release': '4.0', 'source': 'successful-setup', 'attempt_id': 'setup', 'revision_id': 'setup-revision',
            'schema_identity': 'a' * 64, 'database_revision': 'advanced'}
    migration.observe(migration.target, schema_evidence=high)
    assert state(migration)['snapshot']['migration']['schema_mode'] == 'unchanged'
    assert state(migration)['snapshot']['migration']['schema_release'] == high


def test_unreadable_schema_evidence_blocks_without_changing_committed_state(migration):
    before = copy.deepcopy(state(migration))
    with pytest.raises(PreparationError, match='unreadable schema'):
        migration.observe(migration.target, schema_evidence={'release': 'broken'})
    assert state(migration) == before


def test_update_disable_between_ready_and_publish_blocks_system_authority(migration, monkeypatch):
    candidate = queue(migration)
    ready(migration, candidate)
    monkeypatch.setenv('DAALLOWUPDATES', 'false')
    assert migration.advance() == 'failed'
    assert state(migration)['snapshot']['migration']['state'] == 'failed'


def test_source_policy_is_never_silently_replaced_by_new_image(migration):
    before = state(migration)
    migration.target['config'] = {'index_url': 'https://different.invalid/simple'}
    migration.observe(migration.target)
    assert migration.prepare_observed() == 'failed'
    assert state(migration)['snapshot']['config'] == before['snapshot']['config']


def test_observe_fences_other_unpublished_candidates_and_not_their_committed_material(migration):
    with migration.engine.begin() as connection:
        tables = migration.tables
        connection.execute(text("INSERT INTO test_managed_candidate (id,requester_id,operation,request_digest,parent_id,publication_generation,input_generation,target_generation,runtime_generation,inputs,state,fence) SELECT 'old','1','repair','digest',desired_revision_id,publication_generation,input_generation,target_generation,runtime_generation,'{}'::jsonb,'queued',0 FROM test_managed_environment_state"))
        connection.execute(tables['candidate_material'].insert().values(id='secret', candidate_id='old', path='/old/secret',
            usable=True, committed=False, shared=False, deleted=False))
    migration.observe(migration.target)
    with migration.engine.connect() as connection:
        assert connection.scalar(select(tables['candidate_material'].c.usable).where(tables['candidate_material'].c.id == 'secret')) is False


def test_replacement_preserves_declared_git_ref_sha_subdirectory_and_committed_credentials(migration):
    from docassemble.base.managed_git import parse_git_requirement
    declaration = 'Example @ git+https://example.org/repo.git@main#subdirectory=package'
    _, source, _ = parse_git_requirement(declaration)
    source.update(ref_kind='branch', resolved_sha='a' * 40)
    with migration.engine.begin() as connection:
        current = migration._environment(connection)
        identity = connection.scalar(select(migration.tables['package_identity'].c.id).where(
            migration.tables['package_identity'].c.normalized_name == 'example'))
        connection.execute(migration.tables['requirement_contribution'].insert().values(id='git-source', identity_id=identity,
            declaration=declaration, origin='interactive', active=True))
        connection.execute(update(migration.tables['environment_state']).values(snapshot=dict(current['snapshot'],
            git_sources={'example': source}, git_credentials={'example': 'committed-reference'})))
    candidate = queue(migration)
    claim, result = ready(migration, candidate)
    assert claim.inputs['baseline']['git_sources'] == {'example': source}
    assert claim.inputs['git_credentials'] == {'example': 'committed-reference'}
    assert result['git_sources'] == {'example': source}
    changed = copy.deepcopy(result)
    changed['git_sources']['example']['resolved_sha'] = 'b' * 40
    with pytest.raises(PreparationError, match='fixed'):
        Preparation._validate_result(claim.inputs, changed)
    assert migration.advance() == 'published'
    assert state(migration)['snapshot']['git_sources'] == {'example': source}
    assert state(migration)['snapshot']['git_credentials'] == {'example': 'committed-reference'}


def test_replacement_captures_zip_direct_hash_and_private_bindings_without_source_refresh(migration):
    direct = {'digest': 'a' * 64, 'url': 'https://example.org/fixed.zip', 'version': '1.0'}
    with migration.engine.begin() as connection:
        current = migration._environment(connection)
        connection.execute(update(migration.tables['requirement_contribution']).values(artifact_ref='b' * 64))
        policy = {'private_indexes': {'private': 'https://example.org/simple'}, 'private_bindings': {'example': 'private'}}
        connection.execute(update(migration.tables['environment_state']).values(snapshot=dict(current['snapshot'],
            direct_sources={'direct-name': direct}, config=policy)))
    migration.target['config'] = policy
    candidate = queue(migration)
    claim = migration.claim(candidate)
    assert claim.inputs['baseline']['direct_sources'] == {'direct-name': direct}
    assert claim.inputs['baseline']['config'] == policy
    assert all(row['artifact_ref'] == 'b' * 64 for row in claim.inputs['contributions'])



def test_image_downgrade_then_explicit_upgrade_below_schema_high_water_never_selects_older_setup(migration):
    from docassemble.base.managed_publication import Publication
    from test_core_upgrade_postgres import upgrade_result
    migration.target['core'] = core('1.0')
    high = {'release': '4.0', 'source': 'successful-setup', 'attempt_id': 'setup', 'revision_id': 'setup-revision',
            'schema_identity': 'a' * 64, 'database_revision': 'advanced'}
    migration.observe(migration.target, schema_evidence=high)
    assert migration.prepare_observed() == 'queued'
    ready(migration, state(migration)['snapshot']['migration']['candidate_id'])
    assert migration.advance() == 'published'
    installer = Installation(migration.engine, lambda *_: True, 'test_')
    applied = installer.claim_installation('replacement')
    assert installer.report_terminal({'identity': applied.identity, 'outcome': 'applied-normal', 'normal_verified': True,
        'mutation_started': True, 'child_safe': True, 'failure': None,
        'inventory': {name: pin['version'] for name, pin in applied.snapshot['desired_inventory'].items()},
        'readiness': {'schema_verified': True, 'roles': sorted(RUNTIME_ROLES + INFRASTRUCTURE_ROLES)}}) == 'reported'
    publisher = Publication(migration.engine, lambda *_: True, lambda *_: {}, 'test_')
    candidate = publisher.prepare('2', None, 'upgrade-below-schema', operation='upgrade',
        release_selection={'mode': 'exact', 'version': '2.0'})
    claim = publisher.claim(candidate)
    result = upgrade_result(claim, release='2.0')
    result['bundle']['inventory']['example'] = {'version': '2.0', 'source_digest': 'b' * 64}
    for key, before in [('desired_diff', claim.inputs['baseline']['desired_inventory']),
                        ('runtime_diff', claim.inputs['baseline']['runtime']['inventory'])]:
        inventory = result['bundle']['inventory']
        result[key] = {name: {'before': before.get(name), 'after': inventory.get(name)}
            for name in set(before) | set(inventory) if before.get(name) != inventory.get(name)}
    assert publisher.publish_ready(claim, result)
    with publisher.engine.connect() as connection:
        prepared = connection.execute(select(publisher.tables['candidate']).where(publisher.tables['candidate'].c.id == candidate)).mappings().one()
    publisher.approve('2', candidate, prepared['result_digest'], 'explicit-upgrade')
    current = state(publisher)
    assert current['snapshot']['core']['docassemble-webapp']['version'] == '2.0'
    assert current['snapshot']['schema_mode'] == 'unchanged'
    assert current['snapshot']['schema_release'] == high
    with pytest.raises(PreparationError, match='fresh downgrade is unsupported'):
        publisher.prepare('2', None, 'still-not-a-downgrade', operation='upgrade',
            release_selection={'mode': 'exact', 'version': '1.5'})
