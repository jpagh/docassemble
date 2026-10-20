"""Explicit same-target restore, independent of the application interpreter.

The operator's restricted authorization is the old-deployment exclusion authority,
NOT a lease, copied PID, heartbeat or health receipt. This experimental seam
supports one all-role container and trusted existing-backup contents only.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
import uuid
from typing import NoReturn

from sqlalchemy import create_engine, select, update, text
from sqlalchemy.engine import make_url

from docassemble.base.managed_preparation import digest
from .admission import Admission
from .authority import MutationLock, RuntimeLock
from .journal import Journal, utcnow
from .managed_backup import (_sha, canonical_records, dumped_records, references,
                             read_records, ManagedBackupError)
from .paths import ExecutorPaths
from .state import _atomic_write_json


class ColdRestoreError(ValueError):
    """Fixed diagnostic only; backups contain source credentials."""


RESTORE_ARGUMENT_ERROR = ('invalid restore arguments; use DA_RESTORE_DATABASE_URL '
                          '(and DA_RESTORE_MAINTENANCE_DATABASE_URL for checkpoints)\n')


class RestoreArgumentParser(argparse.ArgumentParser):
    """Restore argument errors must not echo rejected credential-bearing argv."""

    def error(self, message: str) -> NoReturn:
        self.exit(2, RESTORE_ARGUMENT_ERROR)


def restore_database_url(variable: str) -> str:
    value = os.environ.get(variable)
    if not value or not value.strip():
        raise ColdRestoreError(variable + ' is required; restore refused')
    return value


def authorization(path: Path, dump: Path) -> dict:
    """Require an explicit root-owned 0700 directory and 0600 authorization."""
    if (not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)) or
            path.parent.stat().st_uid != 0 or stat.S_IMODE(path.parent.stat().st_mode) != 0o700):
        raise ColdRestoreError('restricted restore authorization directory required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
            raise ColdRestoreError('root-owned 0600 restore authorization required')
        raw = os.read(fd, 8193)
    finally:
        os.close(fd)
    value = json.loads(raw)
    incarnation = value.get('incarnation')
    if (len(raw) > 8192 or value.get('format') != 1 or value.get('dump_sha256') != _sha(dump) or
            value.get('old_deployment_excluded') is not True or
            not isinstance(incarnation, str) or str(uuid.UUID(incarnation)) != incarnation):
        raise ColdRestoreError('restore authorization binding or old-deployment exclusion missing')
    return {'incarnation': incarnation, 'dump_sha256': value['dump_sha256']}


def _subset(records: dict, snapshot: dict) -> dict:
    # references() includes all committed material for backup creation. For one
    # revision's restorability, include only its credential/artifact bindings.
    # Preparation input baselines retain ancestry/provenance, not dependencies
    # of this revision's frozen runtime. Its own result/intent and explicit
    # recovery selection are mandatory; unrelated ancestor solutions are not.
    snapshot = dict(snapshot)
    if 'publication' in snapshot:
        snapshot['publication'] = {key: value for key, value in snapshot['publication'].items() if key != 'inputs'}
    selected = dict(records, revision=[{'snapshot': snapshot}], environment_state=[], requirement_contribution=[], candidate_material=[])
    credentials = set()
    def walk(value):
        if isinstance(value, dict):
            credentials.update(value.get('git_credentials', {}).values())
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(snapshot)
    selected['candidate_material'] = [row for row in records['candidate_material'] if row['id'] in credentials]
    return selected


def inspect_backup(dump: Path, directory: Path, prefix: str) -> tuple[dict, dict]:
    """Validate every reference; tolerate damage ONLY outside current/recovery."""
    document = json.loads((directory / 'manifest.json').read_text())
    if document.get('checkpoint_only'):
        raise ColdRestoreError('pre-change checkpoint requires the separate full checkpoint restore protocol')
    records = canonical_records(dumped_records(dump, prefix))
    if (document.get('format') != 1 or document.get('managed_objects_complete') is not True or
            document['dump_sha256'] != _sha(dump) or document['records_digest'] != digest(records) or
            json.loads((directory / 'records.json').read_text()) != records or
            references(records, document['local_baselines']) != document['references']):
        raise ColdRestoreError('managed backup snapshot binding invalid')
    files = document['files']
    present = set()
    for path in (directory / 'objects').rglob('*'):
        if path.is_symlink():
            raise ColdRestoreError('managed backup symlink refused')
        if path.is_file():
            present.add(path.relative_to(directory / 'objects').as_posix())
    if present - files.keys():
        raise ColdRestoreError('unlisted managed backup objects refused')
    bad = set()
    for name, record in files.items():
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ColdRestoreError('invalid managed backup object path')
        try:
            if _sha(directory / 'objects' / name) != record['sha256']:
                bad.add(name)
        except (OSError, ManagedBackupError):
            bad.add(name)
    if not {'state/lifecycle.json', 'image/manifest.json', 'image-baseline/baseline.json'} <= files.keys():
        raise ColdRestoreError('mandatory managed backup inventory missing')
    from .registry_builder import verify_retained
    for reference in document['references']['bundles']:
        names = {name for name in files if name.startswith('retained/' + reference + '/')}
        if not names:
            raise ColdRestoreError('retained bundle missing from backup inventory')
        try:
            verify_retained(directory / 'objects/retained' / reference, reference)
        except Exception:
            bad |= names
    for reference in document['references']['artifacts']:
        names = {name for name, entry in files.items() if Path(entry['source']).name == reference}
        if not names:
            raise ColdRestoreError('retained artifact missing from backup inventory')
        for name in names:
            try:
                if _sha(directory / 'objects' / name) != reference:
                    bad.add(name)
            except (OSError, ManagedBackupError):
                bad.add(name)
    for source in document['references']['material_paths']:
        if not any(entry['source'] == source for entry in files.values()):
            raise ColdRestoreError('persistent authentication missing from backup inventory')
    for label in document['references']['indexes']:
        if 'index/' + label not in files:
            raise ColdRestoreError('index authentication missing from backup inventory')
    from .bundles import verify_bundle
    if 'image/manifest.json' not in bad and 'image-baseline/baseline.json' not in bad:
        image = json.loads((directory / 'objects/image/manifest.json').read_text())
        baseline = json.loads((directory / 'objects/image-baseline/baseline.json').read_text())
        from .managed_backup import require_baseline_wheel_map, revision_image_material
        require_baseline_wheel_map(baseline)
        if document.get('revision_image_material') != revision_image_material(records, image):
            raise ColdRestoreError('historical image restore provenance mismatch')
        image_names = {'core-' + image.get('identity', {}).get('core_baseline', 'r1'),
                       baseline['baseline']['image_baseline']['bundle']}
        image_names.update(name for expected, names in document['references']['image_bundles'].items() for name in names
                           if image['bundles'].get(name) == expected)
        for bundle in image_names:
            names = {name for name in files if name.startswith('image/' + bundle + '/')}
            if not names:
                raise ColdRestoreError('image bundle missing from backup inventory')
            if not verify_bundle(directory / 'objects/image' / bundle, image, bundle)[0]:
                bad |= names
        for name, filename in baseline['wheels'].items():
            relative = 'image-baseline/wheels/' + filename
            if relative not in files or files[relative]['sha256'] != baseline['baseline']['core'][name]['source_digest']:
                raise ColdRestoreError('image baseline artifact inventory invalid')
    manifest = json.loads((directory / 'objects/image/manifest.json').read_text()) if 'image/manifest.json' not in bad else {}
    environment, = records['environment_state']
    current = environment['desired_revision_id']

    def affected(snapshot, *, is_current=False):
        if is_current and environment['snapshot'].get('recovery_baseline'):
            snapshot = dict(snapshot, recovery_baseline=environment['snapshot']['recovery_baseline'])
        refs = references(_subset(records, snapshot))
        return {name for name in bad if (
            any(name.startswith('retained/' + ref + '/') for ref in refs['bundles']) or
            any(Path(files[name]['source']).name == ref for ref in refs['artifacts']) or
            files[name]['source'] in refs['material_paths'] or
            name in {'index/' + label for label in refs['indexes']} or
            any(name.startswith('image/' + bundle + '/') for expected, bundles in refs['image_bundles'].items()
                for bundle in bundles if manifest.get('bundles', {}).get(bundle) == expected))}

    required = affected(environment['snapshot']) | affected(document['local_baselines'])
    for contribution in records['requirement_contribution']:
        if contribution['active']:
            required |= affected(contribution)
    unrestorable = []
    for row in records['revision']:
        damaged = affected(row['snapshot'], is_current=row['id'] == current)
        if row['id'] == current:
            required |= damaged
        elif damaged:
            unrestorable.append(row['id'])
    # Lifecycle, local selected/recovery baselines and image recovery are always
    # mandatory. Historical-only retained bundles/artifacts may be unavailable.
    required |= {name for name in bad if name.startswith(('state/', 'image-baseline/')) or
                 name == 'image/manifest.json'}
    manifest = json.loads((directory / 'objects/image/manifest.json').read_text()) if 'image/manifest.json' not in bad else {}
    core = 'core-' + manifest.get('identity', {}).get('core_baseline', 'r1')
    baseline = json.loads((directory / 'objects/image-baseline/baseline.json').read_text()) if not any(n.startswith('image-baseline/') for n in bad) else {}
    image_runtime = baseline.get('baseline', {}).get('image_baseline', {}).get('bundle')
    required |= {name for name in bad if any(name.startswith('image/' + bundle + '/') for bundle in (core, image_runtime) if bundle)}
    if required:
        raise ColdRestoreError('current environment or required recovery material unavailable; service blocked')
    return document, {'incomplete_backup': bool(bad), 'unrestorable_revision_ids': sorted(unrestorable),
                      'warnings': ['INCOMPLETE BACKUP: historical revisions are unrestorable; preserve and repair backup material'] if bad else [],
                      'unavailable_objects': sorted(bad)}


def restore_files(archive: Path, paths: ExecutorPaths) -> None:
    """Packaging of existing local backup config.yml + files/, not a new backup."""
    with tarfile.open(archive) as backup:
        members = backup.getmembers()
        for member in members:
            name = Path(member.name)
            if (name.is_absolute() or '..' in name.parts or not name.parts or
                    name.parts[0] not in ('config.yml', 'files') or
                    (name.parts[0] == 'config.yml' and (len(name.parts) != 1 or not member.isfile())) or
                    not (member.isdir() or member.isfile())):
                raise ColdRestoreError('config/files archive must contain only regular config.yml and files/')
        if not any(member.name == 'config.yml' for member in members):
            raise ColdRestoreError('backup configuration missing')
        for member in members:
            target = paths.config_file if member.name == 'config.yml' else paths.da_root / member.name
            if any(p.is_symlink() for p in (target, *target.parents)):
                raise ColdRestoreError('restore destination symlink refused')
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                source = backup.extractfile(member)
                if source is None:
                    raise ColdRestoreError('regular backup file unavailable')
                with source, target.open('wb') as output:
                    shutil.copyfileobj(source, output)
                os.chmod(target, member.mode & 0o777)
                os.chown(target, member.uid, member.gid)
        for member in reversed(members):
            if member.isdir():
                target = paths.da_root / member.name
                os.chmod(target, member.mode & 0o777)
                os.chown(target, member.uid, member.gid)


def image_owned_directories(document: dict) -> set[Path]:
    """Image bytes and their ancestor metadata belong to the replacement image.

    Ancestors are included even when a parent also contains restored managed
    state, because the replacement image owns that path either way.
    """
    return {parent for name, info in document['files'].items()
            if name.startswith(('image/', 'image-baseline/'))
            for parent in Path(info['source']).parents}


def restore_objects(directory: Path, document: dict, report: dict) -> None:
    image_directories = image_owned_directories(document)
    for source, info in sorted(document['directories'].items(), key=lambda item: len(Path(item[0]).parts)):
        target = Path(source)
        if not target.is_absolute() or any(p.is_symlink() for p in (target, *target.parents)):
            raise ColdRestoreError('managed restore destination symlink refused')
        # Image bytes and their ancestor metadata belong to the replacement
        # image, even when a parent also contains restored managed state.
        if target in image_directories:
            continue
        target.mkdir(parents=True, exist_ok=True)
        os.chmod(target, info['mode'])
        os.chown(target, info['uid'], info['gid'])
    for name, info in document['files'].items():
        # Image-owned material (manifest, qualified baseline, image core bundles)
        # belongs to the replacement image; never overwrite it from the backup.
        if name in report['unavailable_objects'] or name.startswith('image/') or name.startswith('image-baseline/'):
            continue
        target = Path(info['source'])
        if str(target.parent) not in document['directories'] or target.is_symlink():
            raise ColdRestoreError('managed restore destination invalid')
        shutil.copyfile(directory / 'objects' / name, target)
        os.chmod(target, info['mode'])
        os.chown(target, info['uid'], info['gid'])


def historical_success(journal: Journal, attempt: dict, snapshot: dict) -> bool:
    """Reconcile a lost DB success report as HISTORY, never local health."""
    from .roles import RUNTIME_ROLES, INFRASTRUCTURE_ROLES
    receipt, operation = journal.attempt_result, journal.operation
    identity = receipt.get('identity') if receipt else None
    return bool(receipt and operation and isinstance(identity, dict) and
        identity.get('attempt_id') == attempt['id'] and identity.get('revision_id') == attempt['revision_id'] and
        identity.get('bundle_digest') == snapshot['publication']['result']['bundle']['digest'] and
        identity.get('target') == snapshot['target'] and journal.phase == 'verified' and
        journal.verified_revision == attempt['revision_id'] and operation.id == attempt['id'] and
        operation.revision == attempt['revision_id'] and operation.binding == receipt.get('identity') == attempt['record'].get('claim') and
        receipt.get('outcome') == 'applied-normal' and receipt.get('normal_verified') is True and
        receipt.get('child_safe') is True and receipt.get('mutation_started') is True and receipt.get('failure') is None and
        receipt.get('inventory') == {name: value['version'] for name, value in snapshot['publication']['result']['bundle']['inventory'].items()} and
        receipt.get('readiness') == {'schema_verified': True, 'roles': sorted(RUNTIME_ROLES + INFRASTRUCTURE_ROLES)})


def establish_incarnation(service, paths: ExecutorPaths, authority: dict, report: dict) -> dict:
    """Keep copied attempts/schema as history; never transfer their live authority."""
    journal = Journal.load(paths.journal_file)
    with service.engine.begin() as connection:
        environment = service._environment(connection)
        previous_restore = environment['snapshot'].get('cold_restore')
        historical_hosts = connection.execute(select(service.tables['host_environment'].c.id)).scalars().all()
        if (authority['incarnation'] in historical_hosts or
                previous_restore and previous_restore['incarnation'] == authority['incarnation']):
            raise ColdRestoreError('new restore requires a fresh runtime incarnation')
        candidates = service.tables['candidate']
        connection.execute(update(candidates).where(candidates.c.state.in_(('queued', 'preparing', 'ready'))).values(
            state='cancelled', fence=candidates.c.fence + 1, claim_until=None))
        materials = service.tables['candidate_material']
        disposable = connection.execute(select(materials).where(materials.c.committed.is_(False))).mappings().all()
        connection.execute(update(materials).where(materials.c.committed.is_(False)).values(usable=False, deleted=True))
        snapshot = dict(environment['snapshot'])
        if snapshot.get('runtime', {}).get('kind') == 'normal':
            snapshot['last_normally_verified'] = snapshot['runtime']
        snapshot['runtime'] = {'kind': 'unknown', 'inventory': {}, 'reason': 'cold-restore'}
        attempts = service.tables['installation_attempt']
        latest = connection.execute(select(attempts).where(attempts.c.revision_id == environment['desired_revision_id']).order_by(
            attempts.c.record['created_at'].as_string().desc(), attempts.c.id).limit(1)).mappings().first()
        revision = connection.scalar(select(service.tables['revision'].c.snapshot).where(
            service.tables['revision'].c.id == environment['desired_revision_id']))
        if not revision:
            raise ColdRestoreError('restored desired revision missing; repair required')
        reconciled = bool(latest and latest['record'].get('phase') == 'running' and historical_success(journal, latest, revision))
        blocked = (not latest or (latest['record'].get('phase') not in ('pending', 'succeeded') and not reconciled) or
                   journal.schema_uncertain or bool(journal.failure) or journal.phase not in ('idle', 'verified'))
        active = environment['active_attempt_id']
        if active:
            row = connection.execute(select(attempts).where(attempts.c.id == active)).mappings().one()
            record = dict(row['record'], restore_superseded_by=authority['incarnation'])
            if reconciled and row['id'] == latest['id']:
                record.update(phase='succeeded', terminal=journal.attempt_result)
            elif record.get('phase') in ('pending', 'running'):
                record.update(phase='blocked', fence=record.get('fence', 0) + 1)
            connection.execute(update(attempts).where(attempts.c.id == active).values(record=record))
        restored = dict(authority, **report, state='blocked' if blocked else 'pending',
                        source_attempt_id=latest['id'] if latest else None, historical_success_reconciled=reconciled,
                        established_at=utcnow())
        if journal.schema_succeeded and journal.schema is not None and journal.schema.revision == environment['desired_revision_id']:
            restored['schema_success'] = journal.to_dict()['schema']
        elif latest and (latest['record'].get('phase') == 'succeeded' or reconciled):
            if revision.get('schema_mode') != 'unchanged':
                restored['state'] = 'blocked'
        if previous_restore:
            restored['previous_incarnation'] = previous_restore['incarnation']
        snapshot['cold_restore'] = restored
        connection.execute(update(service.tables['environment_state']).where(service.tables['environment_state'].c.id == 1).values(
            active_attempt_id=None, snapshot=snapshot, runtime_generation=environment['runtime_generation'] + 1))
    # Candidate-only paths are trusted stored paths, but never follow symlinks or
    # delete shared/committed bytes. Never touch a restored committed credential.
    committed_paths = {row['path'] for row in read_materials(service) if row['committed']}
    for row in disposable:
        if not row['shared'] and row['path'] not in committed_paths:
            target = Path(row['path'])
            if any(parent.is_symlink() for parent in target.parents):
                raise ColdRestoreError('candidate material cleanup symlink refused')
            target.unlink(missing_ok=True)
    _atomic_write_json(paths.state_dir / 'installation-host.json', {'id': authority['incarnation']})
    _atomic_write_json(paths.state_dir / 'cold-restore.json', restored)
    return restored


def read_materials(service):
    with service.engine.connect() as connection:
        return connection.execute(select(service.tables['candidate_material'])).mappings().all()


def require_incarnation(service, paths: ExecutorPaths) -> None:
    from .checkpoint_restore import require_restore_ready
    require_restore_ready(paths)
    with service.engine.connect() as connection:
        environment = connection.execute(select(service.tables['environment_state'])).mappings().one()
    restored = environment['snapshot'].get('cold_restore')
    if restored:
        local = json.loads((paths.state_dir / 'cold-restore.json').read_text())
        host = json.loads((paths.state_dir / 'installation-host.json').read_text())
        if local['incarnation'] != restored['incarnation'] or host['id'] != restored['incarnation']:
            raise ColdRestoreError('foreign restore incarnation; executor authority refused')


def bootstrap(paths: ExecutorPaths, dump: Path, files: Path, authorization_file: Path, database_url: str, prefix: str) -> dict:
    authority = authorization(authorization_file, dump)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            if connection.scalar(text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema'))")):
                raise ColdRestoreError('replacement database must be empty; live database restore refused')
    finally:
        engine.dispose()
    directory = Path(str(dump) + '.managed')
    document, report = inspect_backup(dump, directory, prefix)
    with MutationLock(paths.state_dir), RuntimeLock(paths.state_dir):
        if (paths.state_dir / 'cold-restore.json').exists():
            raise ColdRestoreError('restore already initialized; explicit operator recovery required')
        Admission(paths.state_dir).close('cold restore bootstrap; no copied health authority')
        _atomic_write_json(paths.state_dir / 'cold-restore.json', dict(authority, state='restoring'))
        # A different-target restore is migration (ticket 19): preserve the
        # replacement image's identity and reject before overwriting anything.
        replacement_image = json.loads(paths.manifest_file.read_text())
        backup_image = json.loads((directory / 'objects/image/manifest.json').read_text())
        if (backup_image.get('base_image_digest') != replacement_image.get('base_image_digest') or
                backup_image.get('identity') != replacement_image.get('identity')):
            raise ColdRestoreError('different-target restore requires migration; restore onto the recorded image')
        restore_files(files, paths)
        restore_objects(directory, document, report)
        _atomic_write_json(paths.state_dir / 'cold-restore-inventory.json', document)
        url = make_url(database_url)
        env = dict(os.environ, PGDATABASE=url.database or '', PGUSER=url.username or '', PGPASSWORD=url.password or '',
                   PGHOST=url.host or '', PGPORT=str(url.port or 5432))
        for option, variable in {'options': 'PGOPTIONS', 'sslmode': 'PGSSLMODE', 'sslcert': 'PGSSLCERT',
                                 'sslkey': 'PGSSLKEY', 'sslrootcert': 'PGSSLROOTCERT', 'sslcrl': 'PGSSLCRL'}.items():
            if option in url.query:
                env[variable] = str(url.query[option])
        subprocess.run(['pg_restore', '--exit-on-error', '-d', url.database or '', str(dump)], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        engine = create_engine(database_url)
        try:
            from .installation import Installation
            service = Installation(engine, lambda *_: False, prefix)
            with engine.connect() as connection:
                if digest(canonical_records(read_records(connection, prefix))) != document['records_digest']:
                    raise ColdRestoreError('restored DB does not match managed snapshot')
            result = establish_incarnation(service, paths, authority, report)
        finally:
            engine.dispose()
        return result


def main(argv=None):
    parser = RestoreArgumentParser(description=__doc__)
    parser.add_argument('--dump', type=Path, required=True)
    parser.add_argument('--files', type=Path, required=True, help='tar of existing backup config.yml and files/')
    parser.add_argument('--authorization', type=Path, required=True)
    parser.add_argument('--prefix', default='')
    args = parser.parse_args(argv)
    try:
        database_url = restore_database_url('DA_RESTORE_DATABASE_URL')
        result = bootstrap(ExecutorPaths.from_env(), args.dump, args.files, args.authorization, database_url, args.prefix)
        for warning in result['warnings']:
            print(warning, flush=True)
        print('cold restore inputs complete; new incarnation established; boot must verify exact desired runtime', flush=True)
        return 0
    except Exception:
        if not os.environ.get('DA_RESTORE_DATABASE_URL', '').strip():
            print('DA_RESTORE_DATABASE_URL is required; restore refused', flush=True)
        else:
            print('cold restore refused; inspect authorization, snapshot and mandatory objects; no substitution', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
