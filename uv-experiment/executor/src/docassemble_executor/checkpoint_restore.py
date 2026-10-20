"""Intentional operator-only data rewind. Never called by installation recovery.

A stopped/excluded old deployment is required, as in cold restore. A restricted
root authorization selects one checkpoint and acknowledges irreversible data
loss. Preflight reconstructs a disposable DB and a fresh frozen environment;
only then may the selected live DB/files be replaced. The external journal is
not restored from the dump. Interrupted restores require explicit operator work.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import uuid
from urllib.parse import unquote, urlsplit

from sqlalchemy import create_engine, select, text, update
from sqlalchemy.engine import make_url

from docassemble.base.managed_checkpoint import restore_package_projection
from docassemble.base.managed_preparation import digest
from .admission import Admission
from .authority import MutationLock, RuntimeLock
from .checkpoints import backup_inputs, require_complete_backup, verify_checkpoint
from .cold_restore import (ColdRestoreError, RestoreArgumentParser, authorization,
                           image_owned_directories, restore_database_url, restore_objects)
from .journal import Journal, utcnow
from .managed_backup import _sha, canonical_records, read_records, _sync_directory
from .paths import ExecutorPaths
from .state import _atomic_write_json

WARNING = ('FULL CHECKPOINT RESTORE: packages, database and corresponding files/configuration are rewound. '
           'All subsequent data changes, including user progress and grants, are lost. Pending background work is discarded. '
           'External effects such as sent email cannot be undone. This is not package-only rollback.')


class MaintenanceRestoreError(ColdRestoreError):
    """Operator endpoint validation failed before any database replacement."""


class RestoreMaterialError(ColdRestoreError):
    """Required captured environment material failed validation."""


class RestoreSchemaError(ColdRestoreError):
    """Captured package/schema compatibility failed validation."""


class RestoreDestinationError(ColdRestoreError):
    """Captured destination layout failed validation."""


class RestoreBrokerError(ColdRestoreError):
    """Qualified broker endpoint or transport validation failed."""


RESTORE_FAILURE_MESSAGES = {
    MaintenanceRestoreError: 'DA_RESTORE_MAINTENANCE_DATABASE_URL must address the captured application cluster/database postgres; restore refused',
    RestoreMaterialError: 'checkpoint required retained material unavailable or invalid; no substitution; consumers remain gated',
    RestoreSchemaError: 'checkpoint package/schema compatibility unverified; consumers remain gated',
    RestoreDestinationError: 'checkpoint destination/layout invalid or unsupported; consumers remain gated',
    RestoreBrokerError: 'checkpoint qualified Redis broker unavailable or transport layout unverified; consumers remain gated',
}
RESTORE_FAILURE = ('checkpoint unavailable/incomplete or restore interrupted; inspect checkpoint-restore.json; '
                   'no substitution or automatic retry')


def require_restore_ready(paths: ExecutorPaths) -> None:
    journal = paths.state_dir / 'checkpoint-restore.json'
    if journal.exists() or journal.is_symlink():
        record = json.loads(journal.read_text())
        if record.get('state') != 'inputs-restored':
            raise ColdRestoreError('interrupted full checkpoint restore; inspect checkpoint-restore.json phase/staging_database; '
                                   'keep deployment excluded and explicitly restore again with fresh authorization')


def checkpoint_authorization(path: Path, directory: Path, document: dict) -> dict:
    authority = authorization(path, directory / 'database')
    value = json.loads(path.read_text())
    if (value.get('action') != 'full-checkpoint-restore' or value.get('checkpoint_id') != document['id'] or
            value.get('checkpoint_sha256') != _sha(directory / 'checkpoint.json') or
            value.get('acknowledge_subsequent_data_loss') is not True):
        raise ColdRestoreError('explicit checkpoint selection and subsequent-data-loss acknowledgement required')
    return dict(authority, checkpoint_id=document['id'], checkpoint_sha256=value['checkpoint_sha256'])


def broker_keys() -> list[str]:
    # Kombu's qualified default Redis transport: priority_steps=[0,3,6,9],
    # separator=\\x06\\x16. Never FLUSHDB or delete application/API-key state.
    return [queue + ('\x06\x16' + str(priority) if priority else '')
            for queue in ('celery', 'single') for priority in (0, 3, 6, 9)] + ['unacked', 'unacked_index', 'unacked_mutex']


def _broker_command(broker: str, *arguments: str) -> str:
    try:
        parsed = urlsplit(broker)
        if parsed.scheme != 'redis' or not parsed.hostname:
            raise ValueError
        database = int(unquote(parsed.path.removeprefix('/')) or '0')
        if database < 0:
            raise ValueError
        command = ['redis-cli', '--no-auth-warning', '-h', parsed.hostname,
                   '-p', str(parsed.port or 6379), '-n', str(database)]
        if parsed.username:
            command += ['--user', unquote(parsed.username)]
        env = dict(os.environ)
        env.pop('REDISCLI_AUTH', None)
        if parsed.password is not None:
            env['REDISCLI_AUTH'] = unquote(parsed.password)
        result = subprocess.run([*command, '--raw', *arguments], env=env,
                                capture_output=True, text=True, timeout=30)
        if result.returncode or result.stdout.startswith(('ERR', 'WRONGTYPE', 'NOAUTH')):
            raise ValueError
        return result.stdout.strip()
    except (OSError, ValueError, subprocess.SubprocessError, UnicodeError):
        raise RestoreBrokerError('checkpoint Redis broker unavailable; consumers must remain gated') from None


def broker_fingerprint(broker: str) -> str:
    try:
        parsed = urlsplit(broker)
        if (parsed.scheme != 'redis' or not parsed.hostname or parsed.query or parsed.fragment or
                ';' in broker or not parsed.path.removeprefix('/').isdigit() and parsed.path not in ('', '/')):
            raise ValueError
        endpoint = {'scheme': parsed.scheme, 'host': parsed.hostname.lower().rstrip('.'),
                    'port': parsed.port or 6379, 'database': int(parsed.path.removeprefix('/') or '0')}
        return digest(endpoint)
    except (ValueError, TypeError, AttributeError):
        raise RestoreBrokerError('checkpoint requires qualified Redis broker; endpoint unresolved') from None


def require_checkpoint_broker(paths: ExecutorPaths, broker: str) -> None:
    """Bind actual worker startup to the endpoint whose purge was committed."""
    journal = paths.state_dir / 'checkpoint-restore.json'
    binding = paths.state_dir / 'checkpoint-broker.json'
    if not any(path.exists() or path.is_symlink() for path in (journal, binding)):
        return
    try:
        if journal.is_symlink():
            raise ValueError
        if binding.is_symlink():
            raise ValueError
        record = json.loads(binding.read_text())
        expected = record['broker_fingerprint']
        if (record.get('state') != 'inputs-restored' or not isinstance(expected, str) or
                expected != broker_fingerprint(broker)):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError, RestoreBrokerError):
        raise RestoreBrokerError('checkpoint broker binding unavailable or mismatched; worker startup refused') from None


def require_broker_persistence(broker: str) -> None:
    # This bounded restore protocol qualifies synchronous RDB only. AOF replay
    # has a separate durability contract; never certify it using an RDB SAVE.
    if _broker_command(broker, 'CONFIG', 'GET', 'appendonly') != 'appendonly\nno':
        raise RestoreBrokerError('checkpoint broker persistence mode unqualified')


def purge_broker(broker: str) -> None:
    require_broker_persistence(broker)
    removed = _broker_command(broker, 'DEL', *broker_keys())
    if not removed.isdigit() or _broker_command(broker, 'EXISTS', *broker_keys()) != '0':
        raise RestoreBrokerError('checkpoint stale broker work could not be discarded; remain gated')
    # SAVE blocks until the replacement RDB has been fsynced and renamed. An
    # error (including concurrent BGSAVE) leaves the restore journal interrupted.
    if _broker_command(broker, 'SAVE') != 'OK' or _broker_command(broker, 'EXISTS', *broker_keys()) != '0':
        raise RestoreBrokerError('checkpoint broker purge durability unverified; remain gated')


def preflight_broker(config: dict) -> str:
    value = config.get('rabbitmq')
    from .config import environment_value
    if os.environ.get('ENVIRONMENT_TAKES_PRECEDENCE', 'false').lower().strip() == 'true':
        override = environment_value('RABBITMQ')
        if override not in (None, ''):
            value = override
    if not isinstance(value, str):
        raise RestoreBrokerError('checkpoint restore requires qualified Redis broker and celery/single queues; unknown transport refused')
    broker: str = value
    broker_fingerprint(broker)
    for variable in ('CELERY_BROKER_URL', 'CELERY_BROKER_READ_URL'):
        celery_override = os.environ.get(variable)
        if celery_override is not None and broker_fingerprint(celery_override) != broker_fingerprint(broker):
            raise RestoreBrokerError('checkpoint consumer broker endpoint mismatch')
    parsed = urlsplit(broker)
    routes = config.get('celery task routes') or {}
    if not isinstance(routes, dict):
        raise RestoreBrokerError('checkpoint broker routes unqualified')
    if (parsed.scheme != 'redis' or not parsed.hostname or ';' in broker or
            any(not isinstance(route, dict) or route.get('queue', 'celery') not in ('celery', 'single') for route in routes.values())):
        raise RestoreBrokerError('checkpoint restore requires qualified Redis broker and celery/single queues; unknown transport refused')
    if _broker_command(broker, 'PING') != 'PONG':
        raise RestoreBrokerError('checkpoint Redis broker unavailable')
    require_broker_persistence(broker)
    for key in broker_keys():
        expected = 'hash' if key == 'unacked' else 'zset' if key == 'unacked_index' else 'string' if key == 'unacked_mutex' else 'list'
        if _broker_command(broker, 'TYPE', key) not in ('none', expected):
            raise RestoreBrokerError('checkpoint Redis transport key layout unqualified')
    return broker


def _database_env(url) -> dict:
    env = dict(os.environ, PGDATABASE=url.database or '', PGUSER=url.username or '', PGPASSWORD=url.password or '',
               PGHOST=url.host or '', PGPORT=str(url.port or 5432))
    for option, variable in {'options': 'PGOPTIONS', 'sslmode': 'PGSSLMODE', 'sslcert': 'PGSSLCERT',
                             'sslkey': 'PGSSLKEY', 'sslrootcert': 'PGSSLROOTCERT', 'sslcrl': 'PGSSLCRL'}.items():
        if option in url.query:
            env[variable] = str(url.query[option])
    return env


def maintenance_url(target, supplied: str | None):
    """Separate operator DB credentials, never an app-role privilege grant."""
    operator = make_url(supplied) if supplied else target.set(database='postgres')
    if (operator.get_backend_name() != 'postgresql' or operator.database != 'postgres' or
            (operator.port or 5432) != (target.port or 5432)):
        raise ColdRestoreError('maintenance DB endpoint must match the captured application cluster')
    def addresses(url):
        host = url.host or ''
        if host.startswith('/'):
            if not Path(host).is_dir():
                raise ColdRestoreError('maintenance DB endpoint unreadable')
            return {host}
        return {row[4][0] for row in socket.getaddrinfo(host or 'localhost', url.port or 5432, type=socket.SOCK_STREAM)}
    try:
        if not addresses(operator) & addresses(target):
            raise ColdRestoreError('maintenance DB endpoint must match the captured application cluster')
        identities = []
        for url in (target, operator):
            engine = create_engine(url.set(database='postgres'), connect_args={'connect_timeout': 5})
            try:
                with engine.connect() as connection:
                    identities.append(tuple(connection.execute(text(
                        'SELECT inet_server_addr()::text, inet_server_port(), pg_postmaster_start_time()')).one()))
            finally:
                engine.dispose()
        if identities[0] != identities[1]:
            raise ColdRestoreError('maintenance DB endpoint resolves to a different cluster')
    except ColdRestoreError:
        raise
    except Exception:
        raise ColdRestoreError('maintenance DB endpoint unreadable or operator credentials unavailable') from None
    return operator.set(database='postgres')


def _admin(url, statement: str) -> None:
    engine = create_engine(url.set(database='postgres'), isolation_level='AUTOCOMMIT', connect_args={'connect_timeout': 5})
    try:
        with engine.connect() as connection:
            connection.execute(text(statement))
    finally:
        engine.dispose()


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _validate_destinations(directory: Path, document: dict, paths: ExecutorPaths, *, local: bool = True) -> None:
    for source in document['directories']:
        target = Path(source)
        if not target.is_absolute() or any(p.is_symlink() for p in (target, *target.parents)):
            raise RestoreDestinationError('checkpoint restore destination symlink or relative path refused')
    for name, row in document['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise RestoreDestinationError('checkpoint object path invalid')
        if name.startswith('cloud/'):
            continue
        target = Path(row['source'])
        if (str(target.parent) not in document['directories'] or
                any(p.is_symlink() for p in (target, *target.parents))):
            raise RestoreDestinationError('checkpoint file destination invalid')
        if name.startswith('files/') and target != paths.da_root / name:
            raise RestoreDestinationError('checkpoint file-storage target incompatible')
        if name.startswith('config/') and target != paths.da_root / name:
            raise RestoreDestinationError('checkpoint configuration target incompatible')
    if local and not any(row['source'] == str(paths.config_file) for row in document['files'].values()):
        raise RestoreDestinationError('checkpoint active configuration unavailable')
    for target in (paths.da_root / 'files', paths.da_root / 'config'):
        if any(p.is_symlink() for p in (target, *target.parents)):
            raise RestoreDestinationError('checkpoint destination symlink refused')
        if directory == target or target in directory.parents or paths.state_dir == target or target in paths.state_dir.parents:
            raise RestoreDestinationError('checkpoint or executor journal inside replaced files/configuration')
        if target.exists() and any(p.is_symlink() for p in target.rglob('*')):
            raise RestoreDestinationError('live files/configuration symlinks refused')


def rewind_database(service, paths, authority, document) -> dict:
    """Applied intent replaces the advanced publication, never snapshot pending work.

    Called on the private validation DB before destructive work. Accounts/grants
    and interview data remain exactly the capture-point dump contents.
    """
    from docassemble.base.managed_config import OBSERVED_CONFIG_ID
    from .installation import Installation
    assert isinstance(service, Installation)
    with service.engine.begin() as connection:
        environment = service._environment(connection)
        hosts = connection.scalars(select(service.tables['host_environment'].c.id)).all()
        if authority['incarnation'] in hosts:
            raise ColdRestoreError('fresh checkpoint restore incarnation required')
        revision_id = document['restore']['desired_revision_id']
        revision = connection.scalar(select(service.tables['revision'].c.snapshot).where(service.tables['revision'].c.id == revision_id))
        if not revision or not revision.get('publication', {}).get('result', {}).get('bundle'):
            raise ColdRestoreError('recorded pre-change applied environment unavailable; operator repair required')
        if revision.get('target') != document['pre_change']['target']:
            raise ColdRestoreError('recorded pre-change target mismatch')
        try:
            restore_package_projection(connection, service.prefix, revision)
        except ValueError:
            raise ColdRestoreError('checkpoint applied revision lacks usable Package projection; recapture with current tooling required') from None
        contributions = service.tables['requirement_contribution']
        connection.execute(update(contributions).values(active=False))
        for row in revision['contributions']:
            stored = connection.execute(select(contributions).where(contributions.c.id == row['id'])).mappings().first()
            if not stored or any(stored[key] != row[key] for key in ('identity_id', 'origin', 'declaration', 'artifact_ref')):
                raise ColdRestoreError('pre-change contribution evidence unavailable')
            connection.execute(update(contributions).where(contributions.c.id == row['id']).values(active=True))
        observed = service.tables['observed_config']
        connection.execute(observed.delete())
        if 'observed_config' in revision:
            connection.execute(observed.insert().values(id=OBSERVED_CONFIG_ID,
                generation=revision['observed_config_generation'], snapshot=revision['observed_config']))
        candidates = service.tables['candidate']
        connection.execute(update(candidates).where(candidates.c.state.in_(('queued', 'preparing', 'ready'))).values(
            state='cancelled', fence=candidates.c.fence + 1, claim_until=None))
        materials = service.tables['candidate_material']
        connection.execute(update(materials).where(materials.c.committed.is_(False)).values(usable=False, deleted=True))
        attempts = service.tables['installation_attempt']
        for row in connection.execute(select(attempts)).mappings():
            record = row['record']
            if record.get('phase') in ('pending', 'running'):
                connection.execute(update(attempts).where(attempts.c.id == row['id']).values(record=dict(record,
                    phase='blocked', fence=record.get('fence', 0) + 1, checkpoint_restore_superseded_by=authority['incarnation'])))
        restored = dict(authority, state='pending', source_attempt_id=None, established_at=utcnow(),
            incomplete_backup=False, unrestorable_revision_ids=[], unavailable_objects=[], warnings=[WARNING],
            checkpoint_database_revision=document['database_revision'])
        snapshot = dict(revision, runtime={'kind': 'unknown', 'inventory': {}, 'reason': 'full-checkpoint-restore'},
                        cold_restore=restored)
        # Never retain a pending upgrade's image/schema/recovery selection.
        snapshot.pop('boot_previous_runtime', None)
        connection.execute(update(service.tables['environment_state']).values(desired_revision_id=revision_id,
            active_attempt_id=None, snapshot=snapshot, publication_generation=environment['publication_generation'] + 1,
            input_generation=environment['input_generation'] + 1, runtime_generation=environment['runtime_generation'] + 1))
    return restored


def preflight_environment(paths: ExecutorPaths, directory: Path, revision: dict, staging_url, database_revision: str) -> None:
    """Consume actual frozen artifacts with no old cache/venv or network fallback."""
    from .registry_builder import UV_VERSION
    from .uvsync import sync_frozen
    from .inventory import verify_environment
    if subprocess.run([paths.uv_binary, '--version'], capture_output=True, check=True).stdout.decode().split()[:2] != ['uv', UV_VERSION]:
        raise RestoreMaterialError('checkpoint pinned uv unavailable')
    baseline = json.loads((directory / 'database.managed/objects/image-baseline/baseline.json').read_text())['baseline']
    if (any(revision.get(key) != baseline.get(key) for key in ('target', 'tool', 'image_baseline')) or
            revision['core'] != (revision.get('selected_baseline') or {}).get('core_bundle', {}).get('inventory', baseline['core'])):
        raise RestoreMaterialError('checkpoint recorded environment incompatible with target image/baseline')
    bundle = revision['publication']['result']['bundle']
    retained = directory / 'database.managed/objects/retained' / bundle['digest']
    if (_sha(retained / 'pyproject.toml') != bundle['project_digest'] or _sha(retained / 'uv.lock') != bundle['lock_digest'] or
            json.loads((retained / 'inventory.json').read_text()) != bundle['inventory']):
        raise RestoreMaterialError('checkpoint applied bundle binding invalid')
    # sync_frozen copies the verified project to non-root private staging. All
    # required wheels are in the retained project, not disposable uv cache.
    import pwd
    account = pwd.getpwnam(os.environ['DA_ENVIRONMENT_USER'])
    with tempfile.TemporaryDirectory(prefix='checkpoint-preflight-', dir=account.pw_dir) as scratch:
        os.chown(scratch, account.pw_uid, account.pw_gid)
        venv, cache = Path(scratch) / 'venv', Path(scratch) / 'cache'
        result = sync_frozen(retained, venv, paths.uv_binary, cache, env_extra={
            'UV_OFFLINE': '1', 'UV_NO_CACHE': '1', 'UV_NO_CONFIG': '1'})
        if not result.ok or not verify_environment(venv, {name: row['version'] for name, row in bundle['inventory'].items()},
                json.loads((retained / 'installed-material.json').read_text())).ok:
            raise RestoreMaterialError('checkpoint required frozen source/artifact unavailable or expired; no data replaced; '
                                   'repair retained material/access, never solve a substitute')
        from .alembic_probe import check_schema
        from .config import DatabaseSettings
        db = DatabaseSettings(host=staging_url.host or 'localhost', port=staging_url.port,
                              user=staging_url.username, password=staging_url.password, name=staging_url.database)
        if revision.get('schema_mode') == 'unchanged':
            from .rollback_schema import check_rollback_schema
            schema = check_rollback_schema(venv, db, retained)
        else:
            manifest = json.loads(paths.manifest_file.read_text())
            schema = check_schema(venv, db, () if revision.get('selected_baseline') else tuple(manifest.get('compatible_schema_revisions', ())))
        if not schema.usable or schema.database_revision != database_revision:
            raise RestoreSchemaError('checkpoint package/schema compatibility unverified; no data replaced')


def restore_checkpoint(paths: ExecutorPaths, directory: Path, authorization_file: Path, database_url: str,
                       prefix: str = '', *, environment_file: Path | None = None,
                       maintenance_database_url: str | None = None) -> dict:
    if os.geteuid() != 0 or os.environ.get('CONTAINERROLE', 'all').strip(':') != 'all':
        raise ColdRestoreError('full checkpoint restore requires independent root operator in single all-role deployment')
    url = make_url(database_url)
    if url.get_backend_name() != 'postgresql' or not url.database or url.database in ('postgres', 'template0', 'template1'):
        raise ColdRestoreError('explicit non-system PostgreSQL target required')
    if not directory.is_absolute() or any(p.is_symlink() for p in (directory, *directory.parents)):
        raise ColdRestoreError('absolute checkpoint directory required; symlinks refused')
    document = verify_checkpoint(directory, prefix)
    authority = checkpoint_authorization(authorization_file, directory, document)
    managed_directory = directory / 'database.managed'
    managed = json.loads((managed_directory / 'manifest.json').read_text())
    current_image = json.loads(paths.manifest_file.read_text())
    recorded_image = json.loads((managed_directory / 'objects/image/manifest.json').read_text())
    if any(current_image.get(key) != recorded_image.get(key) for key in ('base_image_digest', 'identity', 'bundles')):
        raise ColdRestoreError('checkpoint target incompatible; deploy the recorded image first')
    _validate_destinations(directory, document, paths)
    _validate_destinations(managed_directory, managed, paths, local=False)
    from .config import load_config, database_settings
    active = next(directory / 'objects' / name for name, row in document['files'].items() if row['source'] == str(paths.config_file))
    config = load_config(active)
    db = database_settings(config)
    broker = preflight_broker(config)
    if (db.name != url.database or db.user != url.username or (db.host or '') != (url.host or '') or
            (db.port or 5432) != (url.port or 5432) or (db.password or '') != (url.password or '') or
            (config.get('db', {}).get('table prefix') or '') != prefix):
        raise ColdRestoreError('checkpoint database target incompatible; original DB identity/configuration required')
    try:
        operator_url = maintenance_url(url, maintenance_database_url)
    except Exception:
        raise MaintenanceRestoreError(RESTORE_FAILURE_MESSAGES[MaintenanceRestoreError]) from None
    cloud = None
    if document.get('cloud'):
        from .cloud_checkpoints import CloudCheckpoint
        if environment_file is None:
            raise ColdRestoreError('trusted cloud restore environment required')
        inputs = backup_inputs(environment_file)
        require_complete_backup(inputs, config)
        cloud = CloudCheckpoint(inputs)
        if cloud.provider != document['cloud']['provider'] or cloud.container != document['cloud']['container']:
            raise ColdRestoreError('checkpoint cloud target incompatible')
        cloud.live_listing()  # Verify current read/list access before any destructive work.
    staging_name = 'da_checkpoint_' + uuid.uuid4().hex
    staging_url = url.set(database=staging_name)
    record = dict(authority, state='preflight', phase='database-validation', staging_database=staging_name,
                  target_database=url.database, started_at=utcnow(), warning=WARNING,
                  broker_fingerprint=broker_fingerprint(broker))
    journal_path = paths.state_dir / 'checkpoint-restore.json'
    destructive = False
    staged = False
    with MutationLock(paths.state_dir):
        # Root authorization asserts old deployment exclusion. Local gates and
        # OS leases additionally exclude cooperating surviving consumers/writers.
        Admission(paths.state_dir).close('operator full checkpoint restore')
        if not Admission(paths.state_dir).drain(30):
            raise ColdRestoreError('checkpoint restore drain incomplete; no data replaced')
        with RuntimeLock(paths.state_dir):
            pass
        if journal_path.exists():
            previous = json.loads(journal_path.read_text())
            if previous.get('incarnation') == authority['incarnation']:
                raise ColdRestoreError('fresh authorization required; inspect prior checkpoint restore journal')
            _atomic_write_json(paths.state_dir / 'attempt-history' / ('checkpoint-' + previous['incarnation'] + '.json'), previous)
        _atomic_write_json(journal_path, record)
        try:
            _admin(operator_url, 'CREATE DATABASE ' + _quoted(staging_name) + ' OWNER ' + _quoted(url.username or '') + ' TEMPLATE template0')
            staged = True
            subprocess.run(['pg_restore', '--exit-on-error', '-d', staging_name, str(directory / 'database')],
                env=_database_env(staging_url), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            engine = create_engine(staging_url)
            try:
                from .installation import Installation
                service = Installation(engine, lambda *_: False, prefix)
                with engine.connect() as connection:
                    if digest(canonical_records(read_records(connection, prefix))) != managed['records_digest']:
                        raise ColdRestoreError('checkpoint restored validation DB differs from snapshot')
                    observed = connection.scalar(text('SELECT version_num FROM alembic_version'))
                    if observed != document['database_revision']:
                        raise RestoreSchemaError('checkpoint schema evidence mismatch')
                restored = rewind_database(service, paths, authority, document)
                with engine.connect() as connection:
                    revision = connection.scalar(select(service.tables['revision'].c.snapshot).where(
                        service.tables['revision'].c.id == document['restore']['desired_revision_id']))
                preflight_environment(paths, directory, revision, staging_url, document['database_revision'])
            finally:
                engine.dispose()
            # Validate everything again immediately before the irreversible boundary.
            verify_checkpoint(directory, prefix)
            with RuntimeLock(paths.state_dir):
                destructive = True
                record.update(state='restoring', phase='database-replace')
                _atomic_write_json(journal_path, record)
                _admin(operator_url, 'DROP DATABASE IF EXISTS ' + _quoted(url.database) + ' WITH (FORCE)')
                _admin(operator_url, 'ALTER DATABASE ' + _quoted(staging_name) + ' RENAME TO ' + _quoted(url.database))
                staged = False
                record['phase'] = 'files-replace'
                _atomic_write_json(journal_path, record)
                for target in (paths.da_root / 'files', paths.da_root / 'config'):
                    if target.exists():
                        shutil.rmtree(target)
                local = dict(document, files={name: row for name, row in document['files'].items() if not name.startswith('cloud/')})
                restore_objects(directory, local, {'unavailable_objects': []})
                if cloud:
                    record['phase'] = 'cloud-files-replace'
                    _atomic_write_json(journal_path, record)
                    cloud.restore(directory, document['cloud'])
                record['phase'] = 'broker-work-discard'
                _atomic_write_json(journal_path, record)
                purge_broker(broker)
                record['phase'] = 'managed-authority-reset'
                _atomic_write_json(journal_path, record)
                restore_objects(managed_directory, managed, {'unavailable_objects': []})
                # Copied lifecycle receipts describe the pending publication, not
                # restore permission. Keep them only as historical evidence.
                old_journal = Journal.load(paths.journal_file)
                _atomic_write_json(paths.state_dir / 'attempt-history' / ('checkpoint-source-' + authority['incarnation'] + '.json'), old_journal.to_dict())
                Journal().save(paths.journal_file)
                baselines = {key: revision[key] for key in ('image_baseline', 'selected_baseline', 'schema_mode') if key in revision}
                baselines['store'] = os.environ.get('DA_DURABLE_WORK_STORE', '')
                baselines['recovery_baseline'] = revision.get('selected_baseline') or {
                    'image_baseline': revision.get('image_baseline'), 'core': revision['core']}
                _atomic_write_json(paths.state_dir / 'published-baselines.json', baselines)
                _atomic_write_json(paths.state_dir / 'installation-host.json', {'id': authority['incarnation']})
                _atomic_write_json(paths.state_dir / 'cold-restore.json', restored)
                _atomic_write_json(paths.state_dir / 'cold-restore-inventory.json', managed)
                # The completion marker follows durable DB rename and every
                # restored local/managed byte and directory entry, not just the
                # top-level file-storage directory.
                for restored_document in (local, managed):
                    for name, row in restored_document['files'].items():
                        if name.startswith(('image/', 'image-baseline/')):
                            continue
                        with Path(row['source']).open('rb') as handle:
                            os.fsync(handle.fileno())
                    # Image-owned directories were deliberately never restored, so
                    # they cannot be fsynced here; any other absent restored
                    # directory must still fail loudly.
                    image_owned = image_owned_directories(restored_document)
                    for source in sorted(restored_document['directories'], key=lambda value: len(Path(value).parts), reverse=True):
                        target = Path(source)
                        if target in image_owned:
                            continue
                        _sync_directory(target)
                # Stale local health/clearances cannot establish readiness.
                # Committed source authentication is retained.
                for name in ('applied.json', 'initialized.json', 'schema-clearance.json', 'rollback-schema-check.json', 'publication-reported.json'):
                    (paths.state_dir / name).unlink(missing_ok=True)
                record.update(state='inputs-restored', phase='awaiting-frozen-runtime-verification', completed_at=utcnow())
                # Workers are www-data, while the operator journal remains
                # private. This projection contains only a credential-free hash.
                _atomic_write_json(paths.state_dir / 'checkpoint-broker.json', {
                    'state': 'inputs-restored', 'broker_fingerprint': record['broker_fingerprint']}, mode=0o644)
                _atomic_write_json(journal_path, record)
                Admission(paths.state_dir).close('checkpoint inputs restored; frozen runtime/schema/role verification required')
            return record
        except BaseException:
            record.update(state='interrupted' if destructive else 'preflight-failed', failed_at=utcnow(),
                action='keep deployment excluded; inspect phase/staging_database; repair material then explicitly restore with fresh root authorization')
            _atomic_write_json(journal_path, record)
            Admission(paths.state_dir).close('full checkpoint restore incomplete; explicit operator recovery required')
            raise
        finally:
            if staged and not destructive:
                _admin(operator_url, 'DROP DATABASE IF EXISTS ' + _quoted(staging_name) + ' WITH (FORCE)')


def main(argv=None):
    parser = RestoreArgumentParser(description=WARNING)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--authorization', type=Path, required=True)
    parser.add_argument('--prefix', default='')
    parser.add_argument('--environment', type=Path)
    args = parser.parse_args(argv)
    print(WARNING, flush=True)
    try:
        database_url = restore_database_url('DA_RESTORE_DATABASE_URL')
        operator_url = restore_database_url('DA_RESTORE_MAINTENANCE_DATABASE_URL')
        restore_checkpoint(ExecutorPaths.from_env(), args.checkpoint, args.authorization, database_url,
                           args.prefix, environment_file=args.environment, maintenance_database_url=operator_url)
        print('checkpoint inputs restored; consumers remain gated until exact frozen runtime/schema/role verification', flush=True)
        return 0
    except Exception as error:
        # Exact owned types map to literals; never surface exception text, even
        # from provider-backed subclasses of the generic restore/backup errors.
        missing = next((name for name in ('DA_RESTORE_DATABASE_URL', 'DA_RESTORE_MAINTENANCE_DATABASE_URL')
                        if not os.environ.get(name, '').strip()), None)
        print(missing + ' is required; restore refused' if missing else
              RESTORE_FAILURE_MESSAGES.get(type(error), RESTORE_FAILURE), flush=True)
        return 1
