"""Explicit observed-snapshot prepare/preview, real PostgreSQL."""
# ruff: noqa: F811 -- imported pytest fixture registration.
from copy import deepcopy

import pytest
from sqlalchemy import select, text, update

from docassemble.base.managed_preparation import Preparation, PreparationError
from docassemble.base.managed_publication import Publication
from test_preparation_postgres import protocol, result, snapshot  # noqa: F401
from test_preview_adapters_postgres import http, adapter  # noqa: F401


@pytest.fixture
def importer(http):
    _, service, _, _ = http
    return Publication(service.engine, service.authorize, lambda *args: pytest.fail('no projection'), 'test_')


def prepare(service, key='config', actor='2', **kwargs):
    return service.prepare(actor, None, key, operation='config', **kwargs)


def candidates(service):
    with service.engine.connect() as connection:
        return [dict(row) for row in connection.execute(select(service.tables['candidate'])).mappings()]


def config_result(claim):
    ready = result(claim)
    ready['config_diff'] = Preparation.config_diff(claim.inputs)
    if claim.inputs['baseline']['runtime']['kind'] != 'normal':
        ready.update(runtime_diff=None, warnings=['runtime-not-normally-verified'])
    return ready


def test_import_requires_observation_without_partial_candidate(importer):
    before = snapshot(importer)
    with pytest.raises(PreparationError, match='requires an observed configuration'):
        prepare(importer)
    assert candidates(importer) == []
    assert snapshot(importer) == before


@pytest.mark.parametrize('actor', ['1', '3', 'alice', None])
def test_import_admin_only_not_developer_or_package_permission(importer, actor):
    importer.observe_config({'python packages': ['Example']}, environ={})
    with pytest.raises(PreparationError, match='forbidden'):
        prepare(importer, actor=actor)
    assert candidates(importer) == []


def test_base_import_checks_current_sql_admin_even_with_permissive_authorizer(importer):
    service = Preparation(importer.engine, lambda *args: True, 'test_')
    importer.observe_config({}, environ={})
    for actor in ('1', '3', 'alice', None):
        with pytest.raises(PreparationError, match='forbidden'):
            prepare(service, actor=actor)
    assert prepare(service)
    with service.engine.begin() as connection:
        connection.execute(text('DELETE FROM test_user_roles WHERE user_id=2'))
    with pytest.raises(PreparationError, match='forbidden'):
        prepare(service, key='after-revocation')
    assert len(candidates(service)) == 1


@pytest.mark.parametrize('revocation', ['updates', 'account', 'api-permission'])
def test_import_also_obeys_existing_current_authorizer(importer, http, revocation):
    _, _, policy, key = http
    importer.observe_config({}, environ={})
    if revocation == 'updates':
        policy['allow_updates'] = False
    elif revocation == 'account':
        with importer.engine.begin() as connection:
            connection.execute(text('UPDATE test_user SET active=false WHERE id=2'))
    else:
        key['permissions'] = ['read_packages']
    with pytest.raises(PreparationError, match='forbidden'):
        prepare(importer)
    assert candidates(importer) == []


@pytest.mark.parametrize('values', [{'declaration': 'Example'}, {'declaration': ''},
    {'assign_owner': True}, {'contribution_id': 'config-contribution'}, {'restart': False}])
def test_import_rejects_declaration_and_interactive_options(importer, values):
    values = dict(values)
    declaration = values.pop('declaration', None)
    with pytest.raises(PreparationError, match='accepts no declaration'):
        importer.prepare('2', declaration, 'bad', operation='config', **values)
    assert candidates(importer) == []


@pytest.mark.parametrize('declaration', ['CORE', 'core>=1', 'Core[extra]; os_name == "never"'])
def test_import_rejects_exact_core_even_inactive_marker(importer, declaration):
    importer.observe_config({'python packages': [declaration]}, environ={})
    before = snapshot(importer)
    with pytest.raises(PreparationError, match='cannot change exact core'):
        prepare(importer)
    assert candidates(importer) == []
    assert snapshot(importer) == before


@pytest.mark.parametrize('source,artifact', [
    ('Example @ https://user:SECRET@source.invalid/example.whl', None),
    ('Example @ git+https://source.invalid/repo', None), ('Example>=1', 'artifact-id')])
def test_import_rejects_conflicting_nonconfig_sources(importer, source, artifact):
    with importer.engine.begin() as connection:
        connection.execute(importer.tables['package_identity'].insert().values(
            id='example', normalized_name='example', display_name='Example', owner_id='1'))
        connection.execute(importer.tables['requirement_contribution'].insert().values(
            id='interactive', identity_id='example', origin='interactive', declaration=source,
            active=True, artifact_ref=artifact))
    importer.observe_config({'python packages': ['EXAMPLE<3']}, environ={})
    before = snapshot(importer)
    with pytest.raises(PreparationError, match='conflicting configuration sources require explicit resolution') as error:
        prepare(importer)
    assert 'SECRET' not in str(error.value)
    assert candidates(importer) == []
    assert snapshot(importer) == before


@pytest.mark.parametrize('empty', [False, True])
def test_import_preserves_interactive_and_exact_combined_declarations(importer, empty):
    interactive = 'Separate[fast]>=1; python_version >= "3.10"'
    with importer.engine.begin() as connection:
        connection.execute(importer.tables['requirement_contribution'].insert().values(
            id='interactive', identity_id='config-package', origin='interactive', declaration=interactive, active=True))
    declarations = [] if empty else [' Separate[extra] <3; os_name == "posix" ', 'New_Package[fast]>=2,<4']
    observed = importer.observe_config({'python packages': declarations}, environ={})
    before = snapshot(importer)
    first = prepare(importer)
    claim = importer.claim(first)
    combined = claim.inputs['contributions']
    kept, = [row for row in combined if row['origin'] != 'config']
    assert kept == next(row for row in before['requirement_contribution'] if row['id'] == 'interactive')
    config = [row for row in combined if row['origin'] == 'config']
    assert [row['declaration'] for row in config] == declarations
    assert not any(row['id'] == 'config-contribution' for row in combined)
    if not empty:
        assert config[0]['identity_id'] == 'config-package'
        assert config[0]['provenance'] == claim.inputs['config_import']['snapshot']['requirements'][0]
        assert config[0]['provenance']['extras'] == ['extra']
        assert config[0]['provenance']['marker'] == 'os_name == "posix"'
    assert claim.inputs['config_import']['generation'] == observed['generation']
    assert claim.inputs['config_import']['digest'] == observed['digest']
    with importer.engine.connect() as connection:
        bound = connection.scalar(select(importer.tables['observed_config'].c.snapshot))
    assert claim.inputs['config_import']['snapshot'] == bound
    assert claim.inputs['baseline'] == before['environment_state'][0]['snapshot']
    assert 'requirement' not in claim.inputs
    assert claim.inputs['ownership_effects'] == {}
    assert prepare(importer) == first
    second = importer.claim(prepare(importer, key='fresh'))
    assert second.inputs['contributions'] == combined
    assert snapshot(importer) == before


def test_import_preserves_environment_input_provenance(importer):
    importer.observe_config({}, environ={'PYTHONPACKAGES': 'Example>=1,<3; python_version >= "3.10"'})
    claim = importer.claim(prepare(importer))
    row, = claim.inputs['contributions']
    assert row['provenance']['origin'] == 'PYTHONPACKAGES'
    assert row['declaration'] == 'Example>=1,<3; python_version >= "3.10"'


@pytest.mark.parametrize('kind', ['normal', 'unknown', 'core-only'])
def test_import_preview_and_result_validation_without_single_requirement(importer, kind):
    with importer.engine.begin() as connection:
        environment = importer.tables['environment_state']
        baseline = deepcopy(connection.scalar(select(environment.c.snapshot)))
        baseline['runtime']['kind'] = kind
        connection.execute(update(environment).values(snapshot=baseline))
    importer.observe_config({'python packages': ['Example>=2']}, environ={})
    before = snapshot(importer)
    candidate = prepare(importer)
    claim = importer.claim(candidate)
    prepared = config_result(claim)
    bad = deepcopy(prepared)
    bad['config_diff']['requirements']['after'] = ['NotPreviewed']
    with pytest.raises(PreparationError, match='invalid ready result'):
        importer.publish_ready(claim, bad)
    assert importer.publish_ready(claim, prepared)
    status = importer.status('2', candidate)
    assert not status['stale'] and not status['approval_supported']
    assert status['preview'] == prepared
    assert status['preview']['config_diff'] == {
        'requirements': {'before': ['Separate[extra]<2; os_name == "posix"'], 'after': ['Example>=2']},
        'source_policy': {'changed': True, 'supported_public_registry': True}}
    assert status['intent_change']['config_import'] == {
        'generation': claim.inputs['config_import']['generation'], 'digest': claim.inputs['config_import']['digest']}
    assert status['intent_change']['requirement'] is None
    assert status['preview']['runtime_diff'] == (prepared['desired_diff'] if kind == 'normal' else None)
    assert status['preview']['warnings'] == ([] if kind == 'normal' else ['runtime-not-normally-verified'])
    assert snapshot(importer) == before


@pytest.mark.parametrize('during_solve', [False, True])
def test_import_new_observation_and_change_back_invalidates_not_rebases(importer, during_solve):
    original = importer.observe_config({'python packages': ['Example>=2']}, environ={})
    candidate = prepare(importer)
    claim = importer.claim(candidate)
    if not during_solve:
        assert importer.publish_ready(claim, config_result(claim))
    importer.observe_config({'python packages': ['Other']}, environ={})
    back = importer.observe_config({'python packages': ['Example>=2']}, environ={})
    assert back['digest'] == original['digest']
    assert back['generation'] == original['generation'] + 2
    status = importer.status('2', candidate)
    assert status['stale'] and status['preview'] is None and 'stale-preview' in status['warnings']
    assert prepare(importer) == candidate  # Replay never refreshes the bound generation.
    assert candidates(importer)[0]['inputs']['config_import']['generation'] == original['generation']
    if during_solve:
        assert not importer.publish_ready(claim, config_result(claim))
        assert importer.status('2', candidate)['state'] == 'failed'
    fresh = prepare(importer, key='new')
    assert not importer.status('2', fresh)['stale']
    assert importer.claim(fresh).inputs['config_import']['generation'] == back['generation']


@pytest.mark.parametrize('configuration', [
    {'python packages': ['Good', '-r SECRET']},
    {'python packages': ['Good', 'Example @ https://SECRET@source.invalid/a.whl']},
    {'python packages': ['arn:aws:secretsmanager:SECRET']},
    {'python packages': ['Example; os_name == "@Microsoft.KeyVault(SECRET)"']},
    {'pip index url': '@Microsoft.KeyVault(SECRET)'},
])
def test_import_unsupported_or_dependency_secret_fails_closed(importer, configuration):
    with pytest.raises(PreparationError) as error:
        importer.observe_config(configuration, environ={})
    assert 'SECRET' not in str(error.value)
    with pytest.raises(PreparationError, match='requires an observed configuration'):
        prepare(importer)
    assert candidates(importer) == []


@pytest.mark.parametrize('policy', [
    {'pip index url': 'https://user:SECRET@private.invalid/simple'},
    {'pip extra index urls': ['https://SECRET@private.invalid/simple']},
    {'pip trusted host': 'private.invalid'},
])
def test_import_unsupported_source_policy_rejected_without_partial_candidate_or_url_echo(importer, policy):
    importer.observe_config({'python packages': ['Example'], **policy}, environ={})
    with pytest.raises(PreparationError, match='public PyPI only') as error:
        prepare(importer)
    assert 'SECRET' not in str(error.value) and 'private.invalid' not in str(error.value)
    assert candidates(importer) == []


def test_qualified_bridge_config_preview_supports_current_admin_approval(importer):
    importer.observe_config({}, environ={})
    with importer.engine.connect() as connection:
        baseline = connection.scalar(select(importer.tables['environment_state'].c.snapshot))
    # The qualified source policy is compared effectively: a public bridge baseline
    # must carry the public PyPI policy a committed public import would produce.
    baseline = deepcopy(baseline)
    baseline['config'] = {'index_url': 'https://pypi.org/simple'}
    service = adapter.QualifiedPublication(importer.engine, importer.authorize, importer.project, 'test_', baseline)
    candidate = prepare(service)
    claim = service.claim(candidate)
    assert service.publish_ready(claim, config_result(claim))
    assert service.status('2', candidate)['approval_supported']


@pytest.mark.parametrize('field', ['private_indexes', 'private_bindings'])
def test_import_rejects_committed_private_policy_without_public_fallback(importer, field):
    with importer.engine.begin() as connection:
        environment = importer.tables['environment_state']
        baseline = deepcopy(connection.scalar(select(environment.c.snapshot)))
        baseline['config'][field] = {}
        connection.execute(update(environment).values(snapshot=baseline))
    importer.observe_config({'python packages': ['Example']}, environ={})
    before = snapshot(importer)
    with pytest.raises(PreparationError, match='private source policy unsupported'):
        prepare(importer)
    assert candidates(importer) == []
    assert snapshot(importer) == before


PRIVATE_INDEX = 'https://packages.invalid/private/simple/'
PRIVATE_SECOND = 'https://packages.invalid/other/simple/'


def private_configuration(order=('alpha', 'beta')):
    urls = {'alpha': PRIVATE_INDEX, 'beta': PRIVATE_SECOND}
    return {'python packages': ['Example>=2'],
            'private indexes': {label: urls[label] for label in order},
            'private bindings': {'example': 'beta'}}


def test_import_private_policy_previews_deterministic_ordering_without_urls(importer):
    importer.observe_config(private_configuration(), environ={})
    candidate = prepare(importer)
    claim = importer.claim(candidate)
    diff = Preparation.config_diff(claim.inputs)
    assert diff['source_policy'] == {
        'changed': True, 'supported_public_registry': False,
        'private_index_order': ['alpha', 'beta'],
        'private_bindings': {'example': 'beta'},
        'cross_index_newest_version': False,
        'insecure_tls_exceptions': []}
    assert 'packages.invalid' not in repr(diff)
    status = importer.status('2', candidate)
    assert status['intent_change']['config_diff'] == diff
    assert 'packages.invalid' not in repr(status)


def test_import_private_index_ordering_is_observed(importer):
    first = importer.observe_config(private_configuration(order=('alpha', 'beta')), environ={})
    second = importer.observe_config(private_configuration(order=('beta', 'alpha')), environ={})
    assert second['generation'] == first['generation'] + 1
    assert second['digest'] != first['digest']


def test_import_private_policy_requires_binding_for_every_distribution(importer):
    configuration = private_configuration()
    configuration['private bindings'] = {'other': 'alpha'}
    importer.observe_config(configuration, environ={})
    before = snapshot(importer)
    with pytest.raises(PreparationError, match='explicit private index binding for every configured distribution'):
        prepare(importer)
    assert candidates(importer) == []
    assert snapshot(importer) == before


def test_import_private_policy_rejects_mixed_pip_source_policy(importer):
    configuration = private_configuration()
    configuration['pip index url'] = 'https://other.invalid/simple'
    importer.observe_config(configuration, environ={})
    with pytest.raises(PreparationError, match='cannot mix private indexes'):
        prepare(importer)
    assert candidates(importer) == []


def test_import_private_index_url_secret_is_rejected_without_echo(importer):
    configuration = private_configuration()
    configuration['private indexes']['alpha'] = 'https://user:SECRET@packages.invalid/private/simple/'
    with pytest.raises(PreparationError) as error:
        importer.observe_config(configuration, environ={})
    assert 'SECRET' not in str(error.value)
    assert 'packages.invalid' not in str(error.value)


def test_import_private_tls_exception_must_reference_a_declared_index(importer):
    configuration = private_configuration()
    configuration['private tls exceptions'] = ['missing']
    with pytest.raises(PreparationError, match='TLS exception'):
        importer.observe_config(configuration, environ={})
    assert candidates(importer) == []
