"""Mandatory image wheel bytes cannot be aliases into disposable cache."""
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
    (directory / 'pyproject.toml').write_text('[project]\nname = "fixture"\nversion = "1.0"\n')
    (directory / 'uv.lock').write_text('[[package]]\nname = "example"\nversion = "1.0"\n'
                                       'source = {path = "wheels/' + wheel.name + '"}\n')
    manifest = {'identity': {}, 'bundles': {'runtime-r1': hash_project(directory)},
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
