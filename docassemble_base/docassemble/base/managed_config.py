"""Dependency-input observation only; never supplies ordinary solve/boot inputs."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
import os
import re
from urllib.parse import urlsplit

from docassemble.base.managed_preparation import PreparationError, digest, parse_registry_requirement


OBSERVED_CONFIG_ID = 'dependency-inputs'

_INDEX_LABEL = re.compile(r'[a-z][a-z0-9_]*')
_BINDING_NAME = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')


def _private_index_url(value: Any) -> str:
    """Credential-free HTTPS simple-index URL; mirrors the executor private policy."""
    if not isinstance(value, str):
        raise PreparationError('private index requires a credential-free HTTPS URL ending in /')
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        raise PreparationError('private index requires a valid credential-free HTTPS URL') from None
    if (any(char.isspace() or ord(char) < 32 for char in value) or parsed.scheme != 'https'
            or not parsed.hostname or parsed.username is not None or parsed.password is not None
            or '?' in value or '#' in value or not parsed.path.endswith('/') or '%' in parsed.path
            or any(part in ('.', '..') for part in parsed.path.split('/'))):
        raise PreparationError('private index requires a credential-free HTTPS URL ending in /')
    return value


def _observed_private_policy(configuration: Mapping[str, Any]) -> dict[str, Any] | None:
    """Validate administrator private indexes, bindings and TLS exceptions.

    Index mapping order is the deterministic secure ordering; it is recorded
    explicitly so a reordered declaration is a distinct observation. Bindings
    are per normalized name and never inferred for an undiscovered private name.
    """
    indexes = configuration.get('private indexes')
    bindings = configuration.get('private bindings')
    exceptions = configuration.get('private tls exceptions')
    if indexes is None and bindings is None and exceptions is None:
        return None
    if (not isinstance(indexes, dict) or not indexes or not isinstance(bindings, dict) or not bindings
            or any(not isinstance(label, str) or not _INDEX_LABEL.fullmatch(label) or label == 'snapshot'
                   for label in indexes)
            or any(not isinstance(name, str) or not _BINDING_NAME.fullmatch(name)
                   or not isinstance(label, str) or label not in indexes
                   for name, label in bindings.items())):
        raise PreparationError('declare explicit private index bindings for direct and transitive names')
    for url in indexes.values():
        _private_index_url(url)
    if len(set(indexes.values())) != len(indexes):
        raise PreparationError('private index URLs must be distinct')
    if exceptions is None:
        exceptions = []
    if (not isinstance(exceptions, list)
            or any(not isinstance(label, str) or label not in indexes for label in exceptions)
            or len(set(exceptions)) != len(exceptions)):
        raise PreparationError('unsupported private TLS exception declaration')
    return {'order': list(indexes), 'indexes': {label: indexes[label] for label in indexes},
            'bindings': dict(bindings), 'tls_exceptions': list(exceptions)}


def _committed_private_policy(private: dict[str, Any]) -> dict[str, Any]:
    config: dict[str, Any] = {
        'private_indexes': {label: private['indexes'][label] for label in private['order']},
        'private_bindings': dict(private['bindings'])}
    if private['tls_exceptions']:
        config['private_tls_exceptions'] = list(private['tls_exceptions'])
    return config


def private_source_policy_config(config: Any) -> bool:
    """True for a credential-free committed private source policy mapping.

    An operator may provision an image baseline with this committed form (index
    labels, per-name bindings and optional unique TLS exception labels). Index
    URLs must stay credential-free, so no secret can be baseline-provisioned.
    """
    if not isinstance(config, Mapping):
        return False
    keys = set(config)
    if (not {'private_indexes', 'private_bindings'} <= keys
            or keys - {'private_indexes', 'private_bindings', 'private_tls_exceptions'}):
        return False
    indexes, bindings = config['private_indexes'], config['private_bindings']
    exceptions = config.get('private_tls_exceptions') or []
    if (not isinstance(indexes, dict) or not indexes or not isinstance(bindings, dict) or not bindings
            or any(not isinstance(label, str) or not _INDEX_LABEL.fullmatch(label) or label == 'snapshot'
                   for label in indexes)
            or any(not isinstance(name, str) or not _BINDING_NAME.fullmatch(name) or not isinstance(label, str)
                   or label not in indexes for name, label in bindings.items())
            or not isinstance(exceptions, list) or len(set(exceptions)) != len(exceptions)
            or any(not isinstance(label, str) or label not in indexes for label in exceptions)):
        return False
    try:
        for url in indexes.values():
            _private_index_url(url)
    except PreparationError:
        return False
    return len(set(indexes.values())) == len(indexes)


def dependency_snapshot(configuration: Mapping[str, Any], *, environ: Mapping[str, str] | None = None,
                        image_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Canonical restricted snapshot, not a public projection (URLs may be secret).

    Config list elements are verbatim declarations. PYTHONPACKAGES is fallback
    only when that key is absent: parse whole-string first, then legacy comma/
    semicolon segments only if ALL parse. Complex multiple declarations belong
    in the config list. Never split a valid compound requirement or marker.
    Only ENVIRONMENT_TAKES_PRECEDENCE enables PIPINDEXURL/PIPEXTRAINDEXURLS
    overrides after a literal config-from overlay, matching config.load; empty
    overrides do nothing, null clears. Cloud references in dependency inputs or
    config-from fail closed; unrelated secrets are ignored. No network/runtime
    config.load side effects or implicit ambient pip policy.
    """
    if not isinstance(configuration, Mapping):
        raise PreparationError('dependency configuration must be a mapping')
    configuration = dict(configuration)
    # The shipped configuration template uses null for an absent overlay.
    if configuration.get('config from') is not None:
        overlay = configuration['config from']
        if not isinstance(overlay, Mapping):
            raise PreparationError('dependency configuration overlay must be a resolved mapping')
        configuration.update(overlay)
    # config.load converts every underscore in a top-level key to a space, in raw
    # insertion order (later keys win). Reproduce it so `python_packages` and
    # `pip_index_url` are observed rather than silently ignored.
    normalized = {}
    for key, value in configuration.items():
        normalized[re.sub(r'_', ' ', key) if isinstance(key, str) else key] = value
    configuration = normalized
    environ = os.environ if environ is None else environ
    image_config = {} if image_config is None else image_config

    def require_resolved(value):
        if isinstance(value, str) and ('arn:aws:secretsmanager:' in value or '@Microsoft.KeyVault(' in value):
            raise PreparationError('dependency secret references require resolved configuration inputs')
        if isinstance(value, (list, tuple)):
            for item in value:
                require_resolved(item)

    origin = 'python packages'
    if origin in configuration:
        declarations = configuration[origin]
        if not isinstance(declarations, list):
            raise PreparationError('python packages must be a list of registry requirements')
    else:
        origin = 'PYTHONPACKAGES'
        value = environ.get(origin)
        require_resolved(value)
        declarations = []
        if value not in (None, '', 'null'):
            try:
                parse_registry_requirement(value)
                declarations = [value]
            except PreparationError:
                declarations = re.split(r'[,;]', value)
                try:
                    for declaration in declarations:
                        parse_registry_requirement(declaration)
                except PreparationError:
                    raise PreparationError('unsupported PYTHONPACKAGES syntax; use the python packages configuration list') from None
    require_resolved(declarations)
    requirements, names = [], set()
    for declaration in declarations:
        requirement = parse_registry_requirement(declaration)
        if requirement.normalized_name in names:
            raise PreparationError('duplicate normalized configuration requirement')
        names.add(requirement.normalized_name)
        requirements.append({**requirement.__dict__, 'extras': list(requirement.extras), 'origin': origin})

    policy = {key: configuration.get(key) for key in
              ('pip index url', 'pip extra index urls', 'pip trusted host')}
    if str(environ.get('ENVIRONMENT_TAKES_PRECEDENCE', 'false')).lower().strip() == 'true':
        for key, variable in (('pip index url', 'PIPINDEXURL'), ('pip extra index urls', 'PIPEXTRAINDEXURLS')):
            value = environ.get(variable)
            if value is not None and value.strip():
                value = value.strip()
                policy[key] = None if value in ('null', 'None', 'Null') else value
    for value in policy.values():
        require_resolved(value)
    require_resolved(image_config.get('index_url'))

    def items(value):
        if value is None or value == '':
            return []
        if isinstance(value, str):
            value = value.split()
        if not isinstance(value, list) or any(not isinstance(item, str) or not item or
                re.search(r'\s|[\x00-\x1f]', item) for item in value):
            raise PreparationError('unsupported pip policy input')
        return value

    def index(value):
        if value is None or value == '':
            return None
        if not isinstance(value, str) or re.search(r'\s|[\x00-\x1f]', value):
            raise PreparationError('unsupported pip index URL')
        try:
            parsed = urlsplit(value)
            if parsed.scheme not in ('https', 'http') or not parsed.hostname:
                raise ValueError
            parsed.port
        except ValueError:
            raise PreparationError('unsupported pip index URL') from None
        return value

    hosts = items(policy['pip trusted host'])
    if any(not re.fullmatch(r'[A-Za-z0-9.\-\[\]:]+', host) or host.startswith('-') for host in hosts):
        raise PreparationError('unsupported pip trusted host')
    inputs = {'requirements': requirements, 'index_policy': {
        'index_url': index(policy['pip index url']),
        'extra_index_urls': [index(value) for value in items(policy['pip extra index urls'])],
        'trusted_hosts': hosts,
        'image_index_url': index(image_config.get('index_url'))},
        'private_policy': _observed_private_policy(configuration)}
    return {**inputs, 'digest': digest(inputs)}


def config_import_policy(snapshot: dict[str, Any], committed_config: dict[str, Any]) -> dict[str, Any]:
    """Validate a bound observation and project the committed source policy.

    Either a public PyPI policy or an explicit private policy is imported. A
    public observation never replaces a committed private policy, and a private
    observation never falls back to a public namesake. Deterministic declared
    index ordering and explicit bindings are retained, never cross-index oldest/
    newest selection.
    """
    try:
        policy = snapshot['index_policy']
        private = snapshot.get('private_policy')
        configuration: dict[str, Any] = {
            'python packages': [row['declaration'] for row in snapshot['requirements']],
            'pip index url': policy['index_url'], 'pip extra index urls': policy['extra_index_urls'],
            'pip trusted host': policy['trusted_hosts']}
        if private is not None:
            configuration['private indexes'] = {label: private['indexes'][label] for label in private['order']}
            configuration['private bindings'] = private['bindings']
            if private['tls_exceptions']:
                configuration['private tls exceptions'] = private['tls_exceptions']
        checked = dependency_snapshot(configuration, environ={},
            image_config={'index_url': policy['image_index_url']})
        for supplied, parsed in zip(snapshot['requirements'], checked['requirements']):
            if supplied['origin'] not in ('python packages', 'PYTHONPACKAGES') or any(
                    supplied[key] != parsed[key] for key in parsed if key != 'origin'):
                raise ValueError()
        if checked['private_policy'] != private:
            raise ValueError()
        if snapshot['digest'] != digest({'requirements': snapshot['requirements'], 'index_policy': policy,
                                         'private_policy': private}):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise PreparationError('invalid observed configuration snapshot') from None
    public = 'https://pypi.org/simple'
    if private is not None:
        if (policy['index_url'] not in (None, public) or policy['image_index_url'] not in (None, public)
                or policy['extra_index_urls'] or policy['trusted_hosts']):
            raise PreparationError('config import cannot mix private indexes with pip source policy')
        for row in snapshot['requirements']:
            if row['normalized_name'] not in private['bindings']:
                raise PreparationError('declare an explicit private index binding for every configured distribution; '
                                       'missing ' + row['normalized_name'])
        return _committed_private_policy(private)
    if 'private_indexes' in committed_config or 'private_bindings' in committed_config:
        # A committed private policy is authoritative; a public observation can
        # never silently re-source its names from a public namesake.
        raise PreparationError('committed private source policy unsupported in a public observation')
    if (policy['index_url'] not in (None, public) or policy['image_index_url'] not in (None, public)
            or policy['extra_index_urls'] or policy['trusted_hosts']):
        raise PreparationError('config import supports public PyPI only; private indexes and extra or trusted-host policies are unsupported')
    return {'index_url': public}
