"""No-solve retained material, rollback schema policy, and no older setup."""
from docassemble.base.managed_python import python_target, python_requires
import copy
import json
from types import SimpleNamespace
import zipfile

import pytest

from docassemble_executor.alembic_probe import SchemaCheck
from docassemble_executor.bundles import sha256_file
from docassemble_executor.preparation import PreparationError, digest
from docassemble_executor.registry_builder import RegistryBuilder
from docassemble_executor.rollback_schema import check_rollback_schema, retained_schema_policy


def bundle_at(tmp_path, *, incompatible='bad', revision='old'):
    directory = tmp_path / 'staging'
    directory.mkdir(parents=True)
    wheels = directory / 'wheels'
    wheels.mkdir()
    with zipfile.ZipFile(wheels / 'docassemble_webapp-1.0-py3-none-any.whl', 'w') as wheel:
        wheel.writestr('docassemble_webapp-1.0.dist-info/METADATA',
                       'Name: docassemble.webapp\nVersion: 1.0\nDocassemble-Incompatible-Schema-Revision: ' + incompatible + '\n')
        wheel.writestr('docassemble/webapp/alembic/versions/old.py', 'revision = ' + repr(revision))
    inventory = {'docassemble-webapp': {'version': '1.0', 'source_digest': 'a' * 64}}
    for filename, value in {'inventory.json': json.dumps(inventory), 'installed-material.json': '{}',
                            'tool.json': json.dumps({'uv': '0.12.21', 'target': python_target()}),
                            'pyproject.toml': '[project]\nrequires-python = ' + json.dumps(python_requires()),
                            'uv.lock': 'requires-python = ' + json.dumps(python_requires()),
                            'solve-project.toml': '[project]\nrequires-python = ' + json.dumps(python_requires()),
                            'solve.lock': 'requires-python = ' + json.dumps(python_requires())}.items():
        (directory / filename).write_text(value)
    manifest = {path.relative_to(directory).as_posix(): sha256_file(path) for path in directory.rglob('*') if path.is_file()}
    reference = digest(manifest)
    (directory / 'manifest.json').write_text(json.dumps(manifest))
    retained = tmp_path / reference
    directory.rename(retained)
    return retained, {'digest': reference, 'project_digest': sha256_file(retained / 'pyproject.toml'),
                      'lock_digest': sha256_file(retained / 'uv.lock'), 'inventory': inventory,
                      'artifacts': [manifest['wheels/docassemble_webapp-1.0-py3-none-any.whl']]}


@pytest.mark.parametrize('revision,status,usable', [('old', 'compatible', True), ('advanced', 'unproven', True),
                                                  ('bad', 'incompatible', False), (None, 'incompatible', False)])
def test_retained_policy_distinguishes_unknown_from_known_incompatible(tmp_path, monkeypatch, revision, status, usable):
    directory, _ = bundle_at(tmp_path)
    monkeypatch.setattr('docassemble_executor.rollback_schema.check_schema',
                        lambda *_: SchemaCheck('compatible' if revision == 'old' else 'incompatible', 'observed', revision, ('old',)))
    check = check_rollback_schema(None, None, directory)
    assert check.status == status and check.usable is usable
    if status == 'unproven':
        assert 'cannot prove' in check.detail
    assert retained_schema_policy(directory)['source'] == 'retained-webapp-wheel-metadata'


def test_unreadable_database_and_retained_inventory_fail_closed(tmp_path, monkeypatch):
    directory, _ = bundle_at(tmp_path, revision='')
    with pytest.raises(PreparationError, match='inventory unreadable'):
        retained_schema_policy(directory)
    # A readable migration directory is not a substitute for readable live DB state.
    directory, _ = bundle_at(tmp_path / 'valid')
    monkeypatch.setattr('docassemble_executor.rollback_schema.check_schema',
                        lambda *_: SchemaCheck('unreadable', 'unreadable'))
    assert not check_rollback_schema(None, None, directory).usable


@pytest.fixture
def restore(tmp_path, monkeypatch):
    directory, bundle = bundle_at(tmp_path)
    baseline = {'core': bundle['inventory'], 'target': python_target(), 'tool': {'uv': '0.12.21'},
                'image_baseline': {'image_target': 'image'}, 'config': {'index_url': 'https://pypi.org/simple'},
                'runtime': {'kind': 'normal', 'inventory': {}}, 'desired_inventory': {}}
    historical = dict(baseline, publication={'result': {'bundle': bundle}}, contributions=[])
    inputs = {'baseline': baseline, 'change_operation': 'rollback', 'ownership_effects': {'identities': {}},
              'contributions': [], 'committed_contributions': [],
              'rollback': {'revision_id': 'history', 'snapshot': historical}}
    monkeypatch.setattr('docassemble_executor.registry_builder.os.geteuid', lambda: 999)
    monkeypatch.setattr('docassemble_executor.registry_builder.subprocess.run', lambda *a, **k: SimpleNamespace(stdout=b'uv 0.12.21'))
    monkeypatch.setattr('docassemble_executor.image_baseline.verify_image_recovery_core', lambda *_: None)
    monkeypatch.setattr('docassemble_executor.image_baseline.load_image_wheels', lambda *_: (baseline, {}))
    monkeypatch.setattr('docassemble_executor.registry_builder.verify_environment', lambda *_: SimpleNamespace(ok=True))
    builder = RegistryBuilder(tmp_path, image_baseline=tmp_path / 'image.json')
    calls = []
    monkeypatch.setattr(builder, '_run', lambda arguments, *args: calls.append(arguments))
    return builder, inputs, directory, calls


def test_exact_restore_uses_only_frozen_offline_no_build_sync(restore):
    builder, inputs, directory, calls = restore
    before = {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}
    result = builder.build(inputs)
    assert result['bundle'] == inputs['rollback']['snapshot']['publication']['result']['bundle']
    assert result['validation']['no_solve']
    assert calls == [['sync', '--frozen', '--offline', '--no-build', '--no-config']]
    assert before == {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}


@pytest.mark.parametrize('fault', ['missing-lock', 'changed-wheel', 'missing-artifact', 'target', 'core'])
def test_missing_or_incompatible_retained_material_never_solves(restore, fault):
    builder, original, directory, calls = restore
    inputs = copy.deepcopy(original)
    if fault == 'missing-lock':
        (directory / 'uv.lock').unlink()
    elif fault == 'changed-wheel':
        next((directory / 'wheels').iterdir()).write_bytes(b'corrupt')
    elif fault == 'missing-artifact':
        inputs['rollback']['snapshot']['publication']['result']['bundle']['artifacts'].append('f' * 64)
    elif fault == 'target':
        inputs['rollback']['snapshot']['target'] = {'python': '3.13'}
    else:
        inputs['rollback']['snapshot']['core'] = {'other': {}}
    with pytest.raises((PreparationError, OSError)):
        builder.build(inputs)
    assert calls == []


def test_rollback_and_later_ordinary_revision_skip_setup(tmp_path, monkeypatch):
    from docassemble_executor import published
    from docassemble_executor.paths import ExecutorPaths
    from dataclasses import replace
    from docassemble_executor.roles import RUNTIME_ROLES, INFRASTRUCTURE_ROLES
    paths = replace(ExecutorPaths.from_env(), intent_file=tmp_path / 'intent.json', state_dir=tmp_path / 'state')
    directory, bundle = bundle_at(tmp_path)
    context = SimpleNamespace(core_baseline='image', expected_core_versions={}, db=None, manifest={})
    monkeypatch.setenv('DA_DURABLE_WORK_STORE', str(tmp_path))
    monkeypatch.setattr(published, 'load_context', lambda *a, **k: context)
    monkeypatch.setattr(published, '_create_tables', lambda *_: pytest.fail('older setup must not run'))
    monkeypatch.setattr('docassemble_executor.rollback_schema.check_rollback_schema', lambda *a: SchemaCheck('unproven', 'advanced'))
    monkeypatch.setattr(published, 'probe_runtime', lambda *a: SimpleNamespace(ok=True))
    monkeypatch.setattr(published, '_start_web', lambda *a, **k: True)
    monkeypatch.setattr(published, 'prepare_roles', lambda *a: [SimpleNamespace(ok=True, name=name)
                        for name in RUNTIME_ROLES + INFRASTRUCTURE_ROLES])
    for operation in ('rollback', 'add'):
        claim = SimpleNamespace(revision_id='restored', snapshot={'schema_mode': 'unchanged',
            'publication': {'inputs': {'change_operation': operation}, 'result': {'bundle': bundle}}})
        assert published.prepare_published(paths, claim)['schema_verified']


def test_matching_historical_and_current_old_target_still_refuses(restore, monkeypatch):
    import sys
    builder, inputs, _, calls = restore
    inputs['baseline']['target'] = {'python': '3.12'}
    inputs['rollback']['snapshot']['target'] = {'python': '3.12'}
    monkeypatch.setattr(sys, 'version_info', (3, 14, 4))
    with pytest.raises(PreparationError, match='qualified non-root Python target'):
        builder.build(inputs)
    assert calls == []


def test_rehashed_foreign_rollback_project_and_lock_refuse_without_solve(restore):
    builder, inputs, directory, calls = restore
    for name in ('pyproject.toml', 'solve-project.toml'):
        (directory / name).write_text('[project]\nrequires-python="==3.12.*"')
    for name in ('uv.lock', 'solve.lock'):
        (directory / name).write_text('requires-python="==3.12.*"')
    manifest = {p.relative_to(directory).as_posix(): sha256_file(p)
                for p in directory.rglob('*') if p.is_file() and p.name != 'manifest.json'}
    reference = digest(manifest)
    (directory / 'manifest.json').write_text(json.dumps(manifest))
    directory.rename(builder.store / reference)
    bundle = inputs['rollback']['snapshot']['publication']['result']['bundle']
    bundle.update(digest=reference, project_digest=manifest['pyproject.toml'], lock_digest=manifest['uv.lock'])
    with pytest.raises(PreparationError, match='historical bundle identity mismatch'):
        builder.build(inputs)
    assert calls == []
