"""Ticket 23: real applied 1.10.14 checkpoint with pending 1.10.15 suppressed."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

from sqlalchemy import create_engine, select, text, update
from sqlalchemy.engine import URL

import core_upgrade_image_proof as core
from image_bridge_proof import database, STORE, DESCRIPTOR, run
from cold_restore_proof import fingerprint_image_owned, assert_image_owned_unchanged
from docassemble_executor.authority import MutationLock, RuntimeLock
from docassemble_executor.boot_observation import installation_host_id
from docassemble_executor.checkpoints import capture, verify_checkpoint
from docassemble_executor.checkpoint_restore import restore_checkpoint, require_restore_ready
from docassemble_executor.config import database_settings, load_config
from docassemble_executor.controller import enter_maintenance, load_context
from docassemble_executor.installation import Installation
from docassemble_executor.inventory import inspect_environment
from docassemble_executor.journal import Journal
from docassemble_executor.managed_backup import _sha
from docassemble_executor.paths import ExecutorPaths
from docassemble_executor.services import ensure_role_and_database
from docassemble_executor.admission import Admission

BACKUP = Path('/cold-backup')


def cloud_inputs():
    provider = os.environ.get('PROOF_CHECKPOINT_PROVIDER', 'local')
    if provider == 'local':
        return {}
    tools = BACKUP / 'cloud-bin'
    tools.mkdir(exist_ok=True)
    for name in ('s4cmd', 'az'):
        shutil.copyfile('/repo/uv-experiment/backup/cloud_cli_fake.py', tools / name)
        (tools / name).chmod(0o755)
    return {'PATH': str(tools) + ':' + os.environ['PATH'], 'CLOUD_FAKE_ROOT': str(BACKUP / 'remote'),
            'S3ENABLE': str(provider == 's3').lower(), 'AZUREENABLE': str(provider == 'azure').lower(),
            'S3BUCKET': 'fixture', 'AZURECONTAINER': 'fixture', 'AZURE_STORAGE_ACCOUNT': 'fixture-account'}


def disk_usage(paths):
    """Allocated bytes, not summed apparent sizes or an estimate of production need."""
    roots = {'runtime': paths.runtime_venv, 'cache': paths.cache_dir, 'retained': STORE,
             'checkpoints': paths.da_root / 'backup/checkpoints', 'transport': BACKUP,
             'image_bundles': paths.bundles_dir, 'state': paths.state_dir,
             'database': Path('/var/lib/postgresql'), 'files': paths.da_root / 'files'}
    return {name: int(subprocess.check_output(['du', '-sx', '--block-size=1', str(path)], text=True).split()[0])
            if path.exists() else 0 for name, path in roots.items()}


def main(action):
    paths = ExecutorPaths.from_env()
    if action == 'capture':
        # The base fixture disables dated backups to avoid scheduled noise.
        # Explicitly enable complete capture in this disposable deployment.
        import yaml
        capture_config = load_config(paths.config_file)
        capture_config.update({'backup days': 14, 'backup file storage': True})
        capture_config['db']['backup'] = True
        inputs = cloud_inputs()
        provider = os.environ.get('PROOF_CHECKPOINT_PROVIDER', 'local')
        if provider == 's3':
            capture_config['s3'] = {'bucket': 'fixture'}
        elif provider == 'azure':
            capture_config['azure'] = {'container': 'fixture', 'account name': 'fixture-account'}
        if inputs:
            live = BACKUP / 'remote/fixture/files/1/interview'
            live.parent.mkdir(parents=True)
            live.write_text('captured cloud interview file')
        paths.config_file.write_text(yaml.safe_dump(capture_config))
        engine, prefix = database()
        publisher = core.publisher_at(paths, engine, prefix)
        installation = Installation(engine, lambda *_: False, prefix)
        with engine.begin() as connection:
            env = installation._environment(connection)
            expected = {'revision': env['desired_revision_id'], 'prefix': prefix,
                        'selected': env['snapshot']['selected_baseline'], 'inventory': inspect_environment(paths.runtime_venv)}
            connection.execute(text('CREATE TABLE checkpoint_progress (value text)'))
            connection.execute(text("INSERT INTO checkpoint_progress VALUES ('captured interview progress')"))
            # Actual account/grant rows are additionally tested by adding a role
            # after capture; full DB restore must not retain it.
        # Synthetic committed credential reference, as in the accepted cold-restore
        # fixture. This qualifies byte/scope preservation, not a live Git host.
        from docassemble.base.managed_git import parse_git_requirement, git_credential_material
        _, source, credential = parse_git_requirement('CheckpointGit @ git+https://fixture:CHECKPOINT_FIXTURE_PAT@example.invalid/repo@main')
        secret_root = Path(os.environ['DA_MANAGED_SECRET_ROOT'])
        secret_root.mkdir(mode=0o700, exist_ok=True)
        secret = secret_root / 'checkpoint-credential'
        secret.write_bytes(git_credential_material(source['repository'], credential))
        secret.chmod(0o600)
        with engine.begin() as connection:
            env = installation._environment(connection)
            snapshot = dict(env['snapshot'], git_credentials={'checkpointgit': secret.name})
            connection.execute(update(installation.tables['environment_state']).values(snapshot=snapshot))
            connection.execute(installation.tables['candidate_material'].insert().values(id=secret.name,
                path=str(secret), usable=True, committed=True, shared=False, deleted=False))
        expected['credential_repository'] = source['repository']
        files = paths.da_root / 'files'
        files.mkdir(exist_ok=True)
        (files / 'checkpoint-proof').write_text('captured interview file')
        core.fixture_wheels(paths, '1.10.15')
        candidate = publisher.prepare('1', None, 'pending-checkpoint-upgrade', operation='upgrade',
            release_selection={'mode': 'exact', 'version': '1.10.15'}, pre_change_checkpoint=True)
        claim = publisher.claim(candidate, lease_seconds=300)
        core.save('upgrade-inputs.json', claim.inputs)
        run(['runuser', '-u', 'da-environment', '--', sys.executable, str(Path(core.__file__)), 'build', '1.10.15'])
        result = json.loads((STORE / 'upgrade-result.json').read_text())
        assert publisher.publish_ready(claim, result)
        preview = publisher.status('1', candidate)
        publication = publisher.approve('1', candidate, preview['result_digest'], 'pending-checkpoint-approve')
        unpublished = publisher.prepare('1', 'Unpublished==1', 'copied-unpublished-worker')
        late = publisher.claim(unpublished)
        expected.update(pending=publication, unpublished=unpublished, late_fence=late.fence)
        measurements = {'before_maintenance': disk_usage(paths)}
        maintenance_started = time.monotonic()
        expected['maintenance_started_monotonic'] = maintenance_started
        with MutationLock(paths.state_dir):
            claim = installation.claim_installation(installation_host_id(paths))
            assert claim.pre_change['runtime']['revision_id'] == expected['revision']
            assert enter_maintenance(load_context(paths, start_infrastructure=False))
            from docassemble_executor.checkpoints import observe_pre_change
            observe_pre_change(paths, claim)
            with RuntimeLock(paths.state_dir):
                kwargs = dict(store=STORE, baseline=DESCRIPTOR, secret_root=secret_root,
                    artifact_roots=[Path(os.environ['DA_MANAGED_ARTIFACT_ROOT']), Path(os.environ['DA_EXECUTOR_ARTIFACT_ROOT'])])
                before_uv = Path('/var/log/docassemble-uv-invocations.log').read_text()
                before_schema = (paths.log_dir / 'create-tables.log').read_bytes()
                from docassemble_executor.managed_backup import ManagedBackupError
                failures = [('disabled-backup', dict(inputs, DABACKUPDAYS='0'))]
                if inputs:
                    failures += [(failure, dict(inputs, CLOUD_FAKE_FAILURE=failure))
                                 for failure in ('auth', 'interrupted-upload')]
                for failure, failed_inputs in failures:
                    try:
                        capture(installation, paths, claim, inputs=failed_inputs, **kwargs)
                    except ManagedBackupError:
                        pass
                    else:
                        raise AssertionError('requested checkpoint failure admitted: ' + failure)
                    assert Path('/var/log/docassemble-uv-invocations.log').read_text() == before_uv
                    assert (paths.log_dir / 'create-tables.log').read_bytes() == before_schema
                    assert inspect_environment(paths.runtime_venv) == expected['inventory']
                status = capture(installation, paths, claim, inputs=inputs, **kwargs)
        measurements.update(maintenance_capture_seconds=time.monotonic() - maintenance_started,
                            capture_seconds=status['duration_seconds'], after_capture=disk_usage(paths))
        directory = next(marker.parent for marker in (paths.da_root / 'backup/checkpoints').glob('*/checkpoint.json') if json.loads(marker.read_text())['id'] == status['id'])
        document = verify_checkpoint(directory, prefix)
        assert document['restore']['desired_revision_id'] == expected['revision'] != document['desired_revision_id']
        assert document['known_good'], document['runtime_label']
        expected.update(checkpoint_id=document['id'], schema=document['database_revision'], known_good=document['known_good'])
        if inputs:
            from docassemble_executor.cloud_checkpoints import CloudCheckpoint
            # No local staging fallback: full restore consumes only retrieved
            # remote checkpoint bytes and rewinds local AND cloud files together.
            shutil.rmtree(paths.da_root / 'backup/checkpoints')
            CloudCheckpoint(inputs).retrieve(status['destination']['prefix'], BACKUP / 'checkpoint', prefix)
            live.write_text('changed cloud interview after checkpoint')
            (live.parent / 'created-after').write_text('later cloud object to lose')
            reserved = BACKUP / 'remote/fixture/backup/keep'
            reserved.parent.mkdir(parents=True, exist_ok=True)
            reserved.write_text('unrelated nightly backup preserved')
        else:
            shutil.copytree(directory, BACKUP / 'checkpoint')
        measurements['after_transport'] = disk_usage(paths)
        expected.update(provider=provider, checkpoint_failures=[name for name, _ in failures], measurements=measurements)
        (BACKUP / 'expected.json').write_text(json.dumps(expected))
        print('PASS actual 1.10.14 applied checkpoint with already-advanced desired 1.10.15; real schema/material/health and drained capture', flush=True)
        return
    expected = json.loads((BACKUP / 'expected.json').read_text())
    if action == 'restore':
        directory = BACKUP / 'checkpoint'
        document = verify_checkpoint(directory, expected['prefix'])
        config_source = next(directory / 'objects' / name for name, row in document['files'].items() if row['source'] == str(paths.config_file))
        config = load_config(config_source)
        db = database_settings(config)
        assert ensure_role_and_database(db).ok
        url = URL.create('postgresql+psycopg2', username=db.user, password=db.password, host=db.host, port=db.port, database=db.name)
        engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE post_checkpoint_progress (value text)'))
            connection.execute(text("INSERT INTO post_checkpoint_progress VALUES ('later progress to lose')"))
            connection.execute(text('CREATE TABLE user_roles (user_id integer, role_id integer)'))
            connection.execute(text('INSERT INTO user_roles VALUES (1, 999999)'))
        engine.dispose()
        # Operator credentials are independent of the application role; never
        # grant CREATEDB to the application merely to make restore work.
        subprocess.run(['su', 'postgres', '-c', 'psql --set ON_ERROR_STOP=1'],
                       input="CREATE ROLE checkpoint_restore_operator LOGIN SUPERUSER PASSWORD 'FixtureRestore23!';",
                       text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        operator_url = url.set(username='checkpoint_restore_operator', password='FixtureRestore23!', database='postgres')
        operator = operator_url.render_as_string(hide_password=False)
        restore_env = dict(os.environ, DA_RESTORE_DATABASE_URL=url.render_as_string(hide_password=False),
                           DA_RESTORE_MAINTENANCE_DATABASE_URL=operator)
        # Pending Redis messages and unacked deliveries cannot survive rewind;
        # unrelated application/API-key state must not be flushed.
        from docassemble_executor.checkpoint_restore import _broker_command, broker_keys, preflight_broker
        from docassemble_executor.services import start_redis
        assert start_redis().ok
        broker = preflight_broker(config)
        for key in broker_keys()[:8]:
            _broker_command(broker, 'LPUSH', key, 'stale-message')
        _broker_command(broker, 'HSET', 'unacked', 'stale-delivery', 'stale-message')
        _broker_command(broker, 'ZADD', 'unacked_index', '1', 'stale-delivery')
        _broker_command(broker, 'SET', 'da23-application-state', 'keep-unrelated-state')
        # New image has no source-container venv/cache/store. Remove even the
        # image defaults to prove cold packages and material consumption.
        for target in (paths.runtime_venv, paths.cache_dir, STORE):
            shutil.rmtree(target, ignore_errors=True)
        paths.runtime_venv.mkdir()
        import pwd
        account = pwd.getpwnam('da-environment')
        os.chown(paths.runtime_venv, account.pw_uid, account.pw_gid)
        paths.ensure_directories()
        files = paths.da_root / 'files'
        files.mkdir(exist_ok=True)
        (files / 'created-after').write_text('later file to lose')
        auth_dir = Path('/root/full-checkpoint-authorization')
        auth_dir.mkdir(mode=0o700)
        auth = auth_dir / 'restore.json'
        authority = {'format': 1, 'action': 'full-checkpoint-restore', 'incarnation': str(uuid.uuid4()),
            'checkpoint_id': document['id'], 'checkpoint_sha256': _sha(directory / 'checkpoint.json'),
            'dump_sha256': _sha(directory / 'database'), 'old_deployment_excluded': True,
            'acknowledge_subsequent_data_loss': True}
        auth.write_text(json.dumps(authority))
        auth.chmod(0o600)
        environment_file = None
        if expected['provider'] != 'local':
            environment_file = auth_dir / 'cloud.json'
            environment_file.write_text(json.dumps(cloud_inputs()))
            environment_file.chmod(0o600)
        restore_started = time.monotonic()
        expected['measurements']['before_restore'] = disk_usage(paths)
        before_uv = Path('/var/log/docassemble-uv-invocations.log').read_text()
        # Real missing artifact rejection occurs before the target DB is dropped.
        wheel = next((directory / 'database.managed/objects/retained').glob('*/wheels/*.whl'))
        held = wheel.with_suffix('.held')
        wheel.rename(held)
        try:
            try:
                restore_checkpoint(paths, directory, auth, url.render_as_string(hide_password=False), expected['prefix'], maintenance_database_url=operator, environment_file=environment_file)
            except (RuntimeError, ValueError, OSError):
                pass
            else:
                raise AssertionError('missing checkpoint wheel admitted')
        finally:
            held.rename(wheel)
        engine = create_engine(url)
        with engine.connect() as connection:
            assert connection.scalar(text('SELECT value FROM post_checkpoint_progress')) == 'later progress to lose'
        engine.dispose()
        # A real process death after the irreversible DB drop must leave the
        # external restoring marker, not a caught-exception cleanup illusion.
        restore_arguments = ['--checkpoint', str(directory), '--authorization', str(auth), '--prefix', expected['prefix']]
        if environment_file:
            restore_arguments += ['--environment', str(environment_file)]
        crash = ('import os,signal; '
                 'import docassemble_executor.checkpoint_restore as r; '
                 'original=r._admin; '
                 'exec("def drop_then_die(url,statement):\\n original(url,statement)\\n if statement.startswith(\'DROP DATABASE IF EXISTS\'): os.kill(os.getpid(),signal.SIGKILL)\\n"); '
                 'r._admin=drop_then_die; '
                 'raise SystemExit(r.main(' + repr(restore_arguments) + '))')
        crash_command = [sys.executable, '-c', crash]
        # The driver already copied and verified the image bridge. Snapshot after
        # that provisioning, immediately before the first destructive restore.
        image_before = fingerprint_image_owned(paths)
        killed = subprocess.run(crash_command, env=restore_env, capture_output=True, text=True)
        for secret in (restore_env['DA_RESTORE_DATABASE_URL'], operator, url.password, operator_url.password):
            if secret:
                assert secret not in repr(crash_command), 'restore credential exposed in crash argv'
                assert secret not in killed.stdout + killed.stderr, 'restore credential exposed in crash output'
        assert killed.returncode == -9, killed.stdout + killed.stderr
        interrupted = json.loads((paths.state_dir / 'checkpoint-restore.json').read_text())
        assert interrupted['state'] == 'restoring' and interrupted['phase'] == 'database-replace'
        from docassemble_executor.cold_restore import ColdRestoreError
        try:
            require_restore_ready(paths)
        except ColdRestoreError:
            pass
        else:
            raise AssertionError('SIGKILL interruption admitted normal startup')
        assert Admission(paths.state_dir).state()['mode'] == 'closed'
        authority['incarnation'] = str(uuid.uuid4())
        auth.write_text(json.dumps(authority))
        command = [sys.executable, '-m', 'docassemble_executor.cli', 'restore-checkpoint', *restore_arguments]
        completed = subprocess.run(command, env=restore_env, capture_output=True, text=True)
        for secret in (restore_env['DA_RESTORE_DATABASE_URL'], operator, url.password, operator_url.password):
            if secret:
                assert secret not in repr(command), 'restore credential exposed in argv'
                assert secret not in completed.stdout + completed.stderr, 'restore credential exposed in output'
        assert completed.returncode == 0, completed.stdout + completed.stderr
        record = json.loads((paths.state_dir / 'checkpoint-restore.json').read_text())
        from docassemble_executor.checkpoint_restore import _admin, _quoted
        _admin(operator_url, 'DROP DATABASE IF EXISTS ' + _quoted(interrupted['staging_database']) + ' WITH (FORCE)')
        (BACKUP / 'image-owned.json').write_text(json.dumps(assert_image_owned_unchanged(paths, image_before)))
        assert record['state'] == 'inputs-restored'
        assert _broker_command(broker, 'EXISTS', *broker_keys()) == '0'
        assert _broker_command(broker, 'GET', 'da23-application-state') == 'keep-unrelated-state'
        require_restore_ready(paths)
        assert not (paths.runtime_venv / 'bin/python').exists(), 'preflight must not establish live runtime authority'
        assert not any(paths.cache_dir.iterdir())
        assert (files / 'checkpoint-proof').read_text() == 'captured interview file'
        assert not (files / 'created-after').exists()
        from docassemble.base.managed_git import scoped_authorization
        import base64
        secret = Path(os.environ['DA_MANAGED_SECRET_ROOT']) / 'checkpoint-credential'
        header = scoped_authorization(expected['credential_repository'], secret.read_bytes())
        assert base64.b64decode(header.removeprefix('Basic ')) == b'fixture:CHECKPOINT_FIXTURE_PAT'
        if environment_file:
            assert (BACKUP / 'remote/fixture/files/1/interview').read_text() == 'captured cloud interview file'
            assert not (BACKUP / 'remote/fixture/files/1/created-after').exists()
            assert (BACKUP / 'remote/fixture/backup/keep').read_text() == 'unrelated nightly backup preserved'
            assert list((BACKUP / 'remote/fixture/checkpoints').glob('*/checkpoint.json'))
        expected['measurements'].update(restore_including_faults_seconds=time.monotonic() - restore_started,
                                        after_restore=disk_usage(paths))
        (BACKUP / 'expected.json').write_text(json.dumps(expected))
        engine = create_engine(url)
        installation = Installation(engine, lambda *_: False, expected['prefix'])
        with engine.connect() as connection:
            env = connection.execute(select(installation.tables['environment_state'])).mappings().one()
            assert env['desired_revision_id'] == expected['revision']
            assert env['active_attempt_id'] is None
            candidate = connection.execute(select(installation.tables['candidate']).where(installation.tables['candidate'].c.id == expected['unpublished'])).mappings().one()
            assert candidate['state'] == 'cancelled' and candidate['fence'] > expected['late_fence']
            assert connection.scalar(text('SELECT value FROM checkpoint_progress')) == 'captured interview progress'
            assert not connection.scalar(text("SELECT to_regclass('post_checkpoint_progress')"))
            assert not connection.scalar(text('SELECT EXISTS (SELECT 1 FROM user_roles WHERE role_id=999999)'))
            attempt = connection.scalar(select(installation.tables['installation_attempt'].c.record).where(
                installation.tables['installation_attempt'].c.id == expected['pending']['attempt_id']))
            assert attempt['phase'] == 'blocked'
        assert installation.claim_installation('copied-old-host') is None
        assert Admission(paths.state_dir).state()['mode'] == 'closed'
        (BACKUP / 'restore-uv-prefix').write_text(Path('/var/log/docassemble-uv-invocations.log').read_text())
        added = Path('/var/log/docassemble-uv-invocations.log').read_text()[len(before_uv):]
        assert '\tsync ' in added and '--frozen' in added and '--no-build' in added
        print('PASS explicit independent full restore: actual PostgreSQL/files/grants rewind, pending work/candidate fenced; real empty-cache pinned-uv preflight; no old runtime', flush=True)
        return
    if action == 'verify':
        assert fingerprint_image_owned(paths) == json.loads((BACKUP / 'image-owned.json').read_text()), 'runtime rebuild changed protected material'
        engine, prefix = database()
        installation = Installation(engine, lambda *_: False, prefix)
        status = json.loads(paths.status_file.read_text())
        assert status['normal_ready'], status
        assert inspect_environment(paths.runtime_venv) == expected['inventory']
        assert inspect_environment(paths.runtime_venv)['docassemble-webapp'] == '1.10.14'
        assert not (paths.log_dir / 'create-tables.log').exists(), 'checkpoint setup rerun'
        with engine.connect() as connection:
            env = connection.execute(select(installation.tables['environment_state'])).mappings().one()
            assert env['desired_revision_id'] == expected['revision'] and env['snapshot']['selected_baseline'] == expected['selected']
            assert env['snapshot']['cold_restore']['state'] == 'verified'
            assert connection.scalar(text('SELECT value FROM checkpoint_progress')) == 'captured interview progress'
            attempt = connection.scalar(select(installation.tables['installation_attempt'].c.record).where(
                installation.tables['installation_attempt'].c.id == Journal.load(paths.journal_file).operation.id))
            assert attempt['checkpoint_database_revision'] == expected['schema']
        log = Path('/var/log/docassemble-uv-invocations.log').read_text()
        prefix_log = (BACKUP / 'restore-uv-prefix').read_text()
        added = log[len(prefix_log):]
        assert len([line for line in added.splitlines() if '\tsync ' in line]) == 1
        assert not any('\t' + verb + ' ' in log[len((BACKUP / 'image-uv.log').read_text()):] for verb in ('lock', 'build'))
        (BACKUP / 'result.json').write_text(json.dumps({'uv': '0.12.21', 'real_database_files_grant_rewind': True,
            'no_old_venv_cache': True, 'applied_not_advanced_desired': True, 'packages': '1.10.14',
            'known_good_capture': expected['known_good'], 'schema_setup_not_repeated': True,
            'copied_candidate_and_worker_fenced': True, 'pending_upgrade_suppressed': True,
            'real_empty_cache_frozen_preflight': True, 'normal_ready_all_12_roles': True,
            'redis_stale_delivery_purge_preserves_application_state': True,
            'sigkill_after_database_drop_remains_gated': True,
            'provider': expected['provider'], 'external_cli_fake': expected['provider'] != 'local',
            'local_and_cloud_files_rewound': expected['provider'] != 'local',
            'committed_credential_bytes_and_scope_restored': True,
            'requested_capture_failures_before_mutation': expected['checkpoint_failures'],
            'measurements': dict(expected['measurements'], verified_steady_state=disk_usage(paths),
                                 maintenance_to_verified_seconds=time.monotonic() - expected['maintenance_started_monotonic']),
            'restore_boot_uv_counts': {verb: sum('\t' + verb + ' ' in line for line in added.splitlines())
                                      for verb in ('lock', 'build', 'sync')}}, indent=2))
        print('PASS full checkpoint restore actual packages 1.10.14 (not pending 1.10.15), schema compatibility/no setup, web/all-12-role readiness; zero solves/builds', flush=True)
        return
    raise ValueError(action)


if __name__ == '__main__':
    main(sys.argv[-1])
