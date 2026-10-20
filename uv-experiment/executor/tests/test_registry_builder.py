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
        'target': {'python': '3.12'}, 'tool': {'uv': '0.12.21'}, 'config': {'index_url': 'https://pypi.org/simple'},
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
    monkeypatch.setattr(module.sys, 'version_info', (3, 12))
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=b'uv 0.12.21'))
    calls = []

    class StopBeforeSolve(Exception):
        pass

    def run(arguments, project, cache, venv):
        calls.append((arguments, tomllib.loads((project / 'pyproject.toml').read_text())))
        raise StopBeforeSolve()

    if image:
        inputs['baseline']['image_baseline'] = {'fixture': True}
        monkeypatch.setattr('docassemble_executor.image_baseline.load_image_wheels',
                            lambda *args: (inputs['baseline'], {}))
    builder = RegistryBuilder(tmp_path, image_baseline=tmp_path if image else None)
    monkeypatch.setattr(builder, '_run', run)
    with pytest.raises(StopBeforeSolve):
        builder.build(inputs)
    arguments, project = calls[0]
    assert arguments == ['lock', '--no-config']
    assert project['project']['dependencies'] == [row['declaration'] for row in inputs['contributions']] + ['core==1.0']
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
        'target': {'python': '3.12'}, 'tool': {'uv': '0.12.21'},
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
                'target': {'python': '3.12'}, 'tool': {'uv': '0.12.21'},
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
    monkeypatch.setattr(module.sys, 'version_info', (3, 12))
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
    monkeypatch.setattr(module.sys, 'version_info', (3, 12))
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
    monkeypatch.setattr(module.sys, 'version_info', (3, 12))
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=b'uv 0.12.21'))
    monkeypatch.setattr(module, 'inspect_environment', lambda venv: {'zip-example': '2.0'})
    calls = []
    def run(arguments, project, cache, venv):
        calls.append(arguments[0])
        if arguments[0] == 'lock':
            (project / 'uv.lock').write_text('[[package]]\nname="zip-example"\nversion="1.0"\nsource={path="artifacts/' + inputs['artifact']['filename'] + '"}\n')
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
    inputs = {'baseline': {'target': {'python': '3.12'}, 'tool': {'uv': '0.12.21'},
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
