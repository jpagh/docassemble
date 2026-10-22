from docassemble.base.managed_python import python_requires, python_target
import json
from pathlib import Path

import pytest

from docassemble_executor.bundles import sha256_file
from docassemble_executor.preparation import PreparationError, digest
from docassemble_executor.registry_builder import RegistryBuilder, verify_retained


def test_registry_builder_retained_bytes_and_file_set(tmp_path):
    source = tmp_path / 'source.tar.gz'
    source.write_bytes(b'retained source, not cache')
    manifest = {'source.tar.gz': sha256_file(source)}
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    expected = digest(manifest)
    verify_retained(tmp_path, expected)
    source.write_bytes(b'changed')
    with pytest.raises(PreparationError, match='bytes mismatch'):
        verify_retained(tmp_path, expected)
    source.write_bytes(b'retained source, not cache')
    (tmp_path / 'extra').touch()
    with pytest.raises(PreparationError, match='file set'):
        verify_retained(tmp_path, expected)


def test_registry_builder_rejects_unqualified_policy_before_tooling(tmp_path):
    with pytest.raises(PreparationError, match='unsupported builder'):
        RegistryBuilder(tmp_path, '/does/not/exist').build({'baseline': {
            'target': 'live-target', 'tool': {}, 'config': {}}})
    assert list(tmp_path.iterdir()) == []


def test_registry_builder_manifest_digest_and_symlink(tmp_path):
    (tmp_path / 'manifest.json').write_text('{}')
    with pytest.raises(PreparationError, match='manifest mismatch'):
        verify_retained(tmp_path, '0' * 64)
    (tmp_path / 'link').symlink_to(Path('/etc/passwd'))
    with pytest.raises(PreparationError, match='file set'):
        verify_retained(tmp_path, digest({}))


def test_registry_builder_concurrent_retention_reuses_identical_bundle(tmp_path, monkeypatch):
    import os
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from docassemble_executor.registry_builder import publish_retained
    stages = [tmp_path / 'first', tmp_path / 'second']
    for stage in stages:
        stage.mkdir()
        (stage / 'source').write_bytes(b'identical retained bytes')
        manifest = {'source': sha256_file(stage / 'source')}
        (stage / 'manifest.json').write_text(json.dumps(manifest))
    expected = digest(manifest)
    retained = tmp_path / expected
    barrier = threading.Barrier(2)
    rename = os.rename

    def racing_rename(source, destination):
        # Both publishers observed absence before either can publish.
        assert not destination.exists()
        barrier.wait(timeout=5)
        rename(source, destination)

    monkeypatch.setattr(os, 'rename', racing_rename)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda stage: publish_retained(stage, retained, expected), stages))
    verify_retained(retained, expected)
    assert not any(stage.exists() for stage in stages)


def test_registry_builder_retention_collision_verifies_and_preserves_other_errors(tmp_path, monkeypatch):
    import errno
    import os
    from docassemble_executor.registry_builder import publish_retained
    retained = tmp_path / 'retained'
    retained.mkdir()
    (retained / 'manifest.json').write_text('{}')
    def collide(*args):
        raise OSError(errno.ENOTEMPTY, 'collision')
    monkeypatch.setattr(os, 'rename', collide)
    with pytest.raises(PreparationError, match='manifest mismatch'):
        publish_retained(tmp_path / 'staging', retained, '0' * 64)
    def denied(*args):
        raise OSError(errno.EACCES, 'denied')
    monkeypatch.setattr(os, 'rename', denied)
    with pytest.raises(OSError) as error:
        publish_retained(tmp_path / 'staging', retained, digest({}))
    assert error.value.errno == errno.EACCES


def config_inputs():
    from docassemble.base.managed_config import dependency_snapshot
    snapshot = dependency_snapshot({'python packages': ['Example[extra]>=1,<3; os_name == "posix"']}, environ={})
    return {'change_operation': 'config', 'ownership_effects': {}, 'baseline': {
        'target': python_target(), 'tool': {'uv': '0.12.21'}, 'config': {'index_url': 'https://pypi.org/simple'},
        'core': {'core': {'version': '1.0', 'source_digest': 'a' * 64}}},
        'config_import': {'generation': 1, 'snapshot': snapshot, 'digest': snapshot['digest']},
        'contributions': [{'id': 'interactive', 'declaration': 'Example>=2'},
                          {'id': 'config', 'declaration': snapshot['requirements'][0]['declaration']}]}


@pytest.mark.parametrize('image', [False, True])
def test_registry_builder_config_solves_all_declarations_without_single_requirement(tmp_path, monkeypatch, image):
    import tomllib
    from types import SimpleNamespace
    import docassemble_executor.registry_builder as module
    inputs = config_inputs()
    monkeypatch.setattr(module.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=b'uv 0.12.21'))
    calls = []

    class StopBeforeSolve(Exception):
        pass

    def run(arguments, project, cache, venv):
        calls.append((arguments, tomllib.loads((project / 'pyproject.toml').read_text())))
        raise StopBeforeSolve()

    if image:
        inputs['baseline']['image_baseline'] = {'fixture': True}
        monkeypatch.setattr('docassemble_executor.image_baseline.load_image_registry_wheels', lambda *a: {})
        monkeypatch.setattr('docassemble_executor.image_baseline.load_image_wheels',
                            lambda *args: (inputs['baseline'], {}))
    builder = RegistryBuilder(tmp_path, image_baseline=tmp_path if image else None)
    monkeypatch.setattr(builder, '_run', run)
    with pytest.raises(StopBeforeSolve):
        builder.build(inputs)
    arguments, project = calls[0]
    assert arguments == ['lock', '--no-config']
    assert project['project']['dependencies'] == [row['declaration'] for row in inputs['contributions']] + ['core==1.0']
    assert project['project']['requires-python'] == python_requires()
    assert project['tool']['uv']['index'] == [{'url': 'https://pypi.org/simple', 'default': True}]
    assert 'requirement' not in inputs


@pytest.mark.parametrize('field,value', [('index_url', 'https://SECRET@private.invalid/simple'),
    ('extra_index_urls', ['https://SECRET@private.invalid/simple']), ('trusted_hosts', ['private.invalid'])])
def test_registry_builder_config_rejects_unsupported_policy_before_tooling(tmp_path, field, value):
    from docassemble_executor.preparation import digest
    inputs = config_inputs()
    snapshot = inputs['config_import']['snapshot']
    snapshot['index_policy'][field] = value
    snapshot['digest'] = digest({key: snapshot[key] for key in ('requirements', 'index_policy', 'private_policy')})
    with pytest.raises(PreparationError, match='public PyPI only') as error:
        RegistryBuilder(tmp_path, '/does/not/exist').build(inputs)
    assert 'SECRET' not in str(error.value)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('field', ['private_indexes', 'private_bindings'])
def test_registry_builder_config_rejects_committed_private_policy_before_tooling(tmp_path, field):
    inputs = config_inputs()
    inputs['baseline']['config'][field] = {}
    with pytest.raises(PreparationError, match='private source policy unsupported'):
        RegistryBuilder(tmp_path, '/does/not/exist').build(inputs)
    assert list(tmp_path.iterdir()) == []


def test_registry_builder_config_import_private_policy_requires_operator_credentials(tmp_path):
    from docassemble.base.managed_config import dependency_snapshot
    from docassemble_executor.registry_builder import validate_public_policy
    snapshot = dependency_snapshot({'python packages': ['Example>=2'],
        'private indexes': {'alpha': 'https://example.invalid/private/simple/'},
        'private bindings': {'example': 'alpha'}}, environ={})
    inputs = {'change_operation': 'config', 'ownership_effects': {}, 'baseline': {
        'target': python_target(), 'tool': {'uv': '0.12.21'},
        'config': {'index_url': 'https://pypi.org/simple'}, 'core': {}},
        'config_import': {'generation': 1, 'snapshot': snapshot, 'digest': snapshot['digest']},
        'contributions': [{'id': 'config', 'declaration': 'Example>=2'}]}
    with pytest.raises(PreparationError, match='private indexes require operator credential references'):
        validate_public_policy(inputs)
    assert list(tmp_path.iterdir()) == []


def zip_inputs(tmp_path, *, version='1.0', wheel=True):
    import importlib.util
    from docassemble.base.managed_artifacts import distribution_identity, file_digest, retain_artifact, open_regular
    spec = importlib.util.spec_from_file_location('zip_fixture', Path(__file__).parents[2] / 'preparation' / 'zip_fixture.py')
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    upload = fixture.distribution(tmp_path / 'misleading.zip', version=version, wheel=wheel)
    root = tmp_path / 'artifacts'
    root.mkdir(mode=0o700)
    with open_regular(upload) as source:
        identity = dict(distribution_identity(source), digest=file_digest(source))
        retain_artifact(root, identity['digest'], source)
    upload.unlink()
    return {'artifact': identity, 'requirement': {'declaration': 'zip-example>=1'},
            'contributions': [], 'ownership_effects': {}, 'baseline': {
                'target': python_target(), 'tool': {'uv': '0.12.21'},
                'config': {'index_url': 'https://pypi.org/simple'}, 'core': {}}}, root


@pytest.mark.parametrize('wheel', [True, False])
def test_registry_builder_retained_zip_path_binding_and_complete_declarations(tmp_path, monkeypatch, wheel):
    import tomllib
    from types import SimpleNamespace
    import docassemble_executor.registry_builder as module
    inputs, root = zip_inputs(tmp_path, wheel=wheel)
    inputs['contributions'] = [{'id': 'config', 'declaration': 'zip-example<3'},
                               {'id': 'old-zip', 'declaration': 'zip-example==0.5', 'artifact_ref': 'a' * 64}]
    inputs['replace_artifact_contributions'] = ['old-zip']
    monkeypatch.setattr(module.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=b'uv 0.12.21'))
    calls = []
    class StopBeforeSolve(Exception):
        pass
    def run(arguments, project, cache, venv):
        config = tomllib.loads((project / 'pyproject.toml').read_text())
        source = config['tool']['uv']['sources']['zip-example']['path']
        assert (project / source).read_bytes() == (root / inputs['artifact']['digest']).read_bytes()
        calls.append(config)
        raise StopBeforeSolve()
    builder = RegistryBuilder(tmp_path / 'bundles', artifact_root=root)
    monkeypatch.setattr(builder, '_run', run)
    with pytest.raises(StopBeforeSolve):
        builder.build(inputs)
    assert calls[0]['project']['dependencies'] == ['zip-example<3', 'zip-example>=1']
    assert calls[0]['tool']['uv']['sources'] == {'zip-example': {'path': 'artifacts/' + inputs['artifact']['filename']}}


@pytest.mark.parametrize('failure', ['unset', 'corrupt', 'symlink', 'identity', 'core', 'conflict', 'traversal'])
def test_registry_builder_retained_zip_source_fails_closed(tmp_path, failure):
    from copy import deepcopy
    from docassemble_executor.registry_builder import retained_artifacts
    inputs, root = zip_inputs(tmp_path)
    path = root / inputs['artifact']['digest']
    if failure == 'unset':
        root = None
    elif failure == 'corrupt':
        path.write_bytes(b'changed')
    elif failure == 'symlink':
        path.unlink()
        path.symlink_to('/etc/passwd')
    elif failure == 'identity':
        inputs['artifact']['version'] = '99'
    elif failure == 'core':
        inputs['baseline']['core']['zip-example'] = {'version': '1.0', 'source_digest': 'a' * 64}
    elif failure == 'conflict':
        second = tmp_path / 'second'
        second.mkdir()
        different, second_root = zip_inputs(second, version='2.0')
        reference = different['artifact']['digest']
        (root / reference).write_bytes((second_root / reference).read_bytes())
        (root / reference).chmod(0o600)
        inputs['contributions'] = [{'id': 'another', 'declaration': 'zip-example>=1', 'artifact_ref': reference}]
    else:
        inputs['artifact']['digest'] = '../outside'
    with pytest.raises(PreparationError):
        retained_artifacts(deepcopy(inputs), root)


@pytest.mark.parametrize('installed', [None, {'zip-example': '1.0'}])
@pytest.mark.parametrize('declaration,rejected', [
    ('Zip_Example[feature]>=1,<2; python_version >= "3.12"', False),
    ('zip-example[feature]>=2; sys_platform == "never-a-real-platform"', False),
    ('zip-example[feature]>=2; python_version >= "3.12"', True),
    ('zip-example<1', True),
])
def test_registry_builder_local_metadata_version_bounds_markers_and_extras(tmp_path, installed, declaration, rejected):
    from docassemble_executor.registry_builder import retained_artifacts, validate_artifact_requirements
    inputs, root = zip_inputs(tmp_path)
    artifacts = retained_artifacts(inputs, root)
    requirements = ['zip-example==1.0', declaration]
    before = list(requirements)
    if rejected:
        with pytest.raises(PreparationError, match='metadata does not satisfy declarations'):
            validate_artifact_requirements(requirements, artifacts, installed)
    else:
        validate_artifact_requirements(requirements, artifacts, installed)
    assert requirements == before


@pytest.mark.parametrize('installed', [{}, {'zip-example': '2.0'}])
def test_registry_builder_postsolve_metadata_must_match_retained_identity(tmp_path, installed):
    from docassemble_executor.registry_builder import retained_artifacts, validate_artifact_requirements
    inputs, root = zip_inputs(tmp_path)
    with pytest.raises(PreparationError, match='metadata does not satisfy declarations'):
        validate_artifact_requirements(['zip-example[feature]>=1,<3'], retained_artifacts(inputs, root), installed)


def test_registry_builder_rejects_local_config_version_conflict_before_solver(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    import docassemble_executor.registry_builder as module
    inputs, root = zip_inputs(tmp_path)
    inputs['contributions'] = [{'id': 'config', 'origin': 'config', 'declaration': 'zip-example[feature]<1'}]
    monkeypatch.setattr(module.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=b'uv 0.12.21'))
    builder = RegistryBuilder(tmp_path / 'bundles', artifact_root=root)
    builder._run = Mock()
    with pytest.raises(PreparationError, match='metadata does not satisfy declarations'):
        builder.build(inputs)
    builder._run.assert_not_called()
    assert not (tmp_path / 'bundles').exists()


def test_registry_builder_rejects_postsolve_installed_metadata_even_if_uv_succeeds(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import docassemble_executor.registry_builder as module
    inputs, root = zip_inputs(tmp_path)
    inputs['baseline'].update(desired_inventory={}, runtime={'kind': 'normal', 'inventory': {}})
    monkeypatch.setattr(module.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=b'uv 0.12.21'))
    monkeypatch.setattr(module, 'inspect_environment', lambda venv: {'zip-example': '2.0'})
    calls = []
    def run(arguments, project, cache, venv):
        calls.append(arguments[0])
        if arguments[0] == 'lock':
            (project / 'uv.lock').write_text('requires-python = ' + json.dumps(python_requires()) + '\n[[package]]\nname="zip-example"\nversion="1.0"\nsource={path="artifacts/' + inputs['artifact']['filename'] + '"}\n')
    builder = RegistryBuilder(tmp_path / 'bundles', artifact_root=root)
    monkeypatch.setattr(builder, '_run', run)
    with pytest.raises(PreparationError, match='metadata does not satisfy declarations'):
        builder.build(inputs)
    assert calls == ['lock', 'sync']


def _bundle(digest, inventory):
    return {'digest': digest, 'project_digest': 'b' * 64, 'lock_digest': 'c' * 64,
            'inventory': inventory, 'artifacts': []}


def test_upgrade_both_bundles_materialize_wheel_payload(tmp_path, monkeypatch):
    # 14b: the registry-solved upgrade core and combined bundles must carry the
    # wheel-payload material the qualified gated apply verifies, so a published
    # upgrade can be installed without weakening the gate.
    from types import SimpleNamespace
    import docassemble_executor.registry_builder as module
    from docassemble.base.managed_release import BASE, DEMO, WEBAPP, upgrade_risk_disclosure
    core_inventory = {BASE: {'version': '2.0.0', 'source_digest': '1' * 64},
                      WEBAPP: {'version': '2.0.0', 'source_digest': '2' * 64},
                      DEMO: {'version': '2.0.0', 'source_digest': '3' * 64},
                      'gunicorn': {'version': '23.0.0', 'source_digest': '5' * 64}}
    combined = dict(core_inventory, separate={'version': '1.5', 'source_digest': '4' * 64})
    calls = []

    class Catalog:
        def versions(self, name):
            assert name == WEBAPP
            return ['2.0.0']

        def requires_dist(self, name, version):
            return ['docassemble-base==2.0.0', 'docassemble-webapp==2.0.0', 'docassemble-demo==2.0.0']

    def fake_solve(inputs, **kwargs):
        calls.append((kwargs['operation'], kwargs.get('materialize')))
        core = kwargs['operation'] == 'upgrade-core'
        inventory = core_inventory if core else combined
        before = inputs['baseline']['desired_inventory']
        diff = {name: {'before': before.get(name), 'after': inventory.get(name)}
                for name in sorted(set(before) | set(inventory)) if before.get(name) != inventory.get(name)}
        validation = ({'core_unchanged': True, 'requirements_satisfied': True,
                       'durable_materialization_verified': True} if core else
                      {'core_matches_release': True, 'requirements_satisfied': True,
                       'durable_materialization_verified': True})
        return {'bundle': _bundle('a' * 64 if core else 'd' * 64, inventory),
                'desired_diff': diff, 'runtime_diff': diff, 'warnings': [],
                'validation': validation, 'ownership_effects': {}}

    monkeypatch.setattr(module, 'parse_release_selection', lambda value: SimpleNamespace(mode='exact', version='2.0.0'))
    monkeypatch.setattr(module, 'select_release', lambda versions, selection: '2.0.0')
    monkeypatch.setattr(module, 'coherent_core', lambda selected, requires: {BASE: '2.0.0', WEBAPP: '2.0.0', DEMO: '2.0.0'})
    monkeypatch.setattr(module, 'core_requirements', lambda coherent: [name + '==' + version for name, version in coherent.items()])
    monkeypatch.setattr(module, 'reject_downgrade', lambda coherent, core: None)
    builder = RegistryBuilder(tmp_path, release_catalog=Catalog())
    monkeypatch.setattr('docassemble_executor.selected_baseline.selected_wheels',
                        lambda store, selected: {name: tmp_path / (name + '.whl') for name in core_inventory})
    monkeypatch.setattr(builder, '_solve', fake_solve)
    inputs = {'baseline': {'target': python_target(), 'tool': {'uv': '0.12.21'},
                           'config': {'index_url': 'https://pypi.org/simple'},
                           'core': {'docassemble-webapp': {'version': '1.0.0', 'source_digest': '9' * 64}},
                           'desired_inventory': {}, 'runtime': {'kind': 'normal', 'inventory': {}}},
              'change_operation': 'upgrade', 'ownership_effects': {}, 'contributions': [],
              'release_selection': {'mode': 'exact', 'version': '2.0.0'}}
    result = builder._build_upgrade(inputs, inputs['baseline'], [], {}, {}, {})
    assert calls == [('upgrade-core', True), ('upgrade', True)]
    assert result['core_bundle']['digest'] == 'a' * 64
    assert result['release']['webapp'] == '2.0.0' and result['release']['core_bundle'] == 'a' * 64
    assert result['risk_disclosure'] == upgrade_risk_disclosure()


@pytest.mark.parametrize('operation,seed', [('update', 'required'), ('upgrade', 'preferred')])
@pytest.mark.parametrize('fault', ['old-lock', 'old-tool'])
def test_parent_solve_target_refused_before_any_uv_lock(tmp_path, monkeypatch, operation, seed, fault):
    store = tmp_path / 'retained'
    parent = store / 'staging'
    parent.mkdir(parents=True)
    requirement = '==3.12.*' if fault == 'old-lock' else python_requires()
    (parent / 'solve-project.toml').write_text('[project]\nrequires-python=' + json.dumps(requirement))
    (parent / 'solve.lock').write_text('requires-python=' + json.dumps(requirement))
    (parent / 'tool.json').write_text(json.dumps({'uv': '0.12.21',
        'target': {'python': '3.12'} if fault == 'old-tool' else python_target()}))
    manifest = {p.name: sha256_file(p) for p in parent.iterdir()}
    reference = digest(manifest)
    (parent / 'manifest.json').write_text(json.dumps(manifest))
    parent.rename(store / reference)
    inputs = {'change_operation': operation, 'contributions': [], 'baseline': {'publication': {'result': {'bundle': {'digest': reference}}}}}
    builder = RegistryBuilder(store)
    monkeypatch.setattr(builder, '_run', lambda *a: pytest.fail('old parent must not reach uv'))
    with pytest.raises(PreparationError, match='unsupported builder target'):
        builder._solve(inputs, operation=operation, core={}, requirements=[], image_wheels={},
            local_artifacts={}, direct_sources={}, authorizations={}, seed_parent=seed)


@pytest.mark.parametrize('target', [{'python': '3.12'}, {'python': '3.13'}, {'python': '3.14.4'},
    {'python': '>=3.12'}, {'python': ['3.14']}, {'python': '3.14', 'extra': 'key'}, {}])
def test_public_policy_foreign_target_refused_before_network_or_uv(tmp_path, monkeypatch, target):
    import sys
    monkeypatch.setattr(sys, 'version_info', (3, 14, 4))
    inputs = config_inputs()
    inputs['baseline']['target'] = target
    with pytest.raises(PreparationError, match='unsupported builder target'):
        RegistryBuilder(tmp_path, '/does/not/exist').build(inputs)
    assert list(tmp_path.iterdir()) == []


def reuse_lock(artifact, *, source=None, marker=None):
    row = dict(artifact.origin)
    if source is not None:
        row['source'] = source
    root = {'name': 'managed-candidate', 'version': '0', 'source': {'virtual': '.'},
            'dependencies': [{'name': 'numpy', **({'marker': marker} if marker else {})}]}
    return {'package': [root, row]}


@pytest.mark.parametrize('fault', ['none', 'hash', 'version', 'private', 'git', 'url', 'upload', 'fixed', 'inactive'])
def test_registry_builder_image_registry_wheel_source_policy_is_not_broadened(tmp_path, fault):
    from registry_wheel_fixture import reusable
    artifact = reusable(tmp_path / 'image')
    lock = reuse_lock(artifact)
    if fault == 'hash':
        lock['package'][1]['sdist'] = dict(artifact.origin['sdist'], hash='sha256:' + 'b' * 64)
    elif fault == 'version':
        lock['package'][1]['version'] = '9.0'
    elif fault in ('private', 'git', 'url', 'upload'):
        lock['package'][1]['source'] = {'private': {'registry': 'https://private.invalid/'},
            'git': {'git': 'https://example.invalid/repo'}, 'url': {'url': artifact.origin['sdist']['url']},
            'upload': {'path': 'artifacts/upload.whl'}}[fault]
    elif fault == 'fixed':
        lock['package'][1]['source'] = {'path': 'wheels/' + artifact.path.name}
    elif fault == 'inactive':
        lock['package'][0]['dependencies'][0]['marker'] = 'sys_platform == "never"'
    project = tmp_path / 'project'
    (project / 'wheels').mkdir(parents=True)
    before = json.dumps(lock, sort_keys=True)
    result = RegistryBuilder(tmp_path / 'store')._reuse_registry_wheels(lock, {'numpy': artifact}, project)
    assert json.dumps(lock, sort_keys=True) == before
    assert set(result) == ({'numpy'} if fault == 'none' else set())
    assert list((project / 'wheels').iterdir()) == ([project / 'wheels' / artifact.path.name] if fault == 'none' else [])


@pytest.mark.parametrize('fault', ['before-copy', 'after-copy'])
def test_registry_builder_image_registry_wheel_copy_hash_mismatch_stops(tmp_path, monkeypatch, fault):
    import docassemble_executor.registry_builder as module
    from registry_wheel_fixture import reusable
    artifact = reusable(tmp_path / 'image')
    project = tmp_path / 'project'
    (project / 'wheels').mkdir(parents=True)
    if fault == 'before-copy':
        artifact.path.write_bytes(b'tampered')
    else:
        def copy(source, destination):
            destination.write_bytes(b'tampered copy')
        monkeypatch.setattr(module.shutil, 'copyfile', copy)
    with pytest.raises(PreparationError, match='invalid image registry wheel'):
        RegistryBuilder(tmp_path / 'store')._reuse_registry_wheels(reuse_lock(artifact), {'numpy': artifact}, project)


@pytest.mark.parametrize('foreign', [False, True])
def test_registry_builder_image_registry_wheel_installs_only_into_disposable_venv(tmp_path, monkeypatch, foreign):
    import os
    import sys
    from types import SimpleNamespace
    import docassemble_executor.registry_builder as module
    project = tmp_path / 'project'
    project.mkdir()
    venv = tmp_path / 'disposable-venv'
    (venv / 'bin').mkdir(parents=True)
    (venv / 'bin/python').symlink_to('/foreign/python' if foreign else sys.executable)
    monkeypatch.setenv('UV_INDEX_URL', 'SECRET')
    monkeypatch.setenv('PIP_INDEX_URL', 'SECRET')
    monkeypatch.setenv('UV_PYTHON', '/executor-python')
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        assert command.count('--python') == 1 and command[-1] == str(venv / 'bin/python')
        assert kwargs['timeout'] == 600
        assert kwargs['env']['UV_PYTHON_DOWNLOADS'] == 'never'
        assert not any(key.startswith('PIP_') or 'INDEX' in key for key in kwargs['env'])
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(module.subprocess, 'run', run)
    builder = RegistryBuilder(tmp_path / 'store')
    arguments = ['pip', 'install', '--no-deps', '--no-index', '--no-build', '--offline', '--no-config', 'wheel.whl']
    if foreign:
        with pytest.raises(PreparationError, match='interpreter'):
            builder._run(arguments, project, tmp_path / 'cache', venv, install_into_venv=True)
        assert not commands
    else:
        builder._run(arguments, project, tmp_path / 'cache', venv, install_into_venv=True)
        assert len(commands) == 1
        assert os.path.samefile(venv / 'bin/python', sys.executable)


def solve_reuse_fixture(tmp_path, monkeypatch, *, installed='2.2.6', reuse=True, intent=None, path_bound=False, payload_ok=True,
                        competing_wheel=False):
    import io
    import tomli_w
    import sys
    from types import SimpleNamespace
    from registry_wheel_fixture import reusable
    import docassemble_executor.registry_builder as module
    artifact = reusable(tmp_path / 'image')
    lock = reuse_lock(artifact)
    if competing_wheel:
        lock['package'][1]['wheels'] = [{'url': 'https://files.pythonhosted.org/published/numpy-2.2.6-py3-none-any.whl',
                                      'hash': 'sha256:' + artifact.wheel_hash}]
    # The fixture declares parent, which transitively requires numpy; a caller's
    # numpy path intent is not necessarily the source selected for that row.
    if intent:
        lock['package'][0]['dependencies'] = [{'name': 'parent'}]
        lock['package'].append({'name': 'parent', 'version': '1', 'source': {'registry': module.INDEX},
                                'dependencies': [{'name': 'numpy'}]})
        # Keep the source-retention fixture focused on numpy; the parent supplies
        # reachability, not an installed distribution in this simulated target.
    if path_bound:
        lock['package'][1]['source'] = {'path': ('wheels/' if intent == 'image' else 'artifacts/') + artifact.path.name}
    lock['requires-python'] = python_requires()
    calls = []
    def run(arguments, project, cache, venv, **kwargs):
        calls.append(list(arguments))
        cache.mkdir(exist_ok=True)
        (venv / 'bin').mkdir(parents=True, exist_ok=True)
        if not (venv / 'bin/python').exists():
            (venv / 'bin/python').symlink_to(sys.executable)
        if arguments[0] == 'lock':
            (project / 'uv.lock').write_text(tomli_w.dumps(lock))
        elif arguments[0] == 'sync' and '--no-build' not in arguments:
            expected_sync = ['sync', '--frozen', '--no-config']
            if not path_bound:
                expected_sync.extend(['--no-install-package', 'numpy'])
            assert arguments == expected_sync, 'sdist compiler tripwire'
            if intent:
                import tomllib
                config = tomllib.loads((project / 'pyproject.toml').read_text())
                assert config['tool']['uv']['sources']['numpy']['path'] == (
                    'wheels/' if intent == 'image' else 'artifacts/') + artifact.path.name
                assert lock['package'][1]['source'] == ({'path': ('wheels/' if intent == 'image' else 'artifacts/') + artifact.path.name}
                    if path_bound else {'registry': module.INDEX})
        elif arguments[:2] == ['pip', 'install']:
            assert kwargs == {'install_into_venv': True}
            assert arguments[2:7] == ['--no-deps', '--no-index', '--no-build', '--offline', '--no-config']
        elif arguments[0] == 'build':
            pytest.fail('independent sdist compiler tripwire')
        elif arguments[0] == 'sync':
            assert arguments == ['sync', '--frozen', '--no-build', '--offline', '--no-config']
    monkeypatch.setattr(module, 'inspect_environment', lambda venv: {} if installed is None else {'numpy': installed})
    def verify(venv, expected, material):
        from docassemble_executor.inventory import wheel_payload_material
        assert expected == {'numpy': '2.2.6'}
        assert material == wheel_payload_material(venv.parent / 'project' / 'wheels')
        return SimpleNamespace(ok=payload_ok)
    monkeypatch.setattr(module, 'verify_environment', verify)
    class Source(io.BytesIO):
        def geturl(self):
            return artifact.origin['sdist']['url']
    def original_source_only(url, **kwargs):
        assert url == artifact.origin['sdist']['url'], 'reuse must never fetch a published wheel'
        return Source(b'original sdist')
    monkeypatch.setattr(module, '_open_registry_artifact', original_source_only)
    builder = RegistryBuilder(tmp_path / 'store')
    monkeypatch.setattr(builder, '_run', run)
    inputs = {'baseline': {'target': python_target(), 'desired_inventory': {}, 'runtime': {'kind': 'normal', 'inventory': {}}},
              'ownership_effects': {}, 'contributions': [], 'requirement': {'declaration': 'numpy==2.2.6'}}
    requirements = ['parent==1'] if intent else ['numpy==2.2.6']
    image_wheels = {'numpy': artifact.path} if intent == 'image' else {}
    local_artifacts = {'numpy': ({'version': '2.2.6', 'filename': artifact.path.name,
        'digest': artifact.wheel_hash}, artifact.path)} if intent == 'artifact' else {}
    result = builder._solve(inputs, operation='add', core={}, requirements=requirements, image_wheels=image_wheels,
        local_artifacts=local_artifacts, direct_sources={}, authorizations={}, seed_parent=None, validate_result=False,
        materialize=True, image_registry_wheels={'numpy': artifact} if reuse else {})
    return builder, result, artifact, calls, tomli_w.dumps(lock)


def test_registry_builder_image_registry_wheel_reuse_skips_both_sdist_build_sites(tmp_path, monkeypatch):
    builder, result, artifact, calls, original_lock = solve_reuse_fixture(tmp_path, monkeypatch)
    assert [call[0] for call in calls] == ['lock', 'sync', 'pip', 'lock', 'sync']
    retained = builder.store / result['bundle']['digest']
    assert (retained / 'solve.lock').read_text() == original_lock
    assert result['bundle']['inventory']['numpy']['source_digest'] == artifact.origin['sdist']['hash'][7:]
    record = json.loads((retained / 'image-registry-artifacts.json').read_text())['numpy']
    assert record['source'] == artifact.origin and record['wheel_hash'] == artifact.wheel_hash
    assert record['image_baseline'] == artifact.image_identity
    assert sha256_file(retained / 'wheels' / artifact.path.name) in result['bundle']['artifacts']
    verify_retained(retained, result['bundle']['digest'])
    (retained / 'image-registry-artifacts.json').write_text('{}')
    with pytest.raises(PreparationError, match='bytes mismatch'):
        verify_retained(retained, result['bundle']['digest'])


def test_registry_builder_reuse_tripwire_detects_removed_substitution(tmp_path, monkeypatch):
    with pytest.raises(AssertionError, match='sdist compiler tripwire'):
        solve_reuse_fixture(tmp_path, monkeypatch, reuse=False)


@pytest.mark.parametrize('installed', [None, '9.0'])
def test_registry_builder_substituted_inventory_cannot_be_filtered_away(tmp_path, monkeypatch, installed):
    with pytest.raises(PreparationError, match='substituted registry inventory mismatch'):
        solve_reuse_fixture(tmp_path, monkeypatch, installed=installed)
    assert not list((tmp_path / 'store').iterdir())


@pytest.mark.parametrize('operation', ['add', 'update', 'remove'])
@pytest.mark.parametrize('candidate_version', ['0.5', '9.0'])
def test_registry_builder_image_registry_wheels_do_not_change_resolved_versions(tmp_path, monkeypatch, operation, candidate_version):
    """Real resolver control: reuse candidates never participate in selection."""
    import functools
    import http.server
    import os
    import shutil
    import subprocess
    import sys
    import threading
    import tomllib
    from registry_wheel_fixture import reusable, wheel
    import docassemble_executor.registry_builder as module
    binary = shutil.which(os.environ.get('UV_BINARY', 'uv'))
    assert subprocess.check_output([binary, '--version'], text=True).split()[:2] == ['uv', '0.12.21']
    registry = tmp_path / 'registry'
    published = wheel(registry / 'files', version='1.0')
    simple = registry / 'simple/numpy'
    simple.mkdir(parents=True)
    (simple / 'index.html').write_text('<a href="../../files/' + published.name + '">' + published.name + '</a>')
    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(registry)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    index = 'http://127.0.0.1:' + str(server.server_port) + '/simple/'
    monkeypatch.setattr(module, 'INDEX', index)
    captured = []
    class Solved(Exception):
        pass
    try:
        for reuse in (False, True):
            builder = RegistryBuilder(tmp_path / ('reused' if reuse else 'control'), binary)
            baseline = {'target': python_target(), 'desired_inventory': {}, 'runtime': {'kind': 'normal', 'inventory': {}}}
            if operation != 'add':
                parent = builder.store / 'parent'
                parent.mkdir(parents=True)
                (parent / 'solve-project.toml').write_text('[project]\nname="managed-candidate"\nversion="0"\nrequires-python=' + json.dumps(python_requires()) + '\ndependencies=["numpy>=1"]\n[tool.uv]\npackage=false\n')
                shutil.copyfile(parent / 'solve-project.toml', parent / 'pyproject.toml')
                (parent / 'tool.json').write_text(json.dumps({'uv': '0.12.21', 'target': python_target()}))
                subprocess.run([binary, 'lock', '--no-config', '--default-index', index, '--python', sys.executable],
                    cwd=parent, env={'PATH': os.environ['PATH'], 'UV_PYTHON_DOWNLOADS': 'never',
                        'UV_CACHE_DIR': str(tmp_path / 'parent-cache')}, check=True, capture_output=True, timeout=600)
                (parent / 'uv.lock').rename(parent / 'solve.lock')
                (parent / 'pyproject.toml').unlink()
                manifest = {p.name: sha256_file(p) for p in parent.iterdir()}
                reference = digest(manifest)
                (parent / 'manifest.json').write_text(json.dumps(manifest))
                parent.rename(builder.store / reference)
                baseline['publication'] = {'result': {'bundle': {'digest': reference}}}
            original_run = builder._run
            def run(arguments, project, cache, venv, **kwargs):
                if arguments[0] == 'sync':
                    captured.append(((project / 'solve.lock').read_bytes(), (project / 'solve-project.toml').read_bytes(),
                                     tomllib.loads((project / 'solve.lock').read_text())))
                    assert '--no-install-package' not in arguments
                    raise Solved()
                original_run([*arguments, '--default-index', index], project, cache, venv, **kwargs)
            monkeypatch.setattr(builder, '_run', run)
            candidates = { 'numpy': reusable(tmp_path / ('low' if not reuse else 'high'), version=candidate_version)}
            inputs = {'baseline': baseline, 'change_operation': operation, 'contributions': [],
                      'requirement': {'declaration': 'numpy>=1', 'normalized_name': 'numpy'}}
            with pytest.raises(Solved):
                builder._solve(inputs, operation=operation, core={}, requirements=['numpy>=1'], image_wheels={},
                    local_artifacts={}, direct_sources={}, authorizations={}, seed_parent='required' if operation != 'add' else None,
                    image_registry_wheels=candidates if reuse else {})
        assert captured[0][:2] == captured[1][:2]
        packages = captured[1][2]['package']
        assert {row['name']: row['version'] for row in packages} == {'managed-candidate': '0', 'numpy': '1.0'}
        assert next(row['source'] for row in packages if row['name'] == 'numpy') == {'registry': index}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_registry_builder_image_registry_wheel_cold_materialization_is_self_contained(tmp_path, monkeypatch):
    import os
    import shutil
    import subprocess
    import sys
    import tomli_w
    from registry_wheel_fixture import reusable
    from docassemble_executor.inventory import inspect_environment, verify_environment, wheel_payload_material
    binary = shutil.which(os.environ.get('UV_BINARY', 'uv'))
    assert subprocess.check_output([binary, '--version'], text=True).split()[:2] == ['uv', '0.12.21']
    protected_before = inspect_environment(Path(sys.prefix))
    artifact = reusable(tmp_path / 'image')
    project = tmp_path / 'project'
    (project / 'wheels').mkdir(parents=True)
    builder = RegistryBuilder(tmp_path / 'store', binary)
    copied = project / 'wheels' / artifact.path.name
    shutil.copyfile(artifact.path, copied)
    venv = tmp_path / 'disposable-venv'
    subprocess.run([builder.uv_binary, 'venv', '--python', sys.executable, str(venv)], check=True, capture_output=True, timeout=600)
    builder._run(['pip', 'install', '--no-deps', '--no-index', '--no-build', '--offline', '--no-config', str(copied)],
        project, tmp_path / 'cache', venv, install_into_venv=True)
    assert inspect_environment(venv) == {'numpy': '2.2.6'}
    assert inspect_environment(Path(sys.prefix)) == protected_before
    config = {'project': {'name': 'cold-reuse', 'version': '0', 'requires-python': python_requires(), 'dependencies': ['numpy==2.2.6']},
              'tool': {'uv': {'package': False, 'sources': {'numpy': {'path': 'wheels/' + artifact.path.name}}}}}
    (project / 'pyproject.toml').write_text(tomli_w.dumps(config))
    builder._run(['lock', '--offline', '--no-config'], project, tmp_path / 'cache', venv)
    shutil.rmtree(artifact.path.parent)
    shutil.rmtree(venv)
    shutil.rmtree(tmp_path / 'cache')
    builder._run(['sync', '--frozen', '--no-build', '--offline', '--no-config'], project, tmp_path / 'cache', venv)
    assert verify_environment(venv, {'numpy': '2.2.6'}, wheel_payload_material(project / 'wheels')).ok
    copied.unlink()
    shutil.rmtree(venv)
    shutil.rmtree(tmp_path / 'cache')
    with pytest.raises(PreparationError):
        builder._run(['sync', '--frozen', '--no-build', '--offline', '--no-config'], project, tmp_path / 'cache', venv)


@pytest.mark.parametrize('case', ['well-formed', 'parser-defect', 'missing', 'duplicate'])
def test_registry_builder_reuse_rejects_malformed_metadata_before_copy(tmp_path, case):
    from registry_wheel_fixture import METADATA_CASES, reusable, wheel
    from docassemble_executor.image_baseline import ImageRegistryWheel
    artifact = reusable(tmp_path / 'image')
    path = wheel(artifact.path.parent, metadata_bytes=METADATA_CASES[case])
    artifact = ImageRegistryWheel(path, sha256_file(path), artifact.origin, artifact.image_identity)
    project = tmp_path / 'project'
    (project / 'wheels').mkdir(parents=True)
    builder = RegistryBuilder(tmp_path / 'store')
    if case == 'well-formed':
        assert builder._reuse_registry_wheels(reuse_lock(artifact), {'numpy': artifact}, project) == {'numpy': artifact}
        assert (project / 'wheels' / path.name).read_bytes() == path.read_bytes()
    else:
        with pytest.raises(PreparationError, match='invalid image registry wheel'):
            builder._reuse_registry_wheels(reuse_lock(artifact), {'numpy': artifact}, project)
        assert not list((project / 'wheels').iterdir())


@pytest.mark.parametrize('intent', ['image', 'artifact'])
def test_registry_builder_transitive_registry_row_reuses_despite_artifact_intent(tmp_path, monkeypatch, intent):
    builder, result, artifact, calls, original_lock = solve_reuse_fixture(tmp_path, monkeypatch, intent=intent)
    assert calls[1] == ['sync', '--frozen', '--no-config', '--no-install-package', 'numpy']
    assert [call[0] for call in calls] == ['lock', 'sync', 'pip', 'lock', 'sync']
    assert result['bundle']['inventory']['numpy'] == {'version': '2.2.6',
        'source_digest': artifact.origin['sdist']['hash'][7:]}
    retained = builder.store / result['bundle']['digest']
    assert (retained / 'solve.lock').read_text() == original_lock
    assert (retained / 'sources/numpy-2.2.6.tar.gz').read_bytes() == b'original sdist'
    assert (retained / 'installed-material.json').exists()
    import tomllib
    apply = tomllib.loads((retained / 'pyproject.toml').read_text())
    assert apply['tool']['uv']['sources']['numpy'] == {'path': 'wheels/' + artifact.path.name}
    verify_retained(retained, result['bundle']['digest'])


def test_registry_builder_actual_path_bound_artifact_skips_registry_reuse(tmp_path, monkeypatch):
    builder, result, artifact, calls, _ = solve_reuse_fixture(tmp_path, monkeypatch, intent='artifact', path_bound=True)
    assert calls[1] == ['sync', '--frozen', '--no-config']
    assert not any(call[:2] == ['pip', 'install'] for call in calls)
    assert result['bundle']['inventory']['numpy'] == {'version': '2.2.6', 'source_digest': artifact.wheel_hash}
    retained = builder.store / result['bundle']['digest']
    assert not (retained / 'image-registry-artifacts.json').exists()
    assert (retained / 'installed-material.json').exists()
    verify_retained(retained, result['bundle']['digest'])


@pytest.mark.parametrize('different', [False, True], ids=['identical', 'conflicting'])
def test_registry_builder_precopied_reuse_wheel_requires_attested_bytes(tmp_path, different):
    import shutil
    from registry_wheel_fixture import reusable, wheel
    artifact = reusable(tmp_path / 'image')
    project = tmp_path / 'project'
    (project / 'wheels').mkdir(parents=True)
    destination = project / 'wheels' / artifact.path.name
    shutil.copyfile(artifact.path, destination)
    if different:
        wheel(destination.parent, metadata_bytes=b'Metadata-Version: 2.1\nName: numpy\nVersion: 2.2.6\nSummary: different bytes\n')
        before = destination.read_bytes()
        with pytest.raises(PreparationError, match='^conflicting image registry artifact$'):
            RegistryBuilder(tmp_path / 'store')._reuse_registry_wheels(reuse_lock(artifact), {'numpy': artifact}, project)
        assert destination.read_bytes() == before
    else:
        result = RegistryBuilder(tmp_path / 'store')._reuse_registry_wheels(reuse_lock(artifact), {'numpy': artifact}, project)
        assert result == {'numpy': artifact}
        assert sha256_file(destination) == artifact.wheel_hash


@pytest.mark.parametrize('intent', ['image', 'artifact'])
@pytest.mark.parametrize('installed', [None, '9.0'])
def test_registry_builder_transitive_reuse_installed_version_must_match_lock(tmp_path, monkeypatch, intent, installed):
    with pytest.raises(PreparationError, match='^substituted registry inventory mismatch$'):
        solve_reuse_fixture(tmp_path, monkeypatch, intent=intent, installed=installed)
    assert not list((tmp_path / 'store').iterdir())


def test_registry_builder_actual_image_row_without_core_identity_is_classified(tmp_path, monkeypatch):
    with pytest.raises(PreparationError, match='^exact core source baseline changed$'):
        solve_reuse_fixture(tmp_path, monkeypatch, intent='image', path_bound=True)
    assert not list((tmp_path / 'store').iterdir())


@pytest.mark.parametrize('path_bound', [False, True], ids=['substituted-registry', 'provided-artifact'])
def test_registry_builder_row_source_retention_keeps_payload_failure_fatal(tmp_path, monkeypatch, path_bound):
    with pytest.raises(PreparationError, match='^cold-cache materialization mismatch$'):
        solve_reuse_fixture(tmp_path, monkeypatch, intent='artifact', path_bound=path_bound, payload_ok=False)
    assert not list((tmp_path / 'store').iterdir())


def published_wheel_solve(tmp_path, monkeypatch, *, tag='py3-none-any', compatible=True, fault=None,
                          opener_handler=None, observed_calls=None):
    import io
    import os
    import shutil
    import subprocess
    import tomli_w
    from registry_wheel_fixture import origin, wheel
    import docassemble_executor.registry_builder as module
    binary = shutil.which(os.environ.get('UV_BINARY', 'uv'))
    assert subprocess.check_output([binary, '--version'], text=True).split()[:2] == ['uv', '0.12.21']
    published = wheel(tmp_path / 'published', name='bcrypt', version='5.0.0', tag=tag)
    identity = origin('bcrypt', '5.0.0')
    wheel_entry = {'url': 'https://files.pythonhosted.org/' + published.name,
                   'hash': 'sha256:' + sha256_file(published)}
    if fault == 'missing-hash':
        wheel_entry.pop('hash')
    elif fault == 'malformed-hash':
        wheel_entry['hash'] = 'sha256:invalid'
    elif fault == 'wheel-hash':
        wheel_entry['hash'] = 'sha256:' + 'b' * 64
    elif fault in ('url', 'credentials', 'query'):
        wheel_entry['url'] = {'url': 'http://files.pythonhosted.org/',
            'credentials': 'https://secret@files.pythonhosted.org/',
            'query': 'https://files.pythonhosted.org/'}[fault] + published.name + ('?secret=1' if fault == 'query' else '')
    locked = {'version': 1, 'revision': 3, 'requires-python': python_requires(), 'package': [
        {'name': 'managed-candidate', 'version': '0', 'source': {'virtual': '.'}, 'dependencies': [{'name': 'bcrypt'}]},
        dict(identity, wheels=[wheel_entry])]}
    if not compatible:
        locked['package'][1]['wheels'] = [{'url': 'https://files.pythonhosted.org/bcrypt-5.0.0-cp312-cp312-win_amd64.whl',
                                         'hash': wheel_entry.get('hash', '')}]
    calls = observed_calls if observed_calls is not None else []
    downloads = []
    original_inspect = module.inspect_environment
    original_verify = module.verify_environment
    initial = True
    builder = RegistryBuilder(tmp_path / 'store', binary)
    real_run = builder._run
    def run(arguments, project, cache, venv, **kwargs):
        nonlocal initial
        calls.append(list(arguments))
        if arguments[0] == 'lock' and '--offline' not in arguments:
            (project / 'uv.lock').write_text(tomli_w.dumps(locked))
            return
        if arguments[0] == 'sync' and '--no-build' not in arguments:
            assert arguments == ['sync', '--frozen', '--no-config']
            # First sync installs the selected target; later cold consumption is real.
            venv.mkdir()
            cache.mkdir()
            return
        if arguments[0] == 'build':
            assert not compatible, 'compatible published wheel must not be built'
            wheel(Path(arguments[arguments.index('--out-dir') + 1]), name='bcrypt', version='5.0.0')
            return
        if arguments[0] == 'lock':
            project_toml = (project / 'pyproject.toml').read_text()
            assert 'wheels/bcrypt-' in project_toml
            assert (project / 'solve.lock').read_text() == tomli_w.dumps(locked)
            initial = False
        real_run(arguments, project, cache, venv, **kwargs)
    monkeypatch.setattr(builder, '_run', run)
    monkeypatch.setattr(module, 'inspect_environment', lambda venv: {'bcrypt': '5.0.0'} if initial else original_inspect(venv))
    def verify(venv, expected, material):
        assert expected == {'bcrypt': '5.0.0'} and material
        return original_verify(venv, expected, material)
    monkeypatch.setattr(module, 'verify_environment', verify)
    class Download(io.BytesIO):
        def __init__(self, payload, url):
            super().__init__(payload)
            self.url = url
        def geturl(self):
            return self.url
    def download(url, **kwargs):
        downloads.append(url)
        if url == identity['sdist']['url']:
            return Download(b'changed' if fault == 'sdist-hash' else b'original sdist', url)
        assert url == wheel_entry['url'], 'no unlocked artifact URL may be fetched'
        return Download(published.read_bytes(), 'https://foreign.invalid/' if fault == 'redirect' else url)
    if opener_handler is None:
        monkeypatch.setattr(module, '_open_registry_artifact', download)
    else:
        build_opener = module.urllib.request.build_opener
        monkeypatch.setattr(module.urllib.request, 'build_opener',
                            lambda *handlers: build_opener(*handlers, opener_handler))
    inputs = {'baseline': {'target': python_target(), 'desired_inventory': {}, 'runtime': {'kind': 'normal', 'inventory': {}}},
              'contributions': [], 'requirement': {'declaration': 'bcrypt==5.0.0'}, 'ownership_effects': {}}
    result = builder._solve(inputs, operation='add', core={}, requirements=['bcrypt==5.0.0'], image_wheels={},
        local_artifacts={}, direct_sources={}, authorizations={}, seed_parent=None, materialize=True, validate_result=False)
    return builder, result, calls, downloads, identity, published, tomli_w.dumps(locked)


@pytest.mark.parametrize('tag', ['py3-none-any', 'cp38-abi3-manylinux2014_aarch64'])
def test_registry_builder_retains_published_wheel_without_sdist_build(tmp_path, monkeypatch, tag):
    from packaging.tags import sys_tags
    from packaging.utils import parse_wheel_filename
    assert parse_wheel_filename('bcrypt-5.0.0-' + tag + '.whl')[3] & set(sys_tags())
    builder, result, calls, downloads, identity, published, original_lock = published_wheel_solve(tmp_path, monkeypatch, tag=tag)
    assert not any(call[0] == 'build' for call in calls)
    assert len(downloads) == 2 and downloads[0] == identity['sdist']['url']
    retained = builder.store / result['bundle']['digest']
    assert (retained / 'solve.lock').read_text() == original_lock
    assert (retained / 'sources/bcrypt-5.0.0.tar.gz').read_bytes() == b'original sdist'
    assert result['bundle']['inventory']['bcrypt'] == {'version': '5.0.0', 'source_digest': identity['sdist']['hash'][7:]}
    assert (retained / 'wheels' / published.name).read_bytes() == published.read_bytes()
    assert sha256_file(published) in result['bundle']['artifacts']
    assert identity['sdist']['hash'][7:] in result['bundle']['artifacts']
    assert calls[-1] == ['sync', '--frozen', '--no-build', '--offline', '--no-config']
    assert (retained / 'installed-material.json').exists()
    verify_retained(retained, result['bundle']['digest'])


def test_registry_builder_builds_sdist_only_without_compatible_published_wheel(tmp_path, monkeypatch):
    builder, result, calls, downloads, identity, _, _ = published_wheel_solve(tmp_path, monkeypatch, compatible=False)
    assert sum(call[0] == 'build' for call in calls) == 1
    assert downloads == [identity['sdist']['url']]
    assert result['bundle']['inventory']['bcrypt']['source_digest'] == identity['sdist']['hash'][7:]
    assert (builder.store / result['bundle']['digest'] / 'installed-material.json').exists()
    verify_retained(builder.store / result['bundle']['digest'], result['bundle']['digest'])


@pytest.mark.parametrize('fault', ['wheel-hash', 'missing-hash', 'malformed-hash', 'url', 'credentials', 'query', 'redirect', 'sdist-hash'])
def test_registry_builder_published_wheel_retention_refuses_invalid_locked_material(tmp_path, monkeypatch, fault):
    with pytest.raises(PreparationError):
        published_wheel_solve(tmp_path, monkeypatch, fault=fault)
    assert not list((tmp_path / 'store').iterdir())


@pytest.mark.parametrize('competing_wheel', [False, True])
def test_registry_builder_reused_sdist_needs_neither_build_nor_published_download(tmp_path, monkeypatch, competing_wheel):
    import tomllib
    builder, result, artifact, calls, original_lock = solve_reuse_fixture(tmp_path, monkeypatch, competing_wheel=competing_wheel)
    published = tomllib.loads(original_lock)['package'][1].get('wheels', [])
    assert bool(published) == competing_wheel
    if published:
        from packaging.tags import sys_tags
        from packaging.utils import parse_wheel_filename
        assert parse_wheel_filename(Path(published[0]['url']).name)[3] & set(sys_tags())
    assert not any(call[0] == 'build' for call in calls)
    assert calls[1] == ['sync', '--frozen', '--no-config', '--no-install-package', 'numpy']
    retained = builder.store / result['bundle']['digest']
    assert result['bundle']['inventory']['numpy']['source_digest'] == artifact.origin['sdist']['hash'][7:]
    assert (retained / 'installed-material.json').exists()
    verify_retained(retained, result['bundle']['digest'])


@pytest.mark.parametrize('artifact_kind', ['sdist', 'wheel'])
@pytest.mark.parametrize('redirect_kind', ['same-host', 'foreign-host', 'foreign-hop-return'])
def test_registry_builder_registry_artifact_redirect_refused_before_target_request(
        tmp_path, monkeypatch, artifact_kind, redirect_kind):
    import io
    from email.message import Message
    from urllib.request import BaseHandler
    from urllib.response import addinfourl
    from registry_wheel_fixture import origin

    sdist_url = origin('bcrypt', '5.0.0')['sdist']['url']
    wheel_url = 'https://files.pythonhosted.org/bcrypt-5.0.0-py3-none-any.whl'
    locked_url = sdist_url if artifact_kind == 'sdist' else wheel_url
    same_host = 'https://files.pythonhosted.org/off-lock/artifact'
    foreign_host = 'http://foreign.invalid/off-lock/artifact'
    redirects = {locked_url: same_host if redirect_kind == 'same-host' else foreign_host}
    if redirect_kind == 'foreign-hop-return':
        redirects[foreign_host] = same_host
    requests, calls = [], []

    class RecordedTransport(BaseHandler):
        # Exercise the real opener/error/redirect handlers without sockets or DNS.
        handler_order = 100

        def https_open(self, request):
            requests.append(request.full_url)
            assert request.timeout == 60
            headers = Message()
            if request.full_url in redirects:
                headers['Location'] = redirects[request.full_url]
                response = addinfourl(io.BytesIO(b''), headers, request.full_url, 302)
                response.msg = 'Found'
            else:
                assert request.full_url in {sdist_url, wheel_url, same_host, foreign_host}
                payload = (b'original sdist' if request.full_url == sdist_url or artifact_kind == 'sdist'
                           else (tmp_path / 'published/bcrypt-5.0.0-py3-none-any.whl').read_bytes())
                response = addinfourl(io.BytesIO(payload), headers, request.full_url, 200)
                response.msg = 'OK'
            return response

        http_open = https_open

    with pytest.raises(PreparationError, match='^unsupported artifact redirect$'):
        published_wheel_solve(tmp_path, monkeypatch, opener_handler=RecordedTransport(), observed_calls=calls)
    assert requests == ([sdist_url] if artifact_kind == 'sdist' else [sdist_url, wheel_url]), 'off-lock request was issued'
    assert not any(url in requests for url in redirects.values()), 'redirect target must never be requested'
    assert not any(call[0] == 'build' for call in calls), 'redirect refusal must never fall back to a build'
    assert not list((tmp_path / 'store').iterdir())
