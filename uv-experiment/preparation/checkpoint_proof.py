"""Ticket 21 extension of the real-backup/pinned-uv disposable fixture proof."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import uuid

from sqlalchemy import select, text, update

import managed_backup_proof as backup
from docassemble_executor.admission import Admission
from docassemble_executor.authority import MutationLock, RuntimeLock
from docassemble_executor.checkpoints import capture, verify_checkpoint
from docassemble_executor.cold_restore import ColdRestoreError, inspect_backup
from docassemble_executor.managed_backup import ObjectCopy


original_backup = backup.create_backup


def with_checkpoints(engine, dump, paths, **kwargs):
    document = original_backup(engine, dump, paths, **kwargs)
    paths.config_file.write_text('db:\n  name: managed_backup_proof\n  user: root\n  host: /var/run/postgresql\n')
    tables = backup.preparation_tables()
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE alembic_version (version_num varchar(255) PRIMARY KEY)'))
        connection.execute(text("INSERT INTO alembic_version VALUES ('pre-change-schema')"))
        old = connection.scalar(select(tables['revision'].c.snapshot).where(tables['revision'].c.id == 'retained'))
        connection.execute(tables['revision'].insert().values(id='pending-upgrade', snapshot=dict(old, pending_upgrade=True)))
        connection.execute(update(tables['environment_state']).values(desired_revision_id='pending-upgrade'))
    service = SimpleNamespace(engine=engine, prefix='', tables=tables)
    paths = replace(paths, runtime_venv=backup.ROOT / 'checkpoint-runtime')
    bundle = old['publication']['result']['bundle']
    reference = bundle['digest']
    env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(paths.runtime_venv), UV_CACHE_DIR=str(backup.ROOT / 'checkpoint-cache'), UV_NO_CACHE='1')
    backup.run(['uv', 'venv', '--python', '/usr/share/docassemble/local3.12/bin/python', str(paths.runtime_venv)])
    backup.run(['uv', 'sync', '--frozen', '--offline', '--no-build', '--no-install-project', '--project', str(kwargs['store'] / reference)], env=env)
    local = paths.da_root / 'files'
    local.mkdir(exist_ok=True)
    (local / 'interview').write_text('pre-change interview progress')
    claim = SimpleNamespace(attempt_id=str(uuid.uuid4()),
        pre_change={'runtime': {'kind': 'normal', 'revision_id': 'retained', 'inventory': bundle['inventory']},
                    'image_baseline': {'proof': 'pre-change-image'}, 'target': old['target'],
                    'schema_release': {'database_revision': 'pre-change-schema'}},
        identity={'attempt_id': '', 'revision_id': 'pending-upgrade'})
    claim.identity['attempt_id'] = claim.attempt_id
    Admission(paths.state_dir).close('checkpoint proof maintenance')
    capture_kwargs = dict(kwargs)
    capture_kwargs.pop('prefix')
    capture_kwargs['paths'] = paths
    with MutationLock(paths.state_dir), RuntimeLock(paths.state_dir):
        writer = subprocess.run([str(Path('/usr/share/docassemble/executor-venv/bin/python')), '-c',
            'from pathlib import Path; from docassemble_executor.authority import RuntimeLock; '
            'from sqlalchemy import create_engine,text; '
            'lock=RuntimeLock(Path(' + repr(str(paths.state_dir)) + '),shared=True); lock.__enter__(); '
            'engine=create_engine("postgresql+psycopg2:///managed_backup_proof"); '
            'engine.begin().__enter__().execute(text("DELETE FROM alembic_version"))'],
            capture_output=True, text=True)
        assert writer.returncode != 0 and 'BlockingIOError' in writer.stderr
        original_copy = ObjectCopy.file
        def interrupted(self, source, relative):
            original_copy(self, source, relative)
            if relative == 'files/interview':
                raise InterruptedError('injected interrupted copy')
        ObjectCopy.file = interrupted
        try:
            try:
                capture(service, claim=claim, inputs={}, **capture_kwargs)
                raise AssertionError('interrupted capture succeeded')
            except InterruptedError:
                pass
        finally:
            ObjectCopy.file = original_copy
        root = paths.da_root / 'backup/checkpoints'
        assert not list(root.rglob('checkpoint.json'))
        first = capture(service, claim=claim, inputs={'DABACKUPDAYS': '14'}, **capture_kwargs)
        second_id = str(uuid.uuid4())
        second_claim = SimpleNamespace(attempt_id=second_id, pre_change=claim.pre_change,
            identity=dict(claim.identity, attempt_id=second_id))
        second = capture(service, claim=second_claim, inputs={'DABACKUPDAYS': '14'}, **capture_kwargs)
        assert first['id'] != second['id'] and not first['known_good'] and not second['known_good']
        completed = list(root.glob('*/checkpoint.json'))
        assert len(completed) == 2
        for marker in completed:
            checkpoint = verify_checkpoint(marker.parent, '')
            assert checkpoint['pre_change'] == claim.pre_change
            assert checkpoint['desired_revision_id'] == 'pending-upgrade'
            assert checkpoint['restore']['desired_revision_id'] == 'retained'
            assert checkpoint['restore']['replay_pending_work'] is False
            assert checkpoint['database_revision'] == 'pre-change-schema'
            assert (marker.parent / 'objects/files/interview').read_text() == 'pre-change interview progress'
            try:
                inspect_backup(marker.parent / 'database', marker.parent / 'database.managed', '')
                raise AssertionError('ordinary cold restore accepted checkpoint')
            except ColdRestoreError:
                pass
        credential = kwargs['secret_root'] / 'credential'
        credential.rename(credential.with_suffix('.held'))
        try:
            try:
                capture(service, claim=claim, inputs={}, **capture_kwargs)
                raise AssertionError('missing mandatory material capture succeeded')
            except (RuntimeError, OSError):
                pass
        finally:
            credential.with_suffix('.held').rename(credential)
        assert len(list(root.glob('*/checkpoint.json'))) == 2
        for marker in completed:
            verify_checkpoint(marker.parent, '')
        with engine.connect() as connection:
            assert connection.scalar(text('SELECT version_num FROM alembic_version')) == 'pre-change-schema'
        assert backup.run([str(paths.runtime_venv / 'bin/python'), '-c', 'import BackupDirect; print(BackupDirect.VALUE)']).stdout.strip() == '1'
    # Evidence has no credentials, dump bytes or credential-bearing manifests.
    (backup.ROOT / 'checkpoint-result.json').write_text(json.dumps({
        'uv': '0.12.21', 'failed_capture_before_mutation': True, 'interrupted_capture_incomplete': True, 'concurrent_writer_excluded': True,
        'same_day_unique': True, 'completed_checkpoints': len(completed), 'known_good': first['known_good'],
        'advanced_desired_separate': True, 'pre_change_manifest_verified': True,
        'ordinary_restore_replay_fenced': True, 'capture_seconds': [first['duration_seconds'], second['duration_seconds']]}, indent=2))
    print('PASS failed capture leaves runtime/schema unchanged; checkpoint interrupted copy; concurrent writer excluded; same-day immutable identities; advanced desired separated; verified pre-change manifest and no upgrade replay', flush=True)
    return document


if __name__ == '__main__':
    backup.create_backup = with_checkpoints
    backup.main()
