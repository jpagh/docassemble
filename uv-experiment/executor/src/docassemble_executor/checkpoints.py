"""Opt-in content-backed checkpoints, separate from ordinary nightly rotation.

Caller owns MutationLock, has drained consumers, and holds RuntimeLock. These
locks exclude cooperating application writers, not arbitrary operator DB/file
edits. No restore or data rewind is performed here.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import time
import uuid

from .alembic_probe import read_database_revision
from .config import database_settings, load_config
from .inventory import verify_environment
from .managed_backup import (ManagedBackupError, ObjectCopy, _sha, _sync_directory,
                             create_backup, verify_backup)
from .state import _atomic_write_json


def backup_inputs(environment_file: Path) -> dict:
    fd = os.open(environment_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ManagedBackupError('trusted backup environment required')
        inputs = json.load(handle)
    if not isinstance(inputs, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in inputs.items()):
        raise ManagedBackupError('invalid backup environment')
    require_complete_backup(inputs, None)
    return inputs


def require_complete_backup(inputs: dict, config: dict | None) -> None:
    # None is only the initial trusted-mapping validation before config is read.
    authoritative = config is not None
    config = config or {}
    # Mirror read_config's enable/default rules without importing the runtime.
    s3, azure = config.get('s3', {}), config.get('azure', {})
    live_s3 = s3.get('enable', s3.get('bucket') is not None)
    live_azure = azure.get('enable', azure.get('container') is not None and azure.get('account name') is not None)
    selected_s3 = inputs.get('S3ENABLE', 'false') == 'true'
    selected_azure = inputs.get('AZUREENABLE', 'false') == 'true'
    actual_provider = 's3' if live_s3 else 'azure' if live_azure else None
    selected_provider = 's3' if selected_s3 else 'azure' if selected_azure else None
    if authoritative and actual_provider != selected_provider:
        raise ManagedBackupError('cloud checkpoint configuration mismatch')
    if ((live_s3 and (not selected_s3 or inputs.get('S3BUCKET') != s3.get('bucket'))) or
            (not live_s3 and live_azure and (selected_s3 or not selected_azure or inputs.get('AZURECONTAINER') != azure.get('container') or
                inputs.get('AZURE_STORAGE_ACCOUNT') != azure.get('account name')))):
        raise ManagedBackupError('cloud checkpoint configuration mismatch')
    if live_s3:
        encryption = s3.get('server side encryption', {})
        for value, key in ((s3.get('endpoint url'), 'S3ENDPOINTURL'),
                           (encryption.get('algorithm'), 'S3_SSE_ALGORITHM'),
                           (encryption.get('customer algorithm'), 'S3_SSE_CUSTOMER_ALGORITHM'),
                           (encryption.get('customer key'), 'S3_SSE_CUSTOMER_KEY'),
                           (encryption.get('KMS key ID'), 'S3_SSE_KMS_KEY_ID')):
            if (value or None) != (inputs.get(key) if inputs.get(key) not in ('', 'null') else None):
                raise ManagedBackupError('cloud checkpoint configuration mismatch')
    if not live_s3 and live_azure:
        connection = azure.get('connection string')
        if connection and inputs.get('AZURE_STORAGE_CONNECTION_STRING') != connection:
            raise ManagedBackupError('cloud checkpoint connection configuration unverified')
        if not connection and inputs.get('AZURE_STORAGE_CONNECTION_STRING') not in (None, '', 'null'):
            # A connection string would override the checked account name; reject
            # it when the active configuration did not specify one.
            raise ManagedBackupError('cloud checkpoint connection configuration unverified')
        if not connection and (azure.get('endpoint suffix', 'core.windows.net') != 'core.windows.net' or
                               azure.get('endpoints protocol', 'https') != 'https' or azure.get('key vault name')):
            raise ManagedBackupError('cloud checkpoint connection configuration unverified')
    if selected_s3 or selected_azure:
        from .cloud_checkpoints import CloudCheckpoint
        CloudCheckpoint(inputs)  # Validate the trusted destination before capture.
    def days(value):
        try:
            converted = int(value)
            return converted if converted >= 0 else 14
        except (TypeError, ValueError, OverflowError):
            return 14  # read_config's supported numeric conversion/default
    if (days(inputs.get('DABACKUPDAYS', '14')) == 0 or inputs.get('BACKUPFILESTORAGE', 'true') != 'true' or
            inputs.get('DAREADONLYFILESYSTEM', 'false') != 'false' or inputs.get('DBBACKUP', 'true') != 'true' or
            days(config.get('backup days', 14)) == 0 or not config.get('backup file storage', True) or
            not config.get('db', {}).get('backup', True) or config.get('read only file system', False)):
        raise ManagedBackupError('complete backups disabled')


def require_supported_uploads(config: dict, paths) -> None:
    # Restore currently supports only the platform files tree. Do not certify a
    # checkpoint of an unused tree when the application writes elsewhere.
    uploads = config.get('uploads', '/usr/share/docassemble/files')
    if (not isinstance(uploads, str) or not Path(uploads).is_absolute() or
            Path(uploads) != paths.da_root / 'files' or
            any(part.is_symlink() for part in (Path(uploads), *Path(uploads).parents))):
        raise ManagedBackupError('effective upload root unsupported for complete checkpoint')


def verify_checkpoint(directory: Path, prefix: str, *, manifest_name: str = 'checkpoint.json') -> dict:
    document = json.loads((directory / manifest_name).read_text())
    uuid.UUID(document['id'])
    if any(Path(name).is_absolute() or any(part in ('', '.', '..') for part in name.split('/')) for name in document['files']):
        raise ManagedBackupError('unsafe checkpoint object path')
    managed = verify_backup(directory / 'database', directory / 'database.managed', prefix)
    if (not managed.get('checkpoint_only') or document.get('complete') is not True or
            document.get('format') != 1 or document['dump_sha256'] != managed['dump_sha256']):
        raise ManagedBackupError('checkpoint binding incomplete')
    present = {p.relative_to(directory / 'objects').as_posix() for p in (directory / 'objects').rglob('*') if not p.is_dir()}
    if present != set(document['files']) or any(_sha(directory / 'objects' / name) != row['sha256']
                                               for name, row in document['files'].items()):
        raise ManagedBackupError('checkpoint local files incomplete')
    from .cloud_checkpoints import verify_cloud_files
    verify_cloud_files(document)
    if document['restore'] != {'protocol': 'full-checkpoint-v1', 'operator_only': True,
            'desired_revision_id': document['pre_change']['runtime'].get('revision_id'),
            'suppress_attempt_ids': [document['operation']['attempt_id']],
            'invalidate_unpublished_candidates': True, 'replay_pending_work': False,
            'requires_data_loss_acknowledgement': True, 'external_side_effects_undone': False}:
        raise ManagedBackupError('unsafe checkpoint restore metadata')
    return document


def capture(service, paths, claim, *, store: Path, baseline: Path, inputs: dict,
            artifact_roots: list[Path], secret_root: Path | None = None,
            index_root: Path | None = None) -> dict:
    """New identity per capture; incomplete directories remain explicit evidence.

    DABACKUPDAYS=0 disables dated backup capture; positive values are recorded but
    operation-linked checkpoints never join automatic dated-directory expiry.
    """
    uuid.UUID(claim.attempt_id)
    started = time.monotonic()
    identity = str(uuid.uuid4())
    status_file = paths.state_dir / 'checkpoints' / (claim.attempt_id + '.json')
    status = {'id': identity, 'attempt_id': claim.attempt_id, 'state': 'capturing',
              'complete': False, 'known_good': False, 'duration_seconds': None,
              'created_at': datetime.now(timezone.utc).isoformat(),
              'retention': 'operator-pruned-no-auto-deletion'}
    _atomic_write_json(status_file, status)
    try:
        config = load_config(paths.config_file)
        require_complete_backup(inputs, config)
        require_supported_uploads(config, paths)
        from .cloud_checkpoints import CloudCheckpoint
        cloud = CloudCheckpoint(inputs) if inputs.get('S3ENABLE') == 'true' or inputs.get('AZUREENABLE') == 'true' else None
        pre_change = claim.pre_change
        if not pre_change or not isinstance(pre_change.get('runtime'), dict):
            raise ManagedBackupError('pre-change runtime evidence missing')
        schema = read_database_revision(database_settings(config))
        if not schema:
            raise ManagedBackupError('pre-change schema unavailable')
        runtime = pre_change['runtime']
        inventory = {name: row['version'] if isinstance(row, dict) else row for name, row in runtime.get('inventory', {}).items()}
        normal_verified = (runtime.get('kind') == 'normal' and bool(inventory) and
                           pre_change.get('runtime_health_verified') is True)
        if normal_verified:
            # Version evidence alone is not an integrity check. Require the old
            # applied bundle's installed-material inventory as well.
            with service.engine.connect() as connection:
                from sqlalchemy import select
                revisions = service.tables['revision']
                snapshot = connection.scalar(select(revisions.c.snapshot).where(revisions.c.id == runtime.get('revision_id')))
            material = None
            if snapshot:
                reference = snapshot['publication']['result']['bundle']['digest']
                material_file = store / reference / 'installed-material.json'
                if material_file.is_file():
                    material = json.loads(material_file.read_text())
            normal_verified = bool(material) and verify_environment(paths.runtime_venv, inventory, material).ok
        root = paths.da_root / 'backup' / 'checkpoints'
        if any(p.is_symlink() for p in (root, *root.parents)):
            raise ManagedBackupError('checkpoint destination symlink refused')
        root.mkdir(parents=True, exist_ok=True)
        directory = root / (claim.attempt_id + '-' + identity)
        directory.mkdir(mode=0o700)  # exclusive: never replace a same-day checkpoint
        copied = ObjectCopy(directory)
        # Cloud deployments may have no complete local file-storage mirror.
        if not cloud or (paths.da_root / 'files').exists():
            copied.tree(paths.da_root / 'files', 'files')
        copied.tree(paths.da_root / 'config', 'config')
        if str(paths.config_file) not in {row['source'] for row in copied.files.values()}:
            copied.file(paths.config_file, 'active-config.yml')
        cloud_files = cloud.capture(copied) if cloud else None
        document = create_backup(service.engine, directory / 'database', paths,
            prefix=service.prefix, store=store, baseline=baseline, artifact_roots=artifact_roots,
            secret_root=secret_root, index_root=index_root, checkpoint_only=True)
        # The gate prevents cooperating writers. Detect unsupported direct file
        # edits during capture instead of advertising their mixed bytes as full.
        if any(_sha(Path(row['source'])) != row['sha256'] for name, row in copied.files.items() if not name.startswith('cloud/')):
            raise ManagedBackupError('local files changed during checkpoint')
        for source, label in ((paths.da_root / 'files', 'files'), (paths.da_root / 'config', 'config')):
            actual = {label + '/' + p.relative_to(source).as_posix() for p in source.rglob('*') if not p.is_dir()}
            expected = {name for name in copied.files if name.startswith(label + '/')}
            if actual != expected:
                raise ManagedBackupError('local file coverage changed during checkpoint')
        if cloud and cloud_files is not None:
            cloud.verify_live(cloud_files)
        if read_database_revision(database_settings(load_config(paths.config_file))) != schema:
            raise ManagedBackupError('schema changed during checkpoint')
        metadata = dict(format=1, id=identity, complete=True, operation=claim.identity,
            created_at=status['created_at'], pre_change=pre_change, database_revision=schema,
            desired_revision_id=document['desired_revision_id'], known_good=normal_verified,
            runtime_label='verified-normal' if normal_verified else 'recovery-or-unverified',
            dump_sha256=document['dump_sha256'], files=copied.files, directories=copied.directories,
            retention=status['retention'], backup_days=inputs.get('DABACKUPDAYS', '14'),
            # The restore consumer must rebuild intent/config/baseline and the
            # Package projection from this revision, not just clear a work slot:
            # the dump still contains the advanced publication's desired rows.
            # Ordinary cold restore refuses checkpoint_only dumps until that
            # distinct operator protocol is implemented.
            restore={'protocol': 'full-checkpoint-v1', 'operator_only': True,
                'desired_revision_id': runtime.get('revision_id'),
                'suppress_attempt_ids': [claim.attempt_id], 'invalidate_unpublished_candidates': True,
                'replay_pending_work': False, 'requires_data_loss_acknowledgement': True,
                'external_side_effects_undone': False})
        if cloud_files is not None:
            metadata['cloud'] = cloud_files
        # Private assembly is not advertised until all bytes and metadata are
        # durable and independently verified. No last-known-good pointer exists.
        _atomic_write_json(directory / 'checkpoint.pending', metadata)
        verify_checkpoint(directory, service.prefix, manifest_name='checkpoint.pending')
        for path in directory.rglob('*'):
            if path.is_file():
                with path.open('rb') as handle:
                    os.fsync(handle.fileno())
        for path in sorted((p for p in directory.rglob('*') if p.is_dir()), reverse=True):
            _sync_directory(path)
        if cloud:
            status['destination'] = cloud.publish(directory, 'checkpoints/' + directory.name + '/', service.prefix)
        os.rename(directory / 'checkpoint.pending', directory / 'checkpoint.json')
        _sync_directory(directory)
        _sync_directory(root)
        _sync_directory(root.parent)
        status.update(state='verified', complete=True, known_good=normal_verified)
    except BaseException:
        status.update(state='failed-incomplete', complete=False)
        raise
    finally:
        status['duration_seconds'] = round(time.monotonic() - started, 3)
        _atomic_write_json(status_file, status)
    return status


def observe_pre_change(paths, claim) -> None:
    """Non-mutating restricted core probe after drain, before exclusive capture.

    Probe children use shared runtime leases; never run them while holding the
    exclusive capture lease. A failed probe labels the checkpoint, not known-good.
    """
    if claim.pre_change is None:
        return
    from packaging.utils import canonicalize_name
    from .health import CORE_DISTRIBUTIONS, probe_runtime
    verified = False
    if claim.pre_change.get('runtime', {}).get('kind') == 'normal' and claim.pre_change.get('core'):
        try:
            # Core bundles include dependencies and use normalized (hyphenated)
            # names; the runtime import probe reports only the three dotted names.
            core = claim.pre_change['core']
            versions = {name: core[canonicalize_name(name)]['version'] for name in CORE_DISTRIBUTIONS}
            from .alembic_probe import check_schema, STATUS_COMPATIBLE
            schema = check_schema(paths.runtime_venv, database_settings(load_config(paths.config_file)))
            verified = (schema.status == STATUS_COMPATIBLE and
                        probe_runtime(paths.runtime_venv, paths.config_file, versions, recovery=True).ok)
        except Exception:
            verified = False
    claim.pre_change['runtime_health_verified'] = verified


def capture_for_installation(service, paths, claim, store: Path, baseline: Path) -> dict:
    service.record_checkpoint(claim, {'state': 'capturing', 'complete': False, 'known_good': False})
    try:
        inputs = backup_inputs(Path('/etc/docassemble/managed-backup.json'))
        return capture(service, paths, claim, store=store, baseline=baseline, inputs=inputs,
            artifact_roots=[Path(os.environ[key]) for key in ('DA_MANAGED_ARTIFACT_ROOT', 'DA_EXECUTOR_ARTIFACT_ROOT') if os.environ.get(key)],
            secret_root=Path(os.environ['DA_MANAGED_SECRET_ROOT']) if os.environ.get('DA_MANAGED_SECRET_ROOT') else None,
            index_root=Path(os.environ['DA_INDEX_CREDENTIAL_ROOT']) if os.environ.get('DA_INDEX_CREDENTIAL_ROOT') else None)
    finally:
        status_file = paths.state_dir / 'checkpoints' / (claim.attempt_id + '.json')
        status = (json.loads(status_file.read_text()) if status_file.exists() else
                  {'state': 'failed-incomplete', 'complete': False, 'known_good': False})
        service.record_checkpoint(claim, status)
