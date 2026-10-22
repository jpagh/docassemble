"""Ticket 18 disposable DB/files restore after a real web-selected core upgrade."""
from docassemble.base.managed_python import python_requires
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
from unittest.mock import patch

from sqlalchemy import select, update
import yaml

from docassemble.base.managed_git import parse_git_requirement, git_credential_material, scoped_authorization
from docassemble_executor.cold_restore import inspect_backup, ColdRestoreError, _subset
from docassemble_executor.config import database_settings
from docassemble_executor.installation import Installation
from docassemble_executor.journal import Journal
from docassemble_executor.managed_backup import create_backup, _sha, references
from docassemble_executor.paths import ExecutorPaths
from docassemble_executor.services import ensure_role_and_database
from docassemble_executor.inventory import inspect_environment
from docassemble_executor.image_baseline import provision_image_baseline
from image_bridge_proof import database, STORE, DESCRIPTOR

BACKUP = Path('/cold-backup')
DUMP = BACKUP / 'database'


def assert_required_material_missing(dump, directory, prefix, name):
    target = directory / 'objects' / name
    # A sibling .held object would trigger the unlisted-object guard instead.
    with tempfile.TemporaryDirectory(dir=directory.parent, prefix='held-restore-object-') as temporary:
        held = Path(temporary) / 'object'
        target.rename(held)
        try:
            try:
                inspect_backup(dump, directory, prefix)
            except ColdRestoreError as error:
                assert str(error) == 'current environment or required recovery material unavailable; service blocked', str(error)
            else:
                raise AssertionError('missing recovery/current material admitted')
        finally:
            held.rename(target)


def fingerprint_image_owned(paths):
    """Bounded byte/metadata sentinels for stock, bundle and protected tooling."""
    stock = paths.da_root / 'local3.14'
    directories = [stock, paths.bundles_dir, Path(sys.executable).parent.parent,
                   Path(sys.executable).resolve().parent.parent, DESCRIPTOR.parent]
    files = [stock / 'pyvenv.cfg', stock / 'bin/certbot.unmanaged', paths.manifest_file,
             Path(sys.executable).resolve(), DESCRIPTOR]
    for name in ('core-r1', 'runtime-r1'):
        bundle = paths.bundles_dir / name
        directories.append(bundle)
        files.extend(bundle / name for name in ('pyproject.toml', 'uv.lock', 'inventory.json'))
    if DESCRIPTOR.exists():
        files.extend(sorted((DESCRIPTOR.parent / 'wheels').glob('*.whl')))
    result = {}
    for path in directories + files:
        if not path.exists():
            continue
        info = path.stat()
        result[str(path)] = {'mode': info.st_mode, 'uid': info.st_uid, 'gid': info.st_gid,
                             'sha256': _sha(path) if path.is_file() else None}
    assert str(stock / 'pyvenv.cfg') in result, 'stock image sentinel missing'
    assert str(DESCRIPTOR) in result, 'qualified image bridge sentinel missing'
    assert paths.runtime_venv.resolve() != stock.resolve(), 'managed runtime collides with stock'
    return result


def assert_image_owned_unchanged(paths, before):
    after = fingerprint_image_owned(paths)
    assert after == before, 'restore changed protected image/stock bytes or metadata'
    return after


def main(action):
    paths = ExecutorPaths.from_env()
    if action == 'backup':
        engine, prefix = database()
        service = Installation(engine, lambda *_: True, prefix)
        secret_root = Path(os.environ['DA_MANAGED_SECRET_ROOT'])
        secret_root.mkdir(mode=0o700, exist_ok=True)
        _, source, credential = parse_git_requirement('RestoreGit @ git+https://fixture:RESTORE_FIXTURE_PAT@example.invalid/repo@main')
        data = git_credential_material(source['repository'], credential)
        secret = secret_root / 'restore-credential'
        secret.write_bytes(data)
        secret.chmod(0o600)
        candidate = service.prepare('fixture', 'Unpublished>=1', 'restore-stale')
        staging = secret_root / 'candidate-only'
        staging.write_text('DISCARD_CANDIDATE_TOKEN')
        with engine.begin() as connection:
            environment = service._environment(connection)
            snapshot = dict(environment['snapshot'], git_credentials={'restoregit': 'restore-credential'})
            connection.execute(update(service.tables['environment_state']).values(snapshot=snapshot))
            for identity, path, committed in [('restore-credential', secret, True), ('candidate-only', staging, False)]:
                connection.execute(service.tables['candidate_material'].insert().values(id=identity, candidate_id=candidate,
                    path=str(path), usable=True, committed=committed, shared=False, deleted=False))
            expected = {'revision': environment['desired_revision_id'], 'selected': snapshot['selected_baseline'],
                        'inventory': inspect_environment(paths.runtime_venv), 'prefix': prefix,
                        'schema': Journal.load(paths.journal_file).to_dict()['schema'], 'candidate': candidate,
                        'repository': source['repository']}
        files = paths.da_root / 'files'
        files.mkdir(exist_ok=True)
        (files / 'restore-fixture').write_text('durable interview fixture')
        with tarfile.open(BACKUP / 'files.tar', 'w') as archive:
            archive.add(paths.config_file, arcname='config.yml', recursive=False)
            archive.add(files, arcname='files')
        create_backup(engine, DUMP, paths, prefix=prefix, store=STORE, baseline=DESCRIPTOR,
            artifact_roots=[Path(os.environ['DA_MANAGED_ARTIFACT_ROOT']), Path(os.environ['DA_EXECUTOR_ARTIFACT_ROOT'])], secret_root=secret_root)
        (BACKUP / 'expected.json').write_text(json.dumps(expected))
        print('PASS complete backup after actual coherent web-selected 1.10.14 upgrade; persistent Git auth and candidate-only staging', flush=True)
        return
    expected = json.loads((BACKUP / 'expected.json').read_text())
    if action == 'restore':
        # Fresh image: remove even its default runtime/cache; these are NOT input.
        for directory in (paths.runtime_venv, paths.cache_dir, paths.state_dir, STORE):
            shutil.rmtree(directory, ignore_errors=True)
        paths.runtime_venv.mkdir()
        import pwd
        account = pwd.getpwnam('da-environment')
        os.chown(paths.runtime_venv, account.pw_uid, account.pw_gid)
        paths.ensure_directories()
        # The replacement owns its image material: restore deliberately never writes
        # image/ or image-baseline/ objects, so this container provisions its own
        # qualified bridge from its immutable image bundles before any boot reads it.
        provision_image_baseline(paths, DESCRIPTOR.parent)
        with tarfile.open(BACKUP / 'files.tar') as archive:
            config = yaml.safe_load(archive.extractfile('config.yml'))
        settings = database_settings(config)
        assert ensure_role_and_database(settings).ok
        from sqlalchemy import create_engine
        from sqlalchemy.engine import URL
        url = URL.create('postgresql+psycopg2', username=settings.user, password=settings.password,
                         host=settings.host, port=settings.port, database=settings.name)
        auth_dir = Path('/root/cold-restore-authorization')
        auth_dir.mkdir(mode=0o700)
        auth = auth_dir / 'restore.json'
        auth.write_text(json.dumps({'format': 1, 'incarnation': str(uuid.uuid4()),
                                   'dump_sha256': _sha(DUMP), 'old_deployment_excluded': True}))
        auth.chmod(0o600)
        # Failure injection on mandatory recovery, current and historical refs
        # occurs against the actual dump/object backup, never a fake inventory.
        directory = Path(str(DUMP) + '.managed')
        document = json.loads((directory / 'manifest.json').read_text())
        current = next(name for name in document['files'] if name.startswith('retained/' + expected['selected']['core_bundle']['digest'] + '/'))
        for name in ('image/core-r1/uv.lock', current):
            assert_required_material_missing(DUMP, directory, expected['prefix'], name)
        records = json.loads((directory / 'records.json').read_text())
        environment, = records['environment_state']
        desired = next(row for row in records['revision'] if row['id'] == expected['revision'])
        mandatory = set()
        for snapshot in (environment['snapshot'], desired['snapshot'], document['local_baselines']):
            mandatory.update(references(_subset(records, snapshot))['bundles'])
        historical = set(document['references']['bundles']) - mandatory
        assert historical, 'proof must retain unrelated historical material'
        (directory / 'objects/retained' / sorted(historical)[0] / 'uv.lock').unlink()
        restore_env = dict(os.environ, DA_RESTORE_DATABASE_URL=url.render_as_string(hide_password=False))
        command = [sys.executable, '-m', 'docassemble_executor.cli', 'restore', '--dump', str(DUMP),
                   '--files', str(BACKUP / 'files.tar'), '--authorization', str(auth), '--prefix', expected['prefix']]
        # Provisioning creates the bridge; snapshot it before destructive restore,
        # which must not create, modify or delete any image-owned material.
        image_before = fingerprint_image_owned(paths)
        completed = subprocess.run(command, env=restore_env, capture_output=True, text=True)
        assert restore_env['DA_RESTORE_DATABASE_URL'] not in repr(command), 'restore URL exposed in argv'
        assert restore_env['DA_RESTORE_DATABASE_URL'] not in completed.stdout + completed.stderr, 'restore URL exposed in output'
        if url.password:
            assert url.password not in completed.stdout + completed.stderr, 'restore credential exposed in output'
        assert completed.returncode == 0, completed.stdout + completed.stderr
        (BACKUP / 'image-owned.json').write_text(json.dumps(assert_image_owned_unchanged(paths, image_before)))
        restored = json.loads((paths.state_dir / 'cold-restore.json').read_text())
        assert restored['state'] == 'pending' and restored['incomplete_backup'] and restored['unrestorable_revision_ids'], restored
        assert not (paths.runtime_venv / 'bin/python').exists()
        assert not any(paths.cache_dir.iterdir())
        assert (paths.da_root / 'files/restore-fixture').read_text() == 'durable interview fixture'
        secret = Path(os.environ['DA_MANAGED_SECRET_ROOT']) / 'restore-credential'
        header = scoped_authorization(expected['repository'], secret.read_bytes())
        assert base64.b64decode(header.removeprefix('Basic ')) == b'fixture:RESTORE_FIXTURE_PAT'
        assert not (secret.parent / 'candidate-only').exists()
        engine = create_engine(url)
        service = Installation(engine, lambda *_: True, expected['prefix'])
        with engine.connect() as connection:
            row = connection.execute(select(service.tables['candidate']).where(service.tables['candidate'].c.id == expected['candidate'])).mappings().one()
            assert row['state'] == 'cancelled'
        from docassemble_executor.cold_restore_boot import prepare_cold_boot
        from docassemble_executor.authority import MutationLock
        with MutationLock(paths.state_dir):
            with patch.dict(os.environ, DAUPDATEONSTART='false'):
                try:
                    prepare_cold_boot(service, paths)
                except ColdRestoreError:
                    pass
                else:
                    raise AssertionError('copied flags admitted unverified restore')
        print('PASS independent actual DB/config/files restore with no old venv/cache; source auth round-trip, stale candidates/secret invalidation, missing material and false flag block', flush=True)
        return
    if action == 'verify':
        assert fingerprint_image_owned(paths) == json.loads((BACKUP / 'image-owned.json').read_text()), 'runtime rebuild changed protected material'
        engine, prefix = database()
        service = Installation(engine, lambda *_: False, prefix)
        status = json.loads(paths.status_file.read_text())
        assert status['normal_ready'], status
        assert not (paths.log_dir / 'create-tables.log').exists(), 'restored successful setup was rerun'
        assert status['cold_restore']['incomplete_backup'] and status['cold_restore']['unrestorable_revision_ids']
        assert 'INCOMPLETE BACKUP' in status['cold_restore']['warnings'][0]
        assert inspect_environment(paths.runtime_venv) == expected['inventory']
        assert inspect_environment(paths.runtime_venv)['docassemble-webapp'] == '1.10.14'
        journal = Journal.load(paths.journal_file)
        assert journal.schema_succeeded and journal.schema.attempt_id != expected['schema']['attempt_id']
        with engine.connect() as connection:
            env = connection.execute(select(service.tables['environment_state'])).mappings().one()
            assert env['desired_revision_id'] == expected['revision']
            assert env['snapshot']['selected_baseline'] == expected['selected']
            attempt = connection.execute(select(service.tables['installation_attempt']).where(service.tables['installation_attempt'].c.id == journal.operation.id)).mappings().one()
            assert attempt['record']['restore_schema'] == expected['schema']
            assert attempt['record']['phase'] == 'succeeded'
            assert attempt['record']['claim']['host_id'] == env['snapshot']['cold_restore']['incarnation']
        invocations = Path('/var/log/docassemble-uv-invocations.log').read_text()
        image_invocations = (BACKUP / 'image-uv.log').read_text()
        assert invocations.startswith(image_invocations)
        invocations = invocations[len(image_invocations):]
        assert not any('\t' + op + ' ' in invocations for op in ('lock', 'build'))
        assert len([line for line in invocations.splitlines() if '\tsync ' in line]) == 1
        assert '--frozen' in invocations
        # A real revoked upstream auth request fails closed after credential
        # round-trip. Retained immutable artifacts themselves need no live token.
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        import urllib.request
        import urllib.error
        class Source(BaseHTTPRequestHandler):
            valid = True
            def do_GET(self):
                self.send_response(200 if self.valid and self.headers.get('Authorization') == header else 401)
                self.end_headers()
            def log_message(self, *_):
                pass
        secret = Path(os.environ['DA_MANAGED_SECRET_ROOT']) / 'restore-credential'
        header = scoped_authorization(expected['repository'], secret.read_bytes())
        server = ThreadingHTTPServer(('127.0.0.1', 0), Source)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        request = urllib.request.Request('http://127.0.0.1:' + str(server.server_port), headers={'Authorization': header})
        assert urllib.request.urlopen(request).status == 200
        Source.valid = False
        try:
            urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError('revoked source credential accepted')
        server.shutdown()
        server.server_close()
        # Actual pinned uv frozen consumption of unavailable external material:
        # a cold direct URL cannot silently become a registry package or re-solve.
        project = Path('/tmp/unavailable-frozen-source')
        project.mkdir()
        (project / 'pyproject.toml').write_text('[project]\nname="unavailable"\nversion="1"\nrequires-python= "' + python_requires() + '"\ndependencies=["absent"]\n')
        lock = 'version = 1\nrevision = 3\nrequires-python = "' + python_requires() + '"\n[[package]]\nname = "absent"\nversion = "1"\nsource = { url = "http://127.0.0.1:1/absent-1-py3-none-any.whl" }\nwheels = [{ url = "http://127.0.0.1:1/absent-1-py3-none-any.whl", hash = "sha256:' + 'a' * 64 + '" }]\n[[package]]\nname = "unavailable"\nversion = "1"\nsource = { virtual = "." }\ndependencies = [{name = "absent"}]\n[package.metadata]\nrequires-dist = [{name = "absent"}]\n'
        (project / 'uv.lock').write_text(lock)
        result = subprocess.run(['uv', 'sync', '--frozen', '--no-build', '--project', str(project)],
            env=dict(os.environ, UV_PROJECT_ENVIRONMENT='/tmp/unavailable-venv', UV_CACHE_DIR='/tmp/unavailable-cache', UV_HTTP_RETRIES='0'),
            capture_output=True, text=True)
        assert result.returncode != 0 and (project / 'uv.lock').read_text() == lock
        assert '127.0.0.1:1' in result.stderr and any(term in result.stderr.lower() for term in ('connect', 'download', 'fetch')), result.stderr
        (BACKUP / 'result.json').write_text(json.dumps({'normal_ready': True, 'real_db_files_restore': True,
            'no_old_venv_cache': True, 'preserved_web_core': '1.10.14', 'schema_setup_not_repeated': True,
            'source_auth_roundtrip': True, 'revoked_auth_rejected': True, 'unavailable_frozen_source_rejected': True,
            'unrelated_missing_history_warns_but_current_verified': True,
            'uv': '0.12.21', 'new_incarnation': env['snapshot']['cold_restore']['incarnation']}, indent=2))
        print('PASS new incarnation exact web-selected core frozen sync + real schema compatibility/web/all-12-role readiness, no solve/setup replay; revoked credentials and unavailable upstream fail without substitution', flush=True)
        return
    raise ValueError('unknown proof action')


if __name__ == '__main__':
    main(sys.argv[-1])
