"""HTTPS Git declarations; credentialed access uses exact-repository references."""
from __future__ import annotations

import base64
import json
import re
from urllib.parse import unquote, urlsplit, urlunsplit

from packaging.requirements import Requirement, InvalidRequirement
from packaging.utils import canonicalize_name

from docassemble.base.managed_preparation import PreparationError, RegistryRequirement


_REJECTED = ('\n', '\r', '\x00', '`', '$', '|', '&', '\\')
_USERINFO = re.compile(r'[A-Za-z0-9._~%+-]+(?::[A-Za-z0-9._~%+-]+)?')
_REDACTED = 'submit an explicitly named HTTPS Git requirement; SSH, CLI, editable, local and shell forms are unsupported'


def parse_git_requirement(declaration: str) -> tuple[RegistryRequirement, dict, str | None]:
    """Return the stored declaration, credential-free identity and optional userinfo.

    A credential-bearing declaration is redacted before it is stored or solved; the
    raw userinfo is returned separately and only ever persists as scoped material.
    """
    try:
        if not isinstance(declaration, str) or len(declaration) > 4096 or any(
                c in declaration for c in _REJECTED):
            raise ValueError()
        parsed = Requirement(declaration)
        url = parsed.url
        if not url or not url.startswith('git+https://'):
            raise ValueError()
        parts = urlsplit(url[4:])
        if (parts.scheme != 'https' or not parts.hostname or
                not re.fullmatch(r'[A-Za-z0-9.:-]+', parts.hostname) or parts.query):
            raise ValueError()
        _ = parts.port  # Validate an explicitly supplied HTTPS port without dropping it.
        credential = None
        if parts.username is not None or parts.password is not None:
            credential = (parts.username or '') + ((':' + parts.password) if parts.password is not None else '')
            if not _USERINFO.fullmatch(credential):
                raise ValueError()
        netloc = parts.netloc.rsplit('@', 1)[-1]
        path, separator, ref = parts.path.rpartition('@')
        if not separator:
            path, ref = parts.path, None
        elif '.' not in parts.hostname and parts.hostname != 'localhost':
            # A dotless (or IPv6-literal) host with a declared ref is ambiguous
            # with a misplaced credential and, once redacted, cannot be re-parsed
            # consistently; refuse before acceptance or credential staging.
            raise ValueError()
        if not path.startswith('/') or path == '/' or not re.fullmatch(r'/[A-Za-z0-9._~%/+-]+', path):
            raise ValueError()
        if ref is not None and (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/+-]*', ref) or
                                '..' in ref or ref.endswith('/') or '@{' in ref):
            raise ValueError()
        subdirectory = None
        if parts.fragment:
            if not parts.fragment.startswith('subdirectory='):
                raise ValueError()
            subdirectory = parts.fragment[len('subdirectory='):]
            if (not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9._/-]*', subdirectory) or
                    any(p in ('', '.', '..') for p in subdirectory.split('/'))):
                raise ValueError()
        repository = urlunsplit((parts.scheme, netloc, path, '', ''))
        source = {'repository': repository, 'subdirectory': subdirectory, 'declared_ref': ref}
        if credential is None:
            stored = declaration
        else:
            stored = parsed.name + ('[' + ','.join(sorted(parsed.extras)) + ']' if parsed.extras else '') + \
                ' @ git+' + repository + ('@' + ref if separator else '') + \
                ('#' + parts.fragment if parts.fragment else '') + \
                (' ; ' + str(parsed.marker) if parsed.marker else '')
        requirement = RegistryRequirement(stored, parsed.name, canonicalize_name(parsed.name),
            tuple(sorted(parsed.extras)), str(parsed.specifier), str(parsed.marker) if parsed.marker else None)
        return requirement, source, credential
    except (InvalidRequirement, ValueError, TypeError, AttributeError):
        raise PreparationError(_REDACTED) from None


def git_credential_material(repository: str, userinfo: str) -> bytes:
    """Opaque scoped payload; never persisted outside restricted credential material."""
    user, _, password = userinfo.partition(':')
    authorization = 'Basic ' + base64.b64encode((unquote(user) + ':' + unquote(password)).encode()).decode()
    return json.dumps({'repository': repository, 'authorization': authorization}, sort_keys=True).encode()


def scoped_authorization(repository: str, payload) -> str | None:
    """Return the Authorization header only for the exact recorded repository."""
    if payload is None:
        return None
    try:
        value = json.loads(payload)
        if (not isinstance(value, dict) or set(value) != {'repository', 'authorization'} or
                value['repository'] != repository or not isinstance(value['authorization'], str) or
                not value['authorization'].startswith('Basic ') or len(value['authorization']) > 4096 or
                '\n' in value['authorization'] or '\r' in value['authorization']):
            raise ValueError()
        return value['authorization']
    except Exception:
        raise PreparationError('Git credential scope mismatch') from None


def legacy_git_declaration(name: str, repository: str, branch: str | None = None) -> str:
    from docassemble.base.managed_preparation import parse_registry_requirement
    if not isinstance(name, str) or not name.strip():
        raise PreparationError('Git transport requires an explicit package name; the name is never inferred from the repository URL')
    requirement = parse_registry_requirement(name)
    if requirement.specifiers:
        raise PreparationError('Git transport requires an explicit distribution name without version bounds')
    if not isinstance(repository, str) or not repository.startswith(('https://', 'git+https://')):
        raise PreparationError('submit an explicitly named HTTPS Git requirement')
    url = repository if repository.startswith('git+') else 'git+' + repository
    # Validate before combining fields: never reinterpret an embedded ref or fragment.
    prefix = requirement.name + ('[' + ','.join(requirement.extras) + ']' if requirement.extras else '')
    marker = ' ; ' + requirement.marker if requirement.marker else ''
    _, source, _ = parse_git_requirement(prefix + ' @ ' + url + marker)
    if branch:
        if source['declared_ref'] is not None or not isinstance(branch, str):
            raise PreparationError('provide one Git ref')
        base, separator, fragment = url.partition('#')
        url = base + '@' + branch + ('#' + fragment if separator else '')
    declaration = prefix + ' @ ' + url + marker
    parse_git_requirement(declaration)
    return declaration


def parse_supported_requirement(declaration: str) -> RegistryRequirement:
    from docassemble.base.managed_preparation import parse_registry_requirement
    from docassemble.base.managed_direct import parse_direct_requirement
    # Route on the parsed URL, never on raw substring: a registry marker may
    # legitimately contain '@' or '://'.
    try:
        parsed = Requirement(declaration)
    except InvalidRequirement:
        return parse_git_requirement(declaration)[0]
    if parsed.url is None:
        return parse_registry_requirement(declaration)
    if parsed.url.startswith('git+'):
        return parse_git_requirement(declaration)[0]
    return parse_direct_requirement(declaration)[0]


def registry_declaration(declaration: str) -> str:
    """Solver-only name/extras/marker; the original source declaration is retained."""
    parsed = Requirement(declaration)
    if parsed.url is None:
        return declaration
    if parsed.url.startswith('git+'):
        parse_git_requirement(declaration)
    else:
        from docassemble.base.managed_direct import parse_direct_requirement
        parse_direct_requirement(declaration)
    return parsed.name + ('[' + ','.join(sorted(parsed.extras)) + ']' if parsed.extras else '') + (
        ' ; ' + str(parsed.marker) if parsed.marker else '')


def source_declarations(inputs: dict) -> dict[str, dict]:
    """All surviving explicit Git source bindings; independent registry bounds stay intact."""
    rows = [row for row in inputs['contributions'] if row['id'] != inputs.get('remove_contribution_id') and
            row['id'] not in inputs.get('replace_git_contributions', []) and
            row['id'] not in inputs.get('replace_artifact_contributions', []) and
            row['id'] not in inputs.get('replace_direct_contributions', [])]
    declarations = [row['declaration'] for row in rows]
    if inputs.get('change_operation', 'add') == 'add':
        declarations.append(inputs['requirement']['declaration'])
    sources = {}
    for declaration in declarations:
        parsed = Requirement(declaration)
        if parsed.url is None or not parsed.url.startswith('git+'):
            continue
        requirement, source, _ = parse_git_requirement(declaration)
        name = requirement.normalized_name
        if name in inputs['baseline']['core'] or (name in sources and sources[name] != source):
            raise PreparationError('conflicting Git sources require explicit replacement')
        if any(row.get('artifact_ref') and canonicalize_name(Requirement(row['declaration']).name) == name for row in rows) or (
                inputs.get('artifact', {}).get('normalized_name') == name):
            raise PreparationError('conflicting distribution sources require explicit replacement')
        sources[name] = source
    return sources


def validate_git_sources(inputs: dict, sources: dict) -> None:
    expected = source_declarations(inputs)
    if set(sources) != set(expected):
        raise PreparationError('invalid recorded Git source identity')
    baseline = inputs['baseline'].get('git_sources', {})
    for name, declaration in expected.items():
        source = sources[name]
        if (set(source) != set(declaration) | {'resolved_sha', 'ref_kind'} or
                any(source.get(key) != value for key, value in declaration.items()) or
                source.get('ref_kind') not in ('branch', 'tag', 'commit', 'historical') or
                not re.fullmatch('[0-9a-f]{40}', source.get('resolved_sha', ''))):
            raise PreparationError('invalid recorded Git source identity')
        previous = baseline.get(name)
        if source['ref_kind'] == 'historical':
            adoption = (inputs.get('adoption', {}) if inputs.get('change_operation') == 'adoption'
                        else inputs['baseline'].get('adoption', {}))
            if (adoption.get('system_actor') != 'system:legacy-adoption' or
                    adoption.get('selected_git', {}).get(name) != source or previous != source):
                raise PreparationError('historical Git pin requires adopted provenance')
        replacing = bool(inputs.get('git_source')) and inputs['requirement']['normalized_name'] == name
        advancing = inputs.get('change_operation') == 'update' and inputs['requirement']['normalized_name'] == name
        if not previous and not replacing:
            raise PreparationError('committed Git source identity unavailable; explicit source replacement required')
        if previous and not replacing:
            if source['ref_kind'] != previous['ref_kind'] or ((not advancing or previous['ref_kind'] != 'branch') and source != previous):
                raise PreparationError('recorded Git source must remain fixed')
        ref = declaration['declared_ref'] or ''
        if re.fullmatch('[0-9a-fA-F]{40}', ref) and source['ref_kind'] != 'commit':
            raise PreparationError('invalid recorded Git source identity')
        if source['ref_kind'] == 'commit' and source['resolved_sha'] != ref.lower():
            raise PreparationError('invalid recorded Git source identity')
