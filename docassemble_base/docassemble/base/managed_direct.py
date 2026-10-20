"""Declarative direct wheel/archive URLs; exact bytes live in the durable store.

A direct reference is accepted only as an explicitly named PEP 508 HTTPS URL whose
path is a wheel or sdist archive. Local, editable, CLI, ``file://`` and non-HTTPS
forms are rejected with fixed redacted errors and are never rewritten into a
different request. Distribution metadata (wheel ``METADATA``/sdist ``PKG-INFO``)
owns identity; the URL is only a retrieval hint.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from docassemble.base.managed_preparation import PreparationError, RegistryRequirement


_REJECTED = ('\n', '\r', '\x00')
_UNSAFE_URL = (' ', '\t', '`', '$', '|', '\\')
_SUPPORTED_SUFFIXES = ('.whl', '.zip')
_REDACTED_VALUE = 'REDACTED'
_REDACTED = ('submit an explicitly named HTTPS wheel or archive URL; '
             'local, editable, CLI and file forms are unsupported')


def redact_direct_url(url: str) -> str:
    """Drop userinfo and the entire query; stable under re-redaction."""
    parts = urlsplit(url)
    host = parts.hostname or ''
    if ':' in host:  # urlsplit strips IPv6 brackets; rebuild valid authority.
        host = '[' + host + ']'
    netloc = host
    if parts.port is not None:
        netloc += ':' + str(parts.port)
    query = _REDACTED_VALUE if parts.query else ''
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


def _stored_declaration(parsed: Requirement, url: str) -> str:
    return parsed.name + ('[' + ','.join(sorted(parsed.extras)) + ']' if parsed.extras else '') + \
        ' @ ' + url + (' ; ' + str(parsed.marker) if parsed.marker else '')


def parse_direct_requirement(declaration: str) -> tuple[RegistryRequirement, dict, None]:
    """Return a redacted stored declaration and the raw retrieval URL.

    The raw URL (including supplied embedded credentials and query) is returned
    separately so it can be used as supplied for the sensitive immutable source
    record and retrieval; the stored/solved declaration is redacted. This is the
    approved direct-URL exception and does not extend to Git or index sources.
    """
    try:
        if (not isinstance(declaration, str) or not declaration or len(declaration) > 4096 or
                any(char in declaration for char in _REJECTED)):
            raise ValueError()
        parsed = Requirement(declaration)
        url = parsed.url
        if not url or not url.startswith('https://'):
            raise ValueError()
        if any(char in url for char in _UNSAFE_URL):
            raise ValueError()
        parts = urlsplit(url)
        if (parts.scheme != 'https' or not parts.hostname or
                not re.fullmatch(r'[A-Za-z0-9.-]+', parts.hostname) or parts.fragment):
            raise ValueError()
        _ = parts.port  # Validate an explicitly supplied HTTPS port without dropping it.
        filename = parts.path.rsplit('/', 1)[-1]
        if not filename.lower().endswith(_SUPPORTED_SUFFIXES):
            raise ValueError()
        requirement = RegistryRequirement(
            _stored_declaration(parsed, redact_direct_url(url)), parsed.name,
            canonicalize_name(parsed.name), tuple(sorted(parsed.extras)), '',
            str(parsed.marker) if parsed.marker else None)
        return requirement, {'url': url, 'filename': filename}, None
    except (InvalidRequirement, ValueError, TypeError, AttributeError):
        raise PreparationError(_REDACTED) from None


def direct_declarations(inputs: dict) -> dict[str, dict]:
    """All surviving explicit direct-URL bindings, keyed by normalized name."""
    rows = [row for row in inputs['contributions'] if row['id'] != inputs.get('remove_contribution_id') and
            row['id'] not in inputs.get('replace_git_contributions', []) and
            row['id'] not in inputs.get('replace_artifact_contributions', []) and
            row['id'] not in inputs.get('replace_direct_contributions', [])]
    declarations = [row['declaration'] for row in rows]
    if inputs.get('change_operation', 'add') == 'add':
        declarations.append(inputs['requirement']['declaration'])
    sources: dict[str, dict] = {}
    for declaration in declarations:
        parsed = Requirement(declaration)
        if parsed.url is None or parsed.url.startswith('git+'):
            continue
        requirement, source, _ = parse_direct_requirement(declaration)
        name = requirement.normalized_name
        if name in inputs['baseline']['core'] or (name in sources and sources[name] != source):
            raise PreparationError('conflicting direct sources require explicit replacement')
        if any(row.get('artifact_ref') and canonicalize_name(Requirement(row['declaration']).name) == name
               for row in rows):
            raise PreparationError('conflicting distribution sources require explicit replacement')
        sources[name] = source
    return sources


def validate_direct_sources(inputs: dict, sources: dict) -> None:
    expected = direct_declarations(inputs)
    if set(sources) != set(expected):
        raise PreparationError('invalid recorded direct source identity')
    baseline = inputs['baseline'].get('direct_sources', {})
    for name, declaration in expected.items():
        source = sources[name]
        if (set(source) != {'url', 'filename', 'digest', 'normalized_name', 'version'} or
                source.get('normalized_name') != name or
                not isinstance(source.get('version'), str) or not source.get('version') or
                not isinstance(source.get('filename'), str) or not source.get('filename') or
                not re.fullmatch('[0-9a-f]{64}', source.get('digest', '')) or
                not isinstance(source.get('url'), str) or
                redact_direct_url(source['url']) != declaration['url']):
            raise PreparationError('invalid recorded direct source identity')
        previous = baseline.get(name)
        replacing = (inputs.get('change_operation') == 'adoption' or
                     bool(inputs.get('direct_source')) and inputs['requirement']['normalized_name'] == name)
        advancing = inputs.get('change_operation') == 'update' and inputs['requirement']['normalized_name'] == name
        if not previous and not replacing:
            raise PreparationError('committed direct source identity unavailable; explicit source replacement required')
        if previous and not replacing and not advancing and source != previous:
            raise PreparationError('recorded direct source must remain fixed')


def redact_direct_result(result):
    """Redacted projection of a ready result; never mutates the stored attestation."""
    if not isinstance(result, dict) or 'direct_sources' not in result:
        return result
    redacted = dict(result)
    redacted['direct_sources'] = {name: dict(source, url=redact_direct_url(source['url']))
                                  for name, source in result['direct_sources'].items()}
    return redacted
