"""Read and validate installed distributions without activating their Python.

RECORD hashes check installed material, not merely version metadata. This is
an integrity/admission check, not a sandbox or a package signature scheme.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import importlib.metadata
import re
from pathlib import Path

from .bundles import sha256_file
from .health import Check


def canonical_name(name: str) -> str:
    return re.sub(r'[-_.]+', '-', name).lower()


def _sites(venv: Path) -> list[str]:
    return [str(path) for path in venv.glob('lib/python3.*/site-packages')]


def inspect_environment(venv: Path) -> dict[str, str]:
    inventory: dict[str, str] = {}
    for dist in importlib.metadata.distributions(path=_sites(venv)):
        name = canonical_name(dist.metadata['Name'])
        if name in inventory:
            raise ValueError('duplicate installed distribution: ' + name)
        inventory[name] = dist.version
    return inventory


def inspect_material(venv: Path) -> dict[str, str | list[str]]:
    """Hash final image-installed files, handling overlapping namespace files.

    Some wheels own the same namespace __init__.py with different RECORD
    hashes. The final installed file in the qualified image is authoritative.
    Installer-generated provenance and bytecode are not portable material.
    """
    paths: dict[Path, list[str]] = {}
    for dist in importlib.metadata.distributions(path=_sites(venv)):
        record = dist.read_text('RECORD')
        if record is None:
            raise ValueError('missing installed RECORD')
        for filename, encoded_hash, _ in csv.reader(record.splitlines()):
            if (not encoded_hash or Path(filename).suffix in ('.pyc', '.pyo')
                    or '__pycache__' in Path(filename).parts
                    or Path(filename).name in ('direct_url.json', 'uv_cache.json', 'uv_build.json')):
                continue
            path = Path(str(dist.locate_file(filename))).resolve()
            if not path.is_relative_to(venv.resolve()):
                raise ValueError('installed material escapes runtime')
            algorithm, encoded = encoded_hash.split('=', 1)
            expected_hash = base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)).hex() if algorithm == 'sha256' else sha256_file(path)
            paths.setdefault(path, []).append(expected_hash)
    material: dict[str, str | list[str]] = {}
    for path, wheel_hashes in sorted(paths.items()):
        actual = sha256_file(path)
        # Installation order of overlapping wheel files is not deterministic.
        # Keep only hashes qualified in the image, never runtime-supplied ones.
        material[path.relative_to(venv.resolve()).as_posix()] = sorted(set([actual, *wheel_hashes])) if len(wheel_hashes) > 1 else actual
    return material


def wheel_payload_material(wheels: Path) -> dict[str, str | list[str]]:
    """Immutable wheel payload hashes, including qualified namespace overlaps.

    Generated interpreter entrypoints/provenance are not wheel payloads. Only the
    Python-3.12 image bridge uses this mapping; no runtime RECORD supplies hashes.
    """
    from zipfile import ZipFile
    material: dict[str, list[str]] = {}
    for wheel in sorted(wheels.glob('*.whl')):
        with ZipFile(wheel) as archive:
            for entry in archive.infolist():
                parts = Path(entry.filename).parts
                if entry.is_dir() or not parts or '..' in parts or Path(entry.filename).is_absolute():
                    if not entry.is_dir():
                        raise ValueError('unsupported wheel payload path')
                    continue
                if parts[0].endswith('.data'):
                    if len(parts) < 3 or parts[1] not in ('purelib', 'platlib'):
                        continue
                    parts = parts[2:]
                if parts[-1] == 'RECORD':
                    continue
                name = 'lib/python3.12/site-packages/' + '/'.join(parts)
                material.setdefault(name, []).append(hashlib.sha256(archive.read(entry)).hexdigest())
    return {name: sorted(set(values)) for name, values in material.items()}


def verify_environment(venv: Path, expected: dict[str, str], material: dict[str, str | list[str]] | None = None) -> Check:
    try:
        actual = inspect_environment(venv)
        if actual != expected:
            return Check('installed-inventory', False, 'installed distribution inventory differs from retained bundle')
        if material is not None:
            # Compare the retained filenames directly, not a mutable RECORD
            # listing which could omit a deleted or changed installed file.
            for filename, expected_hash in material.items():
                path = venv / filename
                if not path.is_file() or sha256_file(path) not in (expected_hash if isinstance(expected_hash, list) else [expected_hash]):
                    return Check('installed-inventory', False, 'installed material differs: ' + filename)
            return Check('installed-inventory', True, 'exact inventory and immutable installed material validate')
        for dist in importlib.metadata.distributions(path=_sites(venv)):
            record = dist.read_text('RECORD')
            if record is None:
                return Check('installed-inventory', False, 'missing RECORD: ' + dist.metadata['Name'])
            for filename, encoded_hash, _ in csv.reader(record.splitlines()):
                # Unhashed RECORD/bytecode entries carry no integrity promise.
                if not encoded_hash:
                    continue
                path = Path(str(dist.locate_file(filename)))
                if not path.is_file():
                    return Check('installed-inventory', False, 'installed file missing: ' + filename)
                algorithm, expected_hash = encoded_hash.split('=', 1)
                digest = hashlib.new(algorithm)
                with path.open('rb') as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                        digest.update(chunk)
                actual_hash = base64.urlsafe_b64encode(digest.digest()).decode().rstrip('=')
                if actual_hash != expected_hash:
                    return Check('installed-inventory', False, 'installed file changed: ' + filename)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return Check('installed-inventory', False, 'unreadable installed inventory: ' + str(error))
    return Check('installed-inventory', True, 'exact installed inventory and RECORD hashes validate')
