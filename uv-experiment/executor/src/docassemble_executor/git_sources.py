"""Resolve public HTTPS refs and build recorded commits, never the live runtime."""
from __future__ import annotations

import json
from email.parser import BytesParser
import zipfile
import os
from pathlib import Path
import re
import subprocess
import tomllib

from packaging.utils import canonicalize_name, parse_wheel_filename, InvalidWheelFilename
from packaging.version import Version, InvalidVersion

from docassemble.base.managed_git import scoped_authorization, source_declarations, validate_git_sources
from .preparation import PreparationError, digest, parse_registry_requirement


def git(arguments: list[str], directory: Path, *, authorization: str | None = None,
        ca_file: Path | None = None) -> bytes:
    # No ambient helpers, URL rewrites, proxy credentials, TLS exceptions or SSH.
    env = {'PATH': os.environ.get('PATH', ''), 'HOME': str(directory),
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
           'GIT_TERMINAL_PROMPT': '0'}
    config = ['-c', 'http.sslVerify=true', '-c', 'http.followRedirects=false',
              '-c', 'protocol.allow=never', '-c', 'protocol.https.allow=always']
    if ca_file is not None:
        # Explicit operator CA only; verification stays on and is never disabled.
        config += ['-c', 'http.sslCAInfo=' + str(ca_file)]
    if authorization is not None:
        # Per-process configuration is never included in the process command line.
        env.update(GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='http.extraHeader',
                   GIT_CONFIG_VALUE_0='Authorization: ' + authorization)
    try:
        result = subprocess.run(['git', *config, *arguments],
            cwd=directory, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
        if result.returncode:
            raise ValueError()
        return result.stdout
    except (OSError, ValueError, subprocess.SubprocessError):
        raise PreparationError('HTTPS Git source unavailable; verify repository, ref and authorisation') from None


def scoped_git_authorization(repository: str, authorizations: dict | None, name: str) -> str | None:
    """Authorize only this exact repository; a mismatch or leak fails closed."""
    payload = (authorizations or {}).get(name)
    return scoped_authorization(repository, payload)


def resolve_source(source: dict, directory: Path, *, authorization: str | None = None,
                   ca_file: Path | None = None) -> dict:
    ref = source['declared_ref']
    if ref and re.fullmatch('[0-9a-fA-F]{40}', ref):
        return dict(source, ref_kind='commit', resolved_sha=ref.lower())
    output = git(['ls-remote', '--symref', source['repository']], directory,
                 authorization=authorization, ca_file=ca_file).decode('utf-8')
    refs = {}
    for line in output.splitlines():
        value, separator, name = line.partition('\t')
        if separator and re.fullmatch('[0-9a-f]{40}', value):
            refs[name] = value
    if ref is None:
        sha, kind = refs.get('HEAD'), 'branch'
    elif ref.startswith('refs/heads/'):
        sha, kind = refs.get(ref), 'branch'
    elif ref.startswith('refs/tags/'):
        sha, kind = refs.get(ref + '^{}', refs.get(ref)), 'tag'
    else:
        branch = 'refs/heads/' + ref
        tag = 'refs/tags/' + ref
        if branch in refs and tag in refs:
            raise PreparationError('ambiguous Git ref; submit an explicit head or tag')
        kind = 'branch' if branch in refs else 'tag'
        sha = refs.get(branch) if kind == 'branch' else refs.get(tag + '^{}', refs.get(tag))
    if not sha:
        raise PreparationError('public HTTPS Git ref not found')
    return dict(source, ref_kind=kind, resolved_sha=sha)


def resolve_sources(inputs: dict, directory: Path, *, authorizations: dict | None = None,
                    ca_file: Path | None = None) -> dict:
    authorizations = authorizations or {}
    sources = {}
    previous = inputs['baseline'].get('git_sources', {})
    for name, declaration in source_declarations(inputs).items():
        authorization = scoped_git_authorization(declaration['repository'], authorizations, name)
        old = previous.get(name)
        replacing = bool(inputs.get('git_source')) and inputs['requirement']['normalized_name'] == name
        advancing = inputs.get('change_operation') == 'update' and inputs['requirement']['normalized_name'] == name
        if not old and not replacing:
            raise PreparationError('committed Git source identity unavailable; explicit source replacement required')
        if old and not replacing:
            if any(old.get(key) != value for key, value in declaration.items()):
                raise PreparationError('Git source changes require explicit replacement')
            source = dict(old)
            if advancing and old['ref_kind'] in ('branch', 'tag'):
                current = resolve_source(declaration, directory, authorization=authorization, ca_file=ca_file)
                if old['ref_kind'] == 'tag' and current != old:
                    raise PreparationError('Git tag moved; explicit source replacement required')
                if old['ref_kind'] == 'branch':
                    source = current
        else:
            source = resolve_source(declaration, directory, authorization=authorization, ca_file=ca_file)
        sources[name] = source
    validate_git_sources(inputs, sources)
    return sources


def build_git_wheels(sources: dict, work: Path, project: Path, build, *,
                     authorizations: dict | None = None, ca_file: Path | None = None) -> dict:
    """Authoritative wheel metadata must match the explicitly authorized identity."""
    authorizations = authorizations or {}
    wheels = {}
    for name, source in sources.items():
        authorization = scoped_git_authorization(source['repository'], authorizations, name)
        checkout = work / ('git-' + name)
        checkout.mkdir()
        git(['init', '--quiet'], checkout)
        git(['fetch', '--quiet', '--depth=1', source['repository'], source['resolved_sha']], checkout,
            authorization=authorization, ca_file=ca_file)
        git(['checkout', '--quiet', '--detach', 'FETCH_HEAD'], checkout)
        if git(['rev-parse', 'HEAD'], checkout).decode().strip() != source['resolved_sha']:
            raise PreparationError('Git checkout identity mismatch')
        location = checkout / (source['subdirectory'] or '')
        if not location.is_dir() or not location.resolve().is_relative_to(checkout.resolve()):
            raise PreparationError('Git subdirectory unavailable')
        pyproject = location / 'pyproject.toml'
        if pyproject.is_file():
            for requirement in tomllib.loads(pyproject.read_text()).get('build-system', {}).get('requires', []):
                parse_registry_requirement(requirement)
        # Retain Git's archive as provenance; frozen application uses built wheels.
        archive = project / 'sources' / (name + '-' + source['resolved_sha'] + '.tar')
        archive.write_bytes(git(['archive', '--format=tar', 'HEAD'], checkout))
        output = work / ('git-wheels-' + name)
        build(['build', '--wheel', '--no-config', '--out-dir', str(output), str(location)])
        built = list(output.glob('*.whl'))
        if len(built) != 1:
            raise PreparationError('built Git metadata identity mismatch')
        try:
            if canonicalize_name(parse_wheel_filename(built[0].name)[0]) != name:
                raise ValueError()
            with zipfile.ZipFile(built[0]) as wheel:
                metadata = [item for item in wheel.namelist() if item.count('/') == 1 and item.endswith('.dist-info/METADATA')]
                if len(metadata) != 1:
                    raise ValueError()
                identity = BytesParser().parsebytes(wheel.read(metadata[0]))
                if (len(identity.get_all('Name', [])) != 1 or len(identity.get_all('Version', [])) != 1 or
                        canonicalize_name(identity.get('Name', '')) != name or
                        Version(identity.get('Version', '')) != parse_wheel_filename(built[0].name)[1]):
                    raise ValueError()
                for requirement in identity.get_all('Requires-Dist', []):
                    parse_registry_requirement(requirement)
        except (ValueError, InvalidVersion, InvalidWheelFilename, zipfile.BadZipFile, OSError):
            raise PreparationError('built Git metadata identity mismatch') from None
        wheels[name] = built[0]
    if sources:
        (project / 'git-sources.json').write_text(json.dumps(sources, sort_keys=True))
    return wheels


def git_source_digest(source: dict) -> str:
    return digest(source)
