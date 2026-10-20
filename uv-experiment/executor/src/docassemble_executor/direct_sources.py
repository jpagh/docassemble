"""Fetch declarative direct URLs into the durable content-addressed store.

Retained sources are never re-fetched during an unrelated solve; the recorded
exact bytes are verified instead. A fetch that changes the bytes at a recorded
URL fails closed unless the caller requested an explicit Update. Upstream
unavailability is reported with a fixed actionable error and never substituted.
"""
from __future__ import annotations

import base64
import hashlib
import io
import ssl
import urllib.parse
import urllib.request

from docassemble.base.managed_artifacts import (
    distribution_identity, open_regular, retain_artifact, verify_artifact)
from docassemble.base.managed_direct import direct_declarations, redact_direct_url
from .preparation import PreparationError

MAX_BYTES = 512 * 1024 * 1024
_UNAVAILABLE = ('direct URL artifact unavailable; submit a new URL or supported '
                'source through normal preparation and approval')


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        target, source = urllib.parse.urlsplit(new_url), urllib.parse.urlsplit(request.full_url)
        if target.scheme != 'https' or target.hostname != source.hostname or target.port != source.port:
            # Never forward a supplied credential to another scheme or host.
            raise PreparationError(_UNAVAILABLE)
        return super().redirect_request(request, file, code, message, headers, new_url)


def download_source(url: str, *, opener=None, ca_file=None) -> bytes:
    """Fetch exact bytes over verified TLS; never retry with weaker trust."""
    if opener is None:
        context = ssl.create_default_context(cafile=ca_file)
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), _HttpsOnlyRedirect())
    headers = {'User-Agent': 'docassemble-managed-preparation'}
    parts = urllib.parse.urlsplit(url)
    if parts.username is not None or parts.password is not None:
        credential = urllib.parse.unquote(parts.username or '') + ':' + urllib.parse.unquote(parts.password or '')
        headers['Authorization'] = 'Basic ' + base64.b64encode(credential.encode()).decode()
        netloc = parts.hostname or ''
        if parts.port is not None:
            netloc += ':' + str(parts.port)
        url = urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    request = urllib.request.Request(url, headers=headers)
    try:
        with opener.open(request, timeout=60) as response:
            if urllib.parse.urlsplit(response.geturl()).scheme != 'https':
                raise PreparationError(_UNAVAILABLE)
            data = response.read(MAX_BYTES + 1)
    except PreparationError:
        raise
    except Exception:
        raise PreparationError(_UNAVAILABLE) from None
    if len(data) > MAX_BYTES:
        raise PreparationError(_UNAVAILABLE)
    return data


def _recorded_bytes(root, previous: dict) -> tuple[dict, object]:
    path = verify_artifact(root, previous['digest'])
    with open_regular(path, owner=path.parent.stat().st_uid) as handle:
        identity = dict(distribution_identity(handle), digest=previous['digest'])
    return identity, path


def resolve_direct_sources(inputs: dict, root, *, download=None, ca_file=None, retain=None):
    """Return recorded source identity plus the local artifact to bind/solve.

    Network retrieval happens here, in the preparation worker, never inside a DB
    transaction. ``retain`` is the dispatcher's trusted locked retain/register
    callback; standalone fixture callers have no concurrent cleanup authority.
    """
    retain = retain or (lambda reference, source=None: (
        verify_artifact(root, reference) if source is None else retain_artifact(root, reference, source)))
    fetch = download or (lambda url: download_source(url, ca_file=ca_file))
    expected = direct_declarations(inputs)
    baseline = inputs['baseline'].get('direct_sources', {})
    incoming = inputs.get('direct_source')
    requirement_name = (inputs.get('requirement') or {}).get('normalized_name')
    operation = inputs.get('change_operation', 'add')
    sources: dict[str, dict] = {}
    artifacts: dict[str, tuple[dict, object]] = {}
    for name, declaration in expected.items():
        previous = baseline.get(name)
        replacing = bool(incoming) and requirement_name == name
        advancing = operation == 'update' and requirement_name == name
        if previous and not replacing and not advancing:
            if redact_direct_url(previous['url']) != declaration['url']:
                raise PreparationError('direct source changes require explicit replacement')
            retain(previous['digest'])
            identity, path = _recorded_bytes(root, previous)
            if identity['normalized_name'] != name:
                raise PreparationError('retained direct source identity mismatch')
            sources[name] = dict(previous)
            artifacts[name] = (identity, path)
            continue
        if incoming and requirement_name == name:
            url = incoming['url']
        elif previous is not None:
            url = previous['url']
        elif operation == 'adoption' and name in inputs.get('adoption_direct_sources', {}):
            url = inputs['adoption_direct_sources'][name]['url']
        else:
            raise PreparationError('direct source is unavailable; provide an authorized replacement')
        data = fetch(url)
        reference = hashlib.sha256(data).hexdigest()
        with io.BytesIO(data) as handle:
            identity = distribution_identity(handle)
        if identity['normalized_name'] != name:
            raise PreparationError('explicit distribution name differs from archive metadata')
        if previous and url == previous['url'] and reference != previous['digest'] and not advancing:
            raise PreparationError('direct URL content changed; explicit Update required')
        path = retain(reference, io.BytesIO(data))
        sources[name] = {'url': url, 'filename': identity['filename'], 'digest': reference,
                         'normalized_name': name, 'version': identity['version']}
        artifacts[name] = (dict(identity, digest=reference), path)
    return sources, artifacts
