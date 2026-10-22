"""Bounded HTTPS distribution snapshots; credentials never enter uv or build processes.

Operator references are NOT candidate credential promotion. Only explicitly bound
names are fetched, without redirects; all subsequent resolution is offline. A
binding selects the administrator's index for that exact name; it does not control
what an upstream proxy itself serves, so an index that proxies PyPI can still
forward a public namesake. Private 401/403/404 and unavailable sources fail closed
and never fall back to public namesakes. TLS verifies by default; only explicit,
visible administrator exceptions relax it, and a certificate failure never
synthesizes one.
"""
from __future__ import annotations

from docassemble.base.managed_python import python_target

import base64
from email.parser import BytesParser
from html.parser import HTMLParser
import json
import os
import re
from pathlib import Path
import ssl
import stat
import tarfile
import tomllib
from typing import Any
from urllib.error import HTTPError
from urllib.parse import unquote, urljoin, urlsplit
from urllib.request import build_opener, HTTPSHandler, HTTPRedirectHandler, Request
import zipfile

from packaging.utils import canonicalize_name, parse_wheel_filename, parse_sdist_filename

from .bundles import sha256_file
from .preparation import PreparationError


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.links.extend(value for key, value in attrs if key == 'href' and value is not None)


def _index_url(value: Any) -> str:
    if not isinstance(value, str):
        raise PreparationError('private index requires a credential-free HTTPS URL ending in /')
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        raise PreparationError('private index requires a valid credential-free HTTPS URL') from None
    if (any(char.isspace() or ord(char) < 32 for char in value) or parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or '?' in value or '#' in value
            or not parsed.path.endswith('/') or '%' in parsed.path
            or any(part in ('.', '..') for part in parsed.path.split('/'))):
        raise PreparationError('private index requires a credential-free HTTPS URL ending in /')
    return value


def _source_unavailable(error: HTTPError) -> PreparationError:
    """Redacted category for a private-index HTTP failure; never a public fallback."""
    if error.code in (401, 403):
        return PreparationError('private index authentication denied (401/403); rotate or authorize the scoped credential')
    if error.code == 404:
        return PreparationError('private index source not found (404); no public fallback')
    return PreparationError('private index unavailable or redirect rejected; no public fallback')


def effective_config(inputs: dict[str, Any]) -> dict[str, Any]:
    """Committed policy for this preparation, including an explicit config import."""
    config = inputs['baseline']['config']
    if inputs.get('change_operation') == 'config':
        from docassemble.base.managed_config import config_import_policy
        config = config_import_policy(inputs['config_import']['snapshot'], config)
    return config


def validate_private_policy(inputs: dict[str, Any]) -> dict[str, Any]:
    from .registry_builder import UV_VERSION
    from .preparation import parse_registry_requirement
    baseline = inputs['baseline']
    config = effective_config(inputs)
    if (baseline['target'] != python_target() or baseline['tool'] != {'uv': UV_VERSION}
            or not {'private_indexes', 'private_bindings'} <= set(config)
            or set(config) - {'private_indexes', 'private_bindings', 'private_tls_exceptions'}):
        raise PreparationError('unsupported private distribution snapshot policy')
    indexes, bindings = config['private_indexes'], config['private_bindings']
    exceptions = config.get('private_tls_exceptions', []) or []
    if (not isinstance(indexes, dict) or not indexes or not isinstance(bindings, dict) or not bindings
            or any(not isinstance(name, str) or not re.fullmatch('[a-z][a-z0-9_]*', name) or name == 'snapshot' for name in indexes)
            or any(not isinstance(name, str) or not re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*', name)
                   or not isinstance(index, str) or index not in indexes
                   for name, index in bindings.items())
            or any(not isinstance(label, str) or label not in indexes for label in exceptions)
            or len(set(exceptions)) != len(exceptions)):
        raise PreparationError('declare explicit private index bindings for direct and transitive names')
    for url in indexes.values():
        _index_url(url)
    if len(set(indexes.values())) != len(indexes):
        raise PreparationError('private index URLs must be distinct')
    rows = list(inputs['contributions'])
    if inputs.get('requirement'):
        rows.append(inputs['requirement'])
    for row in rows:
        parsed = parse_registry_requirement(row['declaration'])
        if parsed.normalized_name not in bindings and parsed.normalized_name not in baseline['core']:
            raise PreparationError('declare an explicit private index binding for this requirement; missing '
                                   + parsed.normalized_name)
    return config


_LABEL = re.compile(r'[a-z][a-z0-9_]*')


def load_operator_references(root: str | os.PathLike | None) -> dict[str, Path]:
    """Map declared index labels to operator reference files from an explicit root.

    The root is operator-provisioned configuration with no default; it and every
    entry are owner-only (0700 root, 0600 regular files). Rotation replaces the
    label's file; revocation deletes it, and the missing reference then fails the
    next fetch closed. File content and exact index scope are validated when the
    credential is used.
    """
    try:
        if not root:
            raise ValueError
        candidate = Path(root)
        if candidate.is_symlink():
            raise ValueError
        root = candidate.resolve(strict=True)
        info = root.stat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise ValueError
        references: dict[str, Path] = {}
        for entry in sorted(root.iterdir()):
            entry_info = entry.lstat()
            if (not _LABEL.fullmatch(entry.name) or entry.name == 'snapshot' or entry.is_symlink()
                    or not stat.S_ISREG(entry_info.st_mode) or entry_info.st_uid != os.geteuid()
                    or stat.S_IMODE(entry_info.st_mode) != 0o600):
                raise ValueError
            references[entry.name] = entry
        if not references:
            raise ValueError
    except (OSError, ValueError):
        raise PreparationError('private index credential root must be an explicit owner-only 0700 '
                               'directory of 0600 reference files named for their index label') from None
    return references


class PrivateIndexSnapshot:
    def __init__(self, references: dict[str, Path], *, ca_file: Path | None = None):
        self.references = references
        self.ca_file = ca_file

    @staticmethod
    def _credential(path: Path, index: str) -> str:
        """Open a bounded owner-only regular reference without following symlinks."""
        try:
            parent = path.parent.lstat()
            if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid()
                    or stat.S_IMODE(parent.st_mode) != 0o700):
                raise ValueError
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as handle:
                info = os.fstat(handle.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                        or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 65536):
                    raise ValueError
                value = json.loads(handle.read(65537))
                if (set(value) != {'index_url', 'username', 'password'} or value['index_url'] != index or
                        any(not isinstance(v, str) or not v or '\n' in v or '\r' in v for v in value.values())
                        or ':' in value['username']):
                    raise ValueError
                return 'Basic ' + base64.b64encode((value['username'] + ':' + value['password']).encode()).decode()
        except Exception:
            raise PreparationError('private index credential reference unavailable; check exact source scope, owner and mode 0600') from None

    @staticmethod
    def _validate_wheel(path: Path, name: str, bindings: dict[str, str], core: dict[str, Any]) -> None:
        from .preparation import parse_registry_requirement
        try:
            _, version, _, _ = parse_wheel_filename(path.name)
            with zipfile.ZipFile(path) as archive:
                entries = [entry for entry in archive.namelist() if entry.endswith('.dist-info/METADATA')]
                if len(entries) != 1:
                    raise ValueError
                with archive.open(entries[0]) as handle:
                    data = handle.read(1024 * 1024 + 1)
                if len(data) > 1024 * 1024:
                    raise ValueError
            metadata = BytesParser().parsebytes(data)
            if canonicalize_name(metadata['Name']) != name or metadata['Version'] != str(version):
                raise ValueError
            for declaration in metadata.get_all('Requires-Dist', []):
                requirement = parse_registry_requirement(declaration)
                if requirement.normalized_name not in bindings and requirement.normalized_name not in core:
                    raise PreparationError('private transitive dependency ' + requirement.normalized_name + ' of wheel '
                                           + name + ' lacks an explicit binding; declare it before preparation '
                                           '(no public fallback)')
        except PreparationError:
            raise
        except Exception:
            raise PreparationError('private wheel metadata invalid or mismatched; source replacement required') from None

    @staticmethod
    def _validate_sdist(path: Path, name: str, bindings: dict[str, str]) -> None:
        """Require explicit PEP 517 prerequisites before executing any build code.

        Read only the bounded root pyproject, never extract an archive here. uv
        subsequently uses a separate shimmed copy to validate hook-returned
        requirements, resolving only against the explicitly bound offline index.
        """
        from .preparation import parse_registry_requirement
        try:
            distribution, _ = parse_sdist_filename(path.name)
            if canonicalize_name(distribution) != name:
                raise ValueError
            root = path.name[:-7] if path.name.endswith('.tar.gz') else path.stem
            member = root + '/pyproject.toml'
            if path.name.endswith('.zip'):
                with zipfile.ZipFile(path) as archive:
                    entries = [entry for entry in archive.infolist() if entry.filename == member]
                    if len(entries) != 1 or entries[0].file_size > 1024 * 1024:
                        raise ValueError
                    data = archive.read(entries[0])
            else:
                with tarfile.open(path, 'r:gz') as archive:
                    members = [entry for entry in archive if entry.name == member]
                    if len(members) != 1 or not members[0].isfile() or members[0].size > 1024 * 1024:
                        raise ValueError
                    handle = archive.extractfile(members[0])
                    if handle is None:
                        raise ValueError
                    with handle:
                        data = handle.read(1024 * 1024 + 1)
            build = tomllib.loads(data.decode('utf-8'))['build-system']
            if (not isinstance(build['requires'], list) or not isinstance(build['build-backend'], str)
                    or not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?::[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)?',
                                        build['build-backend'])):
                raise ValueError
            for declaration in build['requires']:
                requirement = parse_registry_requirement(declaration)
                if requirement.normalized_name not in bindings:
                    raise PreparationError('private build dependency ' + requirement.normalized_name + ' of sdist '
                                           + name + ' lacks an explicit binding; declare it before preparation '
                                           '(no public fallback)')
        except PreparationError:
            raise
        except Exception:
            raise PreparationError('private sdist requires valid matching archive metadata and an explicit PEP 517 build system') from None

    def prepare(self, inputs: dict[str, Any], project: Path) -> tuple[str, dict[str, Path]]:
        config = validate_private_policy(inputs)
        if set(self.references) != set(config['private_indexes']):
            raise PreparationError('private index requires an operator credential reference for each selected index')
        insecure = set(config.get('private_tls_exceptions', []) or [])
        local_sources: dict[str, Path] = {}
        provenance = []
        # uv source-table overrides do not bind transitive requirements. Only
        # administrator-bound names enter this offline, sole default index.
        aggregate = project / 'private-indexes' / 'snapshot'
        aggregate.mkdir(parents=True)
        policy = ('\n[[tool.uv.index]]\nurl = ' + json.dumps(aggregate.as_uri() + '/') + '\ndefault = true\n')
        for label, index in config['private_indexes'].items():
            authorization = self._credential(self.references[label], index)
            context = ssl.create_default_context(cafile=self.ca_file)
            if label in insecure:
                # Explicit, visible administrator exception; never synthesized
                # after a failure and never applied to a label that did not
                # declare it.
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            opener = build_opener(HTTPSHandler(context=context), _NoRedirect())
            local = project / 'private-indexes' / label
            local.mkdir(parents=True)

            def fetch(url: str, limit: int) -> bytes:
                # Both pages and artifacts must be inside THIS index, never a
                # different path on the same host. Redirects are always rejected.
                if not url.startswith(index) or urlsplit(url).query or urlsplit(url).fragment:
                    raise PreparationError('private index link outside configured source; redirects and URL secrets unsupported')
                try:
                    with opener.open(Request(url, headers={'Authorization': authorization}), timeout=60) as response:
                        data = response.read(limit + 1)
                    if len(data) > limit:
                        raise PreparationError('private index response exceeds snapshot limit')
                    return data
                except HTTPError as error:
                    raise _source_unavailable(error) from None
                except PreparationError:
                    raise
                except Exception:
                    raise PreparationError('private index HTTPS request failed; verify TLS and source availability; no public fallback') from None

            for name, selected in sorted(config['private_bindings'].items()):
                if selected != label:
                    continue
                page_url = index + name + '/'
                links = _Links()
                data = fetch(page_url, 1024 * 1024)
                try:
                    links.feed(data.decode('utf-8'))
                except Exception:
                    raise PreparationError('private index requires a UTF-8 simple HTML page') from None
                if not links.links or len(links.links) > 100:
                    raise PreparationError('private distribution snapshot requires 1 to 100 distribution links per bound name')
                directory = local / name
                directory.mkdir()
                html = []
                for link in links.links:
                    try:
                        parsed = urlsplit(urljoin(page_url, link))
                    except ValueError:
                        raise PreparationError('private distribution link invalid; submit a credential-free HTTPS artifact link') from None
                    filename = unquote(Path(parsed.path).name)
                    url = parsed._replace(fragment='').geturl()
                    if (url != page_url + filename or not re.fullmatch(r'[A-Za-z0-9_.+!-]+\.(?:whl|tar\.gz|zip)', filename)
                            or parsed.query or not re.fullmatch(r'sha256=[0-9a-f]{64}', parsed.fragment)):
                        raise PreparationError('private snapshots require same-package HTTPS distribution links with SHA256; no URL secrets')
                    try:
                        if filename.endswith('.whl'):
                            distribution, _, _, tags = parse_wheel_filename(filename)
                            if not filename.endswith('py3-none-any.whl') or not all(t.platform == 'any' and t.abi == 'none' for t in tags):
                                raise ValueError
                        else:
                            distribution, _ = parse_sdist_filename(filename)
                        if canonicalize_name(distribution) != name:
                            raise ValueError
                    except Exception:
                        raise PreparationError('private snapshot requires matching pure Python wheels or named sdists') from None
                    destination = directory / filename
                    if destination.exists():
                        raise PreparationError('duplicate private distribution link')
                    destination.write_bytes(fetch(url, 64 * 1024 * 1024))
                    artifact = sha256_file(destination)
                    if parsed.fragment != 'sha256=' + artifact:
                        raise PreparationError('private distribution SHA256 mismatch')
                    if filename.endswith('.whl'):
                        self._validate_wheel(destination, name, config['private_bindings'], inputs['baseline']['core'])
                    else:
                        self._validate_sdist(destination, name, config['private_bindings'])
                    selected_artifact = destination
                    row: dict[str, Any] = {'name': name, 'index': label, 'url': index,
                           'filename': filename, 'sha256': artifact}
                    if not filename.endswith('.whl'):
                        from .private_build import prepare_build_input
                        selected_artifact = project / 'private-indexes' / 'build-inputs' / label / name / filename
                        prepare_build_input(destination, selected_artifact, config['private_bindings'])
                        row['build_input'] = {'path': selected_artifact.relative_to(project).as_posix(),
                                              'sha256': sha256_file(selected_artifact)}
                    local_sources[str(selected_artifact)] = destination
                    relative = selected_artifact.relative_to(project / 'private-indexes').as_posix()
                    html.append('<a href="../../' + relative + '#sha256=' + sha256_file(selected_artifact) + '">distribution</a>')
                    provenance.append(row)
                (aggregate / name).mkdir()
                (aggregate / name / 'index.html').write_text('\n'.join(html))
        (project / 'private-sources.json').write_text(json.dumps(provenance, sort_keys=True))
        return policy, local_sources
