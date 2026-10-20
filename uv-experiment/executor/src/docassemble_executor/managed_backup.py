"""Snapshot-bound managed objects for the existing full-backup destinations.

The environment row is also the publication/material-cleanup lock. Keep it until
all snapshot references have been copied; retained history is never collected.
This is NOT a coordinated interview database/files checkpoint or runtime lease.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

from sqlalchemy import create_engine, select, text

from docassemble.base.managed_artifacts import open_regular
from docassemble.base.managed_records import preparation_tables
from docassemble.base.managed_preparation import digest
from .bundles import verify_bundle
from .paths import ExecutorPaths
from .registry_builder import verify_retained


RECORDS = ('revision', 'environment_state', 'installation_attempt', 'package_identity',
           'requirement_contribution', 'observed_config', 'host_environment', 'candidate_material')
JSON_COLUMNS = {'snapshot', 'record', 'observation'}
HASH = re.compile('[0-9a-f]{64}')


class ManagedBackupError(RuntimeError):
    """Fixed diagnostics only: source bytes/URLs/credentials must not be logged."""


def read_records(connection, prefix: str) -> dict:
    tables = preparation_tables(prefix)
    return {name: [dict(row) for row in connection.execute(select(tables[name])).mappings()]
            for name in RECORDS}


def _copy_value(value: str):
    if value == r'\N':
        return None
    # PostgreSQL COPY text escaping (not Python's unicode_escape, which corrupts
    # non-ASCII intent). pg_dump emits control characters as these escapes.
    escapes = {'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t', 'v': '\v', '\\': '\\'}
    return re.sub(r'\\([bfnrtv\\])', lambda match: escapes[match[1]], value)


def dumped_records(dump: Path, prefix: str) -> dict:
    """Read the actual archive's managed COPY rows, never query a later live DB."""
    if not re.fullmatch('[A-Za-z0-9_]*', prefix):
        raise ManagedBackupError('invalid managed table prefix')
    names = {prefix + 'managed_' + name: name for name in RECORDS}
    command = ['pg_restore', '--data-only', '--file=-']
    for name in names:
        command += ['--table=' + name]
    # Stream to a private temporary file rather than putting credential-bearing
    # archive output on stdout or retaining an unbounded subprocess capture.
    import tempfile
    records: dict[str, list] = {name: [] for name in RECORDS}
    seen = set()
    with tempfile.TemporaryFile(mode='w+', encoding='utf-8') as output:
        subprocess.run(command + [str(dump)], stdout=output, stderr=subprocess.DEVNULL, check=True)
        output.seek(0)
        active = None
        columns = []
        for line in output:
            line = line.rstrip('\n')
            if active:
                if line == r'\.':
                    active = None
                    continue
                values = line.split('\t')
                if len(values) != len(columns):
                    raise ManagedBackupError('invalid managed dump rows')
                row = dict(zip(columns, map(_copy_value, values)))
                for key, value in row.items():
                    if key in JSON_COLUMNS and value is not None:
                        row[key] = json.loads(value)
                    elif key in ('managed', 'usable', 'committed', 'shared', 'deleted', 'active'):
                        row[key] = value == 't'
                    elif key in ('publication_generation', 'input_generation', 'target_generation',
                                 'runtime_generation', 'generation', 'legacy_package_id') and value is not None:
                        row[key] = int(value)
                    elif key == 'id' and active == 'environment_state':
                        row[key] = int(value)
                records[active].append(row)
            elif line.startswith('COPY '):
                match = re.fullmatch(r'COPY (?:"?[A-Za-z0-9_]+"?\.)?"?([A-Za-z0-9_]+)"? \(([^)]+)\) FROM stdin;', line)
                if not match or match[1] not in names:
                    raise ManagedBackupError('unsupported managed dump COPY format')
                active = names[match[1]]
                seen.add(active)
                columns = [column.strip(' "') for column in match[2].split(',')]
        if active or seen != set(RECORDS):
            raise ManagedBackupError('managed tables missing from dump')
    return records


def canonical_records(records: dict) -> dict:
    # Candidate-only objects are deliberately outside the managed object set.
    return {name: sorted((row for row in rows if name != 'candidate_material' or row['committed']),
                          key=lambda row: json.dumps(row, sort_keys=True))
            for name, rows in records.items()}


def references(records: dict, local_baselines: dict | None = None) -> dict:
    """All retained revisions, not only desired; never destructive history GC."""
    bundles, artifacts, credentials, indexes = set(), set(), set(), set()
    image_bundles: dict[str, set[str]] = {}

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ('bundle', 'core_bundle') and isinstance(child, dict) and 'digest' in child:
                    bundles.add(child['digest'])
                if key == 'image_baseline' and isinstance(child, dict) and 'bundle' in child:
                    name, expected = child['bundle'], child.get('bundle_digest')
                    if not isinstance(name, str) or Path(name).name != name or not isinstance(expected, str) or not HASH.fullmatch(expected):
                        raise ManagedBackupError('invalid recorded image bundle reference')
                    image_bundles.setdefault(expected, set()).add(name)
                if key == 'artifact_ref' and child:
                    artifacts.add(child)
                if key == 'direct_sources':
                    artifacts.update(source['digest'] for source in child.values())
                if key == 'git_credentials':
                    credentials.update(child.values())
                if key == 'private_indexes':
                    indexes.update(child)
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for name in ('revision', 'environment_state', 'requirement_contribution'):
        for row in records[name]:
            walk(row)
    walk(local_baselines or {})
    materials = {row['id']: row for row in records['candidate_material']}
    paths = set()
    for reference in credentials:
        row = materials.get(reference)
        if not row or not row['committed'] or row['deleted']:
            raise ManagedBackupError('required persistent Git authentication unavailable')
        paths.add(row['path'])
    for row in materials.values():
        if row['committed']:
            if row['deleted']:
                raise ManagedBackupError('retained mandatory material is deleted')
            paths.add(row['path'])
    if any(not isinstance(value, str) or not HASH.fullmatch(value) for value in bundles | artifacts):
        raise ManagedBackupError('invalid managed object digest')
    if any(not re.fullmatch('[a-z][a-z0-9_]*', value) for value in indexes):
        raise ManagedBackupError('invalid private index reference')
    return {'bundles': sorted(bundles), 'artifacts': sorted(artifacts),
            'material_paths': sorted(paths), 'indexes': sorted(indexes),
            'image_bundles': {name: sorted(values) for name, values in sorted(image_bundles.items())}}


def require_baseline_wheel_map(descriptor: dict) -> None:
    wheels = descriptor.get('wheels')
    core = descriptor.get('baseline', {}).get('core')
    if not isinstance(wheels, dict) or not isinstance(core, dict) or set(wheels) != set(core):
        raise ManagedBackupError('image baseline wheel map must cover core exactly')


def revision_image_material(records: dict, manifest: dict) -> dict:
    """Historical image digests are provenance; retained revision bundles restore.

    Replacement images do not retain obsolete image projects. A historical
    image reference is complete only with its exact retained publication bundle
    (which includes the core), and any independently selected core bundle.
    """
    material = {}
    for row in records['revision']:
        snapshot = row['snapshot']
        image = snapshot.get('image_baseline') or {}
        if not image.get('bundle'):
            continue
        expected = image.get('bundle_digest')
        if not isinstance(expected, str) or not HASH.fullmatch(expected):
            raise ManagedBackupError('invalid recorded image bundle reference')
        archived = manifest.get('bundles', {}).get(image['bundle']) == expected
        publication = snapshot.get('publication', {}).get('result', {}).get('bundle', {})
        retained = publication.get('digest')
        if not archived and (not isinstance(retained, str) or not HASH.fullmatch(retained)):
            raise ManagedBackupError('historical image revision lacks retained restore bundle')
        subset = dict(records, revision=[row], environment_state=[], requirement_contribution=[])
        material[row['id']] = {'image_bundle_digest': expected, 'image_bundle_name': image['bundle'],
                               'image_bytes_archived': archived,
                               'restore_bundle_digests': references(subset)['bundles']}
    return material


def verify_historical_restore_material(records: dict, manifest: dict, store: Path) -> None:
    for row in records['revision']:
        snapshot = row['snapshot']
        image = snapshot.get('image_baseline') or {}
        if not image.get('bundle') or manifest.get('bundles', {}).get(image['bundle']) == image.get('bundle_digest'):
            continue
        bundle = snapshot.get('publication', {}).get('result', {}).get('bundle', {})
        try:
            retained = store / bundle['digest']
            verify_retained(retained, bundle['digest'])
            if (_sha(retained / 'pyproject.toml') != bundle['project_digest'] or
                    _sha(retained / 'uv.lock') != bundle['lock_digest'] or
                    json.loads((retained / 'inventory.json').read_text()) != bundle['inventory']):
                raise ValueError
        except (KeyError, TypeError, ValueError, OSError, RuntimeError):
            raise ManagedBackupError('historical revision retained restore material invalid') from None


def _regular(path: Path) -> None:
    # Check every component; operator paths do not authorize symlink traversal.
    if any(part.is_symlink() for part in (path, *path.parents)) or not stat.S_ISREG(path.stat().st_mode):
        raise ManagedBackupError('required managed object is not a regular file')


def _sha(path: Path) -> str:
    _regular(path)
    with open_regular(path) as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


class ObjectCopy:
    def __init__(self, destination: Path):
        self.destination = destination
        self.files: dict[str, dict] = {}
        self.directories: dict[str, dict] = {}

    def directory(self, source: Path):
        if any(part.is_symlink() for part in (source, *source.parents)) or not source.is_dir():
            raise ManagedBackupError('required managed directory unavailable')
        info = source.stat()
        self.directories[str(source)] = {'mode': stat.S_IMODE(info.st_mode), 'uid': info.st_uid, 'gid': info.st_gid}

    def file(self, source: Path, relative: str):
        _regular(source)
        self.directory(source.parent)
        if relative in self.files:
            if self.files[relative]['source'] != str(source):
                raise ManagedBackupError('managed backup object collision')
            return
        target = self.destination / 'objects' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        # Use one no-follow regular-file descriptor for hashing and copying.
        # Credential rotation may replace the pathname; it must not redirect a
        # privileged backup reader to unrelated files between those operations.
        with open_regular(source) as handle:
            info = os.fstat(handle.fileno())
            before = hashlib.file_digest(handle, 'sha256').hexdigest()
            handle.seek(0)
            with target.open('xb') as output:
                shutil.copyfileobj(handle, output)
        os.chmod(target, stat.S_IMODE(info.st_mode))
        os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
        if _sha(target) != before:
            raise ManagedBackupError('managed object copy changed')
        self.files[relative] = {'source': str(source), 'sha256': before,
                                'mode': stat.S_IMODE(info.st_mode),
                                'uid': info.st_uid, 'gid': info.st_gid}

    def tree(self, source: Path, relative: str, *, exclude_disposable: bool = False):
        self.directory(source)
        for path in sorted(source.rglob('*')):
            # Match image-bundle hash_project's exclusion of generated source
            # build/cache material. Retained manifest-listed bytes never use
            # this filter: every one of those bytes remains mandatory.
            if exclude_disposable and any(part in ('__pycache__', '.venv', 'build', '.pytest_cache') or
                                          part.endswith('.egg-info') for part in path.relative_to(source).parts):
                continue
            if path.is_symlink():
                raise ManagedBackupError('managed backup symlink refused')
            if path.is_dir():
                self.directory(path)
            else:
                self.file(path, relative + '/' + path.relative_to(source).as_posix())


def copy_objects(records: dict, paths: ExecutorPaths, destination: Path, *, store: Path,
                 baseline: Path, artifact_roots: list[Path], secret_root: Path | None,
                 index_root: Path | None) -> dict:
    copied = ObjectCopy(destination)
    for root in (paths.state_dir, paths.bundles_dir, baseline.parent):
        copied.directory(root)
    selected = paths.state_dir / 'published-baselines.json'
    local_baselines = {}
    if selected.exists() or selected.is_symlink():
        copied.file(selected, 'state/published-baselines.json')
        local_baselines = json.loads((destination / 'objects/state/published-baselines.json').read_text())
    refs = references(records, local_baselines)
    if refs['bundles']:
        copied.directory(store)
    for reference in refs['bundles']:
        directory = store / reference
        verify_retained(directory, reference)
        copied.tree(directory, 'retained/' + reference)
    for source in refs['material_paths']:
        path = Path(source)
        roots = artifact_roots + ([secret_root] if secret_root else [])
        if path.parent not in roots:
            raise ManagedBackupError('required material outside configured stores')
        copied.file(path, 'material/' + digest(str(path)))
    for reference in refs['artifacts']:
        candidates = [root / reference for root in artifact_roots if (root / reference).exists()]
        if not candidates or any(_sha(path) != reference for path in candidates):
            raise ManagedBackupError('required retained distribution unavailable')
        for path in candidates:
            copied.file(path, 'material/' + digest(str(path)))
    for label in refs['indexes']:
        if index_root is None:
            raise ManagedBackupError('required private index authentication unavailable')
        copied.file(index_root / label, 'index/' + label)
    # The original image's full/core recovery material remains mandatory even
    # after a web-selected baseline. Do not copy unrelated image candidates.
    manifest = json.loads(paths.manifest_file.read_text())
    copied.file(paths.manifest_file, 'image/manifest.json')
    descriptor = json.loads(baseline.read_text())
    copied.file(baseline, 'image-baseline/baseline.json')
    image_material = revision_image_material(records, manifest)
    verify_historical_restore_material(records, manifest, store)
    image_names = {descriptor['baseline']['image_baseline']['bundle'],
                   'core-' + manifest.get('identity', {}).get('core_baseline', 'r1')}
    image_names.update(name for expected, names in refs['image_bundles'].items() for name in names
                       if manifest['bundles'].get(name) == expected)
    for name in image_names:
        if Path(name).name != name or not verify_bundle(paths.bundles_dir / name, manifest, name)[0]:
            raise ManagedBackupError('required image recovery bundle unavailable')
        copied.tree(paths.bundles_dir / name, 'image/' + name, exclude_disposable=True)
    require_baseline_wheel_map(descriptor)
    for name, filename in descriptor['wheels'].items():
        if Path(filename).name != filename or _sha(baseline.parent / 'wheels' / filename) != descriptor['baseline']['core'][name]['source_digest']:
            raise ManagedBackupError('required image baseline artifact unavailable')
        copied.file(baseline.parent / 'wheels' / filename, 'image-baseline/wheels/' + filename)
    # Local evidence is not copied as live authority. Admission, PID/worker state,
    # uv cache, candidate staging and runtime venv are deliberately excluded.
    for name in ('desired.json', 'applied.json', 'lifecycle.json', 'initialized.json',
                 'published-baselines.json', 'schema-clearance.json', 'rollback-schema-check.json'):
        source = paths.state_dir / name
        if name == 'lifecycle.json' or source.exists() or source.is_symlink():
            copied.file(source, 'state/' + name)
    history = paths.state_dir / 'attempt-history'
    if history.exists():
        copied.tree(history, 'state/attempt-history')
    return {'references': refs, 'files': copied.files, 'directories': copied.directories,
            'local_baselines': local_baselines, 'revision_image_material': image_material}


def verify_backup(dump: Path, directory: Path, prefix: str = '', *, manifest_name: str = 'manifest.json') -> dict:
    document = json.loads((directory / manifest_name).read_text())
    if (document.get('format') != 1 or document.get('managed_objects_complete') is not True or
            _sha(dump) != document['dump_sha256']):
        raise ManagedBackupError('managed backup dump binding mismatch')
    actual = canonical_records(dumped_records(dump, prefix))
    if digest(actual) != document['records_digest'] or references(actual, document['local_baselines']) != document['references']:
        raise ManagedBackupError('managed backup snapshot references mismatch')
    files = document['files']
    present = {path.relative_to(directory / 'objects').as_posix() for path in (directory / 'objects').rglob('*') if not path.is_dir()}
    if present != set(files) or any(_sha(directory / 'objects' / name) != record['sha256'] for name, record in files.items()):
        raise ManagedBackupError('managed backup objects incomplete')
    if json.loads((directory / 'records.json').read_text()) != actual:
        raise ManagedBackupError('managed backup records mismatch')
    objects = directory / 'objects'
    for reference in document['references']['bundles']:
        verify_retained(objects / 'retained' / reference, reference)
    sources = {entry['source']: objects / name for name, entry in files.items()}
    if any(str(Path(source).parent) not in document['directories'] for source in sources):
        raise ManagedBackupError('managed restore directory metadata incomplete')
    for source in document['references']['material_paths']:
        if source not in sources:
            raise ManagedBackupError('mandatory material missing from inventory')
    for reference in document['references']['artifacts']:
        if not any(Path(source).name == reference and _sha(path) == reference for source, path in sources.items()):
            raise ManagedBackupError('mandatory artifact missing from inventory')
    for label in document['references']['indexes']:
        if 'index/' + label not in files:
            raise ManagedBackupError('mandatory index authentication missing from inventory')
    if 'state/lifecycle.json' not in files:
        raise ManagedBackupError('mandatory lifecycle evidence missing from inventory')
    if document['local_baselines'] and json.loads((objects / 'state/published-baselines.json').read_text()) != document['local_baselines']:
        raise ManagedBackupError('selected baseline evidence mismatch')
    image_manifest = json.loads((objects / 'image/manifest.json').read_text())
    baseline = json.loads((objects / 'image-baseline/baseline.json').read_text())
    verify_historical_restore_material(actual, image_manifest, objects / 'retained')
    if document.get('revision_image_material') != revision_image_material(actual, image_manifest):
        raise ManagedBackupError('historical image restore provenance mismatch')
    image_names = {baseline['baseline']['image_baseline']['bundle'],
                   'core-' + image_manifest.get('identity', {}).get('core_baseline', 'r1')}
    image_names.update(name for expected, names in document['references']['image_bundles'].items() for name in names
                       if image_manifest['bundles'].get(name) == expected)
    for name in image_names:
        if not verify_bundle(objects / 'image' / name, image_manifest, name)[0]:
            raise ManagedBackupError('mandatory image recovery material incomplete')
    require_baseline_wheel_map(baseline)
    for name, filename in baseline['wheels'].items():
        if _sha(objects / 'image-baseline/wheels' / filename) != baseline['baseline']['core'][name]['source_digest']:
            raise ManagedBackupError('mandatory image artifact incomplete')
    return document


def create_backup(engine, dump: Path, paths: ExecutorPaths, *, prefix: str, store: Path,
                  baseline: Path, artifact_roots: list[Path], secret_root: Path | None = None,
                  index_root: Path | None = None, checkpoint_only: bool = False) -> dict:
    destination = Path(str(dump) + '.managed')
    # A new private assembly only. Interrupted copies can never inherit a prior
    # complete marker; callers use a fresh existing-backup temporary directory.
    destination.mkdir(mode=0o700)
    tables = preparation_tables(prefix)
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        with connection.begin():
            environment = connection.execute(select(tables['environment_state']).where(
                tables['environment_state'].c.id == 1).with_for_update()).mappings().one()
            if not environment['managed']:
                raise ManagedBackupError('managed backup requires managed records')
            records = canonical_records(read_records(connection, prefix))
            snapshot = connection.scalar(text('SELECT pg_export_snapshot()'))
            # libpq receives credentials through the child environment, never argv.
            url = engine.url
            env = dict(os.environ, PGDATABASE=url.database or '', PGUSER=url.username or '',
                       PGPASSWORD=url.password or '', PGHOST=url.host or '', PGPORT=str(url.port or 5432))
            for option, variable in {'options': 'PGOPTIONS', 'sslmode': 'PGSSLMODE',
                                     'sslcert': 'PGSSLCERT', 'sslkey': 'PGSSLKEY',
                                     'sslrootcert': 'PGSSLROOTCERT', 'sslcrl': 'PGSSLCRL'}.items():
                if option in url.query:
                    env[variable] = str(url.query[option])
            subprocess.run(['pg_dump', '-Fc', '--snapshot=' + snapshot, '-f', str(dump)],
                           env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            dumped = canonical_records(dumped_records(dump, prefix))
            if records != dumped:
                raise ManagedBackupError('dump does not match enumerated managed snapshot')
            material = copy_objects(dumped, paths, destination, store=store, baseline=baseline,
                                    artifact_roots=artifact_roots, secret_root=secret_root, index_root=index_root)
            (destination / 'records.json').write_text(json.dumps(dumped, sort_keys=True, indent=2))
            document = dict(format=1, managed_objects_complete=True,
                interview_checkpoint_complete=False, dump_sha256=_sha(dump),
                checkpoint_only=checkpoint_only,
                records_digest=digest(dumped), desired_revision_id=environment['desired_revision_id'],
                publication_generation=environment['publication_generation'], **material)
            # Commit marker is LAST; even SIGKILL halfway through a copy has none.
            temporary = destination / 'manifest.pending'
            temporary.write_text(json.dumps(document, sort_keys=True, indent=2))
            verify_backup(dump, destination, prefix, manifest_name='manifest.pending')
            for path in [dump, *destination.rglob('*')]:
                if path.is_file():
                    with path.open('rb') as handle:
                        os.fsync(handle.fileno())
            for path in sorted((p for p in destination.rglob('*') if p.is_dir()), reverse=True):
                _sync_directory(path)
    # No complete marker until both verification and snapshot transaction exit.
    os.replace(temporary, destination / 'manifest.json')
    _sync_directory(destination)
    _sync_directory(destination.parent)
    return document


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dump', type=Path, required=True)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args(argv)
    engine = None
    try:
        paths = ExecutorPaths.from_env()
        from .durable_work import configuration
        url, prefix = configuration(paths)
        if args.verify:
            verify_backup(args.dump, Path(str(args.dump) + '.managed'), prefix)
        else:
            engine = create_engine(url, connect_args={'connect_timeout': 5})
            create_backup(engine, args.dump, paths, prefix=prefix,
                store=Path(os.environ['DA_DURABLE_WORK_STORE']),
                baseline=Path(os.environ['DA_QUALIFIED_IMAGE_BASELINE']),
                artifact_roots=[Path(os.environ[key]) for key in ('DA_MANAGED_ARTIFACT_ROOT', 'DA_EXECUTOR_ARTIFACT_ROOT') if os.environ.get(key)],
                secret_root=Path(os.environ['DA_MANAGED_SECRET_ROOT']) if os.environ.get('DA_MANAGED_SECRET_ROOT') else None,
                index_root=Path(os.environ['DA_INDEX_CREDENTIAL_ROOT']) if os.environ.get('DA_INDEX_CREDENTIAL_ROOT') else None)
        print('backup: managed objects complete; interview data/files checkpoint NOT established')
        return 0
    except Exception:
        print('backup: managed objects incomplete; inspect required records/stores and retry', file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
