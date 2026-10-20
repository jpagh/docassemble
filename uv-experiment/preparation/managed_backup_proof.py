"""Ticket 17: real dump/restore, credential bytes and pinned-uv cold frozen sync.

Disposable fixture data only. No claim of cold-start authority (18), upstream
credential validity, or coordinated interview-file checkpoint (21–23).
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from sqlalchemy import create_engine, select

from docassemble.base.managed_git import parse_git_requirement, git_credential_material, scoped_authorization
from docassemble.base.managed_direct import parse_direct_requirement
from docassemble.base.managed_records import preparation_tables
from docassemble_executor.image_baseline import provision_image_baseline
from docassemble_executor.managed_backup import create_backup, verify_backup
from docassemble_executor.paths import ExecutorPaths
from docassemble_executor.registry_builder import RegistryBuilder, verify_retained
from direct_url_proof import wheel, baseline

ROOT = Path('/tmp/managed-backup-proof')


def run(command, **kwargs):
    return subprocess.run(command, check=True, capture_output=True, text=True, **kwargs)


def main():
    ROOT.mkdir(mode=0o700)
    assert run(['uv', '--version']).stdout.split()[:2] == ['uv', '0.12.21']
    run(['su', 'postgres', '-c', 'createuser -s root'])
    run(['createdb', 'managed_backup_proof'])
    engine = create_engine('postgresql+psycopg2:///managed_backup_proof')
    tables = preparation_tables()
    with engine.begin() as connection:
        next(iter(tables.values())).metadata.create_all(connection)
    paths = ExecutorPaths.from_env()
    paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    paths.config_file.write_text('db:\n  name: managed_backup_proof\n  user: root\n')
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    (paths.state_dir / 'lifecycle.json').write_text('{"phase":"verified","schema_phase":"succeeded"}')
    descriptor = provision_image_baseline(paths, ROOT / 'image-baseline')
    artifacts, retained, secrets = (ROOT / name for name in ('artifacts', 'retained', 'secrets'))
    artifacts.mkdir(mode=0o700)
    secrets.mkdir(mode=0o700)
    os.environ['DA_MANAGED_ARTIFACT_UID'] = str(os.getuid())
    filename, data = wheel('BackupDirect', '1.0')
    declaration = 'BackupDirect @ https://fixture:DIRECT_FIXTURE_TOKEN@example.invalid/' + filename
    requirement, source, _ = parse_direct_requirement(declaration)
    inputs = {'baseline': baseline(), 'contributions': [], 'requirement': requirement.__dict__,
              'direct_source': source, 'ownership_effects': {}}
    # Preparation retains its accepted dedicated non-root build boundary.
    import pwd
    account = pwd.getpwnam('da-environment')
    for path in (ROOT, artifacts):
        os.chown(path, account.pw_uid, account.pw_gid)
    child = os.fork()
    if child == 0:
        try:
            os.setgid(account.pw_gid)
            os.setuid(account.pw_uid)
            result = RegistryBuilder(retained, artifact_root=artifacts, direct_download=lambda url: data).build(inputs)
            (ROOT / 'build-result.json').write_text(json.dumps(result))
        except Exception:
            import traceback
            traceback.print_exc()
            os._exit(1)
        os._exit(0)
    assert os.waitpid(child, 0)[1] == 0
    result = json.loads((ROOT / 'build-result.json').read_text())
    reference = result['bundle']['digest']
    assert result['direct_sources']['backupdirect']['digest'] == hashlib.sha256(data).hexdigest()
    _, git_source, credential = parse_git_requirement('BackupGit @ git+https://fixture:GIT_FIXTURE_PAT@example.invalid/repo@main')
    secret = git_credential_material(git_source['repository'], credential)
    (secrets / 'credential').write_bytes(secret)
    (secrets / 'credential').chmod(0o600)
    (secrets / 'candidate-only').write_text('DO_NOT_BACK_UP_CANDIDATE')
    snapshot = dict(baseline(), publication={'result': result}, direct_sources=result['direct_sources'],
                    git_credentials={'backupgit': 'credential'})
    with engine.begin() as connection:
        connection.execute(tables['revision'].insert().values(id='retained', snapshot=snapshot))
        connection.execute(tables['environment_state'].insert().values(id=1, managed=True,
            desired_revision_id='retained', publication_generation=1, input_generation=1,
            target_generation=1, runtime_generation=1, snapshot=snapshot))
        connection.execute(tables['candidate_material'].insert().values(id='credential',
            path=str(secrets / 'credential'), usable=True, committed=True, shared=False, deleted=False))
    dump = ROOT / 'database'
    document = create_backup(engine, dump, paths, prefix='', store=retained, baseline=descriptor,
                             artifact_roots=[artifacts], secret_root=secrets)
    directory = Path(str(dump) + '.managed')
    assert verify_backup(dump, directory) == document
    assert all('candidate-only' not in item['source'] for item in document['files'].values())
    assert document['managed_objects_complete'] and not document['interview_checkpoint_complete']
    print('PASS snapshot-bound manifest, full/core image recovery, retained bundle/artifact and persistent PAT; candidates excluded', flush=True)
    backup_env = dict(os.environ, DA_UV_MANAGED='true', DBNAME='managed_backup_proof',
        DBTYPE='postgresql', CONTAINERROLE='sql', DABACKUPDAYS='0',
        DA_PREPARATION_DATABASE_URL='postgresql+psycopg2:///managed_backup_proof',
        DA_DURABLE_WORK_STORE=str(retained), DA_QUALIFIED_IMAGE_BASELINE=str(descriptor),
        DA_MANAGED_ARTIFACT_ROOT=str(artifacts), DA_MANAGED_SECRET_ROOT=str(secrets))
    run(['bash', '/repo/Docker/backup.sh'], env=backup_env)
    current = paths.da_root / 'backup/postgres/managed_backup_proof'
    assert verify_backup(current, Path(str(current) + '.managed'))['managed_objects_complete']
    print('PASS callable backup.sh independently captures manifest+dump in existing current PostgreSQL destination', flush=True)

    # Restore a real separate PostgreSQL database and the mandatory objects from
    # this backup only. Delete source stores first; no copied cache/venv is used.
    shutil.rmtree(retained)
    shutil.rmtree(artifacts)
    shutil.rmtree(secrets)
    run(['createdb', 'managed_backup_restored'])
    run(['pg_restore', '--exit-on-error', '-d', 'managed_backup_restored', str(dump)])
    for source, item in sorted(document['directories'].items(), key=lambda pair: len(Path(pair[0]).parts)):
        destination = Path(source)
        destination.mkdir(parents=True, exist_ok=True)
        os.chmod(destination, item['mode'])
        os.chown(destination, item['uid'], item['gid'])
    for name, item in document['files'].items():
        destination = Path(item['source'])
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(directory / 'objects' / name, destination)
        os.chmod(destination, item['mode'])
        os.chown(destination, item['uid'], item['gid'])
    restored = create_engine('postgresql+psycopg2:///managed_backup_restored')
    with restored.connect() as connection:
        restored_snapshot = connection.scalar(select(tables['revision'].c.snapshot))
    assert restored_snapshot == snapshot
    assert (secrets / 'credential').read_bytes() == secret
    authorization = scoped_authorization(git_source['repository'], (secrets / 'credential').read_bytes())
    assert authorization and base64.b64decode(authorization.removeprefix('Basic ')) == b'fixture:GIT_FIXTURE_PAT'
    assert 'DIRECT_FIXTURE_TOKEN' in restored_snapshot['direct_sources']['backupdirect']['url']
    verify_retained(retained / reference, reference)
    # Pinned offline frozen sync from the restored bundle into a brand-new venv,
    # new empty cache, with no upstream or original managed store available.
    venv = ROOT / 'restored-venv'
    run(['uv', 'venv', '--python', '/usr/share/docassemble/local3.12/bin/python', str(venv)])
    env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(venv), UV_CACHE_DIR=str(ROOT / 'empty-cache'), UV_NO_CACHE='1')
    run(['uv', 'sync', '--frozen', '--offline', '--no-build', '--no-install-project', '--project', str(retained / reference)], env=env)
    assert run([str(venv / 'bin/python'), '-c', 'import BackupDirect; print(BackupDirect.VALUE)']).stdout.strip() == '1'
    print('PASS real PostgreSQL/object restoration; PAT/direct-URL credential bytes round-trip; pinned uv 0.12.21 empty-cache offline frozen sync', flush=True)
    (ROOT / 'result.json').write_text(json.dumps({'managed_objects_complete': True,
        'interview_checkpoint_complete': False, 'uv': '0.12.21', 'files': len(document['files']),
        'credential_bytes_roundtrip': True, 'real_database_restore': True,
        'empty_cache_frozen_offline_sync': True}, indent=2))
    engine.dispose()
    restored.dispose()


if __name__ == '__main__':
    main()
