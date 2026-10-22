"""Ticket 22: real PG/pinned-uv capture plus external S3/Azure CLI fakes."""
import sys
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import uuid

from sqlalchemy import select, text, update

import managed_backup_proof as backup
from docassemble_executor.admission import Admission
from docassemble_executor.authority import MutationLock, RuntimeLock
from docassemble_executor.checkpoints import capture
from docassemble_executor.cloud_checkpoints import CloudCheckpoint
from docassemble_executor.managed_backup import ManagedBackupError


original_backup = backup.create_backup


def with_cloud_checkpoints(engine, dump, paths, **kwargs):
    document = original_backup(engine, dump, paths, **kwargs)
    tables = backup.preparation_tables()
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE alembic_version (version_num varchar(255) PRIMARY KEY)'))
        connection.execute(text("INSERT INTO alembic_version VALUES ('pre-change-schema')"))
        old = connection.scalar(select(tables['revision'].c.snapshot).where(tables['revision'].c.id == 'retained'))
        connection.execute(tables['revision'].insert().values(id='pending-upgrade', snapshot=dict(old, pending_upgrade=True)))
        connection.execute(update(tables['environment_state']).values(desired_revision_id='pending-upgrade'))
    service = SimpleNamespace(engine=engine, prefix='', tables=tables)
    tools = backup.ROOT / 'cloud-bin'
    tools.mkdir()
    source = Path('/repo/uv-experiment/backup/cloud_cli_fake.py')
    for name in ('s4cmd', 'az'):
        shutil.copyfile(source, tools / name)
        (tools / name).chmod(0o755)
    local = paths.da_root / 'files'
    local.mkdir(exist_ok=True)
    (local / 'interview').write_bytes(b'local file alongside cloud contents')
    (paths.da_root / 'log').mkdir(exist_ok=True)
    capture_kwargs = dict(kwargs, paths=paths)
    capture_kwargs.pop('prefix')
    Admission(paths.state_dir).close('cloud checkpoint maintenance')
    results = []
    with MutationLock(paths.state_dir), RuntimeLock(paths.state_dir):
        for provider in ('s3', 'azure'):
            inputs = {'PATH': str(tools) + ':' + os.environ['PATH'], 'CLOUD_FAKE_ROOT': str(backup.ROOT / ('remote-' + provider)),
                      'S3ENABLE': 'true' if provider == 's3' else 'false', 'AZUREENABLE': 'true' if provider == 'azure' else 'false',
                      'S3BUCKET': 'fixture', 'AZURECONTAINER': 'fixture', 'AZURE_STORAGE_ACCOUNT': 'fixture-account',
                      'AWS_SECRET_ACCESS_KEY': 'DISPOSABLE-S3-SECRET', 'AZURE_STORAGE_KEY': 'DISPOSABLE-AZURE-SECRET'}
            bucket = Path(inputs['CLOUD_FAKE_ROOT']) / 'fixture'
            (bucket / 'files/1').mkdir(parents=True)
            live = bucket / 'files/1/interview'
            original = b'pre-change cloud interview progress'
            live.write_bytes(original)
            config = ('s3:\n  bucket: fixture\n' if provider == 's3' else
                      'azure:\n  container: fixture\n  account name: fixture-account\n')
            paths.config_file.write_text('db:\n  name: managed_backup_proof\n  user: root\n  host: /var/run/postgresql\n' + config)
            attempt = str(uuid.uuid4())
            claim = SimpleNamespace(attempt_id=attempt,
                pre_change={'runtime': {'kind': 'core-only', 'revision_id': 'retained', 'inventory': {}},
                            'image_baseline': {'proof': 'pre-change-image'}, 'target': old['target']},
                identity={'attempt_id': attempt, 'revision_id': 'pending-upgrade'})
            failures = []
            # The full failure matrix uses real PostgreSQL in focused tests;
            # keep the full-image proof bounded while retaining interrupted
            # remote publication and authentication failures here.
            for failure in ('interrupted-upload', 'auth'):
                try:
                    capture(service, claim=claim, inputs=dict(inputs, CLOUD_FAKE_FAILURE=failure), **capture_kwargs)
                    raise AssertionError('cloud failure injection succeeded: ' + failure)
                except ManagedBackupError as error:
                    assert 'DISPOSABLE' not in str(error)
                live.write_bytes(original)
                failures.append(failure)
            assert not list((bucket / 'checkpoints').rglob('checkpoint.json'))
            first = capture(service, claim=claim, inputs=inputs, **capture_kwargs)
            assert first['complete']
            # Deliberately remove disposable local staging after remote capture;
            # retrieval and nightly-rotation survival cannot rely on those bytes.
            # Repeated same-day captures are covered by the PostgreSQL suite.
            shutil.rmtree(paths.da_root / 'backup/checkpoints')
            # Actual callable nightly backup path, with provider transports faked.
            # Its overwrite/delete flags cannot reach checkpoints/.
            nightly_env = dict(inputs, DA_ROOT=str(paths.da_root), DA_CONFIG_FILE=str(paths.config_file),
                               CONTAINERROLE='web', DA_UV_MANAGED='false', DABACKUPDAYS='1')
            for _ in range(2):
                nightly = subprocess.run(['bash', '/repo/Docker/backup.sh'], env=nightly_env, capture_output=True, text=True)
                assert nightly.returncode == 0, nightly.stderr
            live.write_bytes(b'changed after checkpoint')
            for index, status in enumerate((first,)):
                restored = backup.ROOT / ('retrieved-' + provider + '-' + str(index))
                manifest = CloudCheckpoint(inputs).retrieve(status['destination']['prefix'], restored, '')
                assert manifest['pre_change'] == claim.pre_change
                assert manifest['restore']['desired_revision_id'] == 'retained'
                assert manifest['desired_revision_id'] == 'pending-upgrade'
                assert manifest['database_revision'] == 'pre-change-schema'
                assert manifest['restore']['replay_pending_work'] is False
                assert manifest['retention'] == 'operator-pruned-no-auto-deletion'
                assert not manifest['known_good']
                row = manifest['cloud']['files']['files/1/interview']
                assert (restored / 'objects' / row['file']).read_bytes() == original
                if index == 0:
                    # Materialize a disposable venv from RETRIEVED managed
                    # material only. This is not a full data/authority restore.
                    managed = json.loads((restored / 'database.managed/manifest.json').read_text())
                    sandbox = backup.ROOT / ('cloud-sync-' + provider)
                    sandbox.mkdir()
                    reference = old['publication']['result']['bundle']['digest']
                    project = sandbox / 'retained' / reference
                    shutil.copytree(restored / 'database.managed/objects/retained' / reference, project)
                    for name, evidence in managed['files'].items():
                        source_path = Path(evidence['source'])
                        if source_path.parent in kwargs['artifact_roots']:
                            artifact = sandbox / 'artifacts' / source_path.name
                            artifact.parent.mkdir(exist_ok=True)
                            shutil.copyfile(restored / 'database.managed/objects' / name, artifact)
                    venv = sandbox / 'venv'
                    backup.run(['uv', 'venv', '--python', sys.executable, str(venv)])
                    env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(venv), UV_CACHE_DIR=str(sandbox / 'empty-cache'), UV_NO_CACHE='1')
                    backup.run(['uv', 'sync', '--frozen', '--offline', '--no-build', '--no-install-project', '--project', str(project)], env=env)
                    assert backup.run([str(venv / 'bin/python'), '-c', 'import BackupDirect; print(BackupDirect.VALUE)']).stdout.strip() == '1'
            with engine.connect() as connection:
                assert connection.scalar(text('SELECT version_num FROM alembic_version')) == 'pre-change-schema'
            results.append({'provider': provider, 'external_cli_fake': True, 'completed_checkpoints': 1,
                'failure_injections': failures, 'nightly_rotation_survived': True,
                'remote_retrieval_without_local_staging': True, 'changed_live_content_retained': True,
                'manifest_and_database_binding_verified': True, 'pinned_uv_from_retrieved_content': True, 'capture_seconds': [first['duration_seconds']]})
    (backup.ROOT / 'cloud-checkpoint-result.json').write_text(json.dumps({'uv': '0.12.21', 'providers': results,
        'limitation': 'external CLI fakes; no live S3/Azure qualification and no full cloud restore'}, indent=2))
    print('PASS S3/Azure fake-CLI cloud checkpoint capture, failures, immutable identities, nightly rotation, remote retrieval and integrity', flush=True)
    return document


if __name__ == '__main__':
    backup.create_backup = with_cloud_checkpoints
    backup.main()
