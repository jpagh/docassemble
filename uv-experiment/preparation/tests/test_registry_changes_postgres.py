"""Ticket 06 module-level contributions and actual SQL authority/projection."""
# ruff: noqa: F811 -- imported pytest fixtures are injected by name
from copy import deepcopy

import pytest
from sqlalchemy import text, update

from docassemble.base.managed_preparation import PreparationError
from test_preparation_postgres import protocol, result, snapshot  # noqa: F401
from test_publication_writers_postgres import publisher, intent  # noqa: F401


def seed(publisher):
    with publisher.authority_write() as c:
        package = c.scalar(text("INSERT INTO test_package (name,type,active,core,limitation) VALUES ('Example_Pkg','pip',true,false,'>=1') RETURNING id"))
        c.execute(text("INSERT INTO test_package_auth (package_id,user_id,authtype) VALUES (:id,1,'owner')"), {'id': package})
        c.execute(publisher.tables['package_identity'].insert().values(id='example', normalized_name='example-pkg',
            display_name='Example_Pkg', owner_id='1', legacy_package_id=package))
        c.execute(publisher.tables['requirement_contribution'].insert(), [
            {'id': 'ui-one', 'identity_id': 'example', 'origin': 'interactive', 'declaration': 'Example_Pkg>=1', 'active': True},
            {'id': 'ui-two', 'identity_id': 'example', 'origin': 'interactive', 'declaration': 'example.pkg<4', 'active': True},
            {'id': 'config-one', 'identity_id': 'example', 'origin': 'config', 'declaration': 'EXAMPLE-pkg<5', 'active': True}])


def ready(publisher, operation, *, actor='1', contribution_id=None, key='prepare'):
    candidate = publisher.prepare(actor, 'eXaMpLe.Pkg', key, operation=operation, contribution_id=contribution_id)
    claim = publisher.claim(candidate)
    attestation = result(claim)
    attestation['bundle']['inventory']['example-pkg'] = attestation['bundle']['inventory'].pop('example')
    def diff(before):
        after = attestation['bundle']['inventory']
        return {name: {'before': before.get(name), 'after': after.get(name)}
                for name in sorted(set(before) | set(after)) if before.get(name) != after.get(name)}
    baseline = claim.inputs['baseline']
    attestation['desired_diff'] = diff(baseline['desired_inventory'])
    attestation['runtime_diff'] = diff(baseline['runtime']['inventory']) if baseline['runtime']['kind'] == 'normal' else None
    attestation['warnings'] = publisher.ready_warnings(claim.inputs)
    assert publisher.publish_ready(claim, attestation)
    return candidate, publisher.status(actor, candidate)['result_digest']


def test_nonowner_update_preserves_all_requirements_and_current_grants(publisher):
    seed(publisher)
    before = snapshot(publisher), intent(publisher)
    candidate = ready(publisher, 'update', actor='2')
    inputs = publisher.status('2', candidate[0])['intent_change']
    assert inputs['operation'] == 'update' and len(inputs['retained_contributions']) == 4
    response = publisher.approve('2', *candidate, 'approve')
    after = snapshot(publisher)
    assert after['requirement_contribution'] == before[0]['requirement_contribution']
    assert after['package_identity'] == before[0]['package_identity']
    assert intent(publisher) == before[1]
    assert response['installation'] == 'pending'
    assert publisher.approve('2', *candidate, 'approve') == response
    # Update permission is not permission to edit constraints or replace sources.
    for declaration in ('example-pkg>=2', 'example-pkg[extra]', 'example-pkg @ https://example.test/new.whl'):
        with pytest.raises(PreparationError):
            publisher.prepare('2', declaration, declaration, operation='update')
    with pytest.raises(PreparationError):
        publisher.prepare('2', 'example-pkg>=2', 'edit')
    with pytest.raises(PreparationError):
        publisher.prepare('2', 'example-pkg', 'remove-other', operation='remove', contribution_id='ui-one')


def test_removal_deactivates_only_authorized_contribution_and_reactivation_retains_identity(publisher):
    seed(publisher)
    before = snapshot(publisher), intent(publisher)
    candidate = ready(publisher, 'remove', contribution_id='ui-one')
    preview = publisher.status('1', candidate[0])
    assert preview['intent_change']['remove_contribution_id'] == 'ui-one'
    assert {row['id'] for row in preview['intent_change']['retained_contributions']} == {'ui-two', 'config-one', 'config-contribution'}
    publisher.approve('1', *candidate, 'remove')
    after = snapshot(publisher)
    changed = deepcopy(before[0]['requirement_contribution'])
    next(row for row in changed if row['id'] == 'ui-one')['active'] = False
    assert sorted(after['requirement_contribution'], key=lambda r: r['id']) == sorted(changed, key=lambda r: r['id'])
    assert after['package_identity'] == before[0]['package_identity'] and intent(publisher) == before[1]
    # Finish only through durable executor reporting, never a heartbeat unlock.
    finish(publisher)
    second = ready(publisher, 'remove', contribution_id='ui-two', key='second')
    publisher.approve('1', *second, 'second')
    assert not intent(publisher)[0][0]['active']
    assert intent(publisher)[1] == before[1][1]
    finish(publisher)
    new = publisher.prepare('1', 'Example_Pkg>=1', 'reactivate')
    claim = publisher.claim(new)
    attestation = result(claim)
    attestation['bundle']['inventory'] = snapshot(publisher)['environment_state'][0]['snapshot']['desired_inventory']
    attestation['desired_diff'] = {}
    attestation['runtime_diff'] = None
    attestation['warnings'] = publisher.ready_warnings(claim.inputs)
    assert publisher.publish_ready(claim, attestation)
    publisher.approve('1', new, publisher.status('1', new)['result_digest'], 'reactivate')
    assert intent(publisher)[0][0]['active']
    assert intent(publisher)[1] == before[1][1]
    assert snapshot(publisher)['package_identity'] == before[0]['package_identity']


def finish(publisher, *, failed=False):
    from docassemble_executor.installation import Installation
    service = Installation(publisher.engine, publisher.authorize, 'test_')
    claim = service.claim_installation('host')
    terminal = {'identity': claim.identity, 'outcome': 'failed-stopped' if failed else 'materialized-stopped',
                'normal_verified': False, 'child_safe': True}
    if failed:
        terminal['failure'] = {'stage': 'sync', 'reason': 'sync-failed'}
    service.report_terminal(terminal)


@pytest.mark.parametrize('operation', ['update', 'remove'])
@pytest.mark.parametrize('block', ['busy', 'runtime_generation', 'input_generation', 'target_generation'])
def test_busy_and_stale_approval_leave_all_intent_unchanged(publisher, operation, block):
    seed(publisher)
    candidate = ready(publisher, operation, contribution_id='ui-one' if operation == 'remove' else None)
    with publisher.authority_write() as c:
        if block == 'busy':
            c.execute(publisher.tables['installation_attempt'].insert().values(id='busy', revision_id='baseline', record={}))
            c.execute(update(publisher.tables['environment_state']).values(active_attempt_id='busy'))
        else:
            c.execute(update(publisher.tables['environment_state']).values(**{block: 2}))
    before = snapshot(publisher), intent(publisher)
    with pytest.raises(PreparationError, match='busy' if block == 'busy' else 'stale'):
        publisher.approve('1', *candidate, 'approve')
    assert (snapshot(publisher), intent(publisher)) == before
    if block == 'busy':
        assert publisher.status('1', candidate[0])['state'] == 'ready'


@pytest.mark.parametrize('operation', ['update', 'remove'])
def test_failed_apply_keeps_published_desired_intent(publisher, operation):
    seed(publisher)
    candidate = ready(publisher, operation, contribution_id='ui-one' if operation == 'remove' else None)
    published = publisher.approve('1', *candidate, 'approve')
    before = snapshot(publisher), intent(publisher)
    finish(publisher, failed=True)
    after = snapshot(publisher)
    assert after['environment_state'][0]['desired_revision_id'] == published['revision_id']
    for key in ('revision', 'requirement_contribution', 'package_identity'):
        assert after[key] == before[0][key]
    assert intent(publisher) == before[1]
    assert after['environment_state'][0]['active_attempt_id'] is None
    assert after['installation_attempt'][-1]['record']['phase'] == 'failed'


@pytest.mark.parametrize('operation', ['update', 'remove'])
def test_current_grant_revocation_rejects_without_partial_publication(publisher, operation):
    seed(publisher)
    candidate = ready(publisher, operation, contribution_id='ui-one' if operation == 'remove' else None)
    with publisher.authority_write() as c:
        c.execute(text('DELETE FROM test_package_auth'))
    before = snapshot(publisher), intent(publisher)
    with pytest.raises(PreparationError):
        publisher.approve('1', *candidate, 'approve')
    assert (snapshot(publisher), intent(publisher)) == before


@pytest.mark.parametrize('operation', ['update', 'remove'])
def test_update_remove_cannot_change_exact_core_or_select_missing_identity(publisher, operation):
    before = snapshot(publisher), intent(publisher)
    with pytest.raises(PreparationError):
        publisher.prepare('1', 'missing', 'missing', operation=operation,
                          contribution_id='missing' if operation == 'remove' else None)
    assert (snapshot(publisher), intent(publisher)) == before
    seed(publisher)
    with publisher.authority_write() as c:
        environment = publisher._environment(c)
        state = deepcopy(environment['snapshot'])
        state['core']['example-pkg'] = {'version': '1', 'source_digest': 'a' * 64}
        c.execute(update(publisher.tables['environment_state']).values(snapshot=state))
    before = snapshot(publisher), intent(publisher)
    with pytest.raises(PreparationError, match='exact core'):
        publisher.prepare('1', 'Example_Pkg', 'core', operation=operation,
                          contribution_id='ui-one' if operation == 'remove' else None)
    assert (snapshot(publisher), intent(publisher)) == before


@pytest.mark.parametrize('contribution', ['config-one', 'config-contribution', 'missing'])
def test_remove_cannot_edit_configuration_or_unrelated_contributor(publisher, contribution):
    seed(publisher)
    before = snapshot(publisher), intent(publisher)
    with pytest.raises(PreparationError):
        publisher.prepare('1', 'example-pkg', 'remove', operation='remove', contribution_id=contribution)
    assert (snapshot(publisher), intent(publisher)) == before
