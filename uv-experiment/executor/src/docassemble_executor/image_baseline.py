"""Operator-provisioned experimental image-wheel bridge. Never DB initialization.

Every baseline distribution is fixed to an exact image-lock wheel. Missing wheels
fail closed: this bridge never rebuilds or upgrades the image core.
"""
from __future__ import annotations

from docassemble.base.managed_python import python_target

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import sys
import tomllib
import urllib.parse
import urllib.request

from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename

from .bundles import sha256_file, verify_bundle, verify_python_target
from .paths import ExecutorPaths
from .preparation import PreparationError
from .startup import target_id
from .registry_builder import INDEX, UV_VERSION


def validate_image_wheel_paths(directory: Path) -> None:
    """Mandatory image wheels must be files here, never aliases into a cache."""
    wheels = directory / 'wheels'
    if wheels.is_symlink() or (wheels.exists() and any(path.is_symlink() for path in wheels.iterdir())):
        raise PreparationError('image wheel paths must not contain symlinks')


def _validate_interpreter(paths: ExecutorPaths, manifest: dict) -> None:
    actual = '.'.join(str(part) for part in sys.version_info[:3])
    runtime = paths.runtime_venv / 'bin/python'
    if (manifest.get('identity', {}).get('python_version') != actual
            or ((runtime.exists() or runtime.is_symlink())
                and runtime.resolve() != Path(sys.executable).resolve())):
        raise PreparationError('qualified image identity mismatch')


def provision_image_baseline(paths: ExecutorPaths, destination: Path, bundle_name: str = 'runtime-r1') -> Path:
    """Explicit operator provisioning, not boot/adoption or runtime inference."""
    validate_image_wheel_paths(destination)
    manifest = json.loads(paths.manifest_file.read_text())
    _validate_interpreter(paths, manifest)
    directory = paths.bundles_dir / bundle_name
    if (not verify_python_target(directory / 'pyproject.toml', directory / 'uv.lock')
            or not verify_bundle(directory, manifest, bundle_name)[0]):
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
    baseline = {'core': core, 'target': python_target(), 'tool': {'uv': UV_VERSION},
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
    _validate_interpreter(paths, manifest)
    if document.get('format') != 1 or baseline.get('target') != python_target():
        raise PreparationError('qualified image identity mismatch')
    identity = baseline['image_baseline']
    name = identity['bundle']
    if (identity['image_target'] != target_id(manifest) or
            identity['bundle_digest'] != manifest['bundles'].get(name) or
            not verify_python_target(paths.bundles_dir / name / 'pyproject.toml', paths.bundles_dir / name / 'uv.lock') or
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
    _validate_interpreter(paths, manifest)
    if (not verify_python_target(paths.bundles_dir / name / 'pyproject.toml', paths.bundles_dir / name / 'uv.lock')
            or not verify_bundle(paths.bundles_dir / name, manifest, name)[0]):
        raise PreparationError('retained image recovery core unavailable')


# Preparation-only reuse, never exact core authority or a resolver wheelhouse.
IMAGE_REGISTRY_PACKAGES = frozenset({'numpy', 'scipy'})


def public_sdist_identity(package: dict) -> dict:
    """Public sdist identity; version equality alone never authorizes reuse."""
    import re
    try:
        name = canonicalize_name(package['name'])
        source = package['source']
        sdist = package['sdist']
        url = urllib.parse.urlsplit(sdist['url'])
        if (source != {'registry': INDEX}
                or url.scheme != 'https' or url.hostname != 'files.pythonhosted.org'
                or url.query or url.fragment or url.username or url.password
                or not re.fullmatch(r'sha256:[0-9a-f]{64}', sdist['hash'])
                or not isinstance(package['version'], str)):
            raise ValueError
        return {'name': name, 'version': package['version'], 'source': source,
                'sdist': {'url': sdist['url'], 'hash': sdist['hash']}}
    except (KeyError, TypeError, ValueError):
        raise PreparationError('invalid image registry source identity') from None


def registry_sdist_identity(package: dict) -> dict:
    identity = public_sdist_identity(package)
    if identity['name'] not in IMAGE_REGISTRY_PACKAGES:
        raise PreparationError('ineligible image registry package')
    return identity


def validate_registry_wheel(path: Path, origin: dict) -> str:
    """Validate regular bytes, target tags and archive identity before sharing.

    Metadata-Version is checked on the same parsed document as Name/Version:
    exactly one ASCII major.minor value with positive major is required. Legacy
    1.1 and future 9.3 values are accepted; no supported-version list is imposed.
    Header padding (``Metadata-Version:   2.1``) and a folded continuation
    (``Metadata-Version: \\n 2.1``) are deliberately accepted when parsed as 2.1.
    Missing/duplicate fields, parser defects, zero major, empty/quoted/non-ASCII
    values, prerelease/dev forms, and internal or trailing whitespace in the
    parsed value are refused. Raw header whitespace is not a separate authority.
    """
    from email.parser import BytesParser
    import re
    from zipfile import ZipFile, BadZipFile
    try:
        identity = public_sdist_identity(origin)
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents) or not path.is_file():
            raise ValueError
        name, version, _, tags = parse_wheel_filename(path.name)
        if (canonicalize_name(name) != identity['name'] or str(version) != identity['version']
                or not tags & set(sys_tags())):
            raise ValueError
        with ZipFile(path) as archive:
            members = archive.namelist()
            if len(members) != len(set(members)) or any(
                    member.startswith('/') or '..' in Path(member).parts for member in members):
                raise ValueError
            metadata_names = [member for member in members if member.endswith('.dist-info/METADATA')]
            if len(metadata_names) != 1:
                raise ValueError
            metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
            metadata_versions = metadata.get_all('Metadata-Version', [])
            if (metadata.defects or len(metadata_versions) != 1
                    or not re.fullmatch(r'[0-9]+\.[0-9]+', metadata_versions[0])
                    or not metadata_versions[0].split('.')[0].strip('0')):
                raise ValueError
            if (metadata.get_all('Name') != [metadata['Name']]
                    or metadata.get_all('Version') != [identity['version']]
                    or canonicalize_name(metadata['Name']) != identity['name']):
                raise ValueError
        wheel_hash = sha256_file(path)
        if origin.get('wheel_hash', wheel_hash) != wheel_hash:
            raise ValueError
        return wheel_hash
    except (OSError, KeyError, TypeError, ValueError, BadZipFile):
        raise PreparationError('invalid image registry wheel') from None


@dataclass(frozen=True)
class ImageRegistryWheel:
    path: Path
    wheel_hash: str
    origin: dict
    image_identity: dict


def load_image_registry_wheels(descriptor: Path, paths: ExecutorPaths) -> dict[str, ImageRegistryWheel]:
    """Current manifest-backed build artifacts, separate from fixed core wheels."""
    baseline, _ = load_image_wheels(descriptor, paths)
    identity = baseline['image_baseline']
    directory = paths.bundles_dir / identity['bundle']
    if directory.is_symlink() or any(parent.is_symlink() for parent in directory.parents):
        raise PreparationError('invalid image registry bundle path')
    manifest = json.loads(paths.manifest_file.read_text())
    if (not verify_bundle(directory, manifest, identity['bundle'])[0]
            or not verify_python_target(directory / 'pyproject.toml', directory / 'uv.lock')):
        raise PreparationError('qualified image registry bundle mismatch')
    validate_image_wheel_paths(directory)
    origin_file = directory / 'source-artifacts.json'
    if origin_file.is_symlink():
        raise PreparationError('invalid image registry origin path')
    if not origin_file.exists():
        return {}
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise PreparationError('conflicting image registry origins')
            result[key] = value
        return result
    try:
        origins = json.loads(origin_file.read_text(), object_pairs_hook=unique_pairs)
        locked = tomllib.loads((directory / 'uv.lock').read_text())['package']
        result = {}
        for filename, origin in origins.items():
            name = canonicalize_name(origin['name'])
            if name not in IMAGE_REGISTRY_PACKAGES:
                continue
            if Path(filename).name != filename or name in result:
                raise PreparationError('conflicting image registry wheel paths')
            source = registry_sdist_identity(origin)
            rows = [row for row in locked if canonicalize_name(row['name']) == name]
            if (len(rows) != 1 or rows[0]['version'] != source['version']
                    or rows[0]['source'] != {'path': 'wheels/' + filename}
                    or manifest['inventories'][identity['bundle']]['distributions'].get(name) != source['version']
                    or 'wheel_hash' not in origin):
                raise PreparationError('image registry wheel origin mismatch')
            path = directory / 'wheels' / filename
            wheel_hash = validate_registry_wheel(path, origin)
            result[name] = ImageRegistryWheel(path, wheel_hash, source, dict(identity))
        return result
    except (OSError, KeyError, TypeError, ValueError, AttributeError, RecursionError):
        raise PreparationError('invalid image registry origin evidence') from None
