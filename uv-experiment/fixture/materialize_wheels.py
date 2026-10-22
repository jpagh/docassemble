#!/usr/bin/env python3
"""Retain image-built fixture wheels so ordinary frozen boot never builds.

Local unpublished sources are fixture inputs, not a new production archiving
policy. Source-built runtime-fault remains solely for the interruption proof.
"""

from pathlib import Path
import json
import os

from packaging.tags import sys_tags
from packaging.utils import parse_wheel_filename
import shutil
import subprocess
import sys
import tomllib
import tempfile
import urllib.parse
import urllib.request


# Image construction builds cold native sdists (SciPy takes about 10–12 minutes
# on the measured arm64 host). Allow slower CI headroom; runtime preparation
# keeps its separate 600-second command bound in RegistryBuilder._run.
IMAGE_BUILD_TIMEOUT = 3600


def eligible_sdists(bundle: Path) -> dict[str, dict]:
    """Only public locked sdists lacking a wheel compatible with this target."""
    from docassemble_executor.image_baseline import INDEX, public_sdist_identity
    from docassemble_executor.bundles import verify_python_target
    if not verify_python_target(bundle / 'pyproject.toml', bundle / 'uv.lock'):
        raise ValueError('image build Python target mismatch')
    packages = tomllib.loads((bundle / 'uv.lock').read_text())['package']
    tags = set(sys_tags())
    result = {}
    for package in packages:
        if package['source'] != {'registry': INDEX} or not package.get('sdist'):
            continue
        if any(parse_wheel_filename(Path(urllib.parse.urlsplit(wheel['url']).path).name)[3] & tags
               for wheel in package.get('wheels', [])):
            continue
        identity = public_sdist_identity(package)
        if identity['name'] in result:
            raise ValueError('conflicting image build source')
        result[identity['name']] = identity
    return result


def build_registry_sdists(bundle: Path, output: Path) -> None:
    """Build verified original sdists once, never infer provenance from uv cache."""
    from docassemble_executor.bundles import sha256_file
    from docassemble_executor.image_baseline import UV_VERSION, validate_registry_wheel
    from docassemble_executor.preparation import PreparationError
    env_base = {'PATH': os.environ.get('PATH', ''), 'UV_PYTHON_DOWNLOADS': 'never', 'UV_NO_PROGRESS': '1'}
    if subprocess.run(['uv', '--version'], capture_output=True, check=True, timeout=600).stdout.decode().split()[:2] != ['uv', UV_VERSION]:
        raise ValueError('image build uv release mismatch')
    output.mkdir()  # Must be new: no preexisting same-version build can be claimed.
    origins = {}
    for name, identity in eligible_sdists(bundle).items():
        with tempfile.TemporaryDirectory(prefix='image-sdist-') as temporary:
            root = Path(temporary)
            source = root / Path(urllib.parse.urlsplit(identity['sdist']['url']).path).name
            with urllib.request.urlopen(identity['sdist']['url'], timeout=60) as response:
                redirected = urllib.parse.urlsplit(response.geturl())
                if (redirected.scheme != 'https' or redirected.hostname != 'files.pythonhosted.org'
                        or redirected.username or redirected.query or redirected.fragment):
                    raise ValueError('image sdist redirect mismatch')
                source.write_bytes(response.read())
            if identity['sdist']['hash'] != 'sha256:' + sha256_file(source):
                raise ValueError('image sdist digest mismatch')
            built = root / 'built'
            built.mkdir()
            try:
                subprocess.run(['uv', 'build', '--wheel', '--no-config', '--out-dir', str(built),
                                '--python', sys.executable, str(source)], cwd=root,
                    env=dict(env_base, HOME=str(root), UV_CACHE_DIR=str(root / 'cache')),
                    check=True, timeout=IMAGE_BUILD_TIMEOUT)
            except subprocess.TimeoutExpired:
                raise PreparationError('image registry wheel build timed out') from None
            candidates = list(built.glob('*.whl'))
            if len(candidates) != 1:
                raise ValueError('ambiguous image sdist build')
            wheel = candidates[0]
            wheel_hash = validate_registry_wheel(wheel, identity)
            destination = output / wheel.name
            shutil.copyfile(wheel, destination)
            validate_registry_wheel(destination, dict(identity, wheel_hash=wheel_hash))
            origins[wheel.name] = dict(identity, wheel_hash=wheel_hash)
    (output / 'source-artifacts.json').write_text(json.dumps(origins, indent=2, sort_keys=True) + '\n')


def retain_built(bundle: Path, built: Path) -> None:
    """Share only attested wheels whose source agrees with the variant's lock."""
    from docassemble_executor.image_baseline import validate_registry_wheel
    eligible = eligible_sdists(bundle)
    origin_file = built / 'source-artifacts.json'
    if origin_file.is_symlink() or any(parent.is_symlink() for parent in origin_file.parents):
        raise ValueError('invalid image build origin path')
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('conflicting image built artifacts')
            result[key] = value
        return result
    origins = json.loads(origin_file.read_text(), object_pairs_hook=unique_pairs)
    selected = {}
    for filename, origin in origins.items():
        if Path(filename).name != filename or origin['name'] in selected or 'wheel_hash' not in origin:
            raise ValueError('conflicting image built artifacts')
        selected[origin['name']] = filename, origin
    # Do not partially relabel a variant when any required source differs.
    for name, identity in eligible.items():
        if name not in selected or {key: selected[name][1][key] for key in identity} != identity:
            raise ValueError('variant image sdist origin mismatch')
        filename, origin = selected[name]
        validate_registry_wheel(built / filename, origin)
    project = bundle / 'pyproject.toml'
    text = project.read_text()
    wheels = bundle / 'wheels'
    wheels.mkdir(exist_ok=True)
    retained = {}
    for name, identity in eligible.items():
        filename, origin = selected[name]
        destination = wheels / filename
        if destination.exists():
            raise ValueError('conflicting retained image wheel')
        shutil.copyfile(built / filename, destination)
        validate_registry_wheel(destination, origin)
        retained[filename] = origin
        text = text.replace('dependencies = [', 'dependencies = [\n  "' + name + '==' + identity['version'] + '",', 1)
        text += '\n"' + name + '" = { path = "wheels/' + filename + '" }\n'
    project.write_text(text)
    (bundle / 'source-artifacts.json').write_text(json.dumps(retained, indent=2, sort_keys=True) + '\n')


def main():
    bundle = Path(sys.argv[1])
    project = bundle / 'pyproject.toml'
    text = project.read_text()
    sources = tomllib.loads(text)['tool']['uv']['sources']
    wheels = bundle / 'wheels'
    wheels.mkdir(exist_ok=True)
    if len(sys.argv) > 2 and sys.argv[2] == '--build-registry-sdists':
        build_registry_sdists(bundle, Path(sys.argv[3]))
        return
    if len(sys.argv) > 2 and sys.argv[2] == '--retain-built':
        retain_built(bundle, Path(sys.argv[3]))
        return
    for name, source in sources.items():
        subprocess.run(['uv', 'build', '--wheel', '--out-dir', str(wheels), str(bundle / source['path'])], check=True)
        stem = name.replace('.', '_').replace('-', '_') + '-'
        wheel = next(path for path in wheels.glob('*.whl') if path.name.startswith(stem))
        text = text.replace('path = "' + source['path'] + '"', 'path = "wheels/' + wheel.name + '"')
    project.write_text(text)


if __name__ == '__main__':
    main()
