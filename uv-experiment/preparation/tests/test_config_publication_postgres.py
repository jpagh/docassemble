"""08b2 real PostgreSQL config approval/projection, both schema creation paths."""
# ruff: noqa: F811 -- imported pytest fixture registration.
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from docassemble.base.managed_preparation import PreparationError
from test_preparation_postgres import protocol, snapshot  # noqa: F401
from test_publication_writers_postgres import publisher, adapter, intent  # noqa: F401
from test_config_import_postgres import prepare, config_result
from test_publication_postgres import wait_for_approval_lock


@pytest.fixture
def config_publisher(publisher):
    # The accepted protocol fixture's config contribution is a managed identity,
    # not an unlinked legacy Package eligible for implicit adoption.
    with publisher.engine.begin() as connection:
        connection.execute(text("INSERT INTO test_package (id,name,type,core,active,dependency) "
                                "VALUES (10,'Separate','pip',false,false,false)"))
        connection.execute(update(publisher.tables['package_identity']).values(
            legacy_package_id=10, owner_id=None))
        baseline = connection.scalar(select(publisher.tables['environment_state'].c.snapshot))
        baseline['config'] = {'index_url': 'https://pypi.org/simple'}
        connection.execute(update(publisher.tables['environment_state']).values(snapshot=baseline))
    return adapter.QualifiedPublication(publisher.engine, publisher.authorize, publisher.project, 'test_', baseline)


def ready(service, declarations=None, key='config', configuration=None):
    if configuration is None:
        configuration = {'python packages': declarations if declarations is not None else ['Example>=2']}
    service.observe_config(configuration, environ={})
    candidate = prepare(service, key=key)
    claim = service.claim(candidate)
    result = config_result(claim)
    result['warnings'] = service.ready_warnings(claim.inputs)
    inventory = result['bundle']['inventory']
    def diff(before):
        return {name: {'before': before.get(name), 'after': inventory.get(name)}
                for name in sorted(set(before) | set(inventory)) if before.get(name) != inventory.get(name)}
    result.update(desired_diff=diff(claim.inputs['baseline']['desired_inventory']),
                  runtime_diff=diff(claim.inputs['baseline']['runtime']['inventory']))
    assert service.publish_ready(claim, result)
    return candidate, service.status('2', candidate)['result_digest']


def approve(service, candidate, key='approve', actor='2'):
    return service.approve(actor, *candidate, key)


def state(service):
    with service.engine.connect() as connection:
        candidates = [dict(row) for row in connection.execute(select(service.tables['candidate'])).mappings()]
        results = [dict(row) for row in connection.execute(select(service.tables['publication_result'])).mappings()]
    return snapshot(service), intent(service), candidates, results


def add_interactive(service):
    with service.engine.begin() as connection:
        connection.execute(text("INSERT INTO test_package (id,name,type,core,active,dependency,limitation) "
                                "VALUES (20,'Interactive','pip',false,true,false,'>=1')"))
        connection.execute(text("INSERT INTO test_package_auth (package_id,user_id,authtype) VALUES (20,1,'owner')"))
        connection.execute(service.tables['package_identity'].insert().values(id='interactive-identity',
            normalized_name='interactive', display_name='Interactive', legacy_package_id=20, owner_id='1'))
        connection.execute(service.tables['requirement_contribution'].insert().values(id='interactive-contribution',
            identity_id='interactive-identity', origin='interactive', declaration='Interactive[fast]>=1', active=True))


@pytest.mark.parametrize('change_back', [False, True])
def test_approval_rechecks_bound_observation_under_lock_never_rebases(config_publisher, change_back):
    service = config_publisher
    candidate = ready(service)
    service.observe_config({'python packages': ['Other']}, environ={})
    if change_back:
        service.observe_config({'python packages': ['Example>=2']}, environ={})
    before = state(service)
    with pytest.raises(PreparationError, match='stale candidate'):
        approve(service, candidate)
    assert state(service) == before


@pytest.mark.parametrize('revocation', ['admin', 'account', 'disabled', 'updates', 'api', 'requester'])
@pytest.mark.parametrize('replay', [False, True])
def test_current_admin_requester_and_authority_rechecked(config_publisher, revocation, replay):
    service = config_publisher
    candidate = ready(service)
    if replay:
        approve(service, candidate)
    actor = '2'
    with service.authority_write() as connection:
        if revocation == 'admin':
            connection.execute(text('DELETE FROM test_user_roles WHERE user_id=2'))
        elif revocation == 'account':
            connection.execute(text('UPDATE test_user SET active=false WHERE id=2'))
        elif revocation == 'disabled':
            connection.execute(text("UPDATE test_user SET social_id='disabled$two' WHERE id=2"))
        elif revocation == 'requester':
            connection.execute(text('INSERT INTO test_user_roles VALUES (1,2)'))
            actor = '1'
    if revocation == 'updates':
        service.authorize.policy = lambda: {'allowed': {}, 'allow_updates': False, 'developer_can_install': True}
    elif revocation == 'api':
        service.authorize.api_permission = lambda *args: False
    before = state(service)
    with pytest.raises(PreparationError, match='forbidden|only requester'):
        approve(service, candidate, actor=actor)
    assert state(service) == before


def test_base_publication_config_rechecks_admin_and_requester_even_permissive_callback(config_publisher):
    service = config_publisher
    candidate = ready(service)
    from docassemble.base.managed_publication import Publication
    base = Publication(service.engine, lambda *args: True, service.project, 'test_')
    with service.authority_write() as connection:
        connection.execute(text('INSERT INTO test_user_roles VALUES (1,2)'))
    with pytest.raises(PreparationError, match='only requester'):
        approve(base, candidate, actor='1')
    with service.authority_write() as connection:
        connection.execute(text('DELETE FROM test_user_roles WHERE user_id=2'))
    with pytest.raises(PreparationError, match='forbidden'):
        approve(base, candidate)


def test_multi_identity_ownerless_projection_preserves_interactive_and_transitive(config_publisher):
    service = config_publisher
    add_interactive(service)
    before = snapshot(service)
    interactive_package = next(row for row in intent(service)[0] if row['id'] == 20)
    grants = intent(service)[1]
    declarations = [' Separate[extra]>=1,<3; os_name == "posix" ', 'New_Package>=2', 'Interactive[config]<3']
    candidate = ready(service, declarations)
    assert service.status('2', candidate[0])['config_drift']['pending']
    response = approve(service, candidate)
    after = snapshot(service)
    packages, current_grants = intent(service)
    assert next(row for row in packages if row['id'] == 20) == interactive_package
    assert current_grants == grants
    for row in before['package_identity']:
        assert row in after['package_identity']
    assert next(row for row in before['requirement_contribution'] if row['origin'] == 'interactive') in after['requirement_contribution']
    config = [row for row in after['requirement_contribution'] if row['active'] and row['origin'] == 'config']
    assert sorted(row['declaration'] for row in config) == sorted(declarations)
    new_identity = next(row for row in after['package_identity'] if row['normalized_name'] == 'new-package')
    assert new_identity['owner_id'] is None
    new_package = next(row for row in packages if row['id'] == new_identity['legacy_package_id'])
    assert new_package['type'] == 'pip' and not new_package['active'] and new_package['limitation'] is None
    revision = next(row for row in after['revision'] if row['id'] == response['revision_id'])
    committed = revision['snapshot']
    assert committed['observed_config_generation'] == 1
    with service.engine.connect() as connection:
        assert committed['observed_config'] == connection.scalar(select(service.tables['observed_config'].c.snapshot))
    assert committed['publication']['result']['bundle']['inventory'] == committed['desired_inventory']
    assert committed['publication']['inputs']['config_import']['snapshot'] == committed['observed_config']
    assert service.publication_status('2', revision_id=response['revision_id'])['config_drift'] == {
        'pending': False, 'observed_generation': 1, 'committed_generation': 1}
    assert after['environment_state'][0]['publication_generation'] == 2
    assert after['environment_state'][0]['active_attempt_id'] == response['attempt_id']
    assert len(after['installation_attempt']) == 1
    with service.engine.begin() as connection:
        with pytest.raises(DBAPIError, match='immutable'):
            connection.execute(update(service.tables['revision']).where(
                service.tables['revision'].c.id == response['revision_id']).values(snapshot={}))
    # Later observed edits are pending, never silently included in the revision.
    service.observe_config({'python packages': []}, environ={})
    status = service.publication_status('2', revision_id=response['revision_id'])
    assert status['config_drift'] == {'pending': True, 'observed_generation': 2, 'committed_generation': 1}
    assert next(row for row in snapshot(service)['revision'] if row['id'] == response['revision_id']) == revision
    # Fixture-only completed-slot simulation: no executor or runtime is invoked.
    with service.authority_write() as connection:
        connection.execute(update(service.tables['environment_state']).values(active_attempt_id=None))
    second = ready(service, [], key='remove-config')
    approve(service, second, key='remove-config')
    removed = snapshot(service)
    assert all(not row['active'] for row in removed['requirement_contribution'] if row['origin'] == 'config')
    assert intent(service) == (packages, grants)
    # Removal does not remove a transitive package from the solved inventory.
    assert 'separate' in removed['environment_state'][0]['snapshot']['desired_inventory']
    with service.authority_write() as connection:
        connection.execute(update(service.tables['environment_state']).values(active_attempt_id=None))
    third = ready(service, declarations, key='reactivate')
    approve(service, third, key='reactivate')
    reactivated = snapshot(service)
    assert reactivated['package_identity'] == removed['package_identity']
    assert [row for row in reactivated['requirement_contribution'] if row['origin'] == 'config' and row['active']] == config
    assert intent(service) == (packages, grants)


@pytest.mark.parametrize('mutation', ['source', 'core', 'alias', 'identity', 'legacy', 'contribution'])
def test_current_affected_identity_source_state_fails_closed(config_publisher, mutation):
    service = config_publisher
    candidate = ready(service, ['Separate>=1'])
    with service.authority_write() as connection:
        if mutation == 'source':
            connection.execute(text("UPDATE test_package SET type='git' WHERE id=10"))
        elif mutation == 'core':
            connection.execute(text('UPDATE test_package SET core=true WHERE id=10'))
        elif mutation == 'alias':
            connection.execute(text("INSERT INTO test_package (id,name,type,core) VALUES (11,'SEPARATE','pip',false)"))
        elif mutation == 'identity':
            connection.execute(update(service.tables['package_identity']).values(legacy_package_id=999))
        elif mutation == 'legacy':
            connection.execute(text("INSERT INTO test_package (id,name,type,core) VALUES (11,'Example','pip',false)"))
    if mutation == 'legacy':
        candidate = ready(service, ['Example'], key='unlinked')
    elif mutation == 'contribution':
        with service.authority_write() as connection:
            connection.execute(update(service.tables['requirement_contribution']).values(declaration='Separate<9'))
    before = state(service)
    with pytest.raises(PreparationError, match='Package|contributions changed'):
        approve(service, candidate)
    assert state(service) == before


def test_current_grant_changes_are_preserved_without_single_name_authority_digest(config_publisher):
    service = config_publisher
    add_interactive(service)
    candidate = ready(service, ['Interactive<3', 'Separate>=1'])
    with service.authority_write() as connection:
        connection.execute(text("INSERT INTO test_package_auth (package_id,user_id,authtype) VALUES (10,1,'owner')"))
        connection.execute(text('DELETE FROM test_package_auth WHERE package_id=20'))
    before = intent(service)
    approve(service, candidate)
    assert intent(service) == before


def test_busy_slot_leaves_ready_and_acknowledgment_pending(config_publisher):
    service = config_publisher
    candidate = ready(service)
    with service.authority_write() as connection:
        connection.execute(service.tables['installation_attempt'].insert().values(id='busy', revision_id='baseline', record={}))
        connection.execute(update(service.tables['environment_state']).values(active_attempt_id='busy'))
    before = state(service)
    with pytest.raises(PreparationError, match='installation busy'):
        approve(service, candidate)
    assert state(service) == before
    status = service.status('2', candidate[0])
    assert status['state'] == 'ready' and status['config_drift']['pending']


def test_idempotent_replay_after_expiry_parent_advance_and_observation(config_publisher):
    service = config_publisher
    candidate = ready(service)
    response = approve(service, candidate)
    with service.authority_write() as connection:
        connection.execute(text('ALTER TABLE test_managed_candidate DISABLE TRIGGER USER'))
        connection.execute(text("UPDATE test_managed_candidate SET expires_at=clock_timestamp()-interval '1 second'"))
        connection.execute(text('ALTER TABLE test_managed_candidate ENABLE TRIGGER USER'))
        connection.execute(update(service.tables['environment_state']).values(publication_generation=99))
    service.observe_config({'python packages': ['Later']}, environ={})
    before = state(service)
    assert approve(service, candidate) == response
    with pytest.raises(PreparationError, match='body mismatch'):
        approve(service, (candidate[0], '0' * 64))
    assert state(service) == before


def test_projection_failure_rolls_back_every_intent_write(config_publisher):
    service = config_publisher
    candidate = ready(service, ['Separate>=1', 'New>=1'])
    project = service.project
    def failing(connection, actor, candidate):
        project(connection, actor, candidate)
        raise RuntimeError('projection failure after writes')
    service.project = failing
    before = state(service)
    with pytest.raises(RuntimeError, match='projection failure'):
        approve(service, candidate)
    assert state(service) == before
    assert service.status('2', candidate[0])['config_drift']['pending']


def test_observation_waits_for_config_publication_then_becomes_pending(config_publisher):
    service = config_publisher
    candidate = ready(service)
    entered, release = Event(), Event()
    pids = []
    project = service.project
    def blocking(connection, actor, candidate):
        pids.append(connection.scalar(text('SELECT pg_backend_pid()')))
        entered.set()
        assert release.wait(timeout=10)
        return project(connection, actor, candidate)
    service.project = blocking
    with ThreadPoolExecutor(2) as pool:
        publication = pool.submit(approve, service, candidate)
        assert entered.wait(timeout=10)
        observation = pool.submit(service.observe_config, {'python packages': ['Later']}, environ={})
        try:
            wait_for_approval_lock(service.engine, pids[0])
            assert not observation.done()
        finally:
            release.set()
        response = publication.result(timeout=10)
        assert observation.result(timeout=10)['generation'] == 2
    assert service.publication_status('2', revision_id=response['revision_id'])['config_drift'] == {
        'pending': True, 'observed_generation': 2, 'committed_generation': 1}


@pytest.mark.parametrize('explicit_operation', [False, True])
def test_authenticated_http_config_approve_and_safe_retry_operation_hint(config_publisher, monkeypatch, explicit_operation):
    from flask import Flask
    from flask_login import LoginManager, UserMixin, login_user
    service = config_publisher
    add_interactive(service)
    candidate = ready(service)
    monkeypatch.setattr(adapter, 'preview_service', lambda api=False: service)
    monkeypatch.setattr(adapter, 'managed_installation', lambda: True)
    app = Flask(__name__)
    app.secret_key = 'disposable-config-test'
    manager = LoginManager(app)
    class User(UserMixin):
        id = '2'
    manager.user_loader(lambda identity: User() if identity == '2' else None)
    @app.route('/login')
    def login():
        login_user(User())
        return 'ok'
    app.add_url_rule('/preview', view_func=adapter.preview_request, methods=['GET', 'POST'])
    client = app.test_client()
    assert client.get('/login').status_code == 200
    hint = {'operation': 'config'} if explicit_operation else {}
    other_hint = {} if explicit_operation else {'operation': 'config'}
    body = {'action': 'approve', 'candidate_id': candidate[0], 'result_digest': candidate[1],
            'idempotency_key': 'http-approve'}
    reply = client.post('/preview', json={**body, **hint})
    assert reply.status_code == 202
    published = reply.json
    replay = client.post('/preview', json={**body, **other_hint})
    assert replay.status_code == 202 and replay.json == published
    with service.engine.connect() as connection:
        revision = connection.scalar(select(service.tables['revision'].c.snapshot).where(
            service.tables['revision'].c.id == published['revision_id']))
    assert revision['observed_config_generation'] == 1
    assert revision['publication']['inputs']['change_operation'] == 'config'
    # Only the already-qualified aborted-before-sync path may free a retry slot.
    # Fixture terminal reporting here does not claim a live installation.
    with service.authority_write() as connection:
        attempts = service.tables['installation_attempt']
        record = connection.scalar(select(attempts.c.record).where(attempts.c.id == published['attempt_id']))
        connection.execute(update(attempts).where(attempts.c.id == published['attempt_id']).values(
            record={**record, 'phase': 'aborted', 'terminal': {'outcome': 'aborted-before-sync',
                'mutation_started': False, 'child_safe': False, 'normal_verified': False,
                'failure': {'stage': 'drain', 'reason': 'drain-aborted'}}}))
        connection.execute(update(service.tables['environment_state']).values(active_attempt_id=None))
    before = snapshot(service), intent(service)
    body = {'action': 'retry', 'revision_id': published['revision_id'], 'idempotency_key': 'http-retry'}
    reply = client.post('/preview', json={**body, **hint})
    assert reply.status_code == 202
    retried = reply.json
    assert retried['revision_id'] == published['revision_id'] and retried['attempt_id'] != published['attempt_id']
    replay = client.post('/preview', json={**body, **other_hint})
    assert replay.status_code == 202 and replay.json == retried
    after = snapshot(service)
    assert intent(service) == before[1]
    for name in ('revision', 'package_identity', 'requirement_contribution'):
        assert after[name] == before[0][name]
    assert len(after['installation_attempt']) == 2
    assert after['environment_state'][0]['active_attempt_id'] == retried['attempt_id']


PRIVATE_INDEX = 'https://packages.invalid/private/simple/'


def private_configuration(*, tls=(), name='Example'):
    configuration = {'python packages': [name + '>=2'],
                     'private indexes': {'alpha': PRIVATE_INDEX},
                     'private bindings': {name.lower(): 'alpha'}}
    if tls:
        configuration['private tls exceptions'] = list(tls)
    return configuration


def private_publisher(publisher, configuration):
    """Qualified publisher whose operator baseline carries the matching private policy."""
    from docassemble.base.managed_config import config_import_policy, dependency_snapshot
    observed = dependency_snapshot(configuration, environ={}, image_config={'index_url': 'https://pypi.org/simple'})
    policy = config_import_policy(observed, {'index_url': 'https://pypi.org/simple'})
    with publisher.engine.begin() as connection:
        connection.execute(text("INSERT INTO test_package (id,name,type,core,active,dependency) "
                                "VALUES (10,'Separate','pip',false,false,false)"))
        connection.execute(update(publisher.tables['package_identity']).values(
            legacy_package_id=10, owner_id=None))
        baseline = connection.scalar(select(publisher.tables['environment_state'].c.snapshot))
        baseline['config'] = policy
        connection.execute(update(publisher.tables['environment_state']).values(snapshot=baseline))
    return adapter.QualifiedPublication(publisher.engine, publisher.authorize, publisher.project, 'test_', baseline)


def test_private_config_import_commits_ordering_bindings_and_tls_exception(publisher):
    service = private_publisher(publisher, private_configuration(tls=('alpha',)))
    candidate = ready(service, key='private', configuration=private_configuration(tls=('alpha',)))
    status = service.status('2', candidate[0])
    assert status['preview']['warnings'] == ['private-insecure-tls:alpha', 'private-source:example:alpha']
    assert status['intent_change']['config_diff']['source_policy'] == {
        'changed': False, 'supported_public_registry': False,
        'private_index_order': ['alpha'], 'private_bindings': {'example': 'alpha'},
        'cross_index_newest_version': False, 'insecure_tls_exceptions': ['alpha']}
    response = approve(service, candidate, key='private-approve')
    revision = next(row for row in snapshot(service)['revision'] if row['id'] == response['revision_id'])
    assert revision['snapshot']['config'] == {
        'private_indexes': {'alpha': PRIVATE_INDEX},
        'private_bindings': {'example': 'alpha'},
        'private_tls_exceptions': ['alpha']}
    assert revision['snapshot']['observed_config']['private_policy'] == {
        'order': ['alpha'], 'indexes': {'alpha': PRIVATE_INDEX},
        'bindings': {'example': 'alpha'}, 'tls_exceptions': ['alpha']}
    publication = service.publication_status('2', revision_id=response['revision_id'])
    assert PRIVATE_INDEX not in repr(publication)
    assert PRIVATE_INDEX not in repr(status)


def test_private_config_import_public_namesake_binds_only_private(publisher):
    service = private_publisher(publisher, private_configuration(name='Six'))
    candidate = ready(service, key='namesake', configuration=private_configuration(name='Six'))
    source = service.status('2', candidate[0])['intent_change']['config_diff']['source_policy']
    assert source['supported_public_registry'] is False
    assert source['private_bindings'] == {'six': 'alpha'}
    assert source['cross_index_newest_version'] is False
    response = approve(service, candidate, key='namesake-approve')
    revision = next(row for row in snapshot(service)['revision'] if row['id'] == response['revision_id'])
    assert revision['snapshot']['config'] == {'private_indexes': {'alpha': PRIVATE_INDEX},
                                              'private_bindings': {'six': 'alpha'}}
    assert revision['snapshot']['observed_config']['private_policy']['order'] == ['alpha']
