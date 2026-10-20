"""08a real PostgreSQL observation, exact admin save body; no import authority.

The admin body's unrelated form/ruamel syntax validator is a test double. File
writes, Flask reporting, semantic observer and PostgreSQL transactions are real.
"""
import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
import yaml
from flask import Flask, current_app, flash, make_response, redirect, request
from sqlalchemy import select, text

from docassemble.base.managed import managed_installation
from docassemble.base.managed_config import dependency_snapshot
from docassemble.base.managed_preparation import PreparationError
from test_preparation_postgres import protocol, snapshot  # noqa: F401 -- pytest fixture registration
from test_publication_postgres import publisher, ready, approve, wait_for_approval_lock  # noqa: F401
from test_preview_adapters_postgres import adapter

SOURCE = Path(__file__).resolve().parents[3]


def observed(service):
    with service.engine.connect() as connection:
        return [dict(row) for row in connection.execute(select(service.tables['observed_config'])).mappings()]


def test_underscore_keys_are_normalized_like_config_load(publisher):  # noqa: F811
    # config.load replaces underscores with spaces in raw insertion order, so
    # the underscored spellings must be observed too, not silently dropped.
    response = publisher.observe_config(
        {'python_packages': ['Example>=2'], 'pip_index_url': 'https://private.invalid/simple'}, environ={})
    row, = observed(publisher)
    assert row['generation'] == response['generation'] == 1
    assert row['snapshot']['digest'] == response['digest']
    assert [item['normalized_name'] for item in row['snapshot']['requirements']] == ['example']
    assert row['snapshot']['index_policy']['index_url'] == 'https://private.invalid/simple'


@pytest.mark.parametrize('configuration, winner', [
    ({'python_packages': ['Example>=2'], 'python packages': ['Other>=1']}, 'other'),
    ({'python packages': ['Other>=1'], 'python_packages': ['Example>=2']}, 'example'),
    # config.update keeps an existing key's position but appends new keys, then
    # normalization collapses underscores: a later underscored root key still wins.
    ({'python_packages': ['Other>=1'], 'config from': {'python packages': ['Example>=2']}}, 'example'),
    ({'python packages': ['Other>=1'], 'python_packages': ['Example>=2'],
      'config from': {'python packages': ['Third>=1']}}, 'example'),
])
def test_underscore_collision_uses_config_load_order(publisher, configuration, winner):  # noqa: F811
    publisher.observe_config(configuration, environ={})
    row, = observed(publisher)
    assert [item['normalized_name'] for item in row['snapshot']['requirements']] == [winner]


def test_null_config_from_is_an_absent_overlay(publisher):  # noqa: F811
    # Docker/config/config.yml.dist renders CONFIGFROM=null by default.
    configuration = {'config from': None, 'python packages': ['Example>=2']}
    response = publisher.observe_config(configuration, environ={})
    row, = observed(publisher)
    assert row['snapshot']['digest'] == response['digest']
    assert row['snapshot'] == dependency_snapshot({'python packages': ['Example>=2']}, environ={})


def test_dependency_snapshot_preserves_inputs(publisher):  # noqa: F811
    declaration = 'My_Package[fast,extra]>=1.2,<3; python_version >= "3.10"'
    config = {'python packages': [declaration], 'pip index url': 'https://private.invalid/simple',
              'pip extra index urls': 'https://second.invalid/simple https://third.invalid/simple',
              'pip trusted host': 'private.invalid:8443 third.invalid'}
    response = publisher.observe_config(config, environ={})
    row, = observed(publisher)
    assert row['generation'] == response['generation'] == 1
    assert row['snapshot']['digest'] == response['digest']
    requirement, = row['snapshot']['requirements']
    assert requirement == {'declaration': declaration, 'name': 'My_Package',
        'normalized_name': 'my-package', 'extras': ['extra', 'fast'], 'specifiers': '<3,>=1.2',
        'marker': 'python_version >= "3.10"', 'origin': 'python packages'}
    assert row['snapshot']['index_policy'] == {
        'index_url': 'https://private.invalid/simple',
        'extra_index_urls': ['https://second.invalid/simple', 'https://third.invalid/simple'],
        'trusted_hosts': ['private.invalid:8443', 'third.invalid'], 'image_index_url': None}
    assert dependency_snapshot(config, environ={}, image_config={'index_url': 'https://pypi.org/simple'})[
        'index_policy']['image_index_url'] == 'https://pypi.org/simple'


@pytest.mark.parametrize('packages', [
    ['My_Package>=1', 'my-package<3'], ['-r requirements.txt'], ['-e package'],
    ['Example @ https://secret.invalid/a.whl'], ['git+ssh://secret.invalid/repo'],
    ['/tmp/package'], ['Example; echo SECRET'], ['Example\nOther'], [42], 'Example', None,
])
def test_unsupported_or_duplicate_configuration_is_not_partially_observed(publisher, packages):  # noqa: F811
    publisher.observe_config({'python packages': ['Before<2']}, environ={})
    before = observed(publisher), snapshot(publisher)
    with pytest.raises(PreparationError):
        publisher.observe_config({'python packages': packages}, environ={'PYTHONPACKAGES': 'Fallback'})
    assert (observed(publisher), snapshot(publisher)) == before


@pytest.mark.parametrize('value, declarations', [
    ('Example>=1,<3; python_version >= "3.10"', ['Example>=1,<3; python_version >= "3.10"']),
    ('Example, Other>=2; Third[extra]', ['Example', ' Other>=2', ' Third[extra]']),
    ('', []), ('null', []),
])
def test_environment_whole_requirement_then_complete_legacy_segments(publisher, value, declarations):  # noqa: F811
    publisher.observe_config({}, environ={'PYTHONPACKAGES': value})
    requirements = observed(publisher)[0]['snapshot']['requirements']
    assert [item['declaration'] for item in requirements] == declarations
    assert all(item['origin'] == 'PYTHONPACKAGES' for item in requirements)


@pytest.mark.parametrize('value', ['Example>=1,<3,Other', 'Example; os_name == "posix"; Other',
                                  'Example,,Other', 'Example, -r SECRET', 'Example,example>=2'])
def test_ambiguous_invalid_environment_fails_whole_observation(publisher, value):  # noqa: F811
    with pytest.raises(PreparationError):
        publisher.observe_config({}, environ={'PYTHONPACKAGES': value})
    assert observed(publisher) == []


@pytest.mark.parametrize('precedence', ['false', 'true'])
def test_config_environment_precedence_and_no_ambient_pip_policy(publisher, precedence):  # noqa: F811
    config = {'python packages': [], 'pip index url': 'https://config.invalid/simple',
              'pip extra index urls': ['https://config-extra.invalid/simple']}
    env = {'PYTHONPACKAGES': '-r BAD', 'PIPINDEXURL': 'https://env.invalid/simple',
           'PIPEXTRAINDEXURLS': 'https://env-extra.invalid/simple',
           'ENVIRONMENT_TAKES_PRECEDENCE': precedence,
           'PIP_INDEX_URL': 'https://ambient.invalid/simple', 'PIP_TRUSTED_HOST': 'ambient.invalid'}
    publisher.observe_config(config, environ=env)
    row, = observed(publisher)
    assert row['snapshot']['requirements'] == []
    expected = 'env' if precedence == 'true' else 'config'
    assert row['snapshot']['index_policy'] == {'index_url': f'https://{expected}.invalid/simple',
        'extra_index_urls': [f'https://{expected}-extra.invalid/simple'], 'trusted_hosts': [], 'image_index_url': None}
    assert dependency_snapshot(config, environ={**env, 'ENVIRONMENT_TAKES_PRECEDENCE': 'true',
        'PIPINDEXURL': '', 'PIPEXTRAINDEXURLS': 'null'})['index_policy'] == {
            'index_url': 'https://config.invalid/simple', 'extra_index_urls': [],
            'trusted_hosts': [], 'image_index_url': None}


@pytest.mark.parametrize('policy', [
    {'pip index url': False}, {'pip index url': 'file:///tmp/SECRET'},
    {'pip index url': 'https://host.invalid:BAD/simple'},
    {'pip extra index urls': ['https://ok.invalid/simple', '--index-url']},
    {'pip trusted host': 'host.invalid --disable-pip-version-check'},
])
def test_invalid_policy_fails_without_partial_observation(publisher, policy):  # noqa: F811
    with pytest.raises(PreparationError):
        publisher.observe_config(policy, environ={})
    assert observed(publisher) == []


def test_monotonic_generations_and_unrelated_settings(publisher):  # noqa: F811
    candidate, _ = ready(publisher)
    before = snapshot(publisher)
    assert publisher.status('alice', candidate)['config_drift'] == {
        'pending': False, 'observed_generation': None, 'committed_generation': None}
    first = publisher.observe_config({'python packages': ['Example<3']}, environ={})
    same = publisher.observe_config({'python packages': ['Example<3'], 'unrelated': 'changed'}, environ={})
    away = publisher.observe_config({'python packages': ['Example<4']}, environ={})
    back = publisher.observe_config({'python packages': ['Example<3']}, environ={})
    assert first == same
    assert [first['generation'], away['generation'], back['generation']] == [1, 2, 3]
    assert first['digest'] == back['digest'] != away['digest']
    assert snapshot(publisher) == before
    assert publisher.status('alice', candidate)['config_drift'] == {
        'pending': True, 'observed_generation': 3, 'committed_generation': None}


def test_ordinary_prepare_publication_uses_only_committed_config(publisher):  # noqa: F811
    before = snapshot(publisher)
    candidate = ready(publisher)
    publisher.observe_config({'python packages': ['DriftOnly>=99'],
        'pip index url': 'https://user:SECRET@pending.invalid/simple?token=SECRET'}, environ={})
    with publisher.engine.connect() as connection:
        inputs = connection.scalar(select(publisher.tables['candidate'].c.inputs).where(
            publisher.tables['candidate'].c.id == candidate[0]))
    assert inputs['baseline']['config'] == before['environment_state'][0]['snapshot']['config']
    second = publisher.prepare('alice', 'Other>=1', 'after-observation')
    with publisher.engine.connect() as connection:
        after = connection.scalar(select(publisher.tables['candidate'].c.inputs).where(
            publisher.tables['candidate'].c.id == second))
    assert after['baseline']['config'] == inputs['baseline']['config']
    assert after['contributions'] == before['requirement_contribution']
    assert not publisher.status('alice', candidate[0])['stale']
    response = approve(publisher, candidate)
    status = publisher.publication_status('alice', revision_id=response['revision_id'])
    assert status['config_drift'] == {'pending': True, 'observed_generation': 1, 'committed_generation': None}
    assert 'SECRET' not in repr(status)
    assert snapshot(publisher)['environment_state'][0]['snapshot']['config'] == inputs['baseline']['config']
    assert observed(publisher)[0]['generation'] == 1


def test_observation_waits_for_actual_publication_environment_lock(publisher, monkeypatch):  # noqa: F811
    candidate = ready(publisher)
    original = publisher.project
    locked, release = Event(), Event()
    pids = []

    def pause(connection, actor, candidate):
        pids.append(connection.scalar(text('SELECT pg_backend_pid()')))
        locked.set()
        assert release.wait(15)
        return original(connection, actor, candidate)

    monkeypatch.setattr(publisher, 'project', pause)
    with ThreadPoolExecutor(2) as pool:
        publication = pool.submit(approve, publisher, candidate)
        assert locked.wait(10)
        observation = pool.submit(publisher.observe_config, {'python packages': ['Pending>=1']}, environ={})
        try:
            wait_for_approval_lock(publisher.engine, pids[0])  # pg_blocking_pids, not timing guesses
            assert observed(publisher) == []
            assert not observation.done()
        finally:
            release.set()
        response = publication.result(timeout=10)
        assert observation.result(timeout=10)['generation'] == 1
    assert publisher.publication_status('alice', revision_id=response['revision_id'])['config_drift']['pending']


def config_view(service, monkeypatch, tmp_path, *, managed=True, recovery=False):
    path = SOURCE / 'docassemble_webapp/docassemble/webapp/admin/views.py'
    function = next(node for node in ast.parse(path.read_text()).body
                    if isinstance(node, ast.FunctionDef) and node.name == 'config_page')
    function.decorator_list = []
    filename = tmp_path / 'config.yml'
    filename.write_text('old: value\n')
    pipeline_calls, logs = [], []
    pipe = SimpleNamespace(set=lambda *args: pipeline_calls.append(args),
        expire=lambda *args: pipeline_calls.append(args), execute=lambda: pipeline_calls.append('execute'))
    cloud_writes = []
    cloud = SimpleNamespace(get_key=lambda name: SimpleNamespace(
        set_contents_from_string=lambda value: cloud_writes.append(value)))
    namespace = {'setup_translation': lambda: None, 'current_app': current_app,
        'ConfigForm': lambda form: SimpleNamespace(submit=SimpleNamespace(data=True),
            config_content=SimpleNamespace(data=form['config']), cancel=SimpleNamespace(data=False)),
        'request': request, 'standardyaml': yaml,
        'ruamel': SimpleNamespace(yaml=SimpleNamespace(YAML=lambda: SimpleNamespace(load=yaml.safe_load))),
        'word': lambda value: value, 'flash': flash, 'logmessage': logs.append,
        'cloud': cloud, 'daconfig': {'config file': str(filename)}, 'RECOVERY_MODE': recovery,
        'managed_installation': managed_installation,
        'observe_saved_configuration': adapter.observe_saved_configuration,
        'save_managed_configuration': adapter.save_managed_configuration,
        'current_user': SimpleNamespace(id='1'),
        'make_response': make_response, 'redirect': redirect, 'url_for': lambda endpoint: '/' + endpoint,
        'r': SimpleNamespace(pipeline=lambda: pipe)}
    monkeypatch.setattr(adapter, 'internal_publication_service', lambda: service)
    monkeypatch.setenv('DA_UV_MANAGED', 'true' if managed else 'false')
    monkeypatch.setenv('DA_MANAGED_STATE_DIR', str(tmp_path / 'absent'))
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
    app = Flask(__name__)
    app.secret_key = 'disposable'
    app.config['ALLOW_CONFIGURATION_EDITING'] = True
    app.add_url_rule('/config', view_func=namespace['config_page'], methods=['POST'])
    return app.test_client(), filename, pipeline_calls, cloud_writes, logs


def flashes(client):
    with client.session_transaction() as session:
        return session.get('_flashes', [])


@pytest.mark.parametrize('recovery', [False, True])
def test_admin_save_then_observe_then_success(publisher, monkeypatch, tmp_path, recovery):  # noqa: F811
    client, filename, pipeline_calls, cloud_writes, _ = config_view(
        publisher, monkeypatch, tmp_path, recovery=recovery)
    original = publisher.observe_config

    def observe(configuration, *, environ=None, save_id=None):
        assert filename.read_text() == cloud_writes[-1]
        assert not flashes(client)
        assert save_id, 'serialized save must pass its marker identity'
        return original(configuration, environ=environ if environ is not None else {}, save_id=save_id)

    monkeypatch.setattr(publisher, 'observe_config', observe)
    content = 'config from: null\npython packages: [Example>=1]\nunrelated: setting\n'
    response = client.post('/config', data={'config': content})
    assert response.status_code == 302
    # A managed save never invokes the legacy restart fan-out; the unmanaged
    # restart path is covered separately with managed=False.
    assert response.location == ('/recovery.page' if recovery else '/admin.config_page')
    assert filename.read_text() == content
    assert observed(publisher)[0]['snapshot']['requirements'][0]['declaration'] == 'Example>=1'
    assert ('success', 'The configuration file was saved.') in flashes(client)
    # A managed save never invokes the legacy restart fan-out; the unmanaged
    # path (managed=False) is covered separately.
    assert not pipeline_calls


@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('failure', ['syntax', 'commit'])
def test_admin_completed_save_failed_observation_fails_closed(publisher, monkeypatch, tmp_path, existing, failure):  # noqa: F811
    if existing:
        publisher.observe_config({'python packages': ['Previous<2']}, environ={})
    before = observed(publisher), snapshot(publisher)
    if failure == 'commit':
        # A deferred DB error proves even an executed INSERT/UPDATE is rolled
        # back, rather than just mocking out the semantic method before writes.
        with publisher.engine.begin() as connection:
            connection.execute(text("""CREATE FUNCTION fail_observation() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'SECRET observation commit fault'; END $$"""))
            connection.execute(text('''CREATE CONSTRAINT TRIGGER observation_fault
                AFTER INSERT OR UPDATE ON test_managed_observed_config
                DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fail_observation()'''))
    client, filename, pipeline_calls, cloud_writes, logs = config_view(publisher, monkeypatch, tmp_path)
    content = 'python packages: [Example>=1]\n' if failure == 'commit' else 'python packages: ["-r SECRET"]\n'
    response = client.post('/config', data={'config': content})
    assert response.status_code == 503
    assert filename.read_text() == cloud_writes[-1] == content  # no false file/SQL atomicity claim
    after_observed, after_snapshot = observed(publisher), snapshot(publisher)
    assert after_observed == before[0]
    # The committed unresolved marker is the intended durable difference: it must
    # survive the failed observation so every configuration-bound path stays fenced.
    marker = after_snapshot['environment_state'][0]['snapshot'].pop('config_save_unresolved')
    assert marker['actor'] and marker['write_digest'] and marker['id']
    assert after_snapshot == before[1]
    assert not pipeline_calls
    assert not any(category == 'success' for category, _ in flashes(client))
    assert 'SECRET' not in response.text + repr(flashes(client)) + repr(logs)


def test_legacy_save_does_not_observe_or_reject_dependency_syntax(publisher, monkeypatch, tmp_path):  # noqa: F811
    client, filename, pipeline_calls, _, _ = config_view(publisher, monkeypatch, tmp_path, managed=False)
    before = snapshot(publisher)
    content = 'python packages: ["-r legacy-supported.txt"]\n'
    response = client.post('/config', data={'config': content})
    assert response.status_code == 302
    assert filename.read_text() == content
    assert pipeline_calls
    assert observed(publisher) == []
    assert snapshot(publisher) == before
    assert ('success', 'The configuration file was saved.') in flashes(client)


@pytest.mark.parametrize('precedence', ['false', 'true'])
def test_literal_overlay_precedes_environment_and_ignores_unrelated_secrets(publisher, precedence):  # noqa: F811
    config = {'python packages': ['Base'], 'pip index url': 'https://base.invalid/simple',
        'unrelated': 'arn:aws:secretsmanager:SECRET',
        'config from': {'python packages': ['Overlay[extra]<3'],
                        'pip index url': 'https://overlay.invalid/simple',
                        'unrelated': '@Microsoft.KeyVault(SECRET)'}}
    publisher.observe_config(config, environ={'ENVIRONMENT_TAKES_PRECEDENCE': precedence,
        'PIPINDEXURL': 'https://env.invalid/simple', 'PYTHONPACKAGES': 'Ignored'})
    row, = observed(publisher)
    assert row['snapshot']['requirements'][0]['declaration'] == 'Overlay[extra]<3'
    assert row['snapshot']['index_policy']['index_url'] == (
        'https://env.invalid/simple' if precedence == 'true' else 'https://overlay.invalid/simple')
    assert 'SECRET' not in repr(row)


@pytest.mark.parametrize('configuration', [
    {'config from': 'arn:aws:secretsmanager:SECRET'}, {'config from': False},
    {'config from': ['not a mapping']},
    {'python packages': ['@Microsoft.KeyVault(SECRET)']},
    {'python packages': 'arn:aws:secretsmanager:SECRET'},
    {'pip index url': 'https://index.invalid/@Microsoft.KeyVault(SECRET)'},
    {'pip extra index urls': ['arn:aws:secretsmanager:SECRET']},
    {'pip trusted host': '@Microsoft.KeyVault(SECRET)'},
])
def test_unresolved_dependency_inputs_fail_closed_redacted(publisher, configuration):  # noqa: F811
    with pytest.raises(PreparationError) as error:
        publisher.observe_config(configuration, environ={})
    assert 'SECRET' not in str(error.value)
    assert observed(publisher) == []


@pytest.mark.parametrize('key, value', [
    ('python packages', ['Other']), ('pip index url', 'https://changed.invalid/simple'),
    ('pip extra index urls', ['https://extra.invalid/simple']), ('pip trusted host', 'insecure.invalid'),
])
def test_each_dependency_input_changes_generation(publisher, key, value):  # noqa: F811
    publisher.observe_config({'python packages': ['Example']}, environ={})
    changed = publisher.observe_config({'python packages': ['Example'], key: value}, environ={})
    assert changed['generation'] == 2
