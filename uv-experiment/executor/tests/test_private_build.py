"""Private PEP 517 shim rejects source-policy bypasses before uv resolution."""
import copy
import types

import pytest

from docassemble_executor import _private_build_backend as shim
from docassemble_executor.preparation import PreparationError
from docassemble_executor.private_build import prepare_build_input
from test_private_index import sdist_archive


@pytest.fixture
def backend(monkeypatch):
    backend = types.ModuleType('fixture_backend')
    monkeypatch.setitem(__import__('sys').modules, 'fixture_backend', backend)
    monkeypatch.setattr(shim, '_POLICY', {'backend': 'fixture_backend',
        'requires': ['private-backend>=1'], 'bindings': ['private-backend', 'build-helper']})
    return backend


@pytest.mark.parametrize('hook', ['get_requires_for_build_wheel', 'get_requires_for_build_sdist',
                                 'get_requires_for_build_editable', 'get_requires_for_build_future'])
@pytest.mark.parametrize('requirement', ['unknown==1', 'build-helper @ file:///tmp/backend.whl',
    'build-helper @ https://DISPOSABLE_SECRET@example.invalid/a.whl',
    'build-helper @ https://example.invalid/a.whl?token=DISPOSABLE_SECRET',
    'build-helper @ git+https://example.invalid/repo', '/tmp/backend.whl',
    './build-helper', 'C:\\build-helper.whl', '-r /tmp/requirements.txt', 'build-helper\n',
    'build-helper/../../local', 'build-helper ; extra == "file:DISPOSABLE_SECRET"', 42])
def test_private_build_shim_rejects_every_non_bound_registry_hook_requirement(backend, hook, requirement):
    setattr(backend, hook, lambda config_settings=None: [requirement])
    with pytest.raises(ValueError, match='^private build requirements violate explicit registry bindings$'):
        getattr(shim, hook)()


@pytest.mark.parametrize('value', [None, 'build-helper', ('build-helper',), {'build-helper': 'private'}])
def test_private_build_shim_rejects_ambiguous_hook_result(backend, value):
    backend.get_requires_for_build_wheel = lambda config_settings=None: value
    with pytest.raises(ValueError, match='explicit registry bindings'):
        shim.get_requires_for_build_wheel()


def test_private_build_shim_preserves_requirements_and_delegates_backend_object(backend, monkeypatch):
    requirements = ['Build_Helper[extra]>=1,<3; python_version >= "3.12"', 'private-backend==1']
    original = copy.deepcopy(requirements)
    calls = []
    backend.Backend = types.SimpleNamespace(object=types.SimpleNamespace(
        get_requires_for_build_wheel=lambda config_settings=None: requirements,
        build_wheel=lambda *args: calls.append(args)))
    monkeypatch.setitem(shim._POLICY, 'backend', 'fixture_backend:Backend.object')
    assert shim.get_requires_for_build_wheel({'feature': 'yes'}) is requirements
    assert requirements == original
    shim.build_wheel('wheels', {'feature': 'yes'}, 'metadata')
    assert calls == [('wheels', {'feature': 'yes'}, 'metadata')]
    with pytest.raises(AttributeError):
        shim.get_requires_for_build_sdist()


def test_private_build_shim_rechecks_static_requires_before_backend_import(backend, monkeypatch):
    monkeypatch.setitem(shim._POLICY, 'requires', ['unknown'])
    with pytest.raises(ValueError, match='explicit registry bindings'):
        shim.build_wheel('wheels')


@pytest.mark.parametrize('suffix', ['.zip', '.tar.gz'])
def test_private_build_input_preserves_original_and_records_shimmed_copy(tmp_path, suffix):
    import hashlib
    import tarfile
    import tomllib
    import zipfile
    source = sdist_archive(tmp_path, suffix=suffix)
    original = source.read_bytes()
    destination = tmp_path / 'transformed' / source.name
    prepare_build_input(source, destination, {'private-backend': 'private', 'build-helper': 'private'})
    assert source.read_bytes() == original
    if suffix == '.zip':
        with zipfile.ZipFile(destination) as archive:
            data = {name: archive.read(name) for name in archive.namelist()}
    else:
        with tarfile.open(destination) as archive:
            data = {member.name: archive.extractfile(member).read() for member in archive}
    build = tomllib.loads(data['sample-1.0/pyproject.toml'].decode())['build-system']
    assert build['build-backend'] == '_private_build_backend'
    assert build['backend-path'] == ['.da-private-build']
    shim_data = data['sample-1.0/.da-private-build/_private_build_backend.py']
    assert b"'backend': 'private_backend'" in shim_data
    assert b"'bindings': ['build-helper', 'private-backend']" in shim_data
    first_digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    prepare_build_input(source, destination, {'private-backend': 'private', 'build-helper': 'private'})
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == first_digest


@pytest.mark.parametrize('path', ['../outside', '/outside', 'C:\\outside', '.da-private-build', 42])
def test_private_build_input_fails_closed_on_backend_paths_it_cannot_interpose(tmp_path, path):
    import json
    source = sdist_archive(tmp_path, '[build-system]\nrequires = []\nbuild-backend = "backend"\nbackend-path = ' + json.dumps([path]))
    with pytest.raises(PreparationError, match='build input cannot enforce explicit registry bindings'):
        prepare_build_input(source, tmp_path / 'copy' / source.name, {})


@pytest.mark.parametrize('policy', ['sources.build-helper = {path = "/outside"}',
    'index = [{url = "file:///outside"}]', 'find-links = ["/outside"]',
    'index-url = "https://DISPOSABLE_SECRET@example.invalid/simple"',
    'extra-index-url = ["file:///outside"]', 'default-index = "file:///outside"'])
def test_private_build_input_refuses_sdist_uv_source_overrides(tmp_path, policy):
    source = sdist_archive(tmp_path, '[build-system]\nrequires = []\nbuild-backend = "backend"\n[tool.uv]\n' + policy)
    with pytest.raises(PreparationError, match='^private sdist build input cannot enforce explicit registry bindings$'):
        prepare_build_input(source, tmp_path / 'copy' / source.name, {})


@pytest.mark.parametrize('kind', ['traversal', 'absolute', 'duplicate', 'reserved', 'symlink', 'hardlink'])
def test_private_build_input_refuses_unsafe_or_ambiguous_archive_features(tmp_path, kind):
    import io
    import tarfile
    source = sdist_archive(tmp_path)
    with tarfile.open(source) as archive:
        pyproject = archive.extractfile('sample-1.0/pyproject.toml').read()
    with tarfile.open(source, 'w:gz') as archive:
        member = tarfile.TarInfo('sample-1.0/pyproject.toml')
        member.size = len(pyproject)
        archive.addfile(member, io.BytesIO(pyproject))
        name = {'traversal': 'sample-1.0/../outside', 'absolute': '/outside',
                'duplicate': 'sample-1.0/pyproject.toml',
                'reserved': 'sample-1.0/.da-private-build/override.py'}.get(kind, 'sample-1.0/link')
        member = tarfile.TarInfo(name)
        if kind in ('symlink', 'hardlink'):
            member.type = tarfile.SYMTYPE if kind == 'symlink' else tarfile.LNKTYPE
            member.linkname = '/outside'
            archive.addfile(member)
        else:
            member.size = 1
            archive.addfile(member, io.BytesIO(b'x'))
    with pytest.raises(PreparationError, match='build input cannot enforce explicit registry bindings'):
        prepare_build_input(source, tmp_path / 'copy' / source.name, {})
