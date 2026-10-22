"""Backup policy, image provenance, durable purge and startup binding regressions."""
from dataclasses import replace
import json
import os
import shutil
import subprocess
import uuid

import pytest

from docassemble_executor import checkpoint_restore as restore
from docassemble_executor.checkpoints import require_complete_backup, require_supported_uploads
from docassemble_executor.config import load_config
from docassemble_executor.managed_backup import ManagedBackupError, require_baseline_wheel_map, references
from docassemble_executor.paths import ExecutorPaths


@pytest.mark.parametrize('wheels', [{}, {'extra': 'x'}, {'core': 'x', 'extra': 'x'}])
def test_baseline_wheel_map_must_cover_all_core_and_only_core(wheels):
    with pytest.raises(ManagedBackupError, match='cover core exactly'):
        require_baseline_wheel_map({'baseline': {'core': {'core': {}}}, 'wheels': wheels})


def test_reused_image_bundle_name_preserves_each_content_identity():
    records = {'revision': [{'snapshot': {'image_baseline': {'bundle': 'runtime-r1', 'bundle_digest': value * 64}}}
                            for value in ('a', 'b')], 'environment_state': [],
               'requirement_contribution': [], 'candidate_material': []}
    assert references(records)['image_bundles'] == {'a' * 64: ['runtime-r1'], 'b' * 64: ['runtime-r1']}


@pytest.mark.parametrize('value', ['0', '00', 0, 0.0, False])
def test_numeric_backup_disablement_is_effective(value):
    with pytest.raises(ManagedBackupError, match='disabled'):
        require_complete_backup({}, {'backup days': value})


@pytest.mark.parametrize('value', [0, None, False, ''])
def test_falsey_backup_file_storage_disables_checkpoint(value):
    with pytest.raises(ManagedBackupError, match='disabled'):
        require_complete_backup({}, {'backup file storage': value})


def test_literal_overlay_and_environment_numeric_conversion_disable_backup(tmp_path, monkeypatch):
    file = tmp_path / 'config.yml'
    file.write_text('backup days: 14\nconfig from:\n  backup_days: "0"\n')
    config = load_config(file)
    assert config['backup days'] == '0'
    with pytest.raises(ManagedBackupError, match='disabled'):
        require_complete_backup({}, config)
    monkeypatch.setenv('ENVIRONMENT_TAKES_PRECEDENCE', 'true')
    monkeypatch.setenv('DABACKUPDAYS', '00')
    assert load_config(file)['backup days'] == 0
    file.write_text('config from: arn:aws:secretsmanager:UNRESOLVED\n')
    with pytest.raises(ValueError, match='unresolved'):
        load_config(file)


def test_upload_root_rejects_relative_custom_and_symlink_before_capture(tmp_path):
    paths = replace(ExecutorPaths.from_env(), da_root=tmp_path)
    (tmp_path / 'files').mkdir()
    require_supported_uploads({'uploads': str(tmp_path / 'files')}, paths)
    for value in ('files', str(tmp_path / 'other'), None):
        with pytest.raises(ManagedBackupError, match='upload root'):
            require_supported_uploads({'uploads': value}, paths)
    (tmp_path / 'files').rmdir()
    (tmp_path / 'files').symlink_to(tmp_path / 'other')
    with pytest.raises(ManagedBackupError, match='upload root'):
        require_supported_uploads({'uploads': str(tmp_path / 'files')}, paths)


def test_effective_broker_environment_and_celery_override_binding(monkeypatch):
    monkeypatch.setenv('ENVIRONMENT_TAKES_PRECEDENCE', 'true')
    monkeypatch.setenv('RABBITMQ', 'redis://worker/2')
    calls = []
    def command(broker, *args):
        calls.append(broker)
        return {'PING': 'PONG', 'CONFIG': 'appendonly\nno', 'TYPE': 'none'}[args[0]]
    monkeypatch.setattr(restore, '_broker_command', command)
    assert restore.preflight_broker({'rabbitmq': 'redis://obsolete/0'}) == 'redis://worker/2'
    assert set(calls) == {'redis://worker/2'}
    for variable in ('CELERY_BROKER_URL', 'CELERY_BROKER_READ_URL'):
        monkeypatch.setenv(variable, 'redis://worker/3')
        with pytest.raises(restore.RestoreBrokerError, match='mismatch'):
            restore.preflight_broker({'rabbitmq': 'redis://obsolete/0'})
        monkeypatch.delenv(variable)


@pytest.mark.parametrize('failure', ['aof', 'save', 'reappeared'])
def test_unqualified_or_failed_purge_barrier_refuses_completion(monkeypatch, failure):
    exists = iter(['0', '1' if failure == 'reappeared' else '0'])
    def command(broker, *args):
        if args[0] == 'EXISTS':
            return next(exists)
        return {'CONFIG': 'appendonly\nyes' if failure == 'aof' else 'appendonly\nno',
                'DEL': '1', 'SAVE': 'ERR save failed' if failure == 'save' else 'OK'}[args[0]]
    monkeypatch.setattr(restore, '_broker_command', command)
    with pytest.raises(restore.RestoreBrokerError):
        restore.purge_broker('redis://fixture/0')


@pytest.mark.parametrize('binding', ['missing', 'malformed', 'mismatch', 'match'])
def test_worker_checkpoint_endpoint_binding_is_readonly_and_fail_closed(tmp_path, binding):
    paths = replace(ExecutorPaths.from_env(), state_dir=tmp_path)
    journal = tmp_path / 'checkpoint-restore.json'
    journal.write_text('{}')
    file = tmp_path / 'checkpoint-broker.json'
    if binding != 'missing':
        file.write_text('invalid' if binding == 'malformed' else json.dumps({
            'state': 'inputs-restored', 'broker_fingerprint': restore.broker_fingerprint(
                'redis://example.test/1' if binding == 'mismatch' else 'redis://EXAMPLE.test:6379/0')}))
    before = file.read_bytes() if file.exists() else None
    if binding == 'match':
        restore.require_checkpoint_broker(paths, 'redis://example.test/')
    else:
        with pytest.raises(restore.RestoreBrokerError, match='startup refused'):
            restore.require_checkpoint_broker(paths, 'redis://user:SECRET@example.test/')
    assert (file.read_bytes() if file.exists() else None) == before
    assert journal.read_text() == '{}'


def test_normal_worker_without_checkpoint_is_unaffected(tmp_path):
    restore.require_checkpoint_broker(replace(ExecutorPaths.from_env(), state_dir=tmp_path), 'redis://example.test/0')


def test_purge_survives_real_redis_crash_restart_and_preserves_application_keys(monkeypatch):
    # Use a new isolated container; never restart or flush the shared da-redis.
    if not shutil.which('docker'):
        pytest.skip('Docker required for isolated broker restart')
    name = 'da-broker-barrier-' + uuid.uuid4().hex
    def docker(*args):
        return subprocess.run(['docker', *args], capture_output=True, text=True, check=True).stdout.strip()
    try:
        docker('run', '-d', '--name', name, 'redis:7.4.2-alpine', 'redis-server', '--save', '', '--appendonly', 'no')
        import time
        for _ in range(100):
            try:
                if docker('exec', name, 'redis-cli', 'PING') == 'PONG':
                    break
            except subprocess.CalledProcessError:
                pass
            time.sleep(0.05)
        def command(broker, *args):
            return docker('exec', name, 'redis-cli', '--raw', *args)
        monkeypatch.setattr(restore, '_broker_command', command)
        command('', 'LPUSH', 'celery', 'stale-task')
        command('', 'LPUSH', 'single\x06\x169', 'stale-priority-task')
        command('', 'HSET', 'unacked', 'delivery', 'stale-task')
        command('', 'SET', 'application-state', 'keep')
        assert command('', 'SAVE') == 'OK'  # persisted stale work before restore
        restore.purge_broker('redis://fixture/0')
        docker('kill', '--signal=KILL', name)
        docker('start', name)
        for _ in range(100):
            try:
                if command('', 'PING') == 'PONG':
                    break
            except subprocess.CalledProcessError:
                pass
            time.sleep(0.05)
        assert command('', 'EXISTS', *restore.broker_keys()) == '0'
        assert command('', 'GET', 'application-state') == 'keep'
    finally:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, check=False)


def test_cold_restore_preserves_image_directory_metadata_and_restores_material(tmp_path):
    from docassemble_executor.cold_restore import restore_objects
    stock = tmp_path / 'local3.14'
    stock.mkdir(mode=0o750)
    (stock / 'sentinel').write_bytes(b'stock unchanged')
    image = tmp_path / 'image-owned'
    image.mkdir(mode=0o755)
    baseline = image / 'baseline'
    baseline.mkdir(mode=0o750)
    (baseline / 'baseline.json').write_text('replacement')
    material = tmp_path / 'worker-material'
    material.mkdir(mode=0o700)
    (tmp_path / 'objects/material').mkdir(parents=True)
    (tmp_path / 'objects/material/x').write_text('retained')
    info = {'mode': 0o600, 'uid': os.getuid(), 'gid': os.getgid()}
    document = {'directories': {str(image): dict(info, mode=0o700), str(baseline): dict(info, mode=0o700),
                               str(material): dict(info, mode=0o750)},
                'files': {'image-baseline/baseline.json': dict(info, source=str(baseline / 'baseline.json')),
                          'material/x': dict(info, source=str(material / 'x'))}}
    restore_objects(tmp_path, document, {'unavailable_objects': []})
    assert (stock / 'sentinel').read_bytes() == b'stock unchanged'
    assert stock.stat().st_mode & 0o777 == 0o750
    assert image.stat().st_mode & 0o777 == 0o755
    assert baseline.stat().st_mode & 0o777 == 0o750
    assert material.stat().st_mode & 0o777 == 0o750
    assert (material / 'x').read_text() == 'retained'
    assert (baseline / 'baseline.json').read_text() == 'replacement'


@pytest.mark.parametrize('matched', [True, False])
def test_worker_runtime_startup_checks_actual_celery_broker_before_worker_start(tmp_path, monkeypatch, matched):
    import importlib.util
    import sys
    from types import ModuleType, SimpleNamespace
    from pathlib import Path
    started = []
    def module(name, **attrs):
        value = ModuleType(name)
        value.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, value)
        return value
    module('celery', bootsteps=SimpleNamespace(StartStopStep=object), signals=SimpleNamespace())
    module('celery.worker', state=SimpleNamespace(), strategy=SimpleNamespace())
    module('celery.worker.control', inspect_command=lambda: lambda f: f)
    worker = SimpleNamespace(start=lambda: started.append('started'), exitcode=0)
    app = SimpleNamespace(conf=SimpleNamespace(broker_url='redis://actual/2', broker_read_url='redis://read/2'),
                          steps={'consumer': set()}, tasks={}, Worker=lambda **_: worker)
    module('docassemble.webapp.worker', celery_app=app)
    module('docassemble.webapp.config', daconfig={})
    monkeypatch.setenv('DA_MANAGED_STATE_DIR', str(tmp_path))
    monkeypatch.setattr(sys, 'argv', ['worker_runtime', '--kind', 'single'])
    (tmp_path / 'checkpoint-restore.json').write_text('{}')
    (tmp_path / 'checkpoint-broker.json').write_text(json.dumps({
        'state': 'inputs-restored', 'broker_fingerprint': restore.broker_fingerprint(
            'redis://read:6379/2' if matched else 'redis://actual/2')}))
    source = Path(__file__).parents[1] / 'src/docassemble_executor/worker_runtime.py'
    spec = importlib.util.spec_from_file_location('qualified_worker_runtime', source)
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    if matched:
        assert runtime.main() == 0
        assert started == ['started']
    else:
        with pytest.raises(restore.RestoreBrokerError, match='startup refused'):
            runtime.main()
        assert started == []


@pytest.mark.parametrize('damage', ['missing', 'digest', 'inventory'])
def test_historical_image_revision_requires_exact_retained_restore_inventory(tmp_path, damage):
    from docassemble_executor.managed_backup import verify_historical_restore_material, _sha
    from docassemble.base.managed_preparation import digest
    directory = tmp_path / 'stage'
    directory.mkdir()
    (directory / 'pyproject.toml').write_text('project')
    (directory / 'uv.lock').write_text('lock')
    (directory / 'inventory.json').write_text('{"core": {"version": "1"}}')
    files = {path.name: _sha(path) for path in directory.iterdir()}
    reference = digest(files)
    (directory / 'manifest.json').write_text(json.dumps(files))
    directory.rename(tmp_path / reference)
    bundle = {'digest': reference, 'project_digest': files['pyproject.toml'], 'lock_digest': files['uv.lock'],
              'inventory': {'core': {'version': '1'}}}
    records = {'revision': [{'snapshot': {'image_baseline': {'bundle': 'runtime-r1', 'bundle_digest': 'a' * 64},
                                         'publication': {'result': {'bundle': bundle}}}}]}
    manifest = {'bundles': {'runtime-r1': 'b' * 64}}
    verify_historical_restore_material(records, manifest, tmp_path)
    if damage == 'missing':
        (tmp_path / reference / 'uv.lock').unlink()
    elif damage == 'digest':
        bundle['digest'] = 'c' * 64
    else:
        bundle['inventory'] = {'core': {'version': '2'}}
    with pytest.raises(ManagedBackupError, match='historical revision retained restore material invalid'):
        verify_historical_restore_material(records, manifest, tmp_path)


@pytest.mark.parametrize('key', ['backup days', 'backup file storage', 'uploads', 's3'])
def test_unresolved_cloud_infrastructure_settings_refuse_instead_of_defaulting(tmp_path, key):
    file = tmp_path / 'config.yml'
    file.write_text(json.dumps({key: 'arn:aws:secretsmanager:UNRESOLVED'}))
    with pytest.raises(ValueError, match='unresolved infrastructure'):
        load_config(file)
