"""14.1/14.3 and 16.1/16.2 discriminating retained-source regressions."""
from docassemble.base.managed_python import python_requires, python_target
from copy import deepcopy

import pytest

from docassemble.base.managed_adoption import effective_storage, upload_path, AdoptionBlocked, SYSTEM_ACTOR
from docassemble.base.managed_git import validate_git_sources, parse_git_requirement
from docassemble.base.managed_preparation import PreparationError
from docassemble_executor.git_sources import resolve_sources
from docassemble_executor.legacy_adoption import resolve_git
from test_legacy_adoption import metadata, SOURCE


def historical_inputs(source, *, adoption=True):
    requirement, _, _ = parse_git_requirement('Example @ git+https://example.org/repo@main')
    record = {'system_actor': SYSTEM_ACTOR, 'selected_git': {'example': source}}
    return {'baseline': {'core': {}, 'git_sources': {'example': source},
                         **({'adoption': record} if adoption else {})},
            'contributions': [{'id': 'git', 'declaration': requirement.declaration}],
            'requirement': requirement.__dict__, 'change_operation': 'update'}


def test_symbolic_historical_pin_validates_and_never_refreshes_live_ref(tmp_path, monkeypatch):
    metadata(tmp_path)
    monkeypatch.setenv('DA_RUNTIME_VENV', str(tmp_path))
    def forbidden(*args, **kwargs):
        raise AssertionError('historical pin must not resolve or refresh live ref')
    monkeypatch.setattr('docassemble_executor.legacy_adoption.resolve_source', forbidden)
    monkeypatch.setattr('docassemble_executor.git_sources.resolve_source', forbidden)
    source = resolve_git('example', SOURCE, None)
    assert source == dict(SOURCE, ref_kind='historical', resolved_sha='a' * 40)
    inputs = historical_inputs(source)
    assert resolve_sources(inputs, tmp_path) == {'example': source}
    inputs['change_operation'] = 'remove'
    inputs['remove_contribution_id'] = 'git'
    assert resolve_sources(inputs, tmp_path) == {}
    initial = historical_inputs(source, adoption=False)
    initial.update(change_operation='adoption', adoption={'system_actor': SYSTEM_ACTOR,
                                                         'selected_git': {'example': source}})
    validate_git_sources(initial, {'example': source})


@pytest.mark.parametrize('case', ['no-adoption', 'missing-selected', 'unknown-kind', 'explicit-sha-mismatch'])
def test_historical_kind_cannot_bypass_provenance_or_explicit_commit_equality(case):
    source = dict(SOURCE, ref_kind='historical', resolved_sha='a' * 40)
    inputs = historical_inputs(source)
    if case == 'no-adoption':
        inputs['baseline'].pop('adoption')
    elif case == 'missing-selected':
        inputs['baseline']['adoption']['selected_git'] = {}
    elif case == 'unknown-kind':
        source['ref_kind'] = 'unknown'
    else:
        source.update(ref_kind='commit', declared_ref='b' * 40)
        inputs['contributions'][0]['declaration'] = 'Example @ git+https://example.org/repo@' + 'b' * 40
    with pytest.raises(PreparationError):
        validate_git_sources(inputs, {'example': deepcopy(source)})


@pytest.mark.parametrize('configuration,environ,cloud', [
    ({'s3': {'bucket': 'bucket', 'secret access key': 'SENTINEL'}}, {}, True),
    ({'s3': {'enable': False, 'bucket': 'bucket'}}, {}, False),
    ({'s3': {'enable': 'false'}}, {}, True),  # application treats nonempty YAML strings as truthy
    ({'azure': {'account name': 'name', 'account key': 'SENTINEL'}}, {}, True),
    ({'azure': {'container': 'container'}}, {}, False),
    ({'s3': {'enable': False}}, {'ENVIRONMENT_TAKES_PRECEDENCE': 'true', 'S3BUCKET': 'bucket', 'S3ENABLE': 'true'}, True),
    ({'s3': {'bucket': 'bucket'}}, {'ENVIRONMENT_TAKES_PRECEDENCE': 'true', 'S3ENABLE': 'false'}, False),
    ({}, {'ENVIRONMENT_TAKES_PRECEDENCE': 'true', 'AZUREACCOUNTNAME': 'name', 'AZUREACCOUNTKEY': 'SENTINEL'}, True),
    ({}, {'ENVIRONMENT_TAKES_PRECEDENCE': 'false', 'S3BUCKET': 'bucket'}, False),
    ({}, {'ENVIRONMENT_TAKES_PRECEDENCE': 'true', 'S3_SSE_CUSTOMER_KEY': 'SENTINEL'}, True),
])
def test_effective_storage_is_credential_free_and_rejects_cloud_zip(configuration, environ, cloud, tmp_path):
    storage = effective_storage(configuration, environ)
    assert all(set(value) == {'enable'} and isinstance(value['enable'], bool) for value in storage.values())
    assert 'SENTINEL' not in repr(storage)
    captured = dict(storage, uploads=str(tmp_path))
    if cloud:
        with pytest.raises(AdoptionBlocked, match='restore required ZIP bytes'):
            upload_path(captured, 7)
    else:
        assert upload_path(captured, 7).name == 'file.zip'


@pytest.mark.parametrize('missing_gunicorn', [False, True])
def test_combined_solve_cannot_rebuild_nondeterministic_frozen_core_wheel(tmp_path, monkeypatch, missing_gunicorn):
    import json
    from types import SimpleNamespace
    from docassemble_executor.registry_builder import RegistryBuilder
    from docassemble_executor.bundles import sha256_file
    import docassemble_executor.registry_builder as module
    wheel = tmp_path / 'gunicorn-23.0.0-py3-none-any.whl'
    # A second independent build of the same sdist would produce these other bytes.
    wheel.write_bytes(b'nondeterministic-sdist-build-number-one')
    alternative = b'nondeterministic-sdist-build-number-two'
    core = {'gunicorn': {'version': '23.0.0', 'source_digest': 'a' * 64}}
    baseline = {'core': core, 'target': python_target(), 'tool': {'uv': '0.12.21'},
                'config': {'index_url': 'https://pypi.org/simple'}, 'desired_inventory': {},
                'runtime': {'kind': 'normal', 'inventory': {}}}
    inputs = {'baseline': baseline, 'contributions': [], 'change_operation': 'upgrade', 'ownership_effects': {}}
    builder = RegistryBuilder(tmp_path / 'store')
    calls = []
    def run(args, project, cache, venv):
        calls.append(args[0])
        if args[0] == 'build':
            (project / 'wheels' / wheel.name).write_bytes(alternative)
        if args[0] == 'lock':
            (project / 'uv.lock').write_text('requires-python = ' + json.dumps(python_requires()) + '\n[[package]]\nname="gunicorn"\nversion="23.0.0"\nsource={path="wheels/' + wheel.name + '"}\n')
        if args[0] == 'sync':
            cache.mkdir(exist_ok=True)
            venv.mkdir(exist_ok=True)
            if not missing_gunicorn:
                (venv / 'bin').mkdir(exist_ok=True)
                executable = venv / 'bin' / 'gunicorn'
                executable.write_text('#!/bin/sh\n')
                executable.chmod(0o700)
    monkeypatch.setattr(builder, '_run', run)
    monkeypatch.setattr(module, 'inspect_environment', lambda venv: {'gunicorn': '23.0.0'})
    monkeypatch.setattr(module, 'wheel_payload_material', lambda directory: {'gunicorn': {'build': 'one'}})
    monkeypatch.setattr(module, 'verify_environment', lambda *args: SimpleNamespace(ok=True))
    def solve():
        return builder._solve(inputs, operation='upgrade', core=core, requirements=['gunicorn==23.0.0'],
                              image_wheels={'gunicorn': wheel}, local_artifacts={}, direct_sources={},
                              authorizations={}, seed_parent=None, validate_result=False, materialize=True)
    if missing_gunicorn:
        with pytest.raises(PreparationError, match='protected Gunicorn executable'):
            solve()
        assert list(builder.store.iterdir()) == []
        return
    result = solve()
    retained = builder.store / result['bundle']['digest']
    assert 'build' not in calls
    assert (retained / 'wheels' / wheel.name).read_bytes() == wheel.read_bytes() != alternative
    assert sha256_file(retained / 'wheels' / wheel.name) in result['bundle']['artifacts']
    assert json.loads((retained / 'inventory.json').read_text()) == core
