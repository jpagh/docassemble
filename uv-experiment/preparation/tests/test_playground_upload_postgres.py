"""Real SQL/API-key upload adapters and the source Playground ZIP generator."""
# ruff: noqa: F811 -- PostgreSQL fixtures injected by name
from email.parser import BytesParser
import importlib.util
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import tomllib
import zipfile

from flask import current_app, request
from flask_login import current_user
import pytest
from sqlalchemy import select, text
import werkzeug
import yaml

from docassemble.base.managed_artifacts import distribution_identity
from docassemble.base.managed_preparation import PreparationError
from test_preparation_postgres import protocol  # noqa: F401
from test_publication_writers_postgres import publisher  # noqa: F401
from test_managed_api_postgres import api_http, adapter, call, denied, snapshot  # noqa: F401
from test_zip_install_postgres import store, distribution  # noqa: F401

spec = importlib.util.spec_from_file_location('playground_fixture', Path(__file__).parents[1] / 'playground_fixture.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


@pytest.fixture
def playground_http(api_http, store, tmp_path, monkeypatch):
    h = api_http
    h.client.application._got_first_request = False
    monkeypatch.setenv('DA_MANAGED_ARTIFACT_ROOT', str(store))
    h.client.application.config['ENABLE_PLAYGROUND'] = True
    generated = fixture.generator(tmp_path / 'playground')
    namespace = dict(generated, current_app=current_app, request=request, current_user=current_user,
        managed_installation=lambda: True, werkzeug=werkzeug, standardyaml=yaml,
        fix_package_folder=lambda: None, get_playground_user=lambda: SimpleNamespace(
            id=2, email='fixture@example.invalid', first_name='Fixture', last_name='', timezone='UTC'),
        get_current_project=lambda: 'default', name_of_user=lambda *a, **k: 'Fixture',
        fix_ml_files=lambda *a: None, get_default_timezone=lambda: 'UTC')
    fixture.functions('docassemble_webapp/docassemble/webapp/develop/views.py',
                      {'managed_playground_zip', 'create_playground_package'}, namespace)
    h.client.application.add_url_rule('/createplaygroundpackage',
        view_func=namespace['create_playground_package'], methods=['GET', 'POST'])
    # Source browser route, including form rejection and early legacy isolation.
    namespace.update(PACKAGE_OPERATIONS_UNSUPPORTED='unsupported', jsonify=__import__('flask').jsonify,
        UpdatePackageForm=lambda form: SimpleNamespace(gitbranch=SimpleNamespace(choices=[]),
                                                      validate_on_submit=lambda: True))
    fixture.functions('docassemble_webapp/docassemble/webapp/packages/views.py', {'update_package'}, namespace)
    monkeypatch.setitem(sys.modules, 'docassemble.webapp.packages', SimpleNamespace(__path__=[]))
    # Relative import in the extracted browser route.
    namespace['__package__'] = 'docassemble.webapp.packages'
    h.client.application.add_url_rule('/updatepackage', view_func=namespace['update_package'], methods=['GET', 'POST'])
    directory = generated['SavedFile'](2, True, 'playgroundpackages')
    (directory / 'docassemble.Example').write_text(yaml.safe_dump(fixture.package_info()))
    h.legacy_calls = []
    def forbidden_legacy(*args, **kwargs):
        h.legacy_calls.append((args, kwargs))
        raise AssertionError('legacy install/update/reset dispatch')
    namespace.update(install_zip_package=forbidden_legacy,
                     celery_app=SimpleNamespace(signature=forbidden_legacy))
    # The packages Blueprint registers prefixed endpoints (packages.api_package);
    # resolve by suffix so the fixture seam tracks the real registration.
    api_package_view = next(view for name, view in h.client.application.view_functions.items()
                            if name.endswith('api_package'))
    api_package_view.__globals__.update(
        install_zip_package=forbidden_legacy, celery_app=SimpleNamespace(signature=forbidden_legacy))
    h.generated, h.playground_namespace, h.directory = generated, namespace, directory
    h.client.application._got_first_request = True
    return h


def upload(h, tmp_path, path='/api/package', **fields):
    source = tmp_path / 'misleading.zip'
    if not source.exists():
        distribution(source)
    field = 'zipfile' if path == '/updatepackage' else 'zip'
    data = {field: (io.BytesIO(source.read_bytes()), 'token-secret.zip'), 'idempotency_key': 'upload', **fields}
    return h.client.post(path, data=data, headers={'X-API-Key': h.key})


@pytest.mark.parametrize('path', ['/api/package', '/updatepackage'])
def test_http_zip_prepares_durable_bytes_no_early_intent(playground_http, tmp_path, path):
    h = playground_http
    before = snapshot(h)
    reply = upload(h, tmp_path, path)
    assert reply.status_code == 202, reply.json
    with h.service.engine.connect() as c:
        candidate = c.execute(select(h.service.tables['candidate'])).mappings().one()
        material = c.execute(select(h.service.tables['candidate_material'])).mappings().one()
    assert candidate['inputs']['artifact']['normalized_name'] == 'zip-example'
    assert Path(material['path']).is_file()
    assert snapshot(h)['package'] == before['package']
    assert snapshot(h)['managed_environment_state'] == before['managed_environment_state']
    assert snapshot(h)['managed_revision'] == before['managed_revision']
    assert upload(h, tmp_path, path).json['candidate_id'] == reply.json['candidate_id']
    assert not h.legacy_calls


@pytest.mark.parametrize('path', ['/api/package', '/updatepackage'])
@pytest.mark.parametrize('failure', ['restart', 'mismatch', 'owner', 'role', 'invalid', 'unexpected', 'mixed'])
def test_upload_fails_closed_authority_restart_and_redaction(playground_http, tmp_path, monkeypatch, path, failure):
    h = playground_http
    fields = {}
    if failure == 'restart':
        fields['restart'] = 'false'
    elif failure == 'mismatch':
        fields['requirement'] = 'other-name'
    elif failure == 'owner':
        with h.service.engine.begin() as c:
            c.execute(text("INSERT INTO test_package (name,type,core) VALUES ('zip-example','pip',false)"))
            c.execute(text("INSERT INTO test_package_auth (package_id,user_id,authtype) SELECT id,1,'owner' FROM test_package"))
    elif failure == 'role':
        with h.service.engine.begin() as c:
            c.execute(text('DELETE FROM test_user_roles WHERE user_id=2'))
    elif failure == 'unexpected':
        def broken(*args, **kwargs):
            raise RuntimeError('token-secret postgres://user:pass@host /private/artifacts')
        monkeypatch.setattr(adapter.QualifiedPublication, 'prepare_zip', broken)
        if path == '/updatepackage':
            monkeypatch.setitem(h.playground_namespace, 'UpdatePackageForm', broken)
    elif failure == 'mixed':
        fields['github_url'] = 'https://token-secret@example.invalid/repo'
    before = snapshot(h)
    if failure == 'invalid':
        field = 'zipfile' if path == '/updatepackage' else 'zip'
        reply = h.client.post(path, data={field: (io.BytesIO(b'token-secret'), 'secret.zip'),
            'idempotency_key': 'invalid'}, headers={'X-API-Key': h.key})
    else:
        reply = upload(h, tmp_path, path, **fields)
    denied(reply, 'restart-required' if failure == 'restart' else 'forbidden' if failure in ('owner', 'role') else 'rejected')
    assert snapshot(h) == before
    assert b'token-secret' not in reply.data and b'/private' not in reply.data
    assert not h.legacy_calls


def test_api_upload_read_only_key_cannot_prepare(playground_http, tmp_path):
    import json
    h = playground_http
    h.redis.set(h.redis_key, json.dumps(dict(h.info, permissions=['read_packages'])))
    before = snapshot(h)
    denied(upload(h, tmp_path))
    assert snapshot(h) == before


def test_playground_generates_metadata_preserves_dependencies_and_prepares(playground_http):
    h = playground_http
    declaration = 'not-on-this-server[feature]>=1,<3; python_version >= "3.10"'
    info = fixture.package_info(dependencies=[declaration])
    (h.directory / 'docassemble.Example').write_text(yaml.safe_dump(info))
    with h.generated['make_package_zip']('Example', info, fixture.author_info(), 'UTC', managed=True) as archive:
        with zipfile.ZipFile(archive) as z:
            project = tomllib.loads(z.read('docassemble-Example/pyproject.toml').decode())
            metadata = BytesParser().parsebytes(z.read('docassemble-Example/PKG-INFO'))
            assert project['project']['dependencies'] == metadata.get_all('Requires-Dist') == [declaration]
            assert project['project']['name'] == metadata['Name'] == 'docassemble.Example'
            assert project['project']['version'] == metadata['Version'] == '1.0'
            assert z.getinfo('docassemble-Example/PKG-INFO').date_time == z.getinfo('docassemble-Example/pyproject.toml').date_time
        assert distribution_identity(archive)['normalized_name'] == 'docassemble-example'
    before = snapshot(h)
    reply = h.client.get('/createplaygroundpackage?package=Example&install=1&idempotency_key=playground')
    assert reply.status_code == 202, reply.json
    replay = h.client.get('/createplaygroundpackage?package=Example&install=1&idempotency_key=playground')
    assert replay.json['candidate_id'] == reply.json['candidate_id']
    with h.service.engine.connect() as c:
        candidate = c.execute(select(h.service.tables['candidate'])).mappings().one()
    assert candidate['inputs']['requirement']['declaration'] == 'docassemble.Example'
    assert candidate['inputs']['artifact']['normalized_name'] == 'docassemble-example'
    assert snapshot(h)['package'] == before['package']
    assert snapshot(h)['managed_revision'] == before['managed_revision']
    with h.client.session_transaction() as session:
        assert 'taskwait' not in session and 'serverstarttime' not in session
    assert not h.legacy_calls


@pytest.mark.parametrize('failure', ['restart', 'github', 'pypi', 'missing', 'dependency', 'generation', 'mismatch', 'owner'])
def test_playground_failures_redacted_without_legacy_dispatch(playground_http, monkeypatch, tmp_path, failure):
    h = playground_http
    query = 'package=Example&install=1'
    if failure in ('restart', 'github', 'pypi'):
        query += '&' + ('restart=false' if failure == 'restart' else failure + '=1')
    elif failure == 'missing':
        query = 'package=missing'
    elif failure == 'dependency':
        (h.directory / 'docassemble.Example').write_text(yaml.safe_dump(fixture.package_info(
            dependencies=['secret @ https://token-secret@example.invalid/private.zip'])))
    elif failure == 'generation':
        def broken(*a, **k):
            raise RuntimeError('token-secret /private/data')
        monkeypatch.setitem(h.playground_namespace, 'make_package_zip', broken)
    elif failure == 'mismatch':
        def wrong(*a, **k):
            source = distribution(tmp_path / 'wrong.zip')
            return open(source, 'rb')
        monkeypatch.setitem(h.playground_namespace, 'make_package_zip', wrong)
    elif failure == 'owner':
        with h.service.engine.begin() as c:
            c.execute(text("INSERT INTO test_package (name,type,core) VALUES ('docassemble.Example','pip',false)"))
            c.execute(text("INSERT INTO test_package_auth (package_id,user_id,authtype) SELECT id,1,'owner' FROM test_package"))
    before = snapshot(h)
    reply = h.client.get('/createplaygroundpackage?' + query)
    denied(reply, 'restart-required' if failure == 'restart' else 'forbidden' if failure == 'owner' else 'rejected')
    assert snapshot(h) == before
    assert b'token-secret' not in reply.data and b'/private' not in reply.data
    assert not h.legacy_calls


@pytest.mark.parametrize('declaration', ['unknown[extra]>=2; python_version < "4"', 'unknown==1,!=1.1'])
def test_generator_never_uses_legacy_dependency_lookup(tmp_path, declaration):
    generated = fixture.generator(tmp_path / 'generator')
    with pytest.raises(PreparationError, match='dependencies must be a list'):
        generated['make_package_zip']('Example', fixture.package_info(dependencies=declaration),
            fixture.author_info(), 'UTC', managed=True)
    with generated['make_package_zip']('Example', fixture.package_info(dependencies=[declaration]),
            fixture.author_info(), 'UTC', managed=True) as archive:
        assert distribution_identity(archive)['normalized_name'] == 'docassemble-example'
    with pytest.raises(PreparationError, match='Playground package file unavailable'):
        generated['make_package_zip']('Example', fixture.package_info(module_files=['missing.py']),
            fixture.author_info(), 'UTC', managed=True)


@pytest.mark.parametrize('declaration', ['--index-url secret', 'a @ file:///private/secret', 'git+https://secret/repo', 'a @ https://secret/pkg.whl'])
def test_generator_rejects_unsupported_dependencies_without_lookup(tmp_path, declaration):
    generated = fixture.generator(tmp_path / 'generator')
    with pytest.raises(PreparationError):
        generated['make_package_zip']('Example', fixture.package_info(dependencies=[declaration]),
            fixture.author_info(), 'UTC', managed=True)
