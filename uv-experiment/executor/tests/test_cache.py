"""Safe cache maintenance: authorization, retained boundaries and dispatch exclusion."""
from dataclasses import replace
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from docassemble_executor import cache
from docassemble_executor.authority import MutationLock
from docassemble_executor.cli import build_parser
from docassemble_executor.paths import ExecutorPaths


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(cache.os, 'geteuid', lambda: 0)
    monkeypatch.setenv('DA_ENVIRONMENT_USER', 'nobody')
    monkeypatch.setattr(cache.os, 'chown', lambda *args: None)
    monkeypatch.setattr(cache, 'SUPPORTED_CACHE', tmp_path / 'cache')
    for name in cache.RETAINED_ENV_PATHS:
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / 'retained'
    root.mkdir()
    return replace(ExecutorPaths.from_env(), cache_dir=tmp_path / 'cache', da_root=root,
        state_dir=root, runtime_venv=root / 'venv', bundles_dir=root / 'bundles',
        run_dir=root / 'run', log_dir=root / 'logs', config_file=root / 'config',
        intent_file=root / 'desired.json', journal_file=root / 'lifecycle.json',
        applied_file=root / 'applied.json', status_file=root / 'status.json', manifest_file=root / 'manifest.json',
        repair_request_file=root / 'requests/repair.json')


@pytest.mark.parametrize('action,operation', [('usage', None), ('clear', 'clean'), ('prune', 'prune')])
def test_only_pinned_cache_commands_never_touch_intent_failure_approval_or_material(paths, monkeypatch, action, operation):
    files = [paths.intent_file, paths.journal_file, paths.state_dir / 'approval.json',
             paths.bundles_dir / 'lock', paths.da_root / 'uploaded.zip', paths.da_root / 'committed-PAT',
             paths.da_root / 'mandatory.whl']
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'retained evidence')
    calls = []

    def run(paths, arguments):
        calls.append(arguments)
        # Both usage and destructive commands hold the very same exclusion lock.
        with pytest.raises(BlockingIOError), MutationLock(paths.state_dir):
            pass
        if arguments == ['--version']:
            return 'uv 0.12.21 (fixture)'
        return '42' if arguments[1] == 'size' else ''

    monkeypatch.setattr(cache, '_run', run)
    result = cache.manage_cache(paths, action)
    assert result['before_bytes'] == result['after_bytes'] == 42
    assert result['budget_enforced'] is False
    assert len(calls) == (3 if operation is None else 4)
    if operation:
        assert calls[2][:2] == ['cache', operation]
    assert all(path.read_bytes() == b'retained evidence' for path in files)
    assert build_parser().parse_args(['cache', action]).action == action


def test_non_operator_refused_before_any_tool_or_state_access(paths, monkeypatch):
    monkeypatch.setattr(cache.os, 'geteuid', lambda: 33)
    monkeypatch.setattr(cache, '_run', lambda *a: pytest.fail('tool invoked'))
    with pytest.raises(cache.CacheError, match='operator/root'):
        cache.manage_cache(paths, 'clear')


@pytest.mark.parametrize('action', ['usage', 'clear', 'prune'])
def test_active_preparation_or_application_refuses_maintenance(paths, monkeypatch, action):
    monkeypatch.setattr(cache, '_run', lambda *a: pytest.fail('tool invoked'))
    with MutationLock(paths.state_dir), pytest.raises(cache.CacheError, match='active'):
        cache.manage_cache(paths, action)


@pytest.mark.parametrize('version', ['uv 0.13.1', 'uv 0.12.20', 'other 0.12.21'])
def test_tool_mismatch_never_deletes(paths, monkeypatch, version):
    calls = []
    monkeypatch.setattr(cache, '_run', lambda p, a: calls.append(a) or version)
    with pytest.raises(cache.CacheError, match='pinned uv'):
        cache.manage_cache(paths, 'clear')
    assert calls == [['--version']]


@pytest.mark.parametrize('name', cache.RETAINED_ENV_PATHS)
@pytest.mark.parametrize('relation', ['inside', 'ancestor', 'alias'])
def test_retained_store_overlap_fails_closed(paths, monkeypatch, name, relation):
    paths.cache_dir.mkdir()
    if relation == 'inside':
        root = paths.cache_dir / 'mandatory'
    elif relation == 'ancestor':
        root = paths.cache_dir.parent
    else:
        root = paths.cache_dir.parent / 'retained-alias'
        root.symlink_to(paths.cache_dir, target_is_directory=True)
    monkeypatch.setenv(name, str(root))
    with pytest.raises(cache.CacheError, match='overlaps'):
        cache.validate_cache(paths)


@pytest.mark.parametrize('relation', ['inside', 'ancestor', 'alias', 'alias-out'])
def test_effective_standalone_retained_path_is_checked_without_environment(paths, relation):
    paths.cache_dir.mkdir()
    if relation == 'inside':
        root = paths.cache_dir / 'store'
    elif relation == 'ancestor':
        root = paths.cache_dir.parent
    elif relation == 'alias':
        root = paths.da_root / 'store'
        root.symlink_to(paths.cache_dir / 'store', target_is_directory=True)
    else:
        root = paths.cache_dir / 'store'
        root.symlink_to(paths.da_root, target_is_directory=True)
    with pytest.raises(cache.CacheError, match='overlaps'):
        cache.validate_retained_paths(paths, [root])


def test_effective_standalone_retained_paths_accept_disjoint_roots_without_creating_them(paths):
    roots = [paths.da_root / name for name in ('store', 'baseline.json', 'material', 'artifacts', 'index')]
    assert cache.validate_retained_paths(paths, roots) == paths.cache_dir
    assert not any(root.exists() for root in roots)


@pytest.mark.parametrize('option', ['--store', '--image-baseline', '--material-root', '--artifact-root',
                                   '--index-credential-root'])
@pytest.mark.parametrize('alias', [False, True])
@pytest.mark.parametrize('alternate_cache', [False, True])
def test_standalone_cli_refuses_effective_overlap_before_preparation(paths, option, alias, alternate_cache):
    retained = Path('/var/cache/docassemble/uv') / 'retained'
    if alias:
        link = paths.da_root / 'retained-alias'
        link.symlink_to(retained)
        retained = link
    arguments = ['--store', str(paths.da_root / 'store'), option, str(retained), '--once']
    env = dict(os.environ, DA_MANAGED_STATE_DIR=str(paths.state_dir))
    for name in (*cache.RETAINED_ENV_PATHS, 'DA_PREPARATION_DATABASE_URL', 'UV_CACHE_DIR'):
        env.pop(name, None)
    if alternate_cache:
        env['UV_CACHE_DIR'] = '/tmp/build-cache'
    result = subprocess.run([sys.executable, '-m', 'docassemble_executor.preparation_poller', *arguments],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert 'cache overlaps retained material' in result.stderr + result.stdout
    assert 'DA_PREPARATION_DATABASE_URL' not in result.stderr
    assert not (paths.da_root / 'store').exists()


@pytest.mark.parametrize('relation', ['inside', 'equal', 'ancestor', 'alias', 'alias-out'])
def test_alternate_cache_still_protects_standard_destructive_cache(paths, monkeypatch, relation):
    standard = paths.cache_dir / 'standard'
    standard.mkdir(parents=True)
    monkeypatch.setattr(cache, 'SUPPORTED_CACHE', standard)
    alternate = replace(paths, cache_dir=paths.da_root.parent / 'build-cache')
    if relation == 'inside':
        root = standard / 'retained'
    elif relation == 'equal':
        root = standard
    elif relation == 'ancestor':
        root = standard.parent
    elif relation == 'alias':
        root = paths.da_root / 'store'
        root.symlink_to(standard / 'retained', target_is_directory=True)
    else:
        root = standard / 'retained'
        root.symlink_to(paths.da_root, target_is_directory=True)
    with pytest.raises(cache.CacheError, match='overlaps'):
        cache.validate_retained_paths(alternate, [root])
    assert not alternate.cache_dir.exists()


def test_preparation_with_alternate_cache_refuses_store_before_later_default_clear(paths, monkeypatch):
    from docassemble_executor import preparation_poller
    standard = paths.cache_dir
    alternate = paths.da_root.parent / 'build-cache'
    retained = standard / 'retained'
    monkeypatch.setenv('UV_CACHE_DIR', str(alternate))
    monkeypatch.setenv('DA_MANAGED_STATE_DIR', str(paths.state_dir))
    monkeypatch.setattr(sys, 'argv', ['preparation_poller', '--store', str(retained), '--once'])
    with pytest.raises(cache.CacheError, match='overlaps'):
        preparation_poller.main()
    assert not retained.exists()
    assert not alternate.exists()

    # A later default destructive invocation has no record of the standalone
    # --store argument. Prove it can clear the standard cache in a safe sandbox.
    monkeypatch.delenv('UV_CACHE_DIR')
    standard.mkdir()
    disposable = standard / 'disposable'
    disposable.write_text('reusable cache entry')
    calls = []

    def run(invocation_paths, arguments):
        assert invocation_paths.cache_dir == standard
        calls.append(arguments)
        if arguments == ['--version']:
            return 'uv ' + cache.UV_VERSION
        if arguments[:2] == ['cache', 'clean']:
            disposable.unlink()
            standard.rmdir()
        return '0'

    monkeypatch.setattr(cache, '_run', run)
    assert cache.manage_cache(paths, 'clear')['cache'] == str(standard)
    assert any(arguments[:2] == ['cache', 'clean'] for arguments in calls)
    assert standard.is_dir()
    assert not disposable.exists()
    assert not retained.exists()


def test_nonstandard_preparation_cache_accepts_disjoint_retained_roots(paths):
    alternate = replace(paths, cache_dir=paths.da_root.parent / 'build-cache')
    retained = paths.da_root / 'store'
    assert cache.validate_retained_paths(alternate, [retained]) == alternate.cache_dir
    assert not alternate.cache_dir.exists()
    assert not retained.exists()


def test_arbitrary_override_symlink_and_relative_retained_root_fail_closed(paths, monkeypatch):
    with pytest.raises(cache.CacheError, match='unsupported'):
        cache.validate_cache(replace(paths, cache_dir=paths.da_root))
    paths.cache_dir.symlink_to(paths.da_root, target_is_directory=True)
    with pytest.raises(cache.CacheError, match='symlink'):
        cache.validate_cache(paths)
    paths.cache_dir.unlink()
    monkeypatch.setenv('DA_DURABLE_WORK_STORE', 'relative')
    with pytest.raises(cache.CacheError, match='absolute'):
        cache.validate_cache(paths)


def test_uv_failure_is_actionable_and_redacted(paths):
    broken = paths.da_root / 'uv'
    broken.write_text('#!/bin/sh\necho https://PAT:secret@example.test\nexit 1\n')
    broken.chmod(0o755)
    with pytest.raises(cache.CacheError, match='permissions and free disk') as error:
        cache.manage_cache(replace(paths, uv_binary=str(broken)), 'clear')
    assert 'secret' not in str(error.value)


def test_cancellation_never_implicitly_cleans_cache(paths, monkeypatch):
    paths.cache_dir.mkdir()
    reusable = paths.cache_dir / 'reusable'
    reusable.write_text('still useful')

    class Interrupted:
        cancelled = False
        def wait(self, **kwargs):
            if self.cancelled:
                return 0
            raise KeyboardInterrupt
        def terminate(self):
            self.cancelled = True

    monkeypatch.setattr(cache, 'guarded_popen', lambda *a, **kw: Interrupted())
    with MutationLock(paths.state_dir), pytest.raises(cache.CacheError, match='interrupted'):
        cache._run(paths, ['cache', 'prune'])
    assert reusable.read_text() == 'still useful'


def test_preparation_dispatch_guard_retains_exclusion_after_dispatcher_death(paths, tmp_path):
    # The fresh Python worker imports this disposable stand-in, not a DB builder.
    stub = tmp_path / 'stub/docassemble_executor'
    stub.mkdir(parents=True)
    (stub / '__init__.py').write_text('')
    ready = tmp_path / 'ready'
    release = tmp_path / 'release'
    (stub / 'preparation_poller.py').write_text(
        'import os, subprocess, sys\n'
        'os.fstat(int(sys.argv[-1]))\n'
        'subprocess.Popen([sys.executable, "-c", '
        + repr('import os,time; from pathlib import Path; os.setsid(); '
               + f'Path({str(ready)!r}).touch(); '
               + f'\nwhile not Path({str(release)!r}).exists(): time.sleep(.02)')
        + '], close_fds=True)\n')
    code = ('from docassemble_executor.preparation_poller import guarded_preparation_once; '
            'from docassemble_executor.paths import ExecutorPaths; '
            'import os; '
            f'guarded_preparation_once(ExecutorPaths.from_env(), [], env=dict(os.environ, PYTHONPATH={str(stub.parent)!r}))')
    parent = subprocess.Popen([sys.executable, '-c', code], env=dict(os.environ, DA_MANAGED_STATE_DIR=str(paths.state_dir)))
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert ready.exists()
        parent.kill()
        parent.wait(timeout=5)
        with pytest.raises(BlockingIOError), MutationLock(paths.state_dir):
            pass
    finally:
        release.touch()
        if parent.poll() is None:
            parent.send_signal(signal.SIGTERM)
            parent.wait(timeout=5)
    deadline = time.monotonic() + 10
    while True:
        try:
            with MutationLock(paths.state_dir):
                break
        except BlockingIOError:
            assert time.monotonic() < deadline
            time.sleep(.02)


def test_private_preparation_flag_requires_real_lock_descriptor(paths):
    (paths.state_dir / 'mutation.lock').touch()
    # The standalone entrypoint validates the effective cache root first, so give
    # the child the supported root and let the authority check be the refusal.
    env = dict(os.environ, DA_MANAGED_STATE_DIR=str(paths.state_dir))
    env.pop('UV_CACHE_DIR', None)
    result = subprocess.run([sys.executable, '-m', 'docassemble_executor.preparation_poller',
                             '--store', str(paths.da_root), '--guarded-once', '0'],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert 'preparation mutation authority mismatch' in result.stderr


def test_locked_standalone_refuses_unqualified_docker_daemon_workers(paths):
    result = subprocess.run([sys.executable, '-m', 'docassemble_executor.preparation_poller',
                             '--store', str(paths.da_root), '--worker-image', 'fixture'],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert 'Docker daemon worker exclusion is not qualified' in result.stderr


def test_startup_provisions_only_inert_mutation_lock_for_environment_group(paths, monkeypatch):
    import pwd
    from docassemble_executor import paths as paths_module
    account = pwd.getpwnam('nobody')
    changes = []
    monkeypatch.setattr(paths_module.os, 'chown', lambda *args: changes.append(args))
    paths.ensure_directories()
    lock = paths.state_dir / 'mutation.lock'
    assert lock.stat().st_mode & 0o777 == 0o660
    assert (lock, 0, account.pw_gid) in changes
    assert paths.state_dir.stat().st_mode & 0o777 == 0o755


def test_failure_guidance_never_promises_cache_can_replace_missing_sources():
    from docassemble_executor.uvsync import SyncResult
    failure = SyncResult(False, ['uv', 'sync', '--frozen'], 2, .1, output_tail='locked source unavailable')
    assert 'Restore access and explicitly retry the same revision' in failure.describe()
    assert 'prepare/approve a repair' in failure.describe()
    assert 'never authorizes a new solve' in failure.describe()


def test_both_operator_preparation_entrypoints_use_guard():
    from docassemble_executor import durable_work, preparation_poller
    assert 'guarded_preparation_once(paths, preparation_arguments' in Path(durable_work.__file__).read_text()
    assert 'guarded_preparation_once(paths, arguments)' in Path(preparation_poller.__file__).read_text()
