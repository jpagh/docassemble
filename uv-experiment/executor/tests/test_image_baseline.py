"""Mandatory image wheel bytes cannot be aliases into disposable cache."""
from docassemble.base.managed_python import python_requires, python_target
from dataclasses import replace
import json
import os
import shutil
import subprocess
import sys
import zipfile

import pytest

from docassemble_executor import cache
from docassemble_executor.bundles import hash_project
from docassemble_executor.image_baseline import load_image_wheels, provision_image_baseline
from docassemble_executor.paths import ExecutorPaths
from docassemble_executor.preparation import PreparationError


@pytest.fixture
def image(tmp_path, monkeypatch):
    root = tmp_path / 'retained'
    paths = replace(ExecutorPaths.from_env(), da_root=root, bundles_dir=root / 'bundles',
                    state_dir=root / 'state', runtime_venv=root / 'venv', run_dir=root / 'run',
                    log_dir=root / 'logs', config_file=root / 'config.yml', intent_file=root / 'intent.json',
                    journal_file=root / 'journal.json', applied_file=root / 'applied.json',
                    status_file=root / 'status.json', manifest_file=root / 'bundles/manifest.json',
                    cache_dir=tmp_path / 'cache')
    for name in cache.RETAINED_ENV_PATHS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cache, 'SUPPORTED_CACHE', paths.cache_dir)
    paths.state_dir.mkdir(parents=True)
    directory = paths.bundles_dir / 'runtime-r1'
    wheel = directory / 'wheels/example-1.0-py3-none-any.whl'
    wheel.parent.mkdir(parents=True)
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('example-1.0.dist-info/METADATA', 'Metadata-Version: 2.1\nName: example\nVersion: 1.0\n')
        archive.writestr('example-1.0.dist-info/WHEEL', 'Wheel-Version: 1.0\nTag: py3-none-any\n')
    (directory / 'pyproject.toml').write_text('[project]\nname = "fixture"\nversion = "1.0"\nrequires-python = ' + json.dumps(python_requires()) + '\n')
    (directory / 'uv.lock').write_text('requires-python = ' + json.dumps(python_requires()) + '\n[[package]]\nname = "example"\nversion = "1.0"\n'
                                       'source = {path = "wheels/' + wheel.name + '"}\n')
    manifest = {'identity': {'python_version': '.'.join(map(str, sys.version_info[:3]))}, 'bundles': {'runtime-r1': hash_project(directory)},
                'inventories': {'runtime-r1': {'distributions': {'example': '1.0'}}},
                'executor_inventory': {}, 'uv_sha256': 'fixture', 'uv_binary_sha256': 'fixture'}
    paths.manifest_file.write_text(json.dumps(manifest))
    descriptor = provision_image_baseline(paths, root / 'image')
    baseline, wheels = load_image_wheels(descriptor, paths)
    assert set(wheels) == set(baseline['core']) == {'example'}
    return paths, descriptor, wheels['example']


def alias_wheel(paths, wheel, kind):
    cached = paths.cache_dir / 'mandatory' / wheel.name
    cached.parent.mkdir(parents=True)
    shutil.move(wheel, cached)
    if kind == 'directory':
        wheel.parent.rmdir()
        wheel.parent.symlink_to(cached.parent, target_is_directory=True)
    else:
        wheel.symlink_to(cached)
    return cached


@pytest.mark.parametrize('kind', ['directory', 'file'])
def test_load_image_wheels_rejects_alias_even_with_matching_manifest_and_wheel_hash(image, kind):
    paths, descriptor, wheel = image
    cached = alias_wheel(paths, wheel, kind)
    assert wheel.read_bytes() == cached.read_bytes()
    with pytest.raises(PreparationError, match='must not contain symlinks'):
        load_image_wheels(descriptor, paths)


@pytest.mark.parametrize('kind', ['directory', 'file'])
def test_provision_image_baseline_refuses_existing_alias_before_overwriting_bytes(image, kind):
    paths, descriptor, wheel = image
    cached = alias_wheel(paths, wheel, kind)
    cached.write_bytes(b'previously retained material')
    before = descriptor.read_bytes()
    with pytest.raises(PreparationError, match='must not contain symlinks'):
        provision_image_baseline(paths, descriptor.parent)
    assert cached.read_bytes() == b'previously retained material'
    assert descriptor.read_bytes() == before


@pytest.mark.parametrize('kind', ['directory', 'file'])
def test_broken_wheel_aliases_are_rejected_before_read_or_write(image, kind):
    paths, descriptor, wheel = image
    cached = alias_wheel(paths, wheel, kind)
    cached.unlink()
    with pytest.raises(PreparationError, match='must not contain symlinks'):
        load_image_wheels(descriptor, paths)
    with pytest.raises(PreparationError, match='must not contain symlinks'):
        provision_image_baseline(paths, descriptor.parent)
    assert not cached.exists()


@pytest.mark.parametrize('kind', ['directory', 'file'])
def test_cache_validation_rejects_nested_wheel_aliases_outside_retained_root(image, monkeypatch, kind):
    paths, descriptor, wheel = image
    cached = alias_wheel(paths, wheel, kind)
    monkeypatch.setenv('DA_QUALIFIED_IMAGE_BASELINE', str(descriptor))
    with pytest.raises(cache.CacheError, match='wheel layout is unsafe'):
        cache.validate_cache(paths)
    assert cached.read_bytes() == wheel.read_bytes()


@pytest.fixture
def destructive_uv(tmp_path):
    binary = shutil.which(os.environ.get('UV_BINARY', 'uv'))
    if binary is None or subprocess.check_output([binary, '--version'], text=True).split()[:2] != ['uv', '0.12.21']:
        pytest.skip('real destructive-boundary regression requires UV_BINARY pointing to pinned uv 0.12.21')
    trace = tmp_path / 'tool-invocations'
    wrapper = tmp_path / 'traced-uv'
    wrapper.write_text('#!' + sys.executable + '\nimport os, sys\n'
                       + f'with open({str(trace)!r}, "a") as stream: stream.write(repr(sys.argv[1:]) + "\\n")\n'
                       + f'os.execv({binary!r}, [{binary!r}, *sys.argv[1:]])\n')
    wrapper.chmod(0o755)
    return wrapper, trace


@pytest.mark.parametrize('action', ['clear', 'prune'])
@pytest.mark.parametrize('layout', ['standalone-store', 'directory', 'file'])
def test_real_uv_destructive_boundary_refuses_retained_overlap_before_any_tool(image, destructive_uv,
                                                                             monkeypatch, action, layout):
    paths, descriptor, wheel = image
    wrapper, trace = destructive_uv
    # Positive control: the real pinned cleaner destroys a wheel at this exact
    # cache location. Invalid layouts below must not reach even uv --version.
    victim = paths.cache_dir / 'mandatory' / wheel.name
    victim.parent.mkdir(parents=True)
    victim.write_bytes(wheel.read_bytes())
    subprocess.run([str(wrapper), 'cache', 'clean', '--cache-dir', str(paths.cache_dir),
                    '--no-config', '--offline'], check=True, capture_output=True)
    assert not victim.exists()
    assert trace.exists()
    trace.unlink()
    paths = replace(paths, uv_binary=str(wrapper))
    monkeypatch.setattr(cache.os, 'geteuid', lambda: 0)
    if layout == 'standalone-store':
        # An explicit standalone store is rejected before preparation could
        # place durable bytes here, even with no DA_DURABLE_WORK_STORE set.
        with pytest.raises(cache.CacheError, match='overlaps'):
            cache.validate_retained_paths(paths, [paths.cache_dir / 'mandatory'])
        assert not paths.cache_dir.exists()
    else:
        cached = alias_wheel(paths, wheel, layout)
        before = cached.read_bytes()
        monkeypatch.setenv('DA_QUALIFIED_IMAGE_BASELINE', str(descriptor))
        with pytest.raises(cache.CacheError, match='wheel layout is unsafe'):
            cache.manage_cache(paths, action)
        assert cached.read_bytes() == before
        assert descriptor.exists()
    assert not trace.exists()


@pytest.mark.parametrize('fault', ['target', 'manifest', 'runtime', 'lock'])
def test_image_baseline_refuses_foreign_identity_before_provision_writes(image, fault):
    paths, descriptor, wheel = image
    before = descriptor.read_bytes(), wheel.read_bytes()
    if fault == 'target':
        document = json.loads(descriptor.read_text())
        document['baseline']['target'] = {'python': '3.12'}
        descriptor.write_text(json.dumps(document))
        with pytest.raises(PreparationError):
            load_image_wheels(descriptor, paths)
        return
    if fault == 'manifest':
        manifest = json.loads(paths.manifest_file.read_text())
        manifest['identity']['python_version'] = '3.12.14'
        paths.manifest_file.write_text(json.dumps(manifest))
    elif fault == 'runtime':
        python = paths.runtime_venv / 'bin/python'
        python.parent.mkdir(parents=True)
        python.symlink_to('/foreign/protected/python')
    else:
        directory = paths.bundles_dir / 'runtime-r1'
        (directory / 'uv.lock').write_text('requires-python="==3.12.*"\n')
        manifest = json.loads(paths.manifest_file.read_text())
        manifest['bundles']['runtime-r1'] = hash_project(directory)
        paths.manifest_file.write_text(json.dumps(manifest))
    with pytest.raises(PreparationError, match='image baseline bundle mismatch' if fault == 'lock'
                       else 'qualified image identity mismatch'):
        provision_image_baseline(paths, descriptor.parent)
    assert (descriptor.read_bytes(), wheel.read_bytes()) == before
    with pytest.raises(PreparationError, match='qualified image identity mismatch'):
        load_image_wheels(descriptor, paths)


def test_image_baseline_target_is_observed_without_runtime(image):
    paths, descriptor, _ = image
    assert not paths.runtime_venv.exists()
    assert load_image_wheels(descriptor, paths)[0]['target'] == python_target()


@pytest.fixture
def registry_image(image):
    from registry_wheel_fixture import reusable
    paths, _, _ = image
    directory = paths.bundles_dir / 'runtime-r1'
    artifact = reusable(directory / 'wheels')
    with (directory / 'uv.lock').open('a') as stream:
        stream.write('\n[[package]]\nname="numpy"\nversion="2.2.6"\nsource={path="wheels/' + artifact.path.name + '"}\n')
    (directory / 'source-artifacts.json').write_text(json.dumps({artifact.path.name:
        dict(artifact.origin, wheel_hash=artifact.wheel_hash)}))
    manifest = json.loads(paths.manifest_file.read_text())
    manifest['inventories']['runtime-r1']['distributions']['numpy'] = '2.2.6'
    manifest['bundles']['runtime-r1'] = hash_project(directory)
    paths.manifest_file.write_text(json.dumps(manifest))
    descriptor = provision_image_baseline(paths, paths.da_root / 'registry-image')
    return paths, descriptor, artifact


def test_image_registry_wheels_require_current_target_source_and_bytes(registry_image):
    from docassemble_executor.image_baseline import load_image_registry_wheels
    paths, descriptor, artifact = registry_image
    result = load_image_registry_wheels(descriptor, paths)
    assert result == {'numpy': artifact.__class__(artifact.path, artifact.wheel_hash, artifact.origin,
        json.loads(descriptor.read_text())['baseline']['image_baseline'])}


@pytest.mark.parametrize('fault', ['target', 'lock-target', 'bytes', 'missing-hash', 'origin-source', 'origin-url',
    'symlink', 'directory-alias', 'traversal', 'malformed', 'tag', 'metadata-name', 'metadata-version', 'duplicate', 'missing-origin'])
def test_image_registry_wheels_reject_dark_material(registry_image, fault):
    from registry_wheel_fixture import wheel
    from docassemble_executor.image_baseline import load_image_registry_wheels
    paths, descriptor, artifact = registry_image
    directory = artifact.path.parent.parent
    document = json.loads(descriptor.read_text())
    origins = json.loads((directory / 'source-artifacts.json').read_text())
    if fault == 'target':
        document['baseline']['target'] = {'python': '3.12'}
    elif fault == 'lock-target':
        (directory / 'uv.lock').write_text((directory / 'uv.lock').read_text().replace(python_requires(), '==3.12.*'))
    elif fault == 'bytes':
        artifact.path.write_bytes(b'changed')
        with pytest.raises(PreparationError):
            load_image_registry_wheels(descriptor, paths)
        return
    elif fault == 'missing-hash':
        origins[artifact.path.name].pop('wheel_hash')
    elif fault == 'missing-origin':
        origins.pop(artifact.path.name)
        # A fixed local numpy row without its origin must not claim reuse.
        (directory / 'source-artifacts.json').write_text(json.dumps(origins))
        manifest = json.loads(paths.manifest_file.read_text())
        manifest['bundles']['runtime-r1'] = hash_project(directory)
        paths.manifest_file.write_text(json.dumps(manifest))
        document['baseline']['image_baseline']['bundle_digest'] = manifest['bundles']['runtime-r1']
        # target_id includes the manifest; refresh the authenticated descriptor.
        from docassemble_executor.startup import target_id
        document['baseline']['image_baseline']['image_target'] = target_id(manifest)
        descriptor.write_text(json.dumps(document))
        assert load_image_registry_wheels(descriptor, paths) == {}
        return
    elif fault.startswith('origin-'):
        if fault == 'origin-source':
            origins[artifact.path.name]['source'] = {'registry': 'https://private.invalid/simple'}
        else:
            origins[artifact.path.name]['sdist']['url'] = 'https://files.pythonhosted.org/a?secret=1'
    elif fault == 'symlink':
        target = paths.da_root / 'cache-wheel'
        shutil.move(artifact.path, target)
        artifact.path.symlink_to(target)
    elif fault == 'directory-alias':
        target = paths.da_root / 'cache-wheels'
        shutil.move(artifact.path.parent, target)
        artifact.path.parent.symlink_to(target)
    elif fault == 'traversal':
        origins['../' + artifact.path.name] = origins.pop(artifact.path.name)
    elif fault == 'duplicate':
        origins['numpy-2.2.6-1-py3-none-any.whl'] = origins[artifact.path.name]
    else:
        kwargs = {'tag': 'cp312-cp312-win_amd64'} if fault == 'tag' else (
            {'metadata_name': 'scipy'} if fault == 'metadata-name' else {'metadata_version': '9.0'})
        bad = wheel(artifact.path.parent, **kwargs) if fault != 'malformed' else artifact.path
        if fault == 'malformed':
            bad.write_bytes(b'not zip')
        if bad != artifact.path:
            artifact.path.unlink()
            origins[bad.name] = origins.pop(artifact.path.name)
            lock = directory / 'uv.lock'
            lock.write_text(lock.read_text().replace(artifact.path.name, bad.name))
            document['wheels']['numpy'] = bad.name
            (descriptor.parent / 'wheels' / bad.name).write_bytes(bad.read_bytes())
        from docassemble_executor.bundles import sha256_file
        origins[bad.name]['wheel_hash'] = sha256_file(bad)
        document['baseline']['core']['numpy']['source_digest'] = sha256_file(bad)
        (descriptor.parent / 'wheels' / document['wheels']['numpy']).write_bytes(bad.read_bytes())
    (directory / 'source-artifacts.json').write_text(json.dumps(origins))
    manifest = json.loads(paths.manifest_file.read_text())
    manifest['bundles']['runtime-r1'] = hash_project(directory)
    paths.manifest_file.write_text(json.dumps(manifest))
    from docassemble_executor.startup import target_id
    document['baseline']['image_baseline'].update(bundle_digest=manifest['bundles']['runtime-r1'], image_target=target_id(manifest))
    descriptor.write_text(json.dumps(document))
    with pytest.raises(PreparationError):
        load_image_registry_wheels(descriptor, paths)


@pytest.mark.parametrize('case', ['well-formed', 'parser-defect', 'missing', 'duplicate'])
def test_validate_registry_wheel_rejects_malformed_metadata_in_readable_archive(tmp_path, case):
    from email.parser import BytesParser
    from registry_wheel_fixture import METADATA_CASES, origin, wheel
    from docassemble_executor.bundles import sha256_file
    from docassemble_executor.image_baseline import validate_registry_wheel
    metadata = METADATA_CASES[case]
    path = wheel(tmp_path, metadata_bytes=metadata)
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assert archive.read('numpy-2.2.6.dist-info/METADATA') == metadata
    parsed = BytesParser().parsebytes(metadata)
    if case == 'parser-defect':
        assert parsed.defects and parsed.get_all('Requires-Dist') is None
        assert 'Requires-Dist: hidden' in parsed.get_payload()
    else:
        assert not parsed.defects
    if case == 'well-formed':
        assert validate_registry_wheel(path, origin()) == sha256_file(path)
    else:
        with pytest.raises(PreparationError, match='invalid image registry wheel'):
            validate_registry_wheel(path, origin())


@pytest.mark.parametrize('value,accepted', [('1.1', True), ('2.1', True), ('9.3', True), ('01.00', True),
    ('', False), ('garbage', False), ('2', False), ('2.1rc1', False), ('2.1.dev0', False),
    ('0.1', False), ('00.2', False), ('-1.1', False), ('2.1 ', False), ('2. 1', False),
    ('"2.1"', False), ('2.1.0', False), ('２.１', False)])
def test_validate_registry_wheel_metadata_version_requires_single_ascii_major_minor(tmp_path, value, accepted):
    from registry_wheel_fixture import origin, version_metadata, wheel
    from docassemble_executor.image_baseline import validate_registry_wheel
    path = wheel(tmp_path, metadata_bytes=version_metadata(value))
    if accepted:
        assert validate_registry_wheel(path, origin())
    else:
        with pytest.raises(PreparationError, match='invalid image registry wheel'):
            validate_registry_wheel(path, origin())


@pytest.mark.parametrize('case', ['well-formed', 'parser-defect', 'missing', 'duplicate'])
def test_load_image_registry_wheels_rejects_authenticated_malformed_metadata(registry_image, case):
    from registry_wheel_fixture import METADATA_CASES, wheel
    from docassemble_executor.bundles import sha256_file
    from docassemble_executor.image_baseline import load_image_registry_wheels
    from docassemble_executor.startup import target_id
    paths, descriptor, artifact = registry_image
    directory = artifact.path.parent.parent
    path = wheel(artifact.path.parent, metadata_bytes=METADATA_CASES[case])
    origins = {path.name: dict(artifact.origin, wheel_hash=sha256_file(path))}
    (directory / 'source-artifacts.json').write_text(json.dumps(origins))
    manifest = json.loads(paths.manifest_file.read_text())
    manifest['bundles']['runtime-r1'] = hash_project(directory)
    paths.manifest_file.write_text(json.dumps(manifest))
    document = json.loads(descriptor.read_text())
    document['baseline']['image_baseline'].update(bundle_digest=manifest['bundles']['runtime-r1'],
                                                  image_target=target_id(manifest))
    document['baseline']['core']['numpy']['source_digest'] = sha256_file(path)
    (descriptor.parent / 'wheels' / path.name).write_bytes(path.read_bytes())
    descriptor.write_text(json.dumps(document))
    # All byte/identity gates pass; refusal must be METADATA format, not a hash.
    baseline, _ = load_image_wheels(descriptor, paths)
    assert baseline == document['baseline']
    if case == 'well-formed':
        assert load_image_registry_wheels(descriptor, paths)['numpy'].wheel_hash == sha256_file(path)
    else:
        with pytest.raises(PreparationError, match='invalid image registry wheel'):
            load_image_registry_wheels(descriptor, paths)


@pytest.mark.parametrize('header,parsed_values,accepted', [
    ('Metadata-Version: 2.1\n', ['2.1'], True),
    ('Metadata-Version:   2.1\n', ['2.1'], True),
    ('Metadata-Version: \n 2.1\n', ['2.1'], True),
    ('Metadata-Version: 2.\n 1\n', ['2.\n 1'], False),
    ('Metadata-Version: 2.1 \n', ['2.1 '], False),
    ('Metadata-Version: 2.1\nMetadata-Version: 2.1\n', ['2.1', '2.1'], False),
], ids=['plain', 'padded', 'folded', 'internal-newline', 'trailing-space', 'duplicate'])
def test_validate_registry_wheel_metadata_version_uses_parsed_folding_and_padding(tmp_path, header, parsed_values, accepted):
    from email.parser import BytesParser
    from registry_wheel_fixture import origin, wheel
    from docassemble_executor.bundles import sha256_file
    from docassemble_executor.image_baseline import validate_registry_wheel
    metadata = (header + 'Name: numpy\nVersion: 2.2.6\n').encode()
    parsed = BytesParser().parsebytes(metadata)
    assert not parsed.defects
    assert parsed.get_all('Metadata-Version') == parsed_values
    assert parsed['Name'] == 'numpy' and parsed['Version'] == '2.2.6'
    path = wheel(tmp_path, metadata_bytes=metadata)
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assert archive.read('numpy-2.2.6.dist-info/METADATA') == metadata
    if accepted:
        assert validate_registry_wheel(path, origin()) == sha256_file(path)
    else:
        with pytest.raises(PreparationError, match='invalid image registry wheel'):
            validate_registry_wheel(path, origin())
