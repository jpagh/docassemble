"""HTTPS Git declarations, immutable ref selection, scoped credentials and builder identity."""
from docassemble.base.managed_python import python_target
from copy import deepcopy
import base64
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from docassemble.base.managed_git import (
    parse_git_requirement, legacy_git_declaration, source_declarations, validate_git_sources,
    parse_supported_requirement, git_credential_material, scoped_authorization)
from docassemble_executor.preparation import PreparationError, digest
from docassemble_executor.git_sources import resolve_sources, resolve_source, build_git_wheels, git
from docassemble_executor.registry_builder import RegistryBuilder

URL = 'https://example.org/repo.git'
DECLARATION = 'Example_Pkg[feature] @ git+' + URL + '@main#subdirectory=python/pkg ; python_version >= "3.12"'
CREDENTIAL = 'Example @ git+https://user:tok_en@example.org/repo.git@main#subdirectory=python'
SHA = 'a' * 40
NEXT = 'b' * 40


def inputs(declaration=DECLARATION):
    requirement, source, _ = parse_git_requirement(declaration)
    return {'requirement': requirement.__dict__, 'git_source': source, 'contributions': [],
        'ownership_effects': {}, 'baseline': {'core': {}, 'config': {'index_url': 'https://pypi.org/simple'},
            'target': python_target(), 'tool': {'uv': '0.12.21'},
            'desired_inventory': {}, 'runtime': {'kind': 'normal', 'inventory': {}}}}


def recorded(ref='main', kind='branch', sha=SHA):
    return {'repository': URL, 'declared_ref': ref, 'subdirectory': 'python/pkg',
            'ref_kind': kind, 'resolved_sha': sha}


def test_parse_git_requirement_preserves_exact_declaration():
    requirement, source, credential = parse_git_requirement(DECLARATION)
    assert requirement.declaration == DECLARATION
    assert credential is None
    assert requirement.normalized_name == 'example-pkg'
    assert requirement.extras == ('feature',)
    assert source == {key: value for key, value in recorded().items() if key not in ('ref_kind', 'resolved_sha')}
    assert legacy_git_declaration('Example_Pkg[feature]', URL + '#subdirectory=python/pkg', 'main') == (
        'Example_Pkg[feature] @ git+' + URL + '@main#subdirectory=python/pkg')


@pytest.mark.parametrize('declaration,userinfo,stored', [
    (CREDENTIAL, 'user:tok_en', 'Example @ git+https://example.org/repo.git@main#subdirectory=python'),
    ('Example @ git+https://tok_en@example.org/repo.git@v1', 'tok_en',
     'Example @ git+https://example.org/repo.git@v1'),
    ('Example @ git+https://oauth2:tok_en@example.org:8443/a/b.git#subdirectory=python',
     'oauth2:tok_en', 'Example @ git+https://example.org:8443/a/b.git#subdirectory=python'),
])
def test_credential_declaration_is_accepted_redacted_and_scoped(declaration, userinfo, stored):
    requirement, source, credential = parse_git_requirement(declaration)
    assert credential == userinfo
    assert requirement.declaration == stored
    assert 'tok_en' not in requirement.declaration and 'oauth2' not in requirement.declaration
    assert source == {'repository': source['repository'], 'subdirectory': source['subdirectory'],
                      'declared_ref': source['declared_ref']}
    assert '@' not in source['repository'].split('://', 1)[1]


@pytest.mark.parametrize('declaration', [
    'Example @ git+https://tok_en:tok_en@tok_en@example.org/repo.git',
    'Example @ git+https://to ken@example.org/repo.git',
])
def test_ambiguous_or_unsafe_userinfo_rejected_redacted(declaration):
    with pytest.raises(PreparationError) as error:
        parse_git_requirement(declaration)
    assert 'tok_en' not in str(error.value)


@pytest.mark.parametrize('declaration', [
    'foo ; platform_release == "x@y"',
    'foo ; extra == "a://b"',
    'foo>=1,<3 ; os_name == "posix"',
])
def test_registry_marker_with_at_or_scheme_routes_to_registry(declaration):
    # Routing must use the parsed URL, not a raw '@'/'://' substring search.
    requirement = parse_supported_requirement(declaration)
    assert requirement.normalized_name == 'foo' and requirement.declaration == declaration


@pytest.mark.parametrize('declaration', [
    DECLARATION,
    'Example @ git+https://example.org/repo.git@main',
    'Example @ git+https://localhost:8443/private/repo.git@main',
    'Example @ git+https://user:tok_en@example.org/repo.git@v1',
    'Example @ git+https://tok_en@example.org/repo.git@main#subdirectory=python',
])
def test_accepted_declarations_round_trip_through_redaction(declaration):
    # The stored/redacted declaration is re-parsed by the builder and containers,
    # so every accepted input's redacted form must itself be accepted.
    requirement, source, _ = parse_git_requirement(declaration)
    again, again_source, again_credential = parse_git_requirement(requirement.declaration)
    assert again.declaration == requirement.declaration
    assert again_source == source and again_credential is None


@pytest.mark.parametrize('declaration', [
    'git+https://example.org/SECRET.git', 'X @ git+ssh://SECRET@example.org/repo',
    'X @ ssh://example.org/SECRET', 'X @ git+file:///SECRET', '-e git+https://example.org/SECRET',
    '-r SECRET', 'X @ https://example.org/SECRET.zip', 'X @ git+https://example.org/repo?token=SECRET',
    'X @ git+https://example.org/repo#token=SECRET', 'X @ git+https://example.org/repo@main;SECRET',
    'X @ git+https://example.org/repo@$(SECRET)', 'X @ git+https://example.org/repo@`SECRET`',
    'X @ git+https://example.org/repo|SECRET', 'X @ git+https://example.org/repo\nSECRET',
    'X @ git+https://example.org/repo#subdirectory=../SECRET',
    'X @ git+https://example.org/repo#subdirectory=/SECRET',
    'X @ git+https://example.org/repo#subdirectory=ok//SECRET',
    'X @ git+https://example.org/repo#subdirectory=ok&token=SECRET',
    'X @ git+https://example.org/repo#egg=SECRET', '/tmp/SECRET',
    # Dotless/IPv6-literal hosts with a declared ref are ambiguous with a
    # misplaced credential and are refused before acceptance/staging.
    'X @ git+https://ghpTOK/extra@example.org/repo.git',
    'X @ git+https://TOK@gitlab/org/repo.git@main',
    'X @ git+https://TOK@[fd00::1]/repo.git@main',
])
def test_git_declaration_rejections_are_fixed_and_redacted(declaration):
    with pytest.raises(PreparationError) as error:
        parse_git_requirement(declaration)
    assert 'SECRET' not in str(error.value)
    assert str(error.value).startswith('submit an explicitly named HTTPS Git requirement')


@pytest.mark.parametrize('name,url,branch', [(None, URL, None), ('', URL, None), ('X>=1', URL, None),
    ('X', URL + '@main', 'other'), ('X', 'ssh://SECRET/repo', None)])
def test_legacy_git_transport_requires_explicit_name_and_one_ref(name, url, branch):
    with pytest.raises(PreparationError) as error:
        legacy_git_declaration(name, url, branch)
    assert 'SECRET' not in str(error.value)


def test_legacy_git_transport_retains_credential_for_scoped_staging():
    declaration = legacy_git_declaration('Example', 'https://ghp_TOKEN@example.org/repo.git', 'main')
    requirement, source, credential = parse_git_requirement(declaration)
    assert credential == 'ghp_TOKEN'
    assert source['repository'] == 'https://example.org/repo.git'
    assert 'ghp_TOKEN' not in requirement.declaration


def test_credential_material_is_authorization_only_for_the_exact_repository():
    payload = git_credential_material(URL, 'user:tok_en')
    assert scoped_authorization(URL, payload) == 'Basic ' + base64.b64encode(b'user:tok_en').decode()
    assert scoped_authorization(URL, git_credential_material(URL, 'tok_en')) == 'Basic ' + base64.b64encode(b'tok_en:').decode()
    with pytest.raises(PreparationError, match='scope mismatch'):
        scoped_authorization('https://example.org/other.git', payload)
    assert scoped_authorization(URL, None) is None
    with pytest.raises(PreparationError, match='scope mismatch'):
        scoped_authorization(URL, b'not json')


@pytest.mark.parametrize('ref,output,kind,sha', [
    (None, SHA + '\tHEAD\n', 'branch', SHA),
    ('main', SHA + '\trefs/heads/main\n', 'branch', SHA),
    ('v1', SHA + '\trefs/tags/v1\n' + NEXT + '\trefs/tags/v1^{}\n', 'tag', NEXT),
    ('refs/tags/v1', SHA + '\trefs/tags/v1\n', 'tag', SHA),
    (SHA, '', 'commit', SHA),
])
def test_git_ref_resolution(monkeypatch, tmp_path, ref, output, kind, sha):
    monkeypatch.setattr('docassemble_executor.git_sources.git', lambda *args, **kwargs: output.encode())
    source = resolve_source(dict(repository=URL, declared_ref=ref, subdirectory=None), tmp_path)
    assert source['ref_kind'] == kind and source['resolved_sha'] == sha


@pytest.mark.parametrize('output', ['', SHA + '\trefs/tags/main\n' + NEXT + '\trefs/heads/main\n'])
def test_git_ref_unknown_or_ambiguous_rejected(monkeypatch, tmp_path, output):
    monkeypatch.setattr('docassemble_executor.git_sources.git', lambda *args, **kwargs: output.encode())
    with pytest.raises(PreparationError):
        resolve_source(recorded(), tmp_path)


@pytest.mark.parametrize('kind,operation', [('branch', 'add'), ('tag', 'add'), ('commit', 'add'),
                                         ('tag', 'update'), ('commit', 'update')])
def test_git_recorded_source_preserved_or_moved_tag_rejected(monkeypatch, tmp_path, kind, operation):
    ref = SHA if kind == 'commit' else 'main'
    data = inputs(DECLARATION.replace('@main', '@' + ref))
    old = recorded(ref=ref, kind=kind)
    data['baseline']['git_sources'] = {'example-pkg': old}
    data['contributions'] = [{'id': 'git', 'declaration': data['requirement']['declaration']}]
    data.pop('git_source')
    data['change_operation'] = operation
    data['requirement'] = {'declaration': 'Unrelated' if operation == 'add' else 'example-pkg',
                           'normalized_name': 'unrelated' if operation == 'add' else 'example-pkg'}
    calls = []
    def resolve(source, directory, **kwargs):
        calls.append(source)
        return dict(old, resolved_sha=NEXT)
    monkeypatch.setattr('docassemble_executor.git_sources.resolve_source', resolve)
    if kind == 'tag' and operation == 'update':
        with pytest.raises(PreparationError, match='tag moved; explicit source replacement'):
            resolve_sources(data, tmp_path)
        assert len(calls) == 1
    else:
        assert resolve_sources(data, tmp_path) == {'example-pkg': old}
        assert calls == []


@pytest.mark.parametrize('replacement', [False, True])
def test_git_branch_update_and_explicit_source_replacement_advance(monkeypatch, tmp_path, replacement):
    data = inputs()
    data['baseline']['git_sources'] = {'example-pkg': recorded(kind='tag' if replacement else 'branch')}
    data['contributions'] = [{'id': 'old', 'declaration': DECLARATION}, {'id': 'config', 'declaration': 'example-pkg<4'}]
    if replacement:
        data['replace_git_contributions'] = ['old']
    else:
        data.pop('git_source')
        data['change_operation'] = 'update'
    monkeypatch.setattr('docassemble_executor.git_sources.resolve_source',
                        lambda *args, **kwargs: recorded(sha=NEXT))
    assert resolve_sources(data, tmp_path) == {'example-pkg': recorded(sha=NEXT)}


@pytest.mark.parametrize('change', ['sha', 'subdirectory', 'kind', 'missing', 'extra'])
def test_recorded_git_result_cannot_change_unrelated_source(change):
    data = inputs()
    data.pop('git_source')
    data['contributions'] = [{'id': 'git', 'declaration': DECLARATION}]
    data['requirement'] = {'declaration': 'Another', 'normalized_name': 'another'}
    data['baseline']['git_sources'] = {'example-pkg': recorded()}
    sources = deepcopy(data['baseline']['git_sources'])
    if change == 'missing':
        sources = {}
    elif change == 'extra':
        sources['extra'] = recorded()
    else:
        sources['example-pkg'][{'sha': 'resolved_sha', 'kind': 'ref_kind', 'subdirectory': 'subdirectory'}[change]] = (
            NEXT if change == 'sha' else 'tag' if change == 'kind' else 'other')
    with pytest.raises(PreparationError):
        validate_git_sources(data, sources)


@pytest.mark.parametrize('conflict', ['git', 'artifact', 'core'])
def test_git_source_binding_conflicts_fail_closed(conflict):
    data = inputs()
    if conflict == 'git':
        data['contributions'] = [{'id': 'independent', 'declaration': DECLARATION.replace('@main', '@other')}]
    elif conflict == 'artifact':
        data['contributions'] = [{'id': 'archive', 'declaration': 'example-pkg', 'artifact_ref': 'a' * 64}]
    else:
        data['baseline']['core']['example-pkg'] = {}
    with pytest.raises(PreparationError):
        source_declarations(data)


def test_git_process_has_tls_defaults_no_ambient_credentials_or_shell(monkeypatch, tmp_path):
    monkeypatch.setenv('GIT_SSL_NO_VERIFY', 'true')
    monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
    def run(argv, **kwargs):
        assert argv[:5] == ['git', '-c', 'http.sslVerify=true', '-c', 'http.followRedirects=false']
        assert 'GIT_SSL_NO_VERIFY' not in kwargs['env']
        assert 'GIT_CONFIG_COUNT' not in kwargs['env']
        assert kwargs['env']['GIT_CONFIG_GLOBAL'] == '/dev/null'
        assert kwargs.get('shell', False) is False
        return SimpleNamespace(returncode=1, stdout=b'SECRET', stderr=b'SECRET')
    monkeypatch.setattr('docassemble_executor.git_sources.subprocess.run', run)
    with pytest.raises(PreparationError) as error:
        git(['ls-remote', URL], tmp_path)
    assert 'SECRET' not in str(error.value)


def test_git_injects_scoped_authorization_header_and_explicit_ca_only_when_given(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr('docassemble_executor.git_sources.subprocess.run',
                        lambda argv, **kwargs: (seen.append((argv, kwargs['env'])), SimpleNamespace(returncode=0, stdout=b''))[1])
    git(['ls-remote', URL], tmp_path, authorization='Basic Zm9vOmJhcg==', ca_file=tmp_path / 'ca.pem')
    argv, env = seen[-1]
    # Scoped authorization is delivered through per-process Git configuration in
    # the environment; it must never appear in the process command line.
    assert not any('Zm9vOmJhcg==' in argument for argument in argv)
    assert 'http.extraHeader' not in ' '.join(argv)
    assert env['GIT_CONFIG_COUNT'] == '1'
    assert env['GIT_CONFIG_KEY_0'] == 'http.extraHeader'
    assert env['GIT_CONFIG_VALUE_0'] == 'Authorization: Basic Zm9vOmJhcg=='
    assert 'http.sslCAInfo=' + str(tmp_path / 'ca.pem') in argv
    assert 'http.sslVerify=true' in argv
    git(['ls-remote', URL], tmp_path)
    argv, env = seen[-1]
    assert not any(argument.startswith(('http.extraHeader', 'http.sslCAInfo')) for argument in argv)
    assert 'GIT_CONFIG_COUNT' not in env and 'GIT_CONFIG_VALUE_0' not in env


def test_resolve_sources_scopes_authorization_to_the_exact_repository(monkeypatch, tmp_path):
    data = inputs()
    payload = git_credential_material(URL, 'tok_en')
    calls = []
    def resolve(source, directory, *, authorization=None, ca_file=None):
        calls.append((source['repository'], authorization))
        return dict(source, resolved_sha=SHA, ref_kind='branch')
    monkeypatch.setattr('docassemble_executor.git_sources.resolve_source', resolve)
    assert resolve_sources(data, tmp_path, authorizations={'example-pkg': payload}) == {'example-pkg': recorded()}
    assert calls == [(URL, 'Basic ' + base64.b64encode(b'tok_en:').decode())]
    scoped = inputs(CREDENTIAL.replace('repo.git', 'other.git'))
    with pytest.raises(PreparationError, match='scope mismatch'):
        resolve_sources(scoped, tmp_path, authorizations={'example': payload})


@pytest.mark.parametrize('mismatch', ['filename', 'metadata', None])
def test_git_build_subdirectory_and_authoritative_metadata(monkeypatch, tmp_path, mismatch):
    project = tmp_path / 'project'
    (project / 'sources').mkdir(parents=True)
    def command(argv, directory, **kwargs):
        if argv[0] == 'checkout':
            (directory / 'python/pkg').mkdir(parents=True)
        return (SHA + '\n').encode() if argv[0] == 'rev-parse' else b'tree'
    monkeypatch.setattr('docassemble_executor.git_sources.git', command)
    def build(argv):
        assert argv[-1].endswith('git-example-pkg/python/pkg')
        output = Path(argv[argv.index('--out-dir') + 1])
        output.mkdir()
        name = 'other' if mismatch == 'filename' else 'example_pkg'
        with zipfile.ZipFile(output / (name + '-1.0-py3-none-any.whl'), 'w') as archive:
            archive.writestr(name + '-1.0.dist-info/METADATA', 'Name: ' + (
                'other' if mismatch == 'metadata' else 'Example_Pkg') + '\nVersion: 1.0\n')
    if mismatch:
        with pytest.raises(PreparationError, match='metadata identity mismatch'):
            build_git_wheels({'example-pkg': recorded()}, tmp_path, project, build)
    else:
        wheels = build_git_wheels({'example-pkg': recorded()}, tmp_path, project, build)
        assert list(wheels) == ['example-pkg']
        assert json.loads((project / 'git-sources.json').read_text()) == {'example-pkg': recorded()}
        assert list((project / 'sources').iterdir())[0].read_bytes() == b'tree'


def test_git_build_fetch_uses_scoped_authorization_only(monkeypatch, tmp_path):
    project = tmp_path / 'project'
    (project / 'sources').mkdir(parents=True)
    calls = []
    def command(argv, directory, **kwargs):
        calls.append((argv[0], kwargs.get('authorization')))
        if argv[0] == 'checkout':
            (directory / 'python/pkg').mkdir(parents=True)
        return (SHA + '\n').encode() if argv[0] == 'rev-parse' else b'tree'
    monkeypatch.setattr('docassemble_executor.git_sources.git', command)
    def build(argv):
        output = Path(argv[argv.index('--out-dir') + 1])
        output.mkdir()
        with zipfile.ZipFile(output / 'example_pkg-1.0-py3-none-any.whl', 'w') as archive:
            archive.writestr('example_pkg-1.0.dist-info/METADATA', 'Name: Example_Pkg\nVersion: 1.0\n')
    payload = git_credential_material(URL, 'tok_en')
    build_git_wheels({'example-pkg': recorded()}, tmp_path, project, build, authorizations={'example-pkg': payload})
    assert [authorization for operation, authorization in calls if operation == 'fetch'] == [
        'Basic ' + base64.b64encode(b'tok_en:').decode()]
    assert all(authorization is None for operation, authorization in calls if operation != 'fetch')


def test_registry_builder_git_pinned_wheel_with_complete_constraints(monkeypatch, tmp_path):
    import tomllib
    import docassemble_executor.registry_builder as module
    data = inputs()
    data['contributions'] = [{'id': 'config', 'declaration': 'example-pkg<2'},
                             {'id': 'replaced', 'declaration': DECLARATION.replace('@main', '@old')}]
    data['replace_git_contributions'] = ['replaced']
    monkeypatch.setattr(module.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=b'uv 0.12.21'))
    monkeypatch.setattr(module, 'resolve_sources', lambda *args, **kwargs: {'example-pkg': recorded()})
    def wheels(sources, work, project, build, **kwargs):
        wheel = work / 'example_pkg-1.0-py3-none-any.whl'
        wheel.write_bytes(b'validated by build_git_wheels')
        return {'example-pkg': wheel}
    monkeypatch.setattr(module, 'build_git_wheels', wheels)
    class StopBeforeSolve(Exception):
        pass
    def run(argv, project, cache, venv):
        config = tomllib.loads((project / 'pyproject.toml').read_text())
        assert config['project']['dependencies'] == ['example-pkg<2', 'Example_Pkg[feature] ; python_version >= "3.12"']
        assert config['tool']['uv']['sources'] == {'example-pkg': {'path': 'wheels/example_pkg-1.0-py3-none-any.whl'}}
        raise StopBeforeSolve()
    builder = RegistryBuilder(tmp_path / 'bundles')
    monkeypatch.setattr(builder, '_run', run)
    with pytest.raises(StopBeforeSolve):
        builder.build(data)
    assert digest(recorded()) != digest(recorded(sha=NEXT))


def test_registry_builder_refuses_unresolved_credentials(monkeypatch, tmp_path):
    data = inputs()
    data['git_credentials'] = {'example-pkg': 'ref'}
    with pytest.raises(PreparationError, match='restricted in-process preparation worker'):
        RegistryBuilder(tmp_path / 'bundles').build(data)
    data['git_authorizations'] = {}
    with pytest.raises(PreparationError, match='reference unavailable'):
        RegistryBuilder(tmp_path / 'bundles').build(data)


@pytest.mark.parametrize('stage', ['build-system', 'runtime'])
def test_git_rejects_transitive_direct_sources_before_registry_solve(monkeypatch, tmp_path, stage):
    project = tmp_path / 'project'
    (project / 'sources').mkdir(parents=True)
    calls = []
    def command(argv, directory, **kwargs):
        if argv[0] == 'checkout':
            location = directory / 'python/pkg'
            location.mkdir(parents=True)
            if stage == 'build-system':
                (location / 'pyproject.toml').write_text(
                    '[build-system]\nrequires=["X @ git+ssh://SECRET@example.org/repo"]\n')
        return (SHA + '\n').encode() if argv[0] == 'rev-parse' else b'tree'
    monkeypatch.setattr('docassemble_executor.git_sources.git', command)
    def build(argv):
        calls.append('build')
        output = Path(argv[argv.index('--out-dir') + 1])
        output.mkdir()
        with zipfile.ZipFile(output / 'example_pkg-1.0-py3-none-any.whl', 'w') as archive:
            archive.writestr('example_pkg-1.0.dist-info/METADATA',
                'Name: Example_Pkg\nVersion: 1.0\nRequires-Dist: X @ git+ssh://SECRET@example.org/repo\n')
    with pytest.raises(PreparationError) as error:
        build_git_wheels({'example-pkg': recorded()}, tmp_path, project, build)
    assert 'SECRET' not in str(error.value)
    assert calls == ([] if stage == 'build-system' else ['build'])
