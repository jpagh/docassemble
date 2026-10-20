"""Transform a private sdist copy to interpose the bounded PEP 517 source gate."""
from __future__ import annotations

import gzip
import io
from pathlib import Path, PurePosixPath
import stat
import tarfile
import tomllib
import zipfile

import tomli_w

from .preparation import PreparationError

_SHIM_DIRECTORY = '.da-private-build'
_SHIM_MODULE = '_private_build_backend'
_LIMIT = 256 * 1024 * 1024


def prepare_build_input(source: Path, destination: Path, bindings: dict[str, str]) -> None:
    """Preserve the original elsewhere; produce a hashed, credential-free copy.

    Reject archive features that cannot be safely and unambiguously interposed.
    The shim delegates the original backend while checking hook prerequisites
    before uv can resolve them. No executor interpreter or credential is injected.
    """
    try:
        root = source.name[:-7] if source.name.endswith('.tar.gz') else source.stem
        files: dict[str, bytes] = {}
        total = 0
        count = 0

        def add(name: str, size: int, read, *, directory: bool = False):
            nonlocal total, count
            count += 1
            parts = PurePosixPath(name).parts
            if (count > 10000 or not parts or parts[0] != root or '\\' in name
                    or name.startswith('/') or '..' in parts or ':' in name
                    or name.rstrip('/') != '/'.join(parts)
                    or (len(parts) > 1 and parts[1] == _SHIM_DIRECTORY)):
                raise ValueError
            if directory:
                return
            total += size
            if len(parts) < 2 or name in files or total > _LIMIT:
                raise ValueError
            data = read()
            if len(data) != size:
                raise ValueError
            files[name] = data

        if source.name.endswith('.zip'):
            with zipfile.ZipFile(source) as zip_source:
                for zip_member in zip_source.infolist():
                    if stat.S_ISLNK(zip_member.external_attr >> 16):
                        raise ValueError
                    add(zip_member.filename, zip_member.file_size, lambda: zip_source.read(zip_member),
                        directory=zip_member.is_dir())
        else:
            with tarfile.open(source, 'r:gz') as tar_source:
                for tar_member in tar_source:
                    if tar_member.isdir():
                        add(tar_member.name, 0, None, directory=True)
                        continue
                    if not tar_member.isfile():
                        raise ValueError
                    handle = tar_source.extractfile(tar_member)
                    if handle is None:
                        raise ValueError
                    with handle:
                        add(tar_member.name, tar_member.size, handle.read)
        pyproject = root + '/pyproject.toml'
        project = tomllib.loads(files[pyproject].decode('utf-8'))
        # uv source/index overrides inside an sdist are not administrator
        # bindings. Refuse them rather than silently ignoring/replacing them.
        uv_policy = project.get('tool', {}).get('uv', {})
        if any(key in uv_policy for key in ('sources', 'index', 'index-url', 'extra-index-url',
                                           'default-index', 'find-links')):
            raise ValueError
        build = project['build-system']
        backend = build['build-backend']
        if backend.split(':')[0] == _SHIM_MODULE:
            raise ValueError
        paths = build.get('backend-path', [])
        if (not isinstance(paths, list) or any(not isinstance(path, str) or not path
                or path.startswith('/') or '\\' in path or ':' in path or '..' in PurePosixPath(path).parts
                or _SHIM_DIRECTORY in PurePosixPath(path).parts for path in paths)):
            raise ValueError
        policy = {'backend': backend, 'requires': build['requires'], 'bindings': sorted(bindings)}
        shim = Path(__file__).with_name('_private_build_backend.py').read_text()
        files[root + '/' + _SHIM_DIRECTORY + '/' + _SHIM_MODULE + '.py'] = shim.replace(
            '_POLICY: dict = {}', '_POLICY: dict = ' + repr(policy)).encode()
        build['build-backend'] = _SHIM_MODULE
        build['backend-path'] = [_SHIM_DIRECTORY, *paths]
        files[pyproject] = tomli_w.dumps(project).encode()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.name.endswith('.zip'):
            with zipfile.ZipFile(destination, 'w') as archive:
                for name, data in sorted(files.items()):
                    archive.writestr(zipfile.ZipInfo(name), data)
        else:
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode='w') as tar_output:
                for name, data in sorted(files.items()):
                    output_member = tarfile.TarInfo(name)
                    output_member.size = len(data)
                    tar_output.addfile(output_member, io.BytesIO(data))
            destination.write_bytes(gzip.compress(output.getvalue(), mtime=0))
    except Exception:
        raise PreparationError('private sdist build input cannot enforce explicit registry bindings') from None
