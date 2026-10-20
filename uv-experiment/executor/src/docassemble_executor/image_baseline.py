"""Operator-provisioned experimental image-wheel bridge. Never DB initialization.

Every baseline distribution is fixed to an exact image-lock wheel. Missing wheels
fail closed: this bridge never rebuilds or upgrades the image core.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tomllib
import urllib.parse
import urllib.request

from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename

from .bundles import sha256_file, verify_bundle
from .paths import ExecutorPaths
from .preparation import PreparationError
from .startup import target_id
from .registry_builder import INDEX, UV_VERSION


def validate_image_wheel_paths(directory: Path) -> None:
    """Mandatory image wheels must be files here, never aliases into a cache."""
    wheels = directory / 'wheels'
    if wheels.is_symlink() or (wheels.exists() and any(path.is_symlink() for path in wheels.iterdir())):
        raise PreparationError('image wheel paths must not contain symlinks')


def provision_image_baseline(paths: ExecutorPaths, destination: Path, bundle_name: str = 'runtime-r1') -> Path:
    """Explicit operator provisioning, not boot/adoption or runtime inference."""
    validate_image_wheel_paths(destination)
    manifest = json.loads(paths.manifest_file.read_text())
    directory = paths.bundles_dir / bundle_name
    if not verify_bundle(directory, manifest, bundle_name)[0]:
        raise PreparationError('image baseline bundle mismatch')
    destination.mkdir(mode=0o755, parents=True, exist_ok=True)
    wheels = destination / 'wheels'
    wheels.mkdir(exist_ok=True)
    expected = manifest['inventories'][bundle_name]['distributions']
    packages = tomllib.loads((directory / 'uv.lock').read_text())['package']
    tags = set(sys_tags())
    core, retained = {}, {}
    for package in packages:
        name = canonicalize_name(package['name'])
        if name not in expected:
            continue
        if package['version'] != expected[name]:
            raise PreparationError('image baseline inventory mismatch')
        source = package['source']
        if set(source) == {'path'} and source['path'].startswith('wheels/'):
            original = directory / source['path']
            filename, wheel_hash = original.name, sha256_file(original)
            shutil.copyfile(original, wheels / filename)
        elif source == {'registry': INDEX}:
            compatible = [item for item in package.get('wheels', [])
                          if parse_wheel_filename(Path(urllib.parse.urlsplit(item['url']).path).name)[3] & tags]
            if not compatible:
                raise PreparationError('exact image wheel unavailable; rebuilding core is unsupported')
            item = compatible[0]
            url = urllib.parse.urlsplit(item['url'])
            if url.scheme != 'https' or url.hostname != 'files.pythonhosted.org' or url.query or url.username:
                raise PreparationError('unsupported image wheel origin')
            filename, wheel_hash = Path(url.path).name, item['hash'].removeprefix('sha256:')
            with urllib.request.urlopen(item['url'], timeout=60) as response:
                if urllib.parse.urlsplit(response.geturl()).hostname != 'files.pythonhosted.org':
                    raise PreparationError('unsupported image wheel redirect')
                (wheels / filename).write_bytes(response.read())
        else:
            raise PreparationError('unsupported image wheel source')
        if sha256_file(wheels / filename) != wheel_hash:
            raise PreparationError('image wheel digest mismatch')
        retained[name] = filename
        core[name] = {'version': expected[name], 'source_digest': wheel_hash}
    if set(core) != set(expected):
        raise PreparationError('incomplete exact image core')
    baseline = {'core': core, 'target': {'python': '3.12'}, 'tool': {'uv': UV_VERSION},
                'config': {'index_url': INDEX},
                'image_baseline': {'image_target': target_id(manifest), 'bundle': bundle_name,
                                   'bundle_digest': manifest['bundles'][bundle_name]}}
    output = destination / 'baseline.json'
    output.write_text(json.dumps({'format': 1, 'baseline': baseline, 'wheels': retained}, sort_keys=True, indent=2))
    return output


def load_image_wheels(descriptor: Path, paths: ExecutorPaths) -> tuple[dict, dict[str, Path]]:
    validate_image_wheel_paths(descriptor.parent)
    document = json.loads(descriptor.read_text())
    baseline = document['baseline']
    manifest = json.loads(paths.manifest_file.read_text())
    identity = baseline['image_baseline']
    name = identity['bundle']
    if (identity['image_target'] != target_id(manifest) or
            identity['bundle_digest'] != manifest['bundles'].get(name) or
            not verify_bundle(paths.bundles_dir / name, manifest, name)[0]):
        raise PreparationError('qualified image identity mismatch')
    expected = manifest['inventories'][name]['distributions']
    if {key: value['version'] for key, value in baseline['core'].items()} != expected:
        raise PreparationError('qualified image core differs from image inventory')
    locked = {canonicalize_name(package['name']): package for package in
              tomllib.loads((paths.bundles_dir / name / 'uv.lock').read_text())['package']}
    wheels = {}
    for name, filename in document['wheels'].items():
        if Path(filename).name != filename or not filename.endswith('.whl'):
            raise PreparationError('invalid image wheel name')
        path = descriptor.parent / 'wheels' / filename
        metadata = parse_wheel_filename(filename)
        package = locked[name]
        if package['source'] == {'path': 'wheels/' + filename}:
            allowed = {sha256_file(paths.bundles_dir / identity['bundle'] / 'wheels' / filename)}
        elif package['source'] == {'registry': INDEX}:
            allowed = {item['hash'].removeprefix('sha256:') for item in package.get('wheels', [])
                       if Path(urllib.parse.urlsplit(item['url']).path).name == filename}
        else:
            allowed = set()
        if (canonicalize_name(metadata[0]) != name or str(metadata[1]) != baseline['core'][name]['version'] or
                sha256_file(path) != baseline['core'][name]['source_digest'] or
                baseline['core'][name]['source_digest'] not in allowed):
            raise PreparationError('qualified image wheel mismatch')
        wheels[name] = path
    if set(wheels) != set(baseline['core']):
        raise PreparationError('incomplete qualified image wheels')
    return baseline, wheels


def verify_image_recovery_core(paths: ExecutorPaths) -> None:
    """Rollback must retain the original recovery bundle as well as image wheels."""
    manifest = json.loads(paths.manifest_file.read_text())
    name = 'core-' + manifest.get('identity', {}).get('core_baseline', 'r1')
    if not verify_bundle(paths.bundles_dir / name, manifest, name)[0]:
        raise PreparationError('retained image recovery core unavailable')
