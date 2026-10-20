"""Managed startup policy; no application imports or dependency resolution."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sysconfig
import tomllib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .bundles import sha256_file, verify_bundle
from .inventory import verify_environment
from .journal import Operation, PHASE_IDLE, PHASES
from .state import _atomic_write_json
from .uvsync import sync_frozen

if TYPE_CHECKING:
    from .controller import Context


class StartupError(ValueError):
    """Startup must stay closed, not fall through to legacy initialization."""


class ManagedModeError(StartupError):
    """Managed mode is disabled."""


class StartupModeError(StartupError):
    """The startup policy is unsupported."""


class UnverifiedInitializationError(StartupError):
    """Retaining a fresh installation is forbidden."""


class InitializationMigrationError(StartupError):
    """The initialization identity requires migration."""


class UnwritablePathError(StartupError):
    """A required managed directory cannot be written."""


class ContainerRoleError(StartupError):
    """Only an all-role container is supported."""


class ReadOnlyFilesystemError(StartupError):
    """Managed boot requires a writable filesystem."""


class CorruptJournalError(StartupError):
    """The managed lifecycle journal is invalid."""


class ManagedAdoptionError(StartupError):
    """Required managed records need explicit legacy adoption."""


class LegacyDatabaseAdoptionError(StartupError):
    """An existing database requires explicit legacy adoption."""


class AppliedInitializationError(StartupError):
    """The applied and initialization records disagree."""


class BootstrapValidationError(StartupError):
    """Independent configuration or infrastructure is unavailable."""


class PinnedUvBinaryError(StartupError):
    """The pinned uv binary does not validate."""


class PinnedUvExecutableError(StartupError):
    """The pinned uv executable does not validate."""


class ExecutorInterpreterError(StartupError):
    """The executor interpreter differs from the image target."""


class HostTargetError(StartupError):
    """The host platform differs from the image target."""


class RuntimeInterpreterError(StartupError):
    """The runtime interpreter differs from the image target."""


# Only these exact owned types have anonymous diagnostics. Their exception
# values (including values supplied by callers) are never used for reporting.
STARTUP_FAILURE_MESSAGES: dict[type[Exception], str] = {
    ManagedModeError: 'managed evidence exists but DA_UV_MANAGED is disabled; legacy boot refused',
    StartupModeError: 'DAUPDATEONSTART must be true, initial, or false',
    UnverifiedInitializationError: 'DAUPDATEONSTART=false requires a verified managed initialization',
    InitializationMigrationError: 'replacement-image migration or invalid initialization is unsupported in this slice',
    UnwritablePathError: 'required managed path is unwritable',
    ContainerRoleError: 'managed startup supports only the single all-role container',
    ReadOnlyFilesystemError: 'required managed state/runtime paths must be writable',
    CorruptJournalError: 'managed journal is corrupt or unsupported',
    ManagedAdoptionError: 'required managed records are missing; legacy adoption is not implemented',
    LegacyDatabaseAdoptionError: 'existing database requires legacy adoption, which is not implemented',
    AppliedInitializationError: 'managed applied/initialization records disagree',
    BootstrapValidationError: 'independent configuration/infrastructure bootstrap failed',
    PinnedUvBinaryError: 'pinned uv binary does not match image inventory',
    PinnedUvExecutableError: 'pinned uv executable does not match image inventory',
    ExecutorInterpreterError: 'executor interpreter does not match immutable target',
    HostTargetError: 'host does not match immutable managed target',
    RuntimeInterpreterError: 'runtime interpreter does not match immutable target',
}


def managed_requested(enabled: bool, state_dir: Path) -> bool:
    evidence = any((state_dir / name).exists() or (state_dir / name).is_symlink() for name in (
        'desired.json', 'applied.json', 'lifecycle.json', 'initialized.json', 'admission.json',
        'published-boot.json', 'installation-host.json', 'cold-restore.json',
    ))
    if evidence and not enabled:
        raise ManagedModeError(STARTUP_FAILURE_MESSAGES[ManagedModeError])
    return enabled


def decide_startup(mode: str, *, initialized: bool, installed_valid: bool, pending: bool) -> str:
    if mode not in ('true', 'initial', 'false'):
        raise StartupModeError(STARTUP_FAILURE_MESSAGES[StartupModeError])
    if initialized and not installed_valid:
        return 'recover'
    if mode == 'false' and not initialized:
        raise UnverifiedInitializationError(STARTUP_FAILURE_MESSAGES[UnverifiedInitializationError])
    if not initialized or (mode == 'true' and pending):
        return 'sync'
    return 'retain'


def target_id(manifest: dict[str, Any]) -> str:
    material = {key: manifest[key] for key in ('identity', 'bundles', 'inventories', 'executor_inventory', 'uv_sha256', 'uv_binary_sha256')}
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def load_initialization(context: Context) -> dict[str, Any] | None:
    path = context.paths.state_dir / 'initialized.json'
    if not path.exists():
        return None
    receipt = json.loads(path.read_text())
    if receipt.get('format') != 1 or receipt.get('target_id') != target_id(context.manifest):
        raise InitializationMigrationError(STARTUP_FAILURE_MESSAGES[InitializationMigrationError])
    if not receipt.get('revision') or not receipt.get('bundle_hash') or not receipt.get('verified_at'):
        raise StartupError('managed initialization record is corrupt')
    return receipt


def record_initialization(context: Context) -> None:
    name = 'runtime-' + str(context.applied.revision)
    _atomic_write_json(context.paths.state_dir / 'initialized.json', {
        'format': 1, 'target_id': target_id(context.manifest),
        'revision': context.applied.revision, 'bundle_hash': context.manifest['bundles'][name],
        'verified_at': context.applied.at,
    })


def _check_writable(directory: Path, user: str | None = None) -> None:
    if not directory.is_dir() or not directory.stat().st_mode & 0o222:
        raise UnwritablePathError(STARTUP_FAILURE_MESSAGES[UnwritablePathError])
    if user:
        command = ['runuser', '-u', user, '--', '/usr/bin/test', '-w', str(directory)]
        if subprocess.run(command, check=False).returncode:
            raise UnwritablePathError(STARTUP_FAILURE_MESSAGES[UnwritablePathError])
    else:
        try:
            with tempfile.TemporaryFile(dir=directory):
                pass
        except OSError as error:
            raise UnwritablePathError(STARTUP_FAILURE_MESSAGES[UnwritablePathError]) from error


def validate_startup(context: Context) -> dict[str, Any] | None:
    """Validate durable identity/inputs before any application interpreter use."""
    paths = context.paths
    from .boot_observation import published_boot_required
    if published_boot_required(paths, context.journal):
        raise StartupError('published DB boot requires independent reconciliation; fixture boot refused')
    role = os.environ.get('CONTAINERROLE', 'all').strip(':')
    if role != 'all':
        raise ContainerRoleError(STARTUP_FAILURE_MESSAGES[ContainerRoleError])
    if os.environ.get('DAREADONLYFILESYSTEM', 'false') == 'true':
        raise ReadOnlyFilesystemError(STARTUP_FAILURE_MESSAGES[ReadOnlyFilesystemError])
    if not context.config or not all(check.ok for check in context.infra_checks):
        raise BootstrapValidationError(STARTUP_FAILURE_MESSAGES[BootstrapValidationError])
    if context.manifest.get('format') != 2:
        raise StartupError('managed image manifest is missing or unsupported; migration is not implemented')
    if context.journal.format != 1 or context.journal.phase not in PHASES:
        raise CorruptJournalError(STARTUP_FAILURE_MESSAGES[CorruptJournalError])
    for value in (context.desired.revision, context.desired.core_baseline, context.applied.revision, context.applied.core_baseline):
        if value is not None and (not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value)):
            raise StartupError('invalid managed revision identifier')
    if not context.desired.revision or not context.desired.core_baseline:
        raise ManagedAdoptionError(STARTUP_FAILURE_MESSAGES[ManagedAdoptionError])
    if context.desired.core_baseline != context.identity['core_baseline']:
        raise StartupError('selected core migration is not implemented')
    user = os.environ.get('DA_ENVIRONMENT_USER') if os.geteuid() == 0 else None
    for directory in (paths.state_dir, paths.run_dir, paths.log_dir):
        _check_writable(directory)
    _check_writable(paths.runtime_venv, user)
    _check_writable(paths.cache_dir, user)
    uv = Path(shutil.which(paths.uv_binary) or paths.uv_binary)
    if sha256_file(uv) != context.manifest['uv_sha256']:
        raise PinnedUvBinaryError(STARTUP_FAILURE_MESSAGES[PinnedUvBinaryError])
    if sha256_file(Path('/usr/local/libexec/uv-pinned')) != context.manifest['uv_binary_sha256']:
        raise PinnedUvExecutableError(STARTUP_FAILURE_MESSAGES[PinnedUvExecutableError])
    tools = context.manifest['executor_inventory']
    tooling = verify_environment(Path('/usr/share/docassemble/executor-venv'), tools['distributions'], tools['files'])
    if not tooling.ok:
        raise StartupError('independent executor inventory does not validate: ' + tooling.detail)
    if sys.version.split()[0] != context.identity['python_version']:
        raise ExecutorInterpreterError(STARTUP_FAILURE_MESSAGES[ExecutorInterpreterError])
    if (sysconfig.get_platform() != context.identity['platform']
            or platform.machine() != context.identity['machine']
            or list(platform.libc_ver()) != context.identity['libc']):
        raise HostTargetError(STARTUP_FAILURE_MESSAGES[HostTargetError])
    python = paths.runtime_venv / 'bin/python'
    if python.exists() and python.resolve() != Path(sys.executable).resolve():
        raise RuntimeInterpreterError(STARTUP_FAILURE_MESSAGES[RuntimeInterpreterError])
    if paths.journal_file.exists():
        raw_journal = json.loads(paths.journal_file.read_text())
        if raw_journal.get('format') != 1 or raw_journal.get('phase') not in PHASES:
            raise CorruptJournalError(STARTUP_FAILURE_MESSAGES[CorruptJournalError])
    receipt = load_initialization(context)
    if receipt and (not paths.journal_file.is_file() or not paths.applied_file.is_file()):
        raise ManagedAdoptionError(STARTUP_FAILURE_MESSAGES[ManagedAdoptionError])
    if receipt is None and (context.applied.revision or context.journal.phase != PHASE_IDLE):
        if not context.journal.operation or context.journal.operation.kind != 'frozen-boot':
            raise ManagedAdoptionError(STARTUP_FAILURE_MESSAGES[ManagedAdoptionError])
    if receipt:
        name = 'runtime-' + str(context.applied.revision)
        if (receipt['revision'] != context.applied.revision
                or receipt['bundle_hash'] != context.manifest['bundles'].get(name)
                or receipt['verified_at'] != context.applied.at):
            raise AppliedInitializationError(STARTUP_FAILURE_MESSAGES[AppliedInitializationError])
    return receipt


def prepare_boot(context: Context, receipt: dict[str, Any] | None) -> str:
    """Retain a verified installation or sync the exact desired image bundle.

    Safety journal decisions take priority in the caller. No resolver is called.
    """
    revision = context.applied.revision if receipt else context.desired.revision
    name = 'runtime-' + str(revision)
    expected = context.manifest['inventories'].get(name)
    if expected is None:
        raise StartupError('requested managed revision has no retained inventory')
    installed = verify_environment(context.paths.runtime_venv, expected['distributions'], expected['files'])
    action = decide_startup(
        os.environ.get('DAUPDATEONSTART', 'true'), initialized=receipt is not None,
        installed_valid=installed.ok, pending=context.desired.revision != revision,
    )
    if action == 'retain':
        ok, detail = verify_bundle(context.paths.runtime_bundle(str(revision)), context.manifest, name)
        if not ok:
            raise StartupError(detail)
    if action != 'sync':
        return action
    if receipt is None:
        # Fixture seeding is explicit authority, not automatic legacy adoption.
        import psycopg2
        try:
            with psycopg2.connect(**context.db.connect_kwargs) as connection, connection.cursor() as cursor:
                cursor.execute("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public')")
                if cursor.fetchone()[0]:
                    raise LegacyDatabaseAdoptionError(STARTUP_FAILURE_MESSAGES[LegacyDatabaseAdoptionError])
        except psycopg2.Error as error:
            raise StartupError('database cannot be checked for fresh initialization') from error
    revision = context.desired.revision
    name = 'runtime-' + str(revision)
    ok, detail = verify_bundle(context.paths.runtime_bundle(str(revision)), context.manifest, name)
    if not ok:
        raise StartupError(detail)
    project = tomllib.loads((context.paths.runtime_bundle(str(revision)) / 'pyproject.toml').read_text())
    if any(not source.get('path', '').endswith('.whl') for source in project['tool']['uv']['sources'].values()):
        raise StartupError('source-built fault fixtures are not supported for ordinary boot')
    context.journal.begin_mutation(Operation(id='boot-' + os.urandom(8).hex(), kind='frozen-boot',
                                           revision=str(revision), previous_revision=context.applied.revision))
    context.journal.save(context.paths.journal_file)
    result = sync_frozen(context.paths.runtime_bundle(str(revision)), context.paths.runtime_venv,
                         context.paths.uv_binary, context.paths.cache_dir, log_dir=context.paths.log_dir)
    if not result.ok:
        context.journal.record_failure('mutation_started', result.describe(), revision=revision)
        context.journal.save(context.paths.journal_file)
        return 'recover'
    expected = context.manifest['inventories'][name]
    installed = verify_environment(context.paths.runtime_venv, expected['distributions'], expected['files'])
    if not installed.ok:
        context.journal.record_failure('mutation_started', installed.detail, revision=revision)
        context.journal.save(context.paths.journal_file)
        return 'recover'
    # Applied/initialization are advanced only after schema and web health.
    return 'synced'
