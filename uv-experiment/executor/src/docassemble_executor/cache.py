"""Operator-only maintenance of the supported disposable uv cache, never history GC."""
from __future__ import annotations

from collections.abc import Iterable
import json
import os
from pathlib import Path
import pwd
import subprocess
import tempfile

from .authority import MutationLock, guarded_popen, mutation_fd
from .paths import ExecutorPaths
from .registry_builder import UV_VERSION

SUPPORTED_CACHE = Path('/var/cache/docassemble/uv')
RETAINED_ENV_PATHS = (
    'DA_DURABLE_WORK_STORE', 'DA_QUALIFIED_IMAGE_BASELINE',
    'DA_MANAGED_ARTIFACT_ROOT', 'DA_EXECUTOR_ARTIFACT_ROOT',
    'DA_MANAGED_SECRET_ROOT', 'DA_INDEX_CREDENTIAL_ROOT',
)


class CacheError(RuntimeError):
    """Fixed, operator-actionable diagnostics without source credentials."""


def validate_retained_paths(paths: ExecutorPaths, retained_paths: Iterable[Path] = ()) -> Path:
    """Reject disposable-cache overlap before a caller creates retained material.

    Standalone entrypoints must pass their effective CLI locations as well as
    the configured environment roots. Retained material must be disjoint from
    both the effective cache and the standard destructive cache, including when
    preparation uses an alternate cache. This check does not create any paths.
    """
    cache = paths.cache_dir
    if not cache.is_absolute():
        raise CacheError('cache root must be absolute; inspect operator configuration')
    if any(part.is_symlink() for part in (cache, *cache.parents)):
        raise CacheError('cache path contains a symlink; restore a dedicated cache directory')
    retained = [paths.da_root, paths.runtime_venv, paths.bundles_dir, paths.state_dir,
                paths.run_dir, paths.log_dir, paths.config_file, paths.intent_file,
                paths.journal_file, paths.applied_file, paths.status_file, paths.manifest_file,
                Path('/var/lib/docassemble'), Path('/var/lib/docassemble-build')]
    retained += list(retained_paths)
    retained += [Path(os.environ[name]) for name in RETAINED_ENV_PATHS if os.environ.get(name)]
    if os.environ.get('DA_QUALIFIED_IMAGE_BASELINE'):
        retained.append(Path(os.environ['DA_QUALIFIED_IMAGE_BASELINE']).parent)
    for root in retained:
        if not root.is_absolute():
            raise CacheError('retained paths must be absolute; inspect operator configuration')
        # Resolve retained paths too: an alias into the cache must fail closed.
        try:
            resolved = root.resolve()
        except (OSError, RuntimeError):
            raise CacheError('retained path cannot be resolved; inspect operator configuration') from None
        if any(disposable == path or disposable in path.parents or path in disposable.parents
               for disposable in (cache, SUPPORTED_CACHE) for path in (root, resolved)):
            raise CacheError('cache overlaps retained material; separate cache and retained stores')
    if cache.exists() and not cache.is_dir():
        raise CacheError('cache root is not a directory')
    return cache


def validate_cache(paths: ExecutorPaths) -> Path:
    if paths.cache_dir != SUPPORTED_CACHE:
        raise CacheError('unsupported cache root; maintenance requires /var/cache/docassemble/uv')
    cache = validate_retained_paths(paths)
    descriptor = os.environ.get('DA_QUALIFIED_IMAGE_BASELINE')
    if descriptor:
        from .image_baseline import validate_image_wheel_paths
        from .preparation import PreparationError
        try:
            validate_image_wheel_paths(Path(descriptor).parent)
        except (OSError, PreparationError):
            raise CacheError('retained image wheel layout is unsafe; restore dedicated wheel files') from None
    return cache


def _run(paths: ExecutorPaths, arguments: list[str]) -> str:
    # No inherited UV/PIP overrides, config discovery, project selection or index
    # credentials. The guardian retains exclusion if this operator process dies.
    user = os.environ.get('DA_ENVIRONMENT_USER')
    if not user:
        raise CacheError('cache maintenance requires a dedicated DA_ENVIRONMENT_USER')
    account = pwd.getpwnam(user)
    if account.pw_uid == 0:
        raise CacheError('cache maintenance requires a non-root environment owner')
    env = {'PATH': os.environ.get('PATH', ''), 'UV_PYTHON_DOWNLOADS': 'never', 'HOME': account.pw_dir}
    # Pinned uv clean removes the cache directory itself. Its parent is
    # root-owned in the image, so maintenance (which never runs build hooks)
    # needs operator privilege; reconstruct ownership before releasing the lock.
    command = [paths.uv_binary, *arguments]
    if arguments[:2] != ['cache', 'clean']:
        command = ['runuser', '-u', user, '--', *command]
    with tempfile.TemporaryFile() as output:
        process = guarded_popen(command, mutation_fd(), env=env, stdout=output, stderr=subprocess.STDOUT)
        try:
            process.wait(timeout=600)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass  # Guardian still excludes mutation until all descendants exit.
            raise CacheError('cache command interrupted; inspect usage and retry explicitly') from None
        if process.returncode:
            raise CacheError('pinned uv cache command failed; inspect cache permissions and free disk space')
        output.seek(0)
        return output.read().decode().strip()


def manage_cache(paths: ExecutorPaths, action: str) -> dict:
    if os.geteuid() != 0:
        raise CacheError('cache maintenance requires the operator/root executor')
    if action not in ('usage', 'clear', 'prune'):
        raise CacheError('unsupported cache action')
    try:
        with MutationLock(paths.state_dir):
            cache = validate_cache(paths)
            if _run(paths, ['--version']).split()[:2] != ['uv', UV_VERSION]:
                raise CacheError('cache maintenance requires pinned uv ' + UV_VERSION)
            options = ['--cache-dir', str(cache), '--no-config', '--offline', '--preview-features', 'cache-size']
            before = int(_run(paths, ['cache', 'size', '--output-format', 'machine', *options]))
            if action != 'usage':
                try:
                    _run(paths, ['cache', 'clean' if action == 'clear' else 'prune', *options])
                finally:
                    # uv clean removes the root, not just its contents. A failed
                    # or interrupted clear must not leave sync unable to cache.
                    account = pwd.getpwnam(os.environ['DA_ENVIRONMENT_USER'])
                    cache.mkdir(mode=0o755, parents=False, exist_ok=True)
                    os.chown(cache, account.pw_uid, account.pw_gid)
            after = int(_run(paths, ['cache', 'size', '--output-format', 'machine', *options]))
            return {'cache': str(cache), 'uv': UV_VERSION, 'action': action,
                    'before_bytes': before, 'after_bytes': after,
                    'budget_enforced': False, 'measurement': 'uv cache size (not a filesystem quota)'}
    except BlockingIOError:
        raise CacheError('preparation/application or cache maintenance active; retry after it exits') from None
    except (OSError, ValueError, KeyError):
        raise CacheError('cache inspection failed; verify pinned uv, cache paths and permissions') from None


def cache_command(paths: ExecutorPaths, action: str) -> int:
    try:
        print(json.dumps(manage_cache(paths, action), sort_keys=True))
        return 0
    except CacheError as error:
        print(str(error))
        return 1
