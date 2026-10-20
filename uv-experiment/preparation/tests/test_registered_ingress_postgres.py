"""Real loopback HTTP/www-data -> root ingestion -> supervised da-environment worker.

Only the builder is a fixture: it verifies material and returns an attestation,
without a uv build or network solve. API Blueprint/auth/session, SQL publication,
privilege drop, copier and descendant-aware ProcessWorkers receipts are real.
Root helpers run via passwordless sudo when pytest itself is not root.
"""
# ruff: noqa: F811 -- imported PostgreSQL fixtures are injected by name
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import signal
import select as os_select
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

import pytest
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.exc import DBAPIError

from docassemble.base.managed_preparation import Preparation, PreparationError, digest
from docassemble.base.managed_publication import Publication
from docassemble.base.managed_artifacts import verify_artifact
from docassemble.base.managed_git import scoped_authorization, source_declarations
from docassemble_executor.legacy_adoption import ingest_registered_material
from docassemble_executor.preparation_poller import PreparationPoller
from test_preparation_postgres import protocol, result  # noqa: F401
from test_publication_writers_postgres import publisher, adapter  # noqa: F401
from test_zip_install_postgres import distribution
from test_registry_changes_postgres import finish
from test_publication_postgres import wait_for_approval_lock

TOKEN = 'DISPOSABLE_HTTP_CREDENTIAL_SENTINEL'
DECLARATION = 'Example @ git+https://user:' + TOKEN + '@example.org/repo.git@main'


def root_command(action):
    command = [sys.executable, str(Path(__file__).resolve()), action]
    if os.geteuid() == 0:
        return command
    return ['sudo', '-n', '--', 'env',
        'PYTHONPATH=' + os.pathsep.join(str(path) for path in sys.path),
        'DA_API_WRITER_REDIS_URL=' + os.environ['DA_API_WRITER_REDIS_URL'], *command]


def helper(config, action, **extra):
    completed = subprocess.run(root_command(action), input=json.dumps(dict(config, **extra)),
        text=True, capture_output=True, timeout=30, env=dict(os.environ,
            PYTHONPATH=os.pathsep.join(str(path) for path in sys.path)))
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.splitlines()[-1])


@pytest.fixture
def ingress(publisher):
    assert pwd.getpwnam('www-data').pw_uid == 33
    account = pwd.getpwnam('da-environment')
    assert account.pw_uid not in (0, 33)
    # The forked server drops to www-data and must still import the interpreter's
    # stdlib afterwards (zipfile needs encodings.cp437). If the interpreter sits
    # behind a directory www-data cannot traverse, the drop fails with a misleading
    # LookupError, so state the precondition instead of reporting a false failure.
    # These tests run for real in the qualified container, whose interpreter is
    # world-readable.
    blocked = next((parent for parent in [Path(sys.base_prefix), *Path(sys.base_prefix).parents]
                    if not parent.stat().st_mode & stat.S_IXOTH), None)
    if blocked is not None:
        pytest.skip('interpreter not traversable by the dropped www-data user (' + str(blocked) +
                    '); run in the qualified container where the interpreter is world-readable')
    directory = Path(tempfile.mkdtemp(prefix='registered-http-'))
    directory.chmod(0o755)
    config = {'directory': str(directory), 'url': os.environ['DA_PREPARATION_TEST_URL'],
        'roots': {name: str(directory / suffix) for name, suffix in (
            ('DA_MANAGED_ARTIFACT_ROOT', 'http-zip'),
            ('DA_MANAGED_WEB_SECRET_ROOT', 'http-git'),
            ('DA_EXECUTOR_ARTIFACT_ROOT', 'worker-zip'),
            ('DA_MANAGED_SECRET_ROOT', 'worker-git'))}}
    helper(config, 'setup')
    process = subprocess.Popen(root_command('http'), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, env=dict(os.environ,
            PYTHONPATH=os.pathsep.join(str(path) for path in sys.path)))
    process.stdin.write(json.dumps(config) + '\n')
    process.stdin.flush()
    try:
        assert os_select.select([process.stdout], [], [], 20)[0], 'HTTP server startup timed out'
        info = json.loads(process.stdout.readline())
        assert info['uid'] == 33
        config['port'] = info['port']
        yield config
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        errors = process.stderr.read()
        if errors:
            print(errors, file=sys.stderr)
        helper(config, 'remove')
        shutil.rmtree(directory)


def http(config, path, body=None, *, upload=None, key='upload'):
    headers = {'X-API-Key': 'disposable-api-token'}
    if upload is not None:
        boundary = 'registered-material-boundary'
        data = (f'--{boundary}\r\nContent-Disposition: form-data; name="idempotency_key"\r\n\r\n{key}\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="zip"; filename="upload.zip"\r\n'
                'Content-Type: application/zip\r\n\r\n').encode() + upload + f'\r\n--{boundary}--\r\n'.encode()
        headers['Content-Type'] = 'multipart/form-data; boundary=' + boundary
    else:
        data = json.dumps(body).encode() if body is not None else None
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(f'http://127.0.0.1:{config["port"]}' + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            assert response.status in (200, 202)
            value = json.load(response)
    except urllib.error.HTTPError as error:
        pytest.fail(error.read().decode())
    assert TOKEN not in json.dumps(value)
    return value


def materials(publisher, candidate):
    with publisher.engine.connect() as connection:
        table = publisher.tables['candidate_material']
        return [dict(row) for row in connection.execute(select(table).where(
            table.c.candidate_id == candidate)).mappings()]


def approve(config, candidate):
    status = http(config, '/api/package_preview?candidate_id=' + candidate)
    assert status['state'] == 'ready'
    return http(config, '/api/package_preview', {'action': 'approve', 'candidate_id': candidate,
        'result_digest': status['result_digest'], 'idempotency_key': 'approve-' + candidate})


def test_repeated_http_zip_pins_worker_destination_and_published_ingress_cleanup(publisher, ingress, tmp_path):
    payload = distribution(tmp_path / 'upload.zip').read_bytes()
    reference = hashlib.sha256(payload).hexdigest()
    roots = ingress['roots']
    for key in ('first', 'identical-reupload'):
        candidate = http(ingress, '/api/package', upload=payload, key=key)['candidate_id']
        assert helper(ingress, 'poll') == 'ready'
        rows = materials(publisher, candidate)
        destination = str(Path(roots['DA_EXECUTOR_ARTIFACT_ROOT']) / reference)
        source = str(Path(roots['DA_MANAGED_ARTIFACT_ROOT']) / reference)
        assert len(rows) == 2
        assert next(row for row in rows if row['path'] == destination)['usable']
        assert not next(row for row in rows if row['path'] == source)['usable']
        with publisher.engine.connect() as connection:
            inputs = connection.scalar(select(publisher.tables['candidate'].c.inputs).where(
                publisher.tables['candidate'].c.id == candidate))
        assert inputs['material_stores']['artifact'] == roots['DA_EXECUTOR_ARTIFACT_ROOT']
        assert inputs['material_stores']['ingress_artifact'] == roots['DA_MANAGED_ARTIFACT_ROOT']
        assert helper(ingress, 'cleanup') == {'artifacts': 0, 'secrets': 0}
        approve(ingress, candidate)
        rows = materials(publisher, candidate)
        assert [row['path'] for row in rows if row['committed']] == [destination]
        # Missing durable exit proof must not be replaced by terminal state.
        workers = publisher.tables['preparation_worker']
        with publisher.engine.begin() as connection:
            connection.execute(update(workers).where(workers.c.candidate_id == candidate).values(exited=False))
        assert helper(ingress, 'cleanup') == {'artifacts': 0, 'secrets': 0}
        assert helper(ingress, 'poll') == 'idle'  # Replays the real ECHILD receipt after identity drop.
        assert helper(ingress, 'cleanup') == {'artifacts': 1, 'secrets': 0}
        evidence = helper(ingress, 'inspect', paths=[source, destination])
        assert not evidence['files'][source]['exists']
        assert evidence['files'][destination]['exists']
        assert helper(ingress, 'cleanup') == {'artifacts': 0, 'secrets': 0}
        finish(publisher)
    assert sum(row['committed'] for row in materials(publisher, candidate)) == 1


def test_shared_http_ingress_cleanup_preserves_other_candidate_until_terminal(publisher, ingress, tmp_path):
    payload = distribution(tmp_path / 'upload.zip').read_bytes()
    reference = hashlib.sha256(payload).hexdigest()
    first = http(ingress, '/api/package', upload=payload, key='first')['candidate_id']
    second = http(ingress, '/api/package', upload=payload, key='second')['candidate_id']
    assert helper(ingress, 'poll') == 'ready'
    assert helper(ingress, 'poll') == 'ready'
    source = str(Path(ingress['roots']['DA_MANAGED_ARTIFACT_ROOT']) / reference)
    destination = str(Path(ingress['roots']['DA_EXECUTOR_ARTIFACT_ROOT']) / reference)
    assert next(row for row in materials(publisher, second) if row['path'] == source)['shared']
    approve(ingress, first)
    assert helper(ingress, 'cleanup') == {'artifacts': 1, 'secrets': 0}
    assert helper(ingress, 'inspect', paths=[source])['files'][source]['exists']
    publisher.cancel('2', second)
    assert helper(ingress, 'cleanup') == {'artifacts': 1, 'secrets': 0}
    evidence = helper(ingress, 'inspect', paths=[source, destination])
    assert not evidence['files'][source]['exists'] and evidence['files'][destination]['exists']
    assert helper(ingress, 'cleanup') == {'artifacts': 0, 'secrets': 0}


def test_http_git_published_credential_ingress_deleted_worker_reference_retained(publisher, ingress):
    candidate = http(ingress, '/api/package_preview', {
        'requirement': DECLARATION, 'idempotency_key': 'git'})['candidate_id']
    assert helper(ingress, 'poll') == 'ready'
    rows = materials(publisher, candidate)
    assert len(rows) == 2
    source = next(row['path'] for row in rows if not row['usable'])
    destination = next(row['path'] for row in rows if row['usable'])
    approve(ingress, candidate)
    assert helper(ingress, 'cleanup') == {'artifacts': 0, 'secrets': 1}
    evidence = helper(ingress, 'inspect', paths=[source, destination])
    assert not evidence['files'][source]['exists']
    assert evidence['files'][destination]['exists']
    assert next(row for row in materials(publisher, candidate) if row['path'] == destination)['committed']
    finish(publisher)
    # Retained credentials remain usable under a subsequent live claim.
    followup = publisher.prepare('2', 'Unrelated', 'reuse-git')
    assert helper(ingress, 'retained', candidate=followup) is True


@pytest.mark.parametrize('fault', ['unregistered', 'digest', 'symlink', 'hardlink', 'owner', 'mode', 'root-mode'])
def test_registered_http_material_copy_refuses_untrusted_input(publisher, ingress, tmp_path, fault):
    payload = distribution(tmp_path / 'upload.zip').read_bytes()
    candidate = http(ingress, '/api/package', upload=payload)['candidate_id']
    before = materials(publisher, candidate)
    evidence = helper(ingress, 'reject', candidate=candidate, fault=fault)
    assert evidence['refused'] is True
    assert evidence['destinations'] == []
    if fault != 'unregistered':
        assert materials(publisher, candidate) == before


def test_http_zip_without_durable_destination_refuses_publication_even_without_split_environment(publisher, ingress, tmp_path):
    payload = distribution(tmp_path / 'upload.zip').read_bytes()
    candidate = http(ingress, '/api/package', upload=payload)['candidate_id']
    claim = publisher.claim(candidate)
    attestation = result(claim)
    artifact = claim.inputs['artifact']
    inventory = deepcopy(claim.inputs['baseline']['desired_inventory'])
    inventory[artifact['normalized_name']] = {'version': artifact['version'], 'source_digest': artifact['digest']}
    attestation['bundle']['inventory'] = inventory
    attestation['bundle']['artifacts'] = [artifact['digest']]
    differences = {artifact['normalized_name']: {'before': None, 'after': inventory[artifact['normalized_name']]}}
    attestation['desired_diff'] = attestation['runtime_diff'] = differences
    assert claim.inputs['material_stores']['artifact'] is None
    assert publisher.publish_ready(claim, attestation)
    with pytest.raises(PreparationError, match='retained distribution artifact unavailable'):
        publisher.approve('2', candidate, publisher.status('2', candidate)['result_digest'], 'approve')
    assert not any(row['committed'] for row in materials(publisher, candidate))


def test_published_ingress_cleanup_serializes_and_retries_unlink_before_tombstone_crash(publisher, ingress):
    candidate = http(ingress, '/api/package_preview', {
        'requirement': DECLARATION, 'idempotency_key': 'cleanup-crash'})['candidate_id']
    assert helper(ingress, 'poll') == 'ready'
    approve(ingress, candidate)
    rows = materials(publisher, candidate)
    source = next(row['path'] for row in rows if not row['usable'])
    destination = next(row['path'] for row in rows if row['committed'])
    with ThreadPoolExecutor(1) as pool:
        with publisher.engine.begin() as connection:
            publisher._environment(connection)
            pid = connection.scalar(text('SELECT pg_backend_pid()'))
            cleanup = pool.submit(helper, ingress, 'cleanup')
            wait_for_approval_lock(publisher.engine, pid)
            assert not cleanup.done()  # The same environment lock excludes terminal writers.
        assert cleanup.result(timeout=15) == {'artifacts': 0, 'secrets': 1}
    # Recreate an invalidated ingress reference and force the tombstone write to
    # abort after unlink, representing process loss/transaction rollback there.
    helper(ingress, 'recreate', candidate=candidate, source=source, destination=destination)
    with publisher.engine.begin() as connection:
        connection.execute(text("""CREATE FUNCTION refuse_ingress_tombstone() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.deleted THEN RAISE EXCEPTION 'injected tombstone crash'; END IF; RETURN NEW; END $$"""))
        connection.execute(text('CREATE TRIGGER refuse_tombstone BEFORE UPDATE ON test_managed_candidate_material '
            'FOR EACH ROW EXECUTE FUNCTION refuse_ingress_tombstone()'))
    assert helper(ingress, 'crash-cleanup') is True
    assert not next(row for row in materials(publisher, candidate) if row['path'] == source)['deleted']
    evidence = helper(ingress, 'inspect', paths=[source, destination])
    assert not evidence['files'][source]['exists'] and evidence['files'][destination]['exists']
    with publisher.engine.begin() as connection:
        connection.execute(text('DROP TRIGGER refuse_tombstone ON test_managed_candidate_material'))
    assert helper(ingress, 'cleanup') == {'artifacts': 0, 'secrets': 1}
    assert helper(ingress, 'cleanup') == {'artifacts': 0, 'secrets': 0}


class BoundaryBuilder:
    """Test-only attestation builder; real ProcessWorkers calls this after setuid."""
    def __init__(self, config):
        self.roots = config['roots']

    def build(self, inputs, **kwargs):
        account = pwd.getpwnam('da-environment')
        assert os.getresuid() == (account.pw_uid,) * 3
        for name in ('DA_MANAGED_ARTIFACT_ROOT', 'DA_MANAGED_WEB_SECRET_ROOT'):
            with pytest.raises(PermissionError):
                list(Path(self.roots[name]).iterdir())
            with pytest.raises(PermissionError):
                (Path(self.roots[name]) / 'worker-must-not-write').write_bytes(b'forbidden')
        inventory = deepcopy(inputs['baseline']['desired_inventory'])
        artifact = inputs.get('artifact')
        references = []
        if artifact:
            path = verify_artifact(Path(self.roots['DA_EXECUTOR_ARTIFACT_ROOT']), artifact['digest'])
            assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact['digest']
            inventory[artifact['normalized_name']] = {'version': artifact['version'], 'source_digest': artifact['digest']}
            references.append(artifact['digest'])
        sources = {}
        for name, source in source_declarations(inputs).items():
            authorization = inputs['git_authorizations'][name]
            reference = inputs['git_credentials'][name]
            assert authorization == (Path(self.roots['DA_MANAGED_SECRET_ROOT']) / reference).read_bytes()
            assert scoped_authorization(source['repository'], authorization)
            sources[name] = dict(source, resolved_sha='a' * 40, ref_kind='branch')
            inventory[name] = {'version': '1.0', 'source_digest': digest(sources[name])}
        def difference(before):
            return {name: {'before': before.get(name), 'after': inventory.get(name)}
                for name in sorted(set(before) | set(inventory)) if before.get(name) != inventory.get(name)}
        result = {'bundle': {'digest': 'd' * 64, 'project_digest': 'e' * 64, 'lock_digest': 'f' * 64,
            'inventory': inventory, 'artifacts': references},
            'desired_diff': difference(inputs['baseline']['desired_inventory']),
            'runtime_diff': (difference(inputs['baseline']['runtime']['inventory'])
                if inputs['baseline']['runtime']['kind'] == 'normal' else None),
            'warnings': Preparation.ready_warnings(inputs), 'ownership_effects': inputs['ownership_effects'],
            'validation': {'core_unchanged': True, 'requirements_satisfied': True,
                'durable_materialization_verified': True}}
        if sources:
            result['git_sources'] = sources
        return result


def helper_main(action, config):
    assert os.geteuid() == 0
    roots = config['roots']
    os.environ.update(roots, DA_MANAGED_ARTIFACT_UID='33')
    engine = create_engine(config['url'], connect_args={'options': '-csearch_path=preparation_test'})
    service = Preparation(engine, lambda *_: True, 'test_')
    if action == 'setup':
        for name, value in roots.items():
            root = Path(value)
            root.mkdir(mode=0o700)
            account = pwd.getpwnam('www-data' if name in (
                'DA_MANAGED_ARTIFACT_ROOT', 'DA_MANAGED_WEB_SECRET_ROOT') else 'da-environment')
            os.chown(root, account.pw_uid, account.pw_gid)
        return True
    if action == 'remove':
        for value in roots.values():
            shutil.rmtree(value)
        return True
    if action == 'http':
        from test_managed_api_postgres import api_http
        from werkzeug.serving import make_server
        monkeypatch = pytest.MonkeyPatch()
        publication = Publication(engine, adapter.PreviewAuthorizer('test_', lambda: {
            'allowed': {}, 'allow_updates': True, 'developer_can_install': True}), adapter.PackageProjection('test_'), 'test_')
        fixture = api_http.__wrapped__(publication, monkeypatch)
        http_fixture = next(fixture)
        # Parent secret root is NOT forwarded as web write authority.
        os.environ['DA_MANAGED_SECRET_ROOT'] = roots['DA_MANAGED_WEB_SECRET_ROOT']
        os.environ.pop('DA_EXECUTOR_ARTIFACT_ROOT', None)
        account = pwd.getpwnam('www-data')
        server = make_server('127.0.0.1', 0, http_fixture.client.application)
        os.setgroups([])
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
        assert os.getresuid() == (33,) * 3
        assert 'DA_EXECUTOR_ARTIFACT_ROOT' not in os.environ
        assert os.environ['DA_MANAGED_SECRET_ROOT'] == roots['DA_MANAGED_WEB_SECRET_ROOT']
        for name in ('DA_EXECUTOR_ARTIFACT_ROOT', 'DA_MANAGED_SECRET_ROOT'):
            with pytest.raises(PermissionError):
                list(Path(roots[name]).iterdir())
            with pytest.raises(PermissionError):
                (Path(roots[name]) / 'web-must-not-write').write_bytes(b'forbidden')
        print(json.dumps({'port': server.server_port, 'uid': os.geteuid()}), flush=True)
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        try:
            server.serve_forever()
        finally:
            server.server_close()
            fixture.close()
            monkeypatch.undo()
        return True
    if action == 'poll':
        return PreparationPoller(service, BoundaryBuilder(config),
            artifact_root=Path(roots['DA_EXECUTOR_ARTIFACT_ROOT']),
            material_root=Path(roots['DA_MANAGED_SECRET_ROOT'])).run_once()
    if action == 'cleanup':
        return {'artifacts': service.reconcile_artifacts(Path(roots['DA_MANAGED_ARTIFACT_ROOT'])),
            'secrets': service.reconcile_material(Path(roots['DA_MANAGED_WEB_SECRET_ROOT']))}
    if action == 'crash-cleanup':
        try:
            service.reconcile_material(Path(roots['DA_MANAGED_WEB_SECRET_ROOT']))
        except DBAPIError as error:
            assert 'injected tombstone crash' in str(error)
            return True
        return False
    if action == 'recreate':
        path = Path(config['source'])
        path.write_bytes(Path(config['destination']).read_bytes())
        path.chmod(0o600)
        os.chown(path, 33, 33)
        with engine.begin() as connection:
            table = service.tables['candidate_material']
            connection.execute(update(table).where(table.c.candidate_id == config['candidate'],
                table.c.path == str(path)).values(deleted=False))
        return True
    if action == 'inspect':
        files = {path: {'exists': Path(path).exists()} for path in config['paths']}
        for name, path in roots.items():
            info = Path(path).stat()
            assert stat.S_IMODE(info.st_mode) == 0o700
            assert info.st_uid == pwd.getpwnam('www-data' if name in (
                'DA_MANAGED_ARTIFACT_ROOT', 'DA_MANAGED_WEB_SECRET_ROOT') else 'da-environment').pw_uid
        for path in config['paths']:
            if Path(path).exists():
                assert stat.S_IMODE(Path(path).stat().st_mode) == 0o600
        return {'files': files}
    claim = service.claim(config['candidate'], lease_seconds=300)
    if action == 'retained':
        from docassemble_executor.legacy_adoption import drop_to_preparation_worker
        drop_to_preparation_worker()
        for reference in claim.inputs['git_credentials'].values():
            assert service.read_git_credential(claim, Path(roots['DA_MANAGED_SECRET_ROOT']), reference)
        return True
    if action == 'reject':
        table = service.tables['candidate_material']
        with engine.connect() as connection:
            row = connection.execute(select(table).where(table.c.candidate_id == claim.candidate_id)).mappings().one()
        path = Path(row['path'])
        fault = config['fault']
        if fault == 'unregistered':
            with engine.begin() as connection:
                connection.execute(table.delete().where(table.c.id == row['id']))
        elif fault == 'digest':
            path.write_bytes(b'digest mismatch')
        elif fault == 'symlink':
            target = path.with_name('outside')
            path.rename(target)
            path.symlink_to(target)
        elif fault == 'hardlink':
            os.link(path, path.with_name('outside'))
        elif fault == 'owner':
            os.chown(path, 0, 0)
        elif fault == 'mode':
            path.chmod(0o644)
        elif fault == 'root-mode':
            path.parent.chmod(0o755)
        try:
            ingest_registered_material(service, claim)
        except PreparationError:
            refused = True
        else:
            refused = False
        return {'refused': refused, 'destinations': list(map(str, Path(roots['DA_EXECUTOR_ARTIFACT_ROOT']).iterdir()))}
    raise AssertionError(action)


if __name__ == '__main__':
    print(json.dumps(helper_main(sys.argv[1], json.loads(sys.stdin.readline()))), flush=True)
