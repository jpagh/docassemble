"""Exercise frozen sync through its external executable boundary."""

import tempfile
from pathlib import Path

import pytest

from docassemble_executor.authority import MutationLock
from docassemble_executor.uvsync import sync_frozen


@pytest.fixture(autouse=True)
def authority(tmp_path, monkeypatch):
    monkeypatch.setenv('DA_MANAGED_STATE_DIR', str(tmp_path))
    from docassemble.base.managed_python import python_requires
    import json
    (tmp_path / 'pyproject.toml').write_text('[project]\nrequires-python=' + json.dumps(python_requires()))
    (tmp_path / 'uv.lock').write_text('requires-python=' + json.dumps(python_requires()))
    with MutationLock(tmp_path):
        yield


def test_recovery_forces_reinstallation_even_if_metadata_says_installed(tmp_path):
    # The executable boundary must remain traversable when root correctly
    # drops to the management account. pytest's root tmp tree is private.
    with tempfile.TemporaryDirectory() as directory:
        tools = Path(directory)
        tools.chmod(0o755)
        uv = tools / "uv"
        uv.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n")
        uv.chmod(0o755)
        logs = tmp_path / "logs"
        result = sync_frozen(tmp_path, tmp_path / "venv", str(uv), tmp_path / "cache", logs, reinstall=True)
    assert result.ok, result.describe()
    # Observe what the executable receives, not its privilege wrapper or the
    # controller's private staging path.
    arguments = result.output_tail.splitlines()
    assert arguments[:3] == ["sync", "--frozen", "--project"]
    assert arguments[4:] == ["--no-build", "--reinstall"]


def test_unavailable_tooling_reports_failure_instead_of_crashing(tmp_path):
    result = sync_frozen(tmp_path, tmp_path / 'venv', str(tmp_path / 'missing-uv'), tmp_path / 'cache')
    assert not result.ok
    assert 'missing-uv' in result.describe()


def test_sync_env_uses_independent_python_and_forbids_downloads(monkeypatch, tmp_path):
    import sys
    from docassemble_executor.uvsync import build_sync_env
    monkeypatch.setenv('UV_PYTHON', 'foreign-system-python')
    env = build_sync_env(tmp_path / 'runtime', tmp_path / 'cache')
    assert env['UV_PYTHON'] == sys.executable
    assert env['UV_PYTHON_DOWNLOADS'] == 'never'


def test_real_pinned_uv_old_lock_refusal_and_new_target_frozen_sync(tmp_path):
    import hashlib
    import json
    import os
    import shutil
    import subprocess
    import sys
    from docassemble.base.managed_python import python_requires
    from docassemble_executor.bundles import verify_python_target
    assert sys.version_info[:2] == (3, 14), 'run real executable regression on the selected image target'
    binary = shutil.which(os.environ.get('UV_BINARY', 'uv'))
    assert binary is not None
    assert subprocess.check_output([binary, '--version'], text=True).split()[:2] == ['uv', '0.12.21']
    project, lock = tmp_path / 'pyproject.toml', tmp_path / 'uv.lock'
    env = dict(os.environ, UV_PYTHON=sys.executable, UV_PYTHON_DOWNLOADS='never',
               UV_OFFLINE='1', UV_NO_CONFIG='1', UV_PROJECT_ENVIRONMENT=str(tmp_path / 'runtime'),
               UV_CACHE_DIR=str(tmp_path / 'cache'))
    def header(requirement):
        return '[project]\nname="target-proof"\nversion="0"\nrequires-python=' + json.dumps(requirement) + '\ndependencies=[]\n[tool.uv]\npackage=false\n'
    def hashes():
        return [hashlib.sha256(p.read_bytes()).hexdigest() for p in (project, lock)]
    project.write_text(header('==3.12.*'))
    lock.write_text('version=1\nrevision=3\nrequires-python="==3.12.*"\n[[package]]\nname="target-proof"\nversion="0"\nsource={virtual="."}\n')
    before = hashes()
    command = [binary, 'sync', '--frozen', '--no-build', '--offline', '--no-config',
               '--python', sys.executable, '--project', str(tmp_path)]
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert hashes() == before
    assert not (tmp_path / 'runtime/bin/python').exists()
    assert command[1] == 'sync' and '--frozen' in command and '--no-build' in command
    # Frozen alone would not check freshness. New project/old lock is preflight-refused.
    project.write_text(header(python_requires()))
    before = hashes()
    assert not verify_python_target(project, lock)
    assert hashes() == before
    lock.unlink()
    subprocess.run([binary, 'lock', '--offline', '--no-config', '--python', sys.executable,
                    '--project', str(tmp_path)], env=env, check=True, capture_output=True)
    assert verify_python_target(project, lock)
    before = hashes()
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert hashes() == before
    assert (tmp_path / 'runtime/bin/python').resolve() == __import__('pathlib').Path(sys.executable).resolve()


@pytest.mark.parametrize('fault', ['old', 'missing', 'malformed', 'project-depth', 'lock-depth'])
def test_sync_frozen_foreign_fake_bundle_has_no_staging_lock_or_mutation(tmp_path, monkeypatch, fault):
    import docassemble_executor.uvsync as module
    bundle = tmp_path / 'foreign'
    bundle.mkdir()
    if fault == 'old':
        (bundle / 'pyproject.toml').write_text('[project]\nrequires-python="==3.12.*"')
        (bundle / 'uv.lock').write_text('requires-python="==3.12.*"')
    elif fault == 'malformed':
        (bundle / 'pyproject.toml').write_text('[')
        (bundle / 'uv.lock').write_text('[')
    elif fault.endswith('-depth'):
        import json
        from docassemble.base.managed_python import python_requires
        (bundle / 'pyproject.toml').write_text('[project]\nrequires-python=' + json.dumps(python_requires()))
        (bundle / 'uv.lock').write_text('requires-python=' + json.dumps(python_requires()))
        path = bundle / ('pyproject.toml' if fault == 'project-depth' else 'uv.lock')
        path.write_text(path.read_text() + '\nunused=' + '[' * 2000 + '0' + ']' * 2000)
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    sentinel = runtime / 'stock-or-managed-sentinel'
    sentinel.write_bytes(b'unchanged')
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob('*'))
    for name in ('RuntimeLock', 'guarded_popen'):
        monkeypatch.setattr(module, name, lambda *a, **k: pytest.fail('target refusal must precede locks/processes'))
    monkeypatch.setattr(module.tempfile, 'TemporaryDirectory', lambda *a, **k: pytest.fail('target refusal must precede staging'))
    result = sync_frozen(bundle, runtime, '/never-run-uv', tmp_path / 'cache')
    assert result.ok is False and result.returncode is None and result.duration_seconds == 0.0
    assert result.output_tail == 'retained project and lock Python target mismatch'
    assert sentinel.read_bytes() == b'unchanged'
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob('*')) == before


def test_startup_old_runtime_lock_returns_recover_with_failed_mutation_latch(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from docassemble_executor import startup
    from docassemble_executor.paths import ExecutorPaths
    from docassemble_executor.state import AppliedState, DesiredState
    from docassemble_executor.journal import Journal
    from dataclasses import replace
    paths = replace(ExecutorPaths.from_env(), bundles_dir=tmp_path / 'bundles',
                    runtime_venv=tmp_path / 'runtime', journal_file=tmp_path / 'journal.json')
    bundle = paths.runtime_bundle('r1')
    bundle.mkdir(parents=True)
    (bundle / 'pyproject.toml').write_text('[project]\nrequires-python="==3.12.*"\n[tool.uv.sources]\n')
    (bundle / 'uv.lock').write_text('requires-python="==3.12.*"')
    context = SimpleNamespace(paths=paths, manifest={'inventories': {'runtime-r1': {'distributions': {}, 'files': {}}}},
        applied=AppliedState('r1', 'r1'), desired=DesiredState('r1', 'r1'), journal=Journal())
    monkeypatch.setattr(startup, 'verify_environment', lambda *a: SimpleNamespace(ok=True))
    monkeypatch.setattr(startup, 'verify_bundle', lambda *a: (True, 'digest verified'))
    monkeypatch.setattr(startup, 'decide_startup', lambda *a, **k: 'sync')
    assert startup.prepare_boot(context, {'fixture': 'initialized'}) == 'recover'
    journal = Journal.load(paths.journal_file)
    assert journal.phase == 'mutation_started' and journal.failure
    assert not paths.runtime_venv.exists()


@pytest.mark.parametrize('fault', ['old', 'project-depth', 'lock-depth'])
def test_controller_old_image_core_lock_latches_recovery_failed(tmp_path, monkeypatch, fault):
    from types import SimpleNamespace
    from docassemble_executor import controller
    from docassemble_executor.paths import ExecutorPaths
    from docassemble_executor.journal import Journal
    from dataclasses import replace
    paths = replace(ExecutorPaths.from_env(), bundles_dir=tmp_path / 'bundles', runtime_venv=tmp_path / 'runtime')
    bundle = paths.core_bundle('r1')
    bundle.mkdir(parents=True)
    (bundle / 'pyproject.toml').write_text('[project]\nrequires-python="==3.12.*"')
    (bundle / 'uv.lock').write_text('requires-python="==3.12.*"')
    if fault != 'old':
        import json
        from docassemble.base.managed_python import python_requires
        (bundle / 'pyproject.toml').write_text('[project]\nrequires-python=' + json.dumps(python_requires()))
        (bundle / 'uv.lock').write_text('requires-python=' + json.dumps(python_requires()))
        path = bundle / ('pyproject.toml' if fault == 'project-depth' else 'uv.lock')
        path.write_text(path.read_text() + '\nunused=' + '[' * 2000 + '0' + ']' * 2000)
    context = SimpleNamespace(paths=paths, journal=Journal(), identity={'core_baseline': 'r1'},
        manifest={'inventories': {'core-r1': {}}}, bundle_name=lambda kind, rev: kind + '-' + rev)
    monkeypatch.setattr(controller, 'verify_bundle', lambda *a: (True, 'digest verified'))
    for name in ('_persist', 'stop_web', '_remove_ready', '_write_status', '_record_error_notification'):
        monkeypatch.setattr(controller, name, lambda *a, **k: None)
    assert controller._restore_core(context, 'failed runtime', serve=False) == 'maintenance'
    assert context.journal.phase == 'recovery_failed' and context.journal.recovery_attempted
    assert not paths.runtime_venv.exists()
