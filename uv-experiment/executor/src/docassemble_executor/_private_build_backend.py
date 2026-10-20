"""PEP 517 shim copied into private build inputs, not an executor entry point.

The snapshot injects the immutable policy below. This is a source-policy gate,
not a sandbox: the authorized backend still executes arbitrary build code.
"""
import importlib
import re

_POLICY: dict = {}


def _validate(requirements):
    # Conservative registry-only gate; uv remains the PEP 508 syntax parser.
    # In particular offline mode alone would still permit local/direct sources.
    if not isinstance(requirements, list):
        raise ValueError('private build requirements violate explicit registry bindings')
    for requirement in requirements:
        if (not isinstance(requirement, str) or any(ord(char) < 32 for char in requirement)
                or any(char in requirement for char in '@/:\\')):
            raise ValueError('private build requirements violate explicit registry bindings')
        match = re.match(r'([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)(?=\s|\[|[<>=!~;]|$)', requirement)
        if (match is None or match[1].endswith(('.whl', '.zip', '.tar.gz'))
                or re.sub(r'[-_.]+', '-', match[1]).lower() not in _POLICY['bindings']):
            raise ValueError('private build requirements violate explicit registry bindings')
    return requirements


def __getattr__(name):
    # Optional missing hooks must remain missing; every present prerequisite hook
    # is intercepted, including wheel/sdist/editable and any future suffix.
    _validate(_POLICY['requires'])
    module, _, attribute = _POLICY['backend'].partition(':')
    backend = importlib.import_module(module)
    if attribute:
        for part in attribute.split('.'):
            backend = getattr(backend, part)
    hook = getattr(backend, name)
    if name.startswith('get_requires_for_build_'):
        def checked(*args, **kwargs):
            return _validate(hook(*args, **kwargs))
        return checked
    return hook
