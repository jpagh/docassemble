"""Coherent core upgrade preparation/preview over real PostgreSQL.

Preview-only (ticket 14a): no approval, publication, runtime or schema mutation.
"""
# ruff: noqa: F811 -- imported PostgreSQL fixtures are injected by name

import pytest
from sqlalchemy import select, text, update

from test_preparation_postgres import protocol, snapshot  # noqa: F401 -- imported pytest fixture
from test_publication_writers_postgres import publisher, adapter  # noqa: F401 -- imported pytest fixtures
from docassemble.base.managed_preparation import Preparation, PreparationError
from docassemble.base.managed_release import upgrade_risk_disclosure

PIN = {'version': '1.0.0', 'source_digest': 'a' * 64}
RELEASE = '2.0.0'


def upgrade_result(claim, *, release=RELEASE, incoherent=False):
    core_inventory = {
        'docassemble-base': {'version': release, 'source_digest': '1' * 64},
        'docassemble-webapp': {'version': release, 'source_digest': '2' * 64},
        'docassemble-demo': {'version': release, 'source_digest': '3' * 64},
        'flask': {'version': '3.1.0', 'source_digest': '4' * 64},
    }
    inventory = {'core': PIN, 'separate': {'version': '1.5', 'source_digest': 'b' * 64}, **core_inventory}
    if incoherent:
        inventory['docassemble-demo'] = {'version': '1.0.0', 'source_digest': '3' * 64}

    def diff(before):
        return {name: {'before': before.get(name), 'after': inventory.get(name)}
                for name in sorted(set(before) | set(inventory)) if before.get(name) != inventory.get(name)}
    return {
        'bundle': {'digest': 'd' * 64, 'project_digest': 'e' * 64, 'lock_digest': 'f' * 64,
                   'inventory': inventory, 'artifacts': ['e' * 64]},
        'core_bundle': {'digest': 'a' * 64, 'project_digest': 'b' * 64, 'lock_digest': 'c' * 64,
                        'inventory': core_inventory, 'artifacts': ['9' * 64]},
        'release': {'selection': {'mode': 'exact', 'version': release}, 'resolved': release,
                    'webapp': release, 'base': release, 'demo': release, 'core_bundle': 'a' * 64},
        'desired_diff': diff(claim.inputs['baseline']['desired_inventory']),
        'runtime_diff': diff(claim.inputs['baseline']['runtime']['inventory']),
        'warnings': Preparation.ready_warnings(claim.inputs),
        'validation': {'core_matches_release': True, 'requirements_satisfied': True,
                       'durable_materialization_verified': True},
        'ownership_effects': claim.inputs['ownership_effects'],
        'risk_disclosure': upgrade_risk_disclosure(),
    }


def test_qualified_upgrade_capture_authority_has_no_single_requirement(publisher):
    # Regression: the qualified path must not dereference inputs['requirement']
    # for an upgrade candidate, which has no single requirement.
    with publisher.engine.begin() as connection:
        baseline = connection.scalar(select(publisher.tables['environment_state'].c.snapshot))
    service = adapter.QualifiedPublication(publisher.engine, publisher.authorize, publisher.project,
                                           'test_', baseline)
    inputs = {'baseline': baseline, 'change_operation': 'upgrade', 'ownership_effects': {},
              'contributions': []}
    with publisher.engine.begin() as connection:
        service._capture_approval_authority(connection, '1', inputs)
    assert 'approval_authority' not in inputs


def test_upgrade_prepare_and_preview_do_not_mutate_runtime_or_intent(protocol):
    before = snapshot(protocol)
    candidate = protocol.prepare('admin', None, 'upgrade-key', operation='upgrade',
                                 release_selection={'mode': 'exact', 'version': RELEASE})
    after = snapshot(protocol)
    assert after['environment_state'] == before['environment_state']
    assert after['installation_attempt'] == []
    assert after['revision'] == before['revision']
    status = protocol.status('admin', candidate)
    assert status['state'] == 'queued' and status['stale'] is False
    assert status['intent_change']['operation'] == 'upgrade'
    assert status['intent_change']['requirement'] is None
    assert status['intent_change']['release_selection'] == {'mode': 'exact', 'version': RELEASE}
    assert status['approval_supported'] is False and status['installation_requested'] is False
    assert protocol.prepare('admin', None, 'upgrade-key', operation='upgrade',
                            release_selection={'mode': 'exact', 'version': RELEASE}) == candidate
    with pytest.raises(PreparationError, match='idempotency key body mismatch'):
        protocol.prepare('admin', None, 'upgrade-key', operation='upgrade',
                         release_selection={'mode': 'exact', 'version': '2.1.0'})
    claim = protocol.claim(candidate)
    assert protocol.publish_ready(claim, upgrade_result(claim))
    assert protocol.status('admin', candidate)['preview']['risk_disclosure'] == upgrade_risk_disclosure()
    preview = protocol.status('admin', candidate)['preview']
    assert preview['release']['webapp'] == RELEASE and preview['core_bundle']['digest'] == 'a' * 64
    assert snapshot(protocol)['environment_state'] == before['environment_state']


def _set_core(protocol, core):
    with protocol.engine.begin() as connection:
        table = protocol.tables['environment_state']
        state = connection.execute(select(table)).mappings().one()
        raised = dict(state['snapshot'], core=core)
        connection.execute(update(table).values(snapshot=raised))


def test_upgrade_rejects_incomplete_selection_and_declaration_edits(protocol):
    with pytest.raises(PreparationError, match='registry release selection'):
        protocol.prepare('admin', None, 'bad', operation='upgrade', release_selection={})
    with pytest.raises(PreparationError, match='upgrade accepts no declaration'):
        protocol.prepare('admin', 'Example>=1', 'bad', operation='upgrade',
                         release_selection={'mode': 'latest-stable'})
    with pytest.raises(PreparationError, match='upgrade accepts no declaration'):
        protocol.prepare('admin', None, 'bad', operation='upgrade', assign_owner=True,
                         release_selection={'mode': 'latest-stable'})


def test_upgrade_rejects_fresh_browser_selected_downgrade(protocol):
    _set_core(protocol, {'docassemble-webapp': {'version': '3.0.0', 'source_digest': 'a' * 64}})
    with pytest.raises(PreparationError, match='fresh downgrade is unsupported'):
        protocol.prepare('admin', None, 'down', operation='upgrade',
                         release_selection={'mode': 'exact', 'version': RELEASE})
    with pytest.raises(PreparationError, match='prereleases require explicit'):
        protocol.prepare('admin', None, 'pre', operation='upgrade',
                         release_selection={'mode': 'exact', 'version': '3.0.0b1'})


def test_upgrade_requires_normally_verified_runtime(protocol):
    with protocol.engine.begin() as connection:
        table = protocol.tables['environment_state']
        state = connection.execute(select(table)).mappings().one()
        connection.execute(update(table).values(snapshot=dict(state['snapshot'],
                         runtime={'kind': 'core-only', 'inventory': {}})))
    with pytest.raises(PreparationError, match='normally verified runtime'):
        protocol.prepare('admin', None, 'recovery', operation='upgrade',
                         release_selection={'mode': 'latest-stable'})


def test_upgrade_incoherent_result_fails_closed_without_publication(protocol):
    candidate = protocol.prepare('admin', None, 'conflict', operation='upgrade',
                                 release_selection={'mode': 'exact', 'version': RELEASE})
    claim = protocol.claim(candidate)
    with pytest.raises(PreparationError, match='incomplete or invalid ready result'):
        protocol.publish_ready(claim, upgrade_result(claim, incoherent=True))
    assert protocol.status('admin', candidate)['state'] == 'preparing'
    assert snapshot(protocol)['installation_attempt'] == []


def qualified(publisher, baseline):
    return adapter.QualifiedPublication(publisher.engine, publisher.authorize, publisher.project, 'test_', baseline)


def upgrade_ready(service, publisher, key, *, actor='2', release=RELEASE):
    candidate = service.prepare(actor, None, key, operation='upgrade',
                                release_selection={'mode': 'exact', 'version': release})
    claim = service.claim(candidate)
    assert service.publish_ready(claim, upgrade_result(claim))
    preview = service.status(actor, candidate)
    assert preview['approval_supported'] and preview['state'] == 'ready' and not preview['stale']
    return candidate, preview['result_digest']


def test_upgrade_approval_publishes_without_intent_or_grant_change(publisher):
    # 14b completes 14a's preview-only upgrade: a real admin approval publishes an
    # immutable upgrade revision and occupies the slot without projecting intent.
    with publisher.engine.begin() as connection:
        baseline = connection.scalar(select(publisher.tables['environment_state'].c.snapshot))
        before = snapshot(publisher)
    service = qualified(publisher, baseline)
    candidate, digest = upgrade_ready(service, publisher, 'upgrade-approve')
    preview = service.status('2', candidate)
    assert preview['intent_change']['operation'] == 'upgrade'
    assert preview['intent_change']['requirement'] is None
    assert preview['intent_change']['release_selection'] == {'mode': 'exact', 'version': RELEASE}
    response = service.approve('2', candidate, digest, 'upgrade-approve')
    after = snapshot(publisher)
    assert response['installation'] == 'pending'
    assert after['environment_state'][0]['active_attempt_id'] == response['attempt_id']
    assert after['environment_state'][0]['desired_revision_id'] == response['revision_id']
    revision = next(row for row in after['revision'] if row['id'] == response['revision_id'])
    assert revision['snapshot']['publication']['result']['release']['webapp'] == RELEASE
    assert revision['snapshot']['publication']['result']['core_bundle']['digest'] == 'a' * 64
    assert revision['snapshot']['publication']['result_digest'] == digest
    selected = revision['snapshot']['selected_baseline']
    assert selected == {key: revision['snapshot']['publication']['result'][key] for key in ('core_bundle', 'release')}
    assert revision['snapshot']['core'] == selected['core_bundle']['inventory']
    assert revision['snapshot']['recovery_baseline'] == {
        'image_baseline': baseline.get('image_baseline'), 'core': baseline['core']}
    assert after['environment_state'][0]['snapshot'] == revision['snapshot']
    from docassemble_executor.installation import Installation
    installation = Installation(publisher.engine, lambda *_: False, 'test_')
    claimed = installation.claim_installation('upgrade-host')
    assert claimed.snapshot['core'] == selected['core_bundle']['inventory']
    # Desired intent, identities and grants are untouched (no ownership transfer).
    assert before['requirement_contribution'] == after['requirement_contribution']
    assert before['package_identity'] == after['package_identity']
    with publisher.engine.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM test_package')).scalar() == 0
        assert connection.execute(text('SELECT count(*) FROM test_package_auth')).scalar() == 0
    # Published upgrade revisions now support restart/publication status and retry
    # projection (14b), rather than rejecting the operation as unsupported.
    status = service.publication_status('2', revision_id=response['revision_id'])
    assert status['revision_id'] == response['revision_id']
    assert status['candidate_id'] == candidate


def test_upgrade_approval_requires_requester_admin_and_fresh_inputs(publisher):
    with publisher.engine.begin() as connection:
        baseline = connection.scalar(select(publisher.tables['environment_state'].c.snapshot))
    service = qualified(publisher, baseline)
    candidate, digest = upgrade_ready(service, publisher, 'upgrade-owner', actor='2')
    before = snapshot(publisher)
    with pytest.raises(PreparationError, match='forbidden'):
        service.approve('1', candidate, digest, 'upgrade-owner')  # developer, not admin requester
    assert snapshot(publisher) == before
    # A generation change (change-away/change-back race) invalidates a fresh approval.
    with publisher.authority_write() as connection:
        connection.execute(update(publisher.tables['environment_state']).values(target_generation=2))
    before_stale = snapshot(publisher)
    with pytest.raises(PreparationError, match='stale'):
        service.approve('2', candidate, digest, 'upgrade-owner')
    assert snapshot(publisher) == before_stale
    assert before_stale['installation_attempt'] == []
