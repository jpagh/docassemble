"""helpers_mail's backup-local provisioning and real managed object selection (no Docker/DB)."""
import ast
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import patch
import zipfile

import pytest

from docassemble.base.managed_preparation import digest
from docassemble_executor import managed_backup
from docassemble_executor.bundles import hash_project, sha256_file
from docassemble_executor.image_baseline import load_image_wheels
from docassemble_executor.paths import ExecutorPaths

EXPERIMENT = Path(__file__).resolve().parents[2]
REQUIRED = ('DA_DURABLE_WORK_STORE', 'DA_QUALIFIED_IMAGE_BASELINE')
INCOMPLETE = 'backup: managed objects incomplete; inspect required records/stores and retry\n'


def helpers_mail_setup():
    """Run the scenario's actual disposable Python setup, not a test reimplementation."""
    tree = ast.parse((EXPERIMENT / 'scenarios/helpers_mail.py').read_text())
    setups = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute) and node.func.attr == 'exec'
              and len(node.args) > 1 and isinstance(node.args[1], ast.List)
              and len(node.args[1].elts) == 4
              and isinstance(node.args[1].elts[2], ast.Constant)
              and 'provision_image_baseline(' in str(node.args[1].elts[2].value)]
    assert len(setups) == 1
    setup = setups[0]
    schedules = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == 'helper'
                 and any(keyword.arg == 'script' for keyword in node.keywords)]
    assert setup.lineno < min(node.lineno for node in schedules if node.lineno > setup.lineno)
    starts = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute) and node.func.attr == 'start']
    assert len(starts) == 1
    environment = ast.literal_eval(next(keyword.value for keyword in starts[0].keywords
                                       if keyword.arg == 'env'))
    assert not set(REQUIRED) & environment.keys()
    return setup.args[1].elts[2].value


@dataclass
class HelperBackupFixture:
    paths: ExecutorPaths
    root: Path
    environment_file: Path
    original_environment: dict

    @property
    def inputs(self):
        return json.loads(self.environment_file.read_text())

    @property
    def store(self):
        return Path(self.inputs['DA_DURABLE_WORK_STORE'])

    @property
    def descriptor(self):
        return Path(self.inputs['DA_QUALIFIED_IMAGE_BASELINE'])


@pytest.fixture
def helper_backup(tmp_path, monkeypatch):
    for name in REQUIRED:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('DA_ROOT', str(tmp_path / 'application'))
    monkeypatch.setenv('DA_BUNDLE_DIR', str(tmp_path / 'bundles'))
    monkeypatch.setenv('DA_MANAGED_STATE_DIR', str(tmp_path / 'state'))
    paths = ExecutorPaths.from_env()
    paths.state_dir.mkdir()
    (paths.state_dir / 'lifecycle.json').write_text('{"phase":"verified"}')
    manifest = {'identity': {}, 'bundles': {}, 'inventories': {},
                'executor_inventory': {}, 'uv_sha256': 'fixture', 'uv_binary_sha256': 'fixture'}
    for name in ('runtime-r1', 'core-r1'):
        directory = paths.bundles_dir / name
        wheel = directory / 'wheels/example-1.0-py3-none-any.whl'
        wheel.parent.mkdir(parents=True)
        with zipfile.ZipFile(wheel, 'w') as archive:
            archive.writestr('example-1.0.dist-info/METADATA',
                             'Metadata-Version: 2.1\nName: example\nVersion: 1.0\n')
            archive.writestr('example-1.0.dist-info/WHEEL',
                             'Wheel-Version: 1.0\nTag: py3-none-any\n')
        (directory / 'pyproject.toml').write_text('[project]\nname = "fixture"\nversion = "1.0"\n')
        (directory / 'uv.lock').write_text('[[package]]\nname = "example"\nversion = "1.0"\n'
                                           'source = {path = "wheels/' + wheel.name + '"}\n')
        manifest['bundles'][name] = hash_project(directory)
        manifest['inventories'][name] = {'distributions': {'example': '1.0'}}
    paths.manifest_file.write_text(json.dumps(manifest))
    webapp = paths.da_root / 'webapp'
    webapp.mkdir(parents=True)
    for script in ('backup.sh', 'backup-hourly.sh'):
        (webapp / script).write_text('#!/bin/bash\necho real-backup-' + script + '\n')
    environment_file = tmp_path / 'managed-backup.json'
    environment_file.write_bytes((EXPERIMENT / 'fixture/helper-backup.json').read_bytes())
    original_environment = dict(os.environ)
    fixture = HelperBackupFixture(paths, tmp_path / 'backup-proof', environment_file, original_environment)
    code = helpers_mail_setup().replace('/tmp/da-backup-proof', str(fixture.root))
    code = code.replace('/etc/docassemble/managed-backup.json', str(environment_file))
    code = code.replace('/usr/share/docassemble/webapp', str(webapp))
    with patch.object(sys, 'argv', ['-c', 'observation-only probe']):
        exec(compile(code, 'helpers_mail disposable backup setup', 'exec'), {})
    return fixture


@pytest.mark.parametrize('provided', [(), ('DA_DURABLE_WORK_STORE',)], ids=['both-missing', 'descriptor-missing'])
def test_helpers_mail_old_mapping_fails_cli_before_backup_creation(provided, capsys):
    environment = json.loads((EXPERIMENT / 'fixture/helper-backup.json').read_text())
    environment.update({name: '/unused' for name in provided})
    with patch.dict(os.environ, environment, clear=True), \
            patch('docassemble_executor.durable_work.configuration', return_value=('postgresql:///unused', '')), \
            patch.object(managed_backup, 'create_engine') as engine, \
            patch.object(managed_backup, 'create_backup') as capture:
        assert managed_backup.main(['--dump', '/unused']) == 1
        capture.assert_not_called()
        engine.return_value.dispose.assert_called_once_with()
    output = capsys.readouterr()
    assert output.out == '' and output.err == INCOMPLETE


def test_helpers_mail_setup_provisions_exact_wheels_and_backup_local_mapping(helper_backup):
    fixture = helper_backup
    legacy = json.loads((EXPERIMENT / 'fixture/helper-backup.json').read_text())
    assert fixture.inputs == dict(legacy, DA_DURABLE_WORK_STORE=str(fixture.root / 'retained'),
                                 DA_QUALIFIED_IMAGE_BASELINE=str(fixture.root / 'image-baseline/baseline.json'))
    assert fixture.store.is_dir() and not list(fixture.store.iterdir())
    assert fixture.store.stat().st_mode & 0o777 == 0o700
    assert fixture.environment_file.stat().st_uid == os.getuid()
    assert fixture.environment_file.stat().st_mode & 0o777 == 0o600

    def ambient(environment):
        # pytest rewrites PYTEST_CURRENT_TEST between the fixture's setup phase and
        # the call phase; every other variable must be exactly as the scenario setup
        # found it, because the store and baseline are deliberately backup-local.
        return {name: value for name, value in environment.items() if name != 'PYTEST_CURRENT_TEST'}

    assert ambient(os.environ) == ambient(fixture.original_environment)
    baseline, wheels = load_image_wheels(fixture.descriptor, fixture.paths)
    assert set(wheels) == set(baseline['core']) == {'example'}
    assert wheels['example'].read_bytes() == (fixture.paths.bundles_dir /
                                            'runtime-r1/wheels/example-1.0-py3-none-any.whl').read_bytes()
    for script in ('backup.sh', 'backup-hourly.sh'):
        assert (fixture.root / script).read_bytes() == ('#!/bin/bash\necho real-backup-' + script + '\n').encode()
        assert 'exec /bin/bash ' + str(fixture.root / script) in (fixture.paths.da_root / 'webapp' / script).read_text()


def test_helpers_mail_provisioned_mapping_reaches_cli_with_expected_managed_inputs(helper_backup, capsys):
    fixture = helper_backup
    environment = dict(fixture.original_environment, **fixture.inputs)
    with patch.dict(os.environ, environment, clear=True), \
            patch('docassemble_executor.durable_work.configuration', return_value=('postgresql:///unused', '')), \
            patch.object(managed_backup, 'create_engine') as engine, \
            patch.object(managed_backup, 'create_backup') as capture:
        assert managed_backup.main(['--dump', str(fixture.root / 'dump')]) == 0
        capture.assert_called_once_with(engine.return_value, fixture.root / 'dump', fixture.paths, prefix='',
                                        store=fixture.root / 'retained',
                                        baseline=fixture.root / 'image-baseline/baseline.json',
                                        artifact_roots=[], secret_root=None, index_root=None)
        engine.return_value.dispose.assert_called_once_with()
    output = capsys.readouterr()
    assert 'managed objects complete' in output.out and output.err == ''


def retained_records(fixture):
    """Make a real retained reference so the store is mandatory, even for an image-only fixture."""
    content = b'mandatory retained lock'
    manifest = {'uv.lock': hashlib.sha256(content).hexdigest()}
    reference = digest(manifest)
    bundle = fixture.store / reference
    bundle.mkdir()
    (bundle / 'uv.lock').write_bytes(content)
    (bundle / 'manifest.json').write_text(json.dumps(manifest))
    return {'revision': [{'id': 'retained', 'snapshot': {'publication': {'result': {'bundle': {'digest': reference}}}}}],
            'environment_state': [], 'requirement_contribution': [], 'candidate_material': []}


def copy_fixture_objects(fixture, records):
    return managed_backup.copy_objects(records, fixture.paths, fixture.root / 'objects-backup',
                                       store=fixture.store, baseline=fixture.descriptor,
                                       artifact_roots=[], secret_root=None, index_root=None)


def test_helpers_mail_provisioned_material_passes_real_managed_object_selection(helper_backup):
    fixture = helper_backup
    copied = copy_fixture_objects(fixture, retained_records(fixture))
    assert copied['references']['bundles']
    files = copied['files']
    assert {'image-baseline/baseline.json', 'image-baseline/wheels/example-1.0-py3-none-any.whl',
            'image/runtime-r1/uv.lock', 'image/core-r1/uv.lock', 'state/lifecycle.json'} <= files.keys()
    for name, record in files.items():
        assert sha256_file(fixture.root / 'objects-backup/objects' / name) == record['sha256']


@pytest.mark.parametrize('missing', ['store', 'descriptor', 'wheel'])
def test_helpers_mail_missing_mandatory_material_still_fails_real_selection(helper_backup, missing):
    fixture = helper_backup
    records = retained_records(fixture)
    if missing == 'store':
        shutil.rmtree(fixture.store)
        error, message = managed_backup.ManagedBackupError, 'required managed directory unavailable'
    else:
        path = fixture.descriptor if missing == 'descriptor' else next((fixture.descriptor.parent / 'wheels').iterdir())
        path.unlink()
        error, message = FileNotFoundError, ''
    with pytest.raises(error, match=message or None):
        copy_fixture_objects(fixture, records)
    assert not (fixture.root / 'objects-backup/manifest.json').exists()
