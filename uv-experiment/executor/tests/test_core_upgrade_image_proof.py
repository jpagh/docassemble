"""Shared core-upgrade proof source bindings; no uv subprocess or container."""
from docassemble.base.managed_python import python_target
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tomllib

import pytest

from docassemble_executor.registry_builder import RegistryBuilder


PREPARATION = Path(__file__).resolve().parents[2] / 'preparation'


@pytest.mark.parametrize('with_user_artifact', [False, True])
def test_core_upgrade_image_proof_injects_fixtures_only_into_fresh_core_solve(
        monkeypatch, tmp_path, with_user_artifact):
    monkeypatch.syspath_prepend(str(PREPARATION))
    spec = importlib.util.spec_from_file_location(
        'core_upgrade_image_proof', PREPARATION / 'core_upgrade_image_proof.py')
    proof = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proof)
    root, store = tmp_path / 'proof', tmp_path / 'store'
    root.mkdir()
    monkeypatch.setattr(proof, 'ROOT', root)
    monkeypatch.setattr(proof, 'STORE', store)
    monkeypatch.setattr(proof, 'DESCRIPTOR', tmp_path / 'image' / 'baseline.json')
    image_directory = proof.DESCRIPTOR.parent / 'wheels'
    fixture_directory = root / 'fixture-release-1.10.14'
    retained_directory = tmp_path / 'retained-core'
    for directory in (image_directory, fixture_directory, retained_directory):
        directory.mkdir(parents=True)
    fixtures = {}
    image_wheels = {}
    for name in sorted(proof.TRIO | {'gunicorn'}):
        version = '23.0.0' if name == 'gunicorn' else '1.10.14'
        filename = name.replace('-', '_') + '-' + version + '-py3-none-any.whl'
        wheel = (image_directory if name == 'gunicorn' else fixture_directory) / filename
        wheel.write_bytes(('fixture bytes for ' + name).encode())
        fixtures[name] = wheel
        retained = retained_directory / filename
        retained.write_bytes(wheel.read_bytes())
        image_wheels[name] = retained
    local_artifacts = {}
    if with_user_artifact:
        wheel = tmp_path / 'user_package-1.0-py3-none-any.whl'
        wheel.write_bytes(b'user artifact bytes')
        local_artifacts['user-package'] = ({'filename': wheel.name, 'version': '1.0',
            'digest': hashlib.sha256(wheel.read_bytes()).hexdigest()}, wheel)
    core = {name: {'version': '23.0.0' if name == 'gunicorn' else '1.10.14'}
            for name in image_wheels}
    inputs = {'change_operation': 'upgrade', 'contributions': [], 'baseline': {
        'core': core, 'target': python_target(), 'tool': {'uv': '0.12.21'},
        'config': {'index_url': 'https://pypi.org/simple'}}}
    (root / 'upgrade-inputs.json').write_text(json.dumps(inputs))
    received = []
    projects = []
    solve = RegistryBuilder._solve

    class StopBeforeUv(Exception):
        pass

    def capture_solve(builder, solve_inputs, **kwargs):
        received.append(kwargs)
        return solve(builder, solve_inputs, **kwargs)

    def parse_before_uv(builder, arguments, project, cache, venv):
        assert arguments == ['lock', '--no-config']
        projects.append(tomllib.loads((project / 'pyproject.toml').read_text()))
        raise StopBeforeUv()

    def forbid_subprocess(*args, **kwargs):
        pytest.fail('proof binding regression must not invoke a subprocess')

    def exercise_both_solves(builder, build_inputs):
        for operation in ('upgrade-core', 'upgrade'):
            supplied = {'operation': operation, 'core': {} if operation == 'upgrade-core' else core,
                'requirements': [name + '==' + value['version'] for name, value in core.items()],
                'image_wheels': {} if operation == 'upgrade-core' else image_wheels,
                'local_artifacts': {} if operation == 'upgrade-core' else local_artifacts,
                'direct_sources': {}, 'authorizations': {},
                'seed_parent': None if operation == 'upgrade-core' else 'preferred',
                'validate_result': False, 'materialize': True}
            if with_user_artifact and operation == 'upgrade':
                supplied['requirements'].append('user-package==1.0')
            with pytest.raises(StopBeforeUv):
                builder._solve(build_inputs, **supplied)
            forwarded = received[-1]
            for key, value in supplied.items():
                if operation != 'upgrade-core' or key != 'local_artifacts':
                    assert forwarded[key] is value
            if operation == 'upgrade-core':
                assert set(forwarded['local_artifacts']) == set(fixtures)
                for name, (identity, wheel) in forwarded['local_artifacts'].items():
                    assert wheel == fixtures[name]
                    assert identity == {'filename': wheel.name, 'version': core[name]['version'],
                        'digest': hashlib.sha256(wheel.read_bytes()).hexdigest()}
            else:
                assert not (set(forwarded['local_artifacts']) & set(fixtures))
        return {'proof_test': True}

    monkeypatch.setattr(RegistryBuilder, 'build', exercise_both_solves)
    monkeypatch.setattr(RegistryBuilder, '_solve', capture_solve)
    monkeypatch.setattr(RegistryBuilder, '_run', parse_before_uv)
    monkeypatch.setattr(subprocess, 'run', forbid_subprocess)
    proof.build('1.10.14')
    assert [call['operation'] for call in received] == ['upgrade-core', 'upgrade']
    assert projects[0]['tool']['uv']['sources'] == {
        name: {'path': 'artifacts/' + wheel.name} for name, wheel in fixtures.items()}
    assert projects[1]['tool']['uv']['sources'] == {
        **{name: {'path': 'wheels/' + wheel.name} for name, wheel in image_wheels.items()},
        **{name: {'path': 'artifacts/' + identity['filename']}
           for name, (identity, _) in local_artifacts.items()}}
    assert json.loads((store / 'upgrade-result.json').read_text()) == {'proof_test': True}


def test_core_upgrade_image_proof_duplicate_image_and_fixture_bindings_are_invalid_toml():
    with pytest.raises(tomllib.TOMLDecodeError):
        tomllib.loads('''[tool.uv.sources]
"docassemble-base" = { path = "wheels/docassemble_base-1.10.14-py3-none-any.whl" }
"docassemble-base" = { path = "artifacts/docassemble_base-1.10.14-py3-none-any.whl" }
''')
