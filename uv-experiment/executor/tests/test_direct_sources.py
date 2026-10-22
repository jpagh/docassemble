"""Direct wheel/archive URLs: declaration identity, redaction, fetch and recording."""
from docassemble.base.managed_python import python_target
from copy import deepcopy
import hashlib
import io
import zipfile

import pytest

from docassemble.base.managed_direct import (
    redact_direct_url, redact_direct_result, parse_direct_requirement, direct_declarations,
    validate_direct_sources)
from docassemble.base.managed_git import parse_supported_requirement, registry_declaration, source_declarations
from docassemble_executor.preparation import PreparationError
from docassemble_executor.direct_sources import resolve_direct_sources
import base64
import urllib.request

URL = 'https://example.org/files/zip_example-1.0-py3-none-any.whl'
DECLARATION = 'ZipExample @ ' + URL
CREDENTIAL = 'ZipExample @ https://user:tok_en@example.org/files/zip_example-1.0-py3-none-any.whl?token=tok_en'


def wheel_bytes(name='ZipExample', version='1.0', requires=()):
    with io.BytesIO() as buffer:
        with zipfile.ZipFile(buffer, 'w') as archive:
            dist = name.lower().replace('-', '_') + '-' + version
            metadata = 'Name: ' + name + '\nVersion: ' + version + '\n'
            metadata += ''.join('Requires-Dist: ' + value + '\n' for value in requires)
            archive.writestr(dist + '.dist-info/METADATA', metadata)
            archive.writestr(dist + '.dist-info/WHEEL', 'Wheel-Version: 1.0\nTag: py3-none-any\n')
        return buffer.getvalue()


def sdist_bytes(name='ZipExample', version='1.0'):
    with io.BytesIO() as buffer:
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr(name.lower().replace('-', '_') + '-' + version + '/PKG-INFO',
                             'Name: ' + name + '\nVersion: ' + version + '\n')
        return buffer.getvalue()


def inputs(declaration=DECLARATION):
    requirement, source, _ = parse_direct_requirement(declaration)
    return {'requirement': requirement.__dict__, 'direct_source': source, 'contributions': [],
        'ownership_effects': {}, 'baseline': {'core': {}, 'config': {'index_url': 'https://pypi.org/simple'},
        'target': python_target(), 'tool': {'uv': '0.12.21'}, 'desired_inventory': {},
        'runtime': {'kind': 'normal', 'inventory': {}}}}


def recorded(declaration=DECLARATION, data=None):
    requirement, source, _ = parse_direct_requirement(declaration)
    data = data if data is not None else wheel_bytes()
    return {'url': source['url'], 'filename': requirement.normalized_name + '-1.0-py3-none-any.whl',
            'digest': hashlib.sha256(data).hexdigest(), 'normalized_name': requirement.normalized_name, 'version': '1.0'}


def store(tmp_path):
    root = tmp_path / 'artifacts'
    root.mkdir(mode=0o700)
    return root


def test_parse_direct_requirement_preserves_name_extras_marker_and_raw_url():
    declaration = DECLARATION + ' ; python_version >= "3.10"'
    requirement, source, credential = parse_direct_requirement(declaration)
    assert credential is None
    assert requirement.normalized_name == 'zipexample' and requirement.extras == ()
    assert requirement.marker == 'python_version >= "3.10"'
    assert requirement.declaration == 'ZipExample @ ' + URL + ' ; python_version >= "3.10"'
    assert source == {'url': URL, 'filename': 'zip_example-1.0-py3-none-any.whl'}
    assert parse_supported_requirement(declaration).declaration == requirement.declaration


@pytest.mark.parametrize('declaration,suffix', [
    ('ZipExample[feature] @ https://example.org/a/zip_example-1.0.zip', '.zip'),
    ('ZipExample @ https://example.org:8443/a/zip_example-1.0-py3-none-any.whl', '.whl'),
])
def test_accepted_archive_forms_round_trip(declaration, suffix):
    requirement, source, _ = parse_direct_requirement(declaration)
    assert source['filename'].endswith(suffix)
    again, again_source, again_credential = parse_direct_requirement(requirement.declaration)
    assert again.declaration == requirement.declaration
    assert again_source['url'] == redact_direct_url(source['url']) and again_credential is None


def test_supplied_credential_is_retained_but_stored_declaration_is_redacted():
    requirement, source, _ = parse_direct_requirement(CREDENTIAL)
    assert source['url'] == 'https://user:tok_en@example.org/files/zip_example-1.0-py3-none-any.whl?token=tok_en'
    assert 'tok_en' not in requirement.declaration
    assert requirement.declaration == (
        'ZipExample @ https://example.org/files/zip_example-1.0-py3-none-any.whl?REDACTED')
    assert ':' not in requirement.declaration.split('://', 1)[1]


@pytest.mark.parametrize('declaration', [
    '-e ZipExample @ https://example.org/files/zip_example.whl',
    '-r SECRET',
    'ZipExample @ file:///tmp/SECRET.whl',
    'ZipExample @ http://example.org/SECRET.whl',
    'ZipExample @ https://example.org/SECRET.tar.gz',
    'ZipExample @ https://example.org/SECRET',
    'ZipExample @ https://example.org/files/zip_example.whl#egg=SECRET',
    'ZipExample @ https://example.org/files/zip_example.whl|SECRET',
    'ZipExample @ https://example.org/files/`SECRET`.whl',
    '/tmp/SECRET.whl',
    'ZipExample @ git+https://example.org/repo.git@main',
    'ZipExample @ https://example.org/files/zip_example.whl?token=SECRET\\x',
])
def test_direct_declaration_rejections_are_fixed_and_redacted(declaration):
    with pytest.raises(PreparationError) as error:
        parse_direct_requirement(declaration)
    assert 'SECRET' not in str(error.value)
    assert str(error.value).startswith('submit an explicitly named HTTPS wheel or archive URL')


def test_git_parser_still_rejects_direct_urls_and_registry_parser_rejects_all_urls():
    with pytest.raises(PreparationError):
        parse_direct_requirement('ZipExample @ git+https://example.org/repo.git@main')
    from docassemble_executor.preparation import parse_registry_requirement
    with pytest.raises(PreparationError):
        parse_registry_requirement('ZipExample @ https://example.org/files/zip_example.whl')


def test_registry_declaration_reduces_direct_source_and_git_sources_skip_it():
    assert registry_declaration(DECLARATION) == 'ZipExample'
    rows = [{'id': 'direct', 'declaration': parse_direct_requirement(DECLARATION)[0].declaration}]
    data = {'contributions': rows, 'baseline': {'core': {}}, 'change_operation': 'update',
            'requirement': {'declaration': 'zipexample', 'normalized_name': 'zipexample'}}
    assert source_declarations(data) == {}
    assert direct_declarations(data) == {'zipexample': {'url': URL, 'filename': 'zip_example-1.0-py3-none-any.whl'}}


@pytest.mark.parametrize('conflict', ['duplicate', 'artifact', 'core'])
def test_direct_source_binding_conflicts_fail_closed(conflict):
    data = inputs()
    if conflict == 'duplicate':
        data['contributions'] = [{'id': 'independent',
            'declaration': parse_direct_requirement('ZipExample @ https://example.org/other.zip')[0].declaration}]
    elif conflict == 'artifact':
        data['contributions'] = [{'id': 'archive', 'declaration': 'zipexample', 'artifact_ref': 'a' * 64}]
    else:
        data['baseline']['core']['zipexample'] = {}
    with pytest.raises(PreparationError):
        direct_declarations(data)


def test_resolve_new_direct_source_fetches_retains_and_records_exact_bytes(tmp_path):
    root = store(tmp_path)
    data, calls = inputs(), []
    data_bytes = wheel_bytes()
    sources, artifacts = resolve_direct_sources(data, root, download=lambda url: (calls.append(url), data_bytes)[1])
    assert calls == [URL]
    source = sources['zipexample']
    assert source == {'url': URL, 'filename': 'zipexample-1.0-py3-none-any.whl',
                      'digest': hashlib.sha256(data_bytes).hexdigest(), 'normalized_name': 'zipexample', 'version': '1.0'}
    assert (root / source['digest']).read_bytes() == data_bytes
    identity, path = artifacts['zipexample']
    assert path == root / source['digest'] and identity['filename'] == 'zipexample-1.0-py3-none-any.whl'
    validate_direct_sources(data, sources)


def test_resolve_sdist_zip_records_pkg_info_identity(tmp_path):
    root = store(tmp_path)
    data = inputs('ZipExample @ https://example.org/files/zip_example-1.0.zip')
    sources, artifacts = resolve_direct_sources(data, root, download=lambda url: sdist_bytes())
    assert sources['zipexample']['filename'] == 'zipexample-1.0.zip'
    assert artifacts['zipexample'][0]['version'] == '1.0'


def test_retained_direct_source_is_never_refetched_and_verifies_recorded_digest(tmp_path):
    root = store(tmp_path)
    data = inputs()
    data_bytes = wheel_bytes()
    sources, _ = resolve_direct_sources(data, root, download=lambda url: data_bytes)
    unrelated = {'requirement': {'declaration': 'Unrelated', 'normalized_name': 'unrelated'},
        'contributions': [{'id': 'direct', 'declaration': parse_direct_requirement(DECLARATION)[0].declaration}],
        'ownership_effects': {}, 'baseline': {**data['baseline'], 'direct_sources': sources}}
    again, artifacts = resolve_direct_sources(unrelated, root,
        download=lambda url: (_ for _ in ()).throw(AssertionError('re-fetch')))
    assert again == sources
    assert artifacts['zipexample'][1] == root / sources['zipexample']['digest']
    assert validate_direct_sources(unrelated, again) is None


def test_changed_bytes_at_same_url_fail_closed_but_explicit_update_accepts(tmp_path):
    root = store(tmp_path)
    data = inputs()
    sources, _ = resolve_direct_sources(data, root, download=lambda url: wheel_bytes())
    baseline = {**data['baseline'], 'direct_sources': sources}
    replacement_requirement, replacement_source, _ = parse_direct_requirement(DECLARATION)
    replacement = {'requirement': replacement_requirement.__dict__, 'contributions': [],
        'ownership_effects': {}, 'direct_source': replacement_source, 'baseline': baseline}
    with pytest.raises(PreparationError, match='content changed; explicit Update required'):
        resolve_direct_sources(replacement, root, download=lambda url: wheel_bytes(version='2.0'))
    update = {'requirement': {'declaration': 'zipexample', 'normalized_name': 'zipexample'},
        'contributions': [{'id': 'direct', 'declaration': parse_direct_requirement(DECLARATION)[0].declaration}],
        'ownership_effects': {}, 'change_operation': 'update', 'baseline': baseline}
    advanced, _ = resolve_direct_sources(update, root, download=lambda url: wheel_bytes(version='2.0'))
    assert advanced['zipexample']['version'] == '2.0' and advanced['zipexample']['digest'] != sources['zipexample']['digest']


def test_upstream_unavailable_is_reported_without_substitution(tmp_path):
    root = store(tmp_path)
    def unavailable(url):
        raise PreparationError('direct URL artifact unavailable; submit a new URL or supported source')
    with pytest.raises(PreparationError, match='submit a new URL'):
        resolve_direct_sources(inputs(), root, download=unavailable)


def test_metadata_name_mismatch_rejected_and_never_retained(tmp_path):
    root = store(tmp_path)
    with pytest.raises(PreparationError, match='differs from archive metadata'):
        resolve_direct_sources(inputs(), root, download=lambda url: wheel_bytes(name='Other'))
    assert list(root.iterdir()) == []


def test_redact_direct_result_drops_userinfo_and_query_values(tmp_path):
    data = inputs(CREDENTIAL)
    sources, _ = resolve_direct_sources(data, store(tmp_path), download=lambda url: wheel_bytes())
    result = {'bundle': {}, 'direct_sources': sources}
    redacted = redact_direct_result(result)
    assert redacted['direct_sources']['zipexample']['url'] == (
        'https://example.org/files/zip_example-1.0-py3-none-any.whl?REDACTED')
    assert 'tok_en' not in repr(redacted)
    assert redacted is not result and sources['zipexample']['url'] != redacted['direct_sources']['zipexample']['url']
    assert redact_direct_result({'bundle': {}}) == {'bundle': {}}


def test_recorded_direct_source_must_remain_fixed():
    data = inputs()
    sources = {'zipexample': recorded()}
    data['baseline']['direct_sources'] = sources
    data['contributions'] = [{'id': 'direct', 'declaration': parse_direct_requirement(DECLARATION)[0].declaration}]
    data.pop('direct_source')
    data['requirement'] = {'declaration': 'Unrelated', 'normalized_name': 'unrelated'}
    broken = deepcopy(sources)
    broken['zipexample']['digest'] = 'b' * 64
    with pytest.raises(PreparationError, match='must remain fixed'):
        validate_direct_sources(data, broken)
    unavailable = dict(data, baseline={'core': {}, 'config': {}, 'target': {}, 'tool': {},
                                       'desired_inventory': {}, 'runtime': {'kind': 'normal', 'inventory': {}}})
    with pytest.raises(PreparationError, match='identity unavailable'):
        validate_direct_sources(unavailable, sources)


class _Response:
    def __init__(self, data, url):
        self._data, self._url = data, url

    def read(self, size=-1):
        return self._data

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _RecordingOpener:
    def __init__(self):
        self.request = None

    def open(self, request, timeout=None):
        self.request = request
        return _Response(b'exact-bytes', request.full_url)


def test_download_source_sends_supplied_userinfo_as_basic_without_keeping_it_in_the_url():
    from docassemble_executor.direct_sources import download_source
    opener = _RecordingOpener()
    assert download_source('https://user:tok_en@example.org/files/a.whl?token=tok_en', opener=opener) == b'exact-bytes'
    assert opener.request.full_url == 'https://example.org/files/a.whl?token=tok_en'
    assert opener.request.get_header('Authorization') == 'Basic ' + base64.b64encode(b'user:tok_en').decode()


def test_download_source_never_forwards_a_credential_to_another_scheme_or_host():
    from docassemble_executor.direct_sources import _HttpsOnlyRedirect
    handler = _HttpsOnlyRedirect()
    request = urllib.request.Request('https://example.org/files/a.whl')
    for target in ('http://example.org/files/a.whl', 'https://other.example.org/files/a.whl',
                   'https://example.org:8443/files/a.whl'):
        with pytest.raises(PreparationError, match='unavailable'):
            handler.redirect_request(request, None, 302, 'Found', {}, target)
