"""Immutable uploaded distributions; paths are operator configuration, never requests."""
from __future__ import annotations

from email.parser import BytesParser
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
import zipfile

from packaging.utils import canonicalize_name
from packaging.version import Version, InvalidVersion

from docassemble.base.managed_preparation import PreparationError


def _euid() -> int:
    return os.geteuid()


def _configured_owner() -> int:
    """Root reads must verify the operator-configured API-writer UID.

    Never trust whichever UID happens to own the directory; a root executor
    that cannot identify the expected writer fails closed.
    """
    if _euid() != 0:
        return os.getuid()
    value = os.environ.get('DA_MANAGED_ARTIFACT_UID')
    if value is None or value == '':
        raise PreparationError('artifact store owner must be configured for root access')
    try:
        return int(value)
    except ValueError:
        raise PreparationError('invalid artifact store owner configuration') from None


def _reject_symlink_components(path: Path) -> None:
    """No component of the operator-configured path may be a symlink.

    Inspect the original components in order without collapsing `..` first:
    `link/../artifacts` must reject `link`, not normalize it away.
    """
    text = os.fspath(path)
    if not os.path.isabs(text):
        text = os.path.join(os.getcwd(), text)
    current = os.sep
    for part in text.split(os.sep):
        if part in ('', '.'):
            continue
        if part == '..':
            current = os.path.dirname(current)
            continue
        current = os.path.join(current, part)
        if os.path.islink(current):
            raise OSError()


def artifact_root(root: Path, *, reader: bool = False) -> Path:
    try:
        _reject_symlink_components(root)
        root = root.resolve(strict=True)
        info = root.stat()
        expected = _configured_owner() if reader else os.getuid()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != expected:
            raise OSError()
        return root
    except (OSError, TypeError, AttributeError):
        raise PreparationError('artifact store must be restricted and operator-owned') from None


def artifact_path(root: Path, reference: str, *, reader: bool = False) -> Path:
    root = artifact_root(root, reader=reader)
    if not isinstance(reference, str) or not re.fullmatch('[0-9a-f]{64}', reference):
        raise PreparationError('invalid artifact reference')
    return root / reference


def open_regular(path: Path, *, owner: int | None = None):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        handle = os.fdopen(fd, 'rb')
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or (owner is not None and (info.st_uid != owner or info.st_mode & 0o077)):
            handle.close()
            raise OSError()
        return handle
    except OSError:
        raise PreparationError('distribution artifact unavailable') from None


def file_digest(handle) -> str:
    handle.seek(0)
    value = hashlib.file_digest(handle, 'sha256').hexdigest()
    handle.seek(0)
    return value


def distribution_identity(handle) -> dict[str, str]:
    """Only one authoritative distribution identity, never the upload filename."""
    try:
        with zipfile.ZipFile(handle) as archive:
            metadata = [name for name in archive.namelist() if
                        name.endswith('.dist-info/METADATA') or name == 'PKG-INFO' or name.endswith('/PKG-INFO')]
            identities = set()
            wheel = any(name.endswith('.dist-info/METADATA') for name in metadata)
            for name in metadata:
                if wheel and not name.endswith('.dist-info/METADATA'):
                    continue
                info = archive.getinfo(name)
                if info.file_size > 1024 * 1024:
                    raise ValueError()
                message = BytesParser().parsebytes(archive.read(name))
                names, versions = message.get_all('Name', []), message.get_all('Version', [])
                if len(names) != 1 or len(versions) != 1 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', names[0]):
                    raise ValueError()
                identities.add((canonicalize_name(names[0]), str(Version(versions[0]))))
            if len(identities) != 1:
                raise ValueError()
            name, version = identities.pop()
            # uv requires a valid wheel filename even though metadata owns identity.
            if wheel:
                from packaging.utils import parse_wheel_filename
                tags = archive.read(next(name for name in archive.namelist() if name.endswith('.dist-info/WHEEL'))).decode()
                tag = next(line[5:].strip() for line in tags.splitlines() if line.startswith('Tag: '))
                if not re.fullmatch(r'[A-Za-z0-9_.]+-[A-Za-z0-9_.]+-[A-Za-z0-9_.]+', tag):
                    raise ValueError()
                filename = name.replace('-', '_') + '-' + version + '-' + tag + '.whl'
                parse_wheel_filename(filename)
            else:
                filename = name + '-' + version + '.zip'
            return {'normalized_name': name, 'version': version, 'filename': filename}
    except (OSError, ValueError, KeyError, StopIteration, UnicodeError, RuntimeError, EOFError,
            NotImplementedError, zipfile.BadZipFile, InvalidVersion):
        raise PreparationError('archive requires usable distribution metadata') from None
    finally:
        handle.seek(0)


def verify_artifact(root: Path, reference: str) -> Path:
    path = artifact_path(root, reference, reader=True)
    with open_regular(path, owner=path.parent.stat().st_uid) as handle:
        if file_digest(handle) != reference:
            raise PreparationError('distribution artifact digest mismatch')
    return path


def retain_artifact(root: Path, reference: str, source) -> Path:
    """Caller holds the environment lock through registration and atomic exposure."""
    root = artifact_root(root)
    destination = artifact_path(root, reference)
    fd, temporary = tempfile.mkstemp(prefix='.ingest-', dir=root)
    try:
        source.seek(0)
        with os.fdopen(fd, 'wb') as output:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        with open_regular(Path(temporary), owner=os.getuid()) as handle:
            if file_digest(handle) != reference:
                raise PreparationError('uploaded artifact changed during ingestion')
        if destination.exists() or destination.is_symlink():
            verify_artifact(root, reference)
        else:
            os.replace(temporary, destination)
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return destination
    except OSError:
        raise PreparationError('distribution artifact retention failed') from None
    finally:
        Path(temporary).unlink(missing_ok=True)
