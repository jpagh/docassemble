"""Public-index registry release catalog for coherent core upgrades.

Selection reads only the operator-approved public index (the same approved
policy the builder already accepts). The selected ``docassemble.webapp``
release's own ``Requires-Dist`` metadata is read from its published artifact;
there is no separate release-metadata source and no fallback to another release.
"""
from __future__ import annotations

import email
import io
import json
import tarfile
import zipfile
from typing import Callable
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from packaging.utils import canonicalize_name, parse_sdist_filename, parse_wheel_filename

from docassemble.base.managed_preparation import PreparationError

WEBAPP = 'docassemble-webapp'
SIMPLE_JSON = 'application/vnd.pypi.simple.v1+json'
Fetch = Callable[[str, dict[str, str]], bytes]


def _file_version(filename: str) -> str | None:
    if filename.endswith('.whl'):
        try:
            return str(parse_wheel_filename(filename)[1])
        except Exception:
            return None
    if filename.endswith(('.tar.gz', '.zip')):
        try:
            return str(parse_sdist_filename(filename)[1])
        except Exception:
            return None
    return None


def _metadata_requires_dist(filename: str, payload: bytes) -> list[str]:
    if filename.endswith('.whl'):
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                names = [name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
                if len(names) != 1:
                    raise ValueError()
                raw = archive.read(names[0]).decode('utf-8')
        except (zipfile.BadZipFile, ValueError, KeyError):
            raise PreparationError('release metadata is unrecognized') from None
    else:
        try:
            with tarfile.open(fileobj=io.BytesIO(payload), mode='r:*') as archive:
                members = [member for member in archive.getmembers()
                           if member.isfile() and member.name.endswith('PKG-INFO') and member.name.count('/') == 1]
                if len(members) != 1:
                    raise ValueError()
                handle = archive.extractfile(members[0])
                if handle is None:
                    raise ValueError()
                raw = handle.read().decode('utf-8')
        except (tarfile.TarError, ValueError, KeyError):
            raise PreparationError('release metadata is unrecognized') from None
    message = email.message_from_string(raw)
    return [str(value) for value in message.get_all('Requires-Dist', [])]


class PublicIndexCatalog:
    """PEP 691 JSON simple-index catalog with an injectable retrieval seam."""

    def __init__(self, index_url: str, *, fetch: Fetch | None = None, timeout: float = 60):
        if not isinstance(index_url, str) or not index_url.startswith('https://'):
            raise PreparationError('release catalog requires an approved HTTPS index')
        self.index_url = index_url.rstrip('/')
        self.fetch = fetch or self._fetch
        self.timeout = timeout
        self._files: dict[str, list[dict]] = {}

    def _fetch(self, url: str, headers: dict[str, str]) -> bytes:
        request = Request(url, headers=headers)
        with urlopen(request, timeout=self.timeout) as response:
            return response.read()

    @staticmethod
    def _check_url(url: str) -> None:
        try:
            parts = urlsplit(url)
            parts.port
        except ValueError:
            raise PreparationError('unsupported registry artifact origin') from None
        if (parts.scheme != 'https' or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.query or parts.fragment):
            raise PreparationError('unsupported registry artifact origin')

    def files(self, project: str) -> list[dict]:
        name = canonicalize_name(project)
        if name not in self._files:
            url = self.index_url + '/' + name + '/'
            self._check_url(url)
            payload = self.fetch(url, {'Accept': SIMPLE_JSON})
            try:
                document = json.loads(payload)
                entries = document['files']
                if not isinstance(entries, list):
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                raise PreparationError('release catalog requires a PEP 691 JSON index') from None
            self._files[name] = [dict(entry) for entry in entries if isinstance(entry, dict)]
        return self._files[name]

    def versions(self, project: str) -> list[str]:
        versions = {version for entry in self.files(project)
                    if isinstance(entry.get('filename'), str)
                    for version in [_file_version(entry['filename'])] if version}
        if not versions:
            raise PreparationError('no registry releases are available')
        return sorted(versions)

    def requires_dist(self, project: str, version: str) -> list[str]:
        entries = self.files(project)
        candidates = [entry for entry in entries
                      if isinstance(entry.get('filename'), str) and _file_version(entry['filename']) == str(version)]
        if not candidates:
            raise PreparationError('selected release artifact is unavailable')
        candidates.sort(key=lambda entry: 0 if entry['filename'].endswith('.whl') else 1)
        entry = candidates[0]
        filename, url = entry['filename'], entry.get('url')
        if not isinstance(url, str):
            raise PreparationError('selected release artifact is unavailable')
        self._check_url(url)
        return _metadata_requires_dist(filename, self.fetch(url, {'Accept': '*/*'}))


def default_release_catalog(index_url: str) -> PublicIndexCatalog:
    return PublicIndexCatalog(index_url)
