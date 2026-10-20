"""Owned boot refusals preserve qualification diagnostics without exception values."""
from dataclasses import replace
import json
import platform
import subprocess
import sys
import sysconfig
from types import SimpleNamespace
import urllib.request

import pytest

from docassemble_executor import controller, startup
from docassemble_executor.health import Check
from docassemble_executor.journal import Journal
from docassemble_executor.paths import ExecutorPaths
from docassemble_executor.state import AppliedState, DesiredState


SECRET = 'SENTINEL_BOOT_DIAGNOSTIC_SECRET'


@pytest.fixture
def boot_inputs(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    paths = replace(ExecutorPaths.from_env(), state_dir=state, run_dir=tmp_path / 'run',
                    log_dir=tmp_path / 'logs', cache_dir=tmp_path / 'cache',
                    runtime_venv=tmp_path / 'runtime', config_file=tmp_path / 'config.yml',
                    intent_file=state / 'desired.json', journal_file=state / 'lifecycle.json',
                    applied_file=state / 'applied.json', status_file=tmp_path / 'status.json',
                    repair_request_file=tmp_path / 'requests/repair.json',
                    manifest_file=tmp_path / 'manifest.json', web_port=0)
    for directory in (state, paths.run_dir, paths.runtime_venv, paths.cache_dir, paths.log_dir):
        directory.mkdir()
    paths.config_file.write_text('db:\n  password: ' + SECRET + '\n')
    manifest = {
        'format': 2,
        'identity': {'core_baseline': 'r1', 'python_version': sys.version.split()[0],
                     'platform': sysconfig.get_platform(), 'machine': platform.machine(),
                     'libc': list(platform.libc_ver())},
        'bundles': {'runtime-r1': 'bundle-hash'},
        'inventories': {'runtime-r1': {'distributions': {}, 'files': {}}},
        'executor_inventory': {'distributions': {}, 'files': {}},
        'uv_sha256': 'uv-hash', 'uv_binary_sha256': 'uv-hash',
    }
    paths.manifest_file.write_text(json.dumps(manifest))
    paths.intent_file.write_text(json.dumps(DesiredState('r1', 'r1').to_dict()))
    Journal(phase='verified').save(paths.journal_file)
    AppliedState('r1', 'r1', 'verified-at').save(paths.applied_file)
    initialization = {'format': 1, 'target_id': startup.target_id(manifest),
                      'revision': 'r1', 'bundle_hash': 'bundle-hash', 'verified_at': 'verified-at'}
    (state / 'initialized.json').write_text(json.dumps(initialization))
    monkeypatch.setenv('DA_UV_MANAGED', 'true')
    monkeypatch.setenv('DAUPDATEONSTART', 'true')
    monkeypatch.setenv('CONTAINERROLE', 'all')
    monkeypatch.setenv('DAREADONLYFILESYSTEM', 'false')
    monkeypatch.delenv('DA_ENVIRONMENT_USER', raising=False)
    monkeypatch.delenv('DA_DURABLE_WORK_STORE', raising=False)
    monkeypatch.setattr(controller, 'start_postgres', lambda: Check('postgres', True))
    monkeypatch.setattr(controller, 'start_redis', lambda: Check('redis', True))
    monkeypatch.setattr(controller, 'start_supervisor', lambda _: Check('supervisor', True))
    monkeypatch.setattr('docassemble_executor.boot_observation.published_boot_required', lambda *a: False)
    monkeypatch.setattr(startup, 'sha256_file', lambda _: 'uv-hash')
    monkeypatch.setattr(startup, 'verify_environment', lambda *a: SimpleNamespace(ok=True))
    monkeypatch.setattr(controller, 'run_normal', lambda _: pytest.fail('refused boot must not activate runtime'))
    return paths


@pytest.fixture
def refused_boot(monkeypatch):
    ingress = SimpleNamespace(shutdown=lambda: None, server_close=lambda: None)
    monkeypatch.setattr(controller, 'start_ingress', lambda *a: ingress)
    handlers = {}
    monkeypatch.setattr(controller.signal, 'signal', lambda signum, handler: handlers.update({signum: handler}))
    monkeypatch.setattr(controller.time, 'sleep', lambda _: handlers[controller.signal.SIGTERM]())
    real_start = controller.start_maintenance_server
    received = []
    def start(port, provider):
        server = real_start(port, provider)
        try:
            for path in ('/status.json', '/health_status', '/recovery/status.json', '/'):
                with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}{path}') as response:
                    received.append(response.read().decode())
        except BaseException:
            server.shutdown()
            server.server_close()
            raise
        return server
    monkeypatch.setattr(controller, 'start_maintenance_server', start)
    return received


@pytest.mark.parametrize('gate,substring,reason', [
    ('fresh-false', 'verified', 'DAUPDATEONSTART=false requires a verified managed initialization'),
    ('split-role', 'all-role', 'managed startup supports only the single all-role container'),
    ('read-only-configured', 'writable', 'required managed state/runtime paths must be writable'),
    ('unwritable-runtime', 'unwritable', 'required managed path is unwritable'),
    ('unwritable-state', 'unwritable', 'required managed path is unwritable'),
    ('invalid-startup-mode', 'DAUPDATEONSTART', 'DAUPDATEONSTART must be true, initial, or false'),
    ('direct-flag-off', 'disabled', 'managed evidence exists but DA_UV_MANAGED is disabled; legacy boot refused'),
    ('corrupt-desired', 'refused', 'validation-failed'),
    ('corrupt-journal', 'refused', 'validation-failed'),
    ('empty-journal', 'corrupt', 'managed journal is corrupt or unsupported'),
    ('corrupt-applied', 'disagree', 'managed applied/initialization records disagree'),
    ('corrupt-initialization', 'migration', 'replacement-image migration or invalid initialization is unsupported in this slice'),
    ('missing-desired', 'adoption', 'required managed records are missing; legacy adoption is not implemented'),
    ('legacy-adoption-gated', 'adoption', 'existing database requires legacy adoption, which is not implemented'),
    ('image-migration-gated', 'migration', 'replacement-image migration or invalid initialization is unsupported in this slice'),
])
def test_boot_qualification_gate_refusal_detail(boot_inputs, refused_boot, monkeypatch, capsys,
                                               gate, substring, reason):
    paths = boot_inputs
    initialization = paths.state_dir / 'initialized.json'
    if gate == 'fresh-false':
        initialization.unlink()
        paths.applied_file.unlink()
        paths.journal_file.unlink()
        monkeypatch.setenv('DAUPDATEONSTART', 'false')
    elif gate == 'split-role':
        monkeypatch.setenv('CONTAINERROLE', 'web')
    elif gate == 'read-only-configured':
        monkeypatch.setenv('DAREADONLYFILESYSTEM', 'true')
    elif gate == 'unwritable-runtime':
        paths.runtime_venv.chmod(0o555)
    elif gate == 'unwritable-state':
        # Container root can close admission on a mode-read-only directory;
        # unprivileged native tests must bypass that write to reach the same gate.
        monkeypatch.setattr(controller, 'Admission', lambda _: SimpleNamespace(close=lambda *a: None))
        paths.state_dir.chmod(0o555)
    elif gate == 'invalid-startup-mode':
        monkeypatch.setenv('DAUPDATEONSTART', 'sometimes')
    elif gate == 'direct-flag-off':
        monkeypatch.setenv('DA_UV_MANAGED', 'false')
    elif gate == 'corrupt-desired':
        paths.intent_file.write_text('corrupt')
    elif gate == 'corrupt-journal':
        paths.journal_file.write_text('corrupt')
    elif gate == 'empty-journal':
        paths.journal_file.write_text('{}')
    elif gate == 'corrupt-applied':
        paths.applied_file.write_text('{}')
    elif gate == 'corrupt-initialization':
        initialization.write_text('{}')
    elif gate == 'missing-desired':
        paths.intent_file.unlink()
    elif gate == 'legacy-adoption-gated':
        for path in (initialization, paths.journal_file, paths.applied_file):
            path.unlink()
        monkeypatch.setattr(startup, 'verify_bundle', lambda *a: (True, 'verified'))
        class Cursor:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def execute(self, query):
                assert 'information_schema.tables' in query
            def fetchone(self):
                return (True,)
        class Connection:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def cursor(self):
                return Cursor()
        monkeypatch.setattr('psycopg2.connect', lambda **kw: Connection())
    elif gate == 'image-migration-gated':
        record = json.loads(initialization.read_text())
        record['target_id'] = 'different-image'
        initialization.write_text(json.dumps(record))
    try:
        assert controller.boot(paths) == 1
    finally:
        paths.state_dir.chmod(0o755)
        paths.runtime_venv.chmod(0o755)
    detail = json.loads(refused_boot[0])['detail']
    assert detail == 'managed boot refused: ' + reason
    assert substring in detail
    assert all(detail in value for value in refused_boot)
    status = json.loads(refused_boot[0])
    assert not status['normal_ready'] and not status['recovery_available']
    assert not (paths.run_dir / 'ready').exists()
    logs = capsys.readouterr()
    for value in [*refused_boot, logs.out, logs.err]:
        assert SECRET not in value
        assert str(paths.state_dir.parent) not in value


@pytest.mark.parametrize('condition,error_type,reason', [
    ('bootstrap', startup.BootstrapValidationError, 'independent configuration/infrastructure bootstrap failed'),
    ('uv-binary', startup.PinnedUvBinaryError, 'pinned uv binary does not match image inventory'),
    ('uv-executable', startup.PinnedUvExecutableError, 'pinned uv executable does not match image inventory'),
    ('executor-interpreter', startup.ExecutorInterpreterError, 'executor interpreter does not match immutable target'),
    ('host', startup.HostTargetError, 'host does not match immutable managed target'),
    ('runtime-interpreter', startup.RuntimeInterpreterError, 'runtime interpreter does not match immutable target'),
])
def test_validate_startup_owned_target_failures(boot_inputs, monkeypatch, condition, error_type, reason):
    context = controller.load_context(boot_inputs)
    if condition == 'bootstrap':
        context.infra_checks = [Check('postgres', False, SECRET)]
    elif condition == 'uv-binary':
        monkeypatch.setattr(startup, 'sha256_file', lambda _: SECRET)
    elif condition == 'uv-executable':
        hashes = iter(['uv-hash', SECRET])
        monkeypatch.setattr(startup, 'sha256_file', lambda _: next(hashes))
    elif condition == 'executor-interpreter':
        context.identity['python_version'] = SECRET
    elif condition == 'host':
        context.identity['machine'] = SECRET
    elif condition == 'runtime-interpreter':
        target = boot_inputs.runtime_venv / SECRET
        target.touch()
        (boot_inputs.runtime_venv / 'bin').mkdir()
        (boot_inputs.runtime_venv / 'bin/python').symlink_to(target)
    with pytest.raises(error_type) as raised:
        startup.validate_startup(context)
    assert str(raised.value) == reason
    assert controller.safe_boot_failure(raised.value) == 'managed boot refused: ' + reason


@pytest.mark.parametrize('error_type', [*startup.STARTUP_FAILURE_MESSAGES, startup.StartupError,
                                       ValueError, KeyError, TypeError, AttributeError])
def test_boot_exception_secret_never_appears_in_status_or_logs(
        boot_inputs, refused_boot, monkeypatch, capsys, caplog, error_type):
    def load(paths):
        config = controller.load_config(paths.config_file)
        raise error_type(config['db']['password'] + ' ' + str(paths.config_file))
    monkeypatch.setattr(controller, 'load_context', load)
    assert controller.boot(boot_inputs) == 1
    expected = startup.STARTUP_FAILURE_MESSAGES.get(error_type, 'validation-failed')
    assert json.loads(refused_boot[0])['detail'] == 'managed boot refused: ' + expected
    logs = capsys.readouterr()
    for value in [*refused_boot, logs.out, logs.err, caplog.text]:
        assert SECRET not in value
        assert str(boot_inputs.config_file) not in value


def test_load_context_caught_configuration_exception_never_logs_secret(
        boot_inputs, refused_boot, monkeypatch, capsys, caplog):
    from docassemble_executor.config import load_config
    def invalid_config(path):
        config = load_config(path)
        raise ValueError(config['db']['password'] + ' ' + str(path))
    monkeypatch.setattr(controller, 'load_config', invalid_config)
    assert controller.boot(boot_inputs) == 1
    logs = capsys.readouterr()
    assert 'managed boot refused: validation-failed' in logs.out
    assert json.loads(refused_boot[0])['detail'] == 'managed boot refused: independent configuration/infrastructure bootstrap failed'
    for value in [*refused_boot, logs.out, logs.err, caplog.text]:
        assert SECRET not in value
        assert str(boot_inputs.config_file) not in value


def test_boot_unknown_exception_and_unknown_owned_subclass_stay_generic():
    class UnexpectedError(Exception):
        pass
    class UnexpectedStartupModeError(startup.StartupModeError):
        pass
    for error in (UnexpectedError(SECRET), UnexpectedStartupModeError(SECRET),
                  ValueError(startup.STARTUP_FAILURE_MESSAGES[startup.StartupModeError])):
        assert controller.safe_boot_failure(error) == 'managed boot refused: validation-failed'


@pytest.mark.parametrize('error,reason', [
    (OSError(SECRET), 'input-unavailable'),
    (subprocess.CalledProcessError(1, SECRET, output=SECRET, stderr=SECRET), 'subprocess-failed'),
])
def test_boot_existing_failure_categories_remain_fixed(error, reason):
    assert controller.safe_boot_failure(error) == 'managed boot refused: ' + reason


@pytest.mark.parametrize('failure', ['mode', 'user', 'temporary-file'])
def test_startup_unwritable_error_values_exclude_paths_and_users(tmp_path, monkeypatch, failure):
    directory = tmp_path / SECRET
    directory.mkdir()
    user = None
    if failure == 'mode':
        directory.chmod(0o555)
    elif failure == 'user':
        user = SECRET
        monkeypatch.setattr(startup.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1))
    else:
        def unavailable(*a, **k):
            raise OSError(SECRET)
        monkeypatch.setattr(startup.tempfile, 'TemporaryFile', unavailable)
    try:
        with pytest.raises(startup.UnwritablePathError) as raised:
            startup._check_writable(directory, user)
        assert str(raised.value) == 'required managed path is unwritable'
        assert controller.safe_boot_failure(raised.value) == 'managed boot refused: required managed path is unwritable'
    finally:
        directory.chmod(0o755)
