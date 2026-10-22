"""Private distribution snapshot policy and secret boundaries (real HTTPS proof separate)."""
from docassemble.base.managed_python import python_target
import base64
import copy
import json
import zipfile
from types import SimpleNamespace

import pytest

from docassemble_executor.preparation import Preparation, PreparationError
from docassemble_executor.private_index import PrivateIndexSnapshot, load_operator_references, validate_private_policy
from docassemble_executor.registry_builder import RegistryBuilder


def inputs():
    return {'baseline': {'target': python_target(), 'tool': {'uv': '0.12.21'}, 'core': {},
            'runtime': {'kind': 'normal'}, 'config': {
                'private_indexes': {'private': 'https://example.invalid/scoped/simple/'},
                'private_bindings': {'sample': 'private'}}},
            'contributions': [], 'requirement': {'declaration': 'Sample[extra]>=1,<3; python_version >= "3.12"'}}


def test_private_index_policy_preserves_declarations_and_previews_bindings():
    value = inputs()
    original = copy.deepcopy(value)
    assert validate_private_policy(value) == value['baseline']['config']
    assert value == original
    assert Preparation.ready_warnings(value) == ['private-source:sample:private']


@pytest.mark.parametrize('url', [
    'https://user:DISPOSABLE_SECRET@example.invalid/simple/',
    'https://DISPOSABLE_SECRET@example.invalid/simple/',
    'https://example.invalid/simple/?token=DISPOSABLE_SECRET',
    'http://example.invalid/simple/', 'file:///tmp/index/',
    'https://example.invalid/simple', 'https://example.invalid/a/../simple/',
    'https://example.invalid/%2e%2e/simple/', 'https://example.invalid:bad/simple/',
    'https://[DISPOSABLE_SECRET/simple/', 'https://example.invalid/simple/\n',
])
def test_private_index_rejects_url_secrets_and_unsafe_urls_before_tooling(tmp_path, url):
    value = inputs()
    value['baseline']['config']['private_indexes']['private'] = url
    with pytest.raises(PreparationError) as error:
        RegistryBuilder(tmp_path / 'store', '/missing/uv', private_snapshot=PrivateIndexSnapshot({})).build(value)
    assert 'DISPOSABLE_SECRET' not in str(error.value)
    assert not (tmp_path / 'store').exists()


@pytest.mark.parametrize('declaration', [
    'sample @ git+ssh://example.invalid/repo', '-e sample', '-r requirements.txt',
    './sample', 'sample && echo DISPOSABLE_SECRET',
    'sample @ https://user:DISPOSABLE_SECRET@example.invalid/a.whl',
    'sample @ https://DISPOSABLE_SECRET@example.invalid/a.whl',
    'sample @ https://example.invalid/a.whl?token=DISPOSABLE_SECRET',
])
def test_private_index_unsupported_requirements_are_not_sanitized(declaration):
    value = inputs()
    value['requirement']['declaration'] = declaration
    with pytest.raises(PreparationError) as error:
        validate_private_policy(value)
    assert 'DISPOSABLE_SECRET' not in str(error.value)
    assert value['requirement']['declaration'] == declaration


@pytest.mark.parametrize('binding', [{'Sample': 'private'}, {'sample': 'other'}, {'../secret': 'private'}, {'sample': []}])
def test_private_index_requires_normalized_explicit_bindings(binding):
    value = inputs()
    value['baseline']['config']['private_bindings'] = binding
    with pytest.raises(PreparationError, match='explicit private index bindings'):
        validate_private_policy(value)


@pytest.mark.parametrize('kind', ['mode', 'symlink', 'parent-mode', 'fields', 'newline', 'large', 'scope'])
def test_private_index_operator_reference_is_restricted_and_errors_redacted(tmp_path, kind):
    tmp_path.chmod(0o700)
    reference = tmp_path / 'credential'
    reference.write_text(json.dumps({'index_url': 'https://example.invalid/scoped/simple/', 'username': 'user', 'password': 'DISPOSABLE_SECRET'}))
    reference.chmod(0o600)
    if kind == 'mode':
        reference.chmod(0o644)
    elif kind == 'symlink':
        target = tmp_path / 'target'
        reference.rename(target)
        reference.symlink_to(target)
    elif kind == 'parent-mode':
        tmp_path.chmod(0o755)
    elif kind == 'fields':
        reference.write_text('{"password":"DISPOSABLE_SECRET"}')
    elif kind == 'newline':
        reference.write_text(json.dumps({'index_url': 'https://example.invalid/scoped/simple/', 'username': 'user', 'password': 'DISPOSABLE_SECRET\n'}))
    elif kind == 'scope':
        reference.write_text(json.dumps({'index_url': 'https://example.invalid/other/simple/', 'username': 'user', 'password': 'DISPOSABLE_SECRET'}))
    else:
        reference.write_bytes(b'DISPOSABLE_SECRET' * 5000)
    with pytest.raises(PreparationError) as error:
        PrivateIndexSnapshot._credential(reference, 'https://example.invalid/scoped/simple/')
    assert 'DISPOSABLE_SECRET' not in str(error.value)


def test_private_index_reference_can_be_reused_without_being_returned_in_preview(tmp_path):
    tmp_path.chmod(0o700)
    reference = tmp_path / 'credential'
    reference.write_text(json.dumps({'index_url': 'https://example.invalid/scoped/simple/', 'username': 'user', 'password': 'DISPOSABLE_SECRET'}))
    reference.chmod(0o600)
    assert PrivateIndexSnapshot._credential(reference, 'https://example.invalid/scoped/simple/').startswith('Basic ')
    assert Preparation.ready_warnings(inputs()) == ['private-source:sample:private']
    assert reference.exists()  # Reuse must not consume/delete committed operator material.


@pytest.mark.parametrize('arguments', [['lock', '--no-config'], ['sync', '--frozen', '--no-config'],
                                        ['build', '--wheel', '--no-config', 'sample-1.0.tar.gz']])
def test_private_index_uv_is_offline_with_no_ambient_credentials(tmp_path, monkeypatch, arguments):
    calls = []
    monkeypatch.setenv('UV_INDEX_PRIVATE_PASSWORD', 'DISPOSABLE_SECRET')
    monkeypatch.setenv('PIP_TRUSTED_HOST', 'github.com')
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr('subprocess.run', run)
    builder = RegistryBuilder(tmp_path, private_snapshot=PrivateIndexSnapshot({}))
    builder._run(arguments, tmp_path, tmp_path / 'cache', tmp_path / 'venv')
    command, kwargs = calls[0]
    assert '--offline' in command and '--default-index' in command
    assert command[command.index('--default-index') + 1] == (tmp_path / 'private-indexes/snapshot').as_uri() + '/'
    assert 'https://' not in ' '.join(command)
    assert 'DISPOSABLE_SECRET' not in json.dumps(kwargs['env'])
    assert not any('TRUSTED' in key or 'PASSWORD' in key for key in kwargs['env'])


def test_private_index_result_requires_exact_source_preview():
    value = inputs()
    value['baseline']['runtime']['kind'] = 'unknown'
    assert Preparation.ready_warnings(value) == ['runtime-not-normally-verified', 'private-source:sample:private']
    value['baseline']['config']['private_bindings']['sample'] = 'https://DISPOSABLE_SECRET@example.invalid/'
    with pytest.raises(PreparationError, match='invalid private source binding'):
        Preparation.ready_warnings(value)


@pytest.mark.parametrize('dependency', ['unknown>=1', 'other @ git+ssh://example.invalid/repo', 'other @ file:///tmp/unsafe', 'other @ https://DISPOSABLE_SECRET@example.invalid/a.whl', 'other @ https://example.invalid/a.whl?token=DISPOSABLE_SECRET'])
def test_private_index_rejects_unsupported_transitive_metadata_before_uv(tmp_path, dependency):
    path = tmp_path / 'sample-1.0-py3-none-any.whl'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('sample-1.0.dist-info/METADATA', 'Name: sample\nVersion: 1.0\nRequires-Dist: ' + dependency + '\n')
    with pytest.raises(PreparationError) as error:
        PrivateIndexSnapshot._validate_wheel(path, 'sample', {'sample': 'private'}, {})
    assert 'DISPOSABLE_SECRET' not in str(error.value)


def wheel_with_dependency(tmp_path, dependency):
    path = tmp_path / 'sample-1.0-py3-none-any.whl'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('sample-1.0.dist-info/METADATA',
                         'Name: sample\nVersion: 1.0\nRequires-Dist: ' + dependency + '\n')
    return path


def test_private_index_parent_binding_does_not_bind_closure(tmp_path):
    path = wheel_with_dependency(tmp_path, 'child==1.0')
    with pytest.raises(PreparationError, match='explicit binding'):
        PrivateIndexSnapshot._validate_wheel(path, 'sample', {'sample': 'private'}, {})
    # The closure name must be declared separately; the parent binding is not enough.
    assert PrivateIndexSnapshot._validate_wheel(
        path, 'sample', {'sample': 'private', 'child': 'private'}, {}) is None


def config_import_inputs():
    from docassemble.base.managed_config import dependency_snapshot
    snapshot = dependency_snapshot({
        'python packages': ['Sample[extra]>=1,<3'],
        'private indexes': {'alpha': 'https://example.invalid/scoped/simple/'},
        'private bindings': {'sample': 'alpha'}},
        environ={})
    return {'change_operation': 'config', 'ownership_effects': {}, 'baseline': {
        'target': python_target(), 'tool': {'uv': '0.12.21'}, 'core': {},
        'runtime': {'kind': 'normal'}, 'config': {'index_url': 'https://pypi.org/simple'}},
        'config_import': {'generation': 1, 'snapshot': snapshot, 'digest': snapshot['digest']},
        'committed_contributions': [],
        'contributions': [{'id': 'config', 'origin': 'config', 'declaration': 'Sample[extra]>=1,<3'}]}


def test_private_index_config_import_uses_effective_private_policy(tmp_path):
    value = config_import_inputs()
    config = validate_private_policy(value)
    assert config == {'private_indexes': {'alpha': 'https://example.invalid/scoped/simple/'},
                      'private_bindings': {'sample': 'alpha'}}
    assert Preparation.ready_warnings(value) == ['private-source:sample:alpha']
    assert Preparation.config_diff(value)['source_policy']['private_index_order'] == ['alpha']
    with pytest.raises(PreparationError, match='operator credential reference'):
        PrivateIndexSnapshot({}).prepare(value, tmp_path / 'project')


def reference_file(tmp_path, index):
    tmp_path.chmod(0o700)
    reference = tmp_path / 'credential'
    reference.write_text(json.dumps({'index_url': index, 'username': 'user', 'password': 'DISPOSABLE_SECRET'}))
    reference.chmod(0o600)
    return reference


def test_private_index_http_failure_categories_never_fall_back_to_public(tmp_path, monkeypatch):
    import io
    from urllib.error import HTTPError
    import docassemble_executor.private_index as module
    index = 'https://example.invalid/scoped/simple/'
    value = inputs()
    value['baseline']['config'] = {'private_indexes': {'private': index}, 'private_bindings': {'sample': 'private'}}
    snapshot = PrivateIndexSnapshot({'private': reference_file(tmp_path, index)})
    monkeypatch.setattr(snapshot, '_credential', lambda *args: 'Basic ZGlzcG9zYWJsZQ==')

    class Opener:
        code = 0

        def open(self, *args, **kwargs):
            raise HTTPError(index + 'sample/', self.code, 'failed', {}, io.BytesIO(b''))

    opener = Opener()
    monkeypatch.setattr(module, 'build_opener', lambda *args, **kwargs: opener)
    for code, match in ((401, '401/403'), (403, '401/403'), (404, '404'), (500, 'no public fallback')):
        opener.code = code
        with pytest.raises(PreparationError) as error:
            snapshot.prepare(value, tmp_path / ('project-%d' % code))
        assert match in str(error.value)
        assert 'DISPOSABLE_SECRET' not in str(error.value)


def test_private_index_tls_defaults_and_explicit_exception(tmp_path, monkeypatch):
    import io
    import ssl
    from urllib.error import HTTPError
    import docassemble_executor.private_index as module
    index = 'https://example.invalid/scoped/simple/'
    contexts = []
    real = ssl.create_default_context

    def recording(*args, **kwargs):
        context = real(*args, **kwargs)
        contexts.append(context)
        return context

    monkeypatch.setattr(module.ssl, 'create_default_context', recording)
    snapshot = PrivateIndexSnapshot({'private': reference_file(tmp_path, index)})
    monkeypatch.setattr(snapshot, '_credential', lambda *args: 'Basic ZGlzcG9zYWJsZQ==')

    class Opener:
        def open(self, *args, **kwargs):
            raise HTTPError(index + 'sample/', 404, 'missing', {}, io.BytesIO(b''))

    monkeypatch.setattr(module, 'build_opener', lambda *args, **kwargs: Opener())
    with pytest.raises(PreparationError, match='404'):
        snapshot.prepare(inputs(), tmp_path / 'default')
    assert contexts[-1].verify_mode != ssl.CERT_NONE and contexts[-1].check_hostname
    value = inputs()
    value['baseline']['config']['private_tls_exceptions'] = ['private']
    with pytest.raises(PreparationError, match='404'):
        snapshot.prepare(value, tmp_path / 'exception')
    assert contexts[-1].verify_mode == ssl.CERT_NONE and not contexts[-1].check_hostname


def operator_root(tmp_path, labels=('private',), secret='DISPOSABLE_SECRET',
                  index='https://example.invalid/scoped/simple/'):
    root = tmp_path / 'credentials'
    root.mkdir(mode=0o700)
    for label in labels:
        reference = root / label
        reference.write_text(json.dumps({'index_url': index, 'username': 'user', 'password': secret}))
        reference.chmod(0o600)
    return root


def test_operator_reference_root_maps_one_owner_only_file_per_label(tmp_path):
    references = load_operator_references(operator_root(tmp_path, labels=('alpha', 'beta')))
    assert set(references) == {'alpha', 'beta'}
    assert all(path.stat().st_mode & 0o777 == 0o600 and path.name == label
               for label, path in references.items())


@pytest.mark.parametrize('kind', ['missing', 'root-mode', 'root-group', 'symlink-root', 'symlink-file',
                                  'file-mode', 'directory', 'bad-name', 'reserved', 'empty'])
def test_operator_reference_root_fails_closed(tmp_path, kind):
    root = operator_root(tmp_path)
    if kind == 'missing':
        root = tmp_path / 'absent'
    elif kind == 'root-mode':
        root.chmod(0o755)
    elif kind == 'root-group':
        root.chmod(0o750)
    elif kind == 'symlink-root':
        real = root.rename(tmp_path / 'real')
        (tmp_path / 'credentials').symlink_to(real)
    elif kind == 'symlink-file':
        (root / 'private').rename(tmp_path / 'target')
        (root / 'private').symlink_to(tmp_path / 'target')
    elif kind == 'file-mode':
        (root / 'private').chmod(0o644)
    elif kind == 'directory':
        (root / 'private').unlink()
        (root / 'private').mkdir()
    elif kind == 'bad-name':
        (root / 'private').rename(root / 'Private')
    elif kind == 'reserved':
        (root / 'private').rename(root / 'snapshot')
    else:
        (root / 'private').unlink()
    with pytest.raises(PreparationError, match='credential root') as error:
        load_operator_references(root)
    assert 'DISPOSABLE_SECRET' not in str(error.value)


def test_operator_reference_rotation_supersedes_and_revocation_fails_closed(tmp_path):
    index = 'https://example.invalid/scoped/simple/'
    root = operator_root(tmp_path, secret='FIRST')
    original = PrivateIndexSnapshot._credential(load_operator_references(root)['private'], index)
    # Rotation = replace the label file; the new token supersedes with no history rewrite.
    (root / 'private').write_text(json.dumps({'index_url': index, 'username': 'user', 'password': 'SECOND'}))
    (root / 'private').chmod(0o600)
    rotated = PrivateIndexSnapshot._credential(load_operator_references(root)['private'], index)
    assert rotated != original and base64.b64encode(b'user:SECOND').decode() in rotated
    # Revocation = delete the label file; the next use fails closed without an echo.
    (root / 'private').unlink()
    with pytest.raises(PreparationError, match='credential reference unavailable') as error:
        PrivateIndexSnapshot._credential(root / 'private', index)
    assert 'SECOND' not in str(error.value)


def test_registry_builder_reads_operator_root_only_for_a_private_policy(tmp_path):
    builder = RegistryBuilder(tmp_path / 'store', index_credential_root=operator_root(tmp_path))
    snapshot = builder._resolve_private_snapshot(inputs())
    assert snapshot is not None and set(snapshot.references) == {'private'}
    public = inputs()
    public['baseline']['config'] = {'index_url': 'https://pypi.org/simple'}
    assert builder._resolve_private_snapshot(public) is None


def test_registry_builder_missing_operator_root_fails_private_build_closed(tmp_path):
    builder = RegistryBuilder(tmp_path / 'store', '/missing/uv', index_credential_root=tmp_path / 'absent')
    with pytest.raises(PreparationError, match='credential root'):
        builder.build(inputs())
    assert not (tmp_path / 'store').exists()


def sdist_archive(tmp_path, build=None, *, suffix='.tar.gz', member=None, duplicate=False, symlink=False):
    import io
    import tarfile
    path = tmp_path / ('sample-1.0' + suffix)
    member = member or 'sample-1.0/pyproject.toml'
    data = (build if build is not None else
            '[build-system]\nrequires = ["private-backend>=1", "build-helper; python_version >= \'3.12\'"]\nbuild-backend = "private_backend"\n').encode()
    if suffix == '.zip':
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr(member, data)
    else:
        with tarfile.open(path, 'w:gz') as archive:
            for _ in range(2 if duplicate else 1):
                info = tarfile.TarInfo(member)
                info.size = len(data)
                if symlink:
                    info.type = tarfile.SYMTYPE
                    info.linkname = '/outside/DISPOSABLE_SECRET'
                    info.size = 0
                archive.addfile(info, None if symlink else io.BytesIO(data))
    return path


@pytest.mark.parametrize('suffix', ['.tar.gz', '.zip'])
def test_private_index_sdist_requires_independently_bound_build_prerequisites(tmp_path, suffix):
    path = sdist_archive(tmp_path, suffix=suffix)
    with pytest.raises(PreparationError, match='private build dependency private-backend of sdist sample lacks an explicit binding'):
        PrivateIndexSnapshot._validate_sdist(path, 'sample', {'sample': 'private'})
    with pytest.raises(PreparationError, match='private build dependency build-helper'):
        PrivateIndexSnapshot._validate_sdist(path, 'sample', {'sample': 'private', 'private-backend': 'private'})
    PrivateIndexSnapshot._validate_sdist(path, 'sample',
        {'sample': 'private', 'private-backend': 'private', 'build-helper': 'other'})


@pytest.mark.parametrize('declaration', [
    'backend @ https://DISPOSABLE_SECRET@example.invalid/backend.whl',
    'backend @ https://example.invalid/backend.whl?token=DISPOSABLE_SECRET',
    'backend @ git+ssh://example.invalid/repo', 'backend @ file:///tmp/backend.whl',
    '-r DISPOSABLE_SECRET', './DISPOSABLE_SECRET',
])
def test_private_index_sdist_rejects_unsupported_build_sources_without_secret_echo(tmp_path, declaration):
    path = sdist_archive(tmp_path, '[build-system]\nrequires = ' + json.dumps([declaration]) + '\nbuild-backend = "backend"\n')
    with pytest.raises(PreparationError) as error:
        PrivateIndexSnapshot._validate_sdist(path, 'sample', {'sample': 'private', 'backend': 'private'})
    assert 'DISPOSABLE_SECRET' not in str(error.value)


@pytest.mark.parametrize('build', [
    '', '[build-system]\nrequires = []\n',
    '[build-system]\nrequires = "backend"\nbuild-backend = "backend"\n',
    '[build-system]\nrequires = []\nbuild-backend = 42\n',
    '[build-system]\nrequires = []\nbuild-backend = ""\n',
    '[build-system]\nrequires = [42]\nbuild-backend = "backend"\n',
    'DISPOSABLE_SECRET', '#' * (1024 * 1024 + 1),
])
@pytest.mark.parametrize('suffix', ['.tar.gz', '.zip'])
def test_private_index_sdist_requires_bounded_explicit_pep517_metadata(tmp_path, build, suffix):
    path = sdist_archive(tmp_path, build, suffix=suffix)
    with pytest.raises(PreparationError) as error:
        PrivateIndexSnapshot._validate_sdist(path, 'sample', {'sample': 'private'})
    assert 'DISPOSABLE_SECRET' not in str(error.value)


@pytest.mark.parametrize('kind', ['identity', 'wrong-root', 'symlink', 'duplicate', 'corrupt'])
def test_private_index_sdist_refuses_ambiguous_or_mismatched_archive(tmp_path, kind):
    path = sdist_archive(tmp_path, member='other/pyproject.toml' if kind == 'wrong-root' else None,
                         duplicate=kind == 'duplicate', symlink=kind == 'symlink')
    if kind == 'corrupt':
        path.write_bytes(b'DISPOSABLE_SECRET')
    with pytest.raises(PreparationError, match='private sdist requires valid matching archive metadata') as error:
        PrivateIndexSnapshot._validate_sdist(path, 'different' if kind == 'identity' else 'sample',
                                              {'sample': 'private'})
    assert 'DISPOSABLE_SECRET' not in str(error.value)


def test_private_index_sdist_snapshot_is_bound_hashed_and_secret_free(tmp_path, monkeypatch):
    import io
    import hashlib
    import docassemble_executor.private_index as module
    source = sdist_archive(tmp_path, '[build-system]\nrequires = []\nbuild-backend = "local_backend"\nbackend-path = ["."]\n')
    data = source.read_bytes()
    index = inputs()['baseline']['config']['private_indexes']['private']
    link = source.name + '#sha256=' + hashlib.sha256(data).hexdigest()
    requests = []

    class Opener:
        def open(self, request, **kwargs):
            requests.append(request)
            return io.BytesIO(data if request.full_url.endswith('.tar.gz') else
                              ('<a href="' + link + '">sdist</a>').encode())

    snapshot = PrivateIndexSnapshot({'private': reference_file(tmp_path, index)})
    monkeypatch.setattr(module, 'build_opener', lambda *args: Opener())
    project = tmp_path / 'project'
    policy, artifacts = snapshot.prepare(inputs(), project)
    assert 'https://' not in policy and 'snapshot' in policy
    assert len(artifacts) == 1
    assert next(iter(artifacts.values())).read_bytes() == data
    assert [request.full_url for request in requests] == [index + 'sample/', index + 'sample/' + source.name]
    assert all(request.get_header('Authorization').startswith('Basic ') for request in requests)
    assert 'DISPOSABLE_SECRET' not in (project / 'private-sources.json').read_text()
    assert '.tar.gz#sha256=' in (project / 'private-indexes/snapshot/sample/index.html').read_text()


@pytest.mark.parametrize('target', [{'python': '3.12'}, {'python': '3.13'}, {'python': '3.14.4'},
    {'python': '>=3.12'}, {'python': ['3.14']}, {'python': '3.14', 'extra': 'key'}, {}])
def test_private_policy_foreign_target_refused_before_retrieval(monkeypatch, target):
    import sys
    from docassemble_executor.private_index import validate_private_policy
    monkeypatch.setattr(sys, 'version_info', (3, 14, 4))
    value = inputs()
    value['baseline']['target'] = target
    with pytest.raises(PreparationError):
        validate_private_policy(value)
