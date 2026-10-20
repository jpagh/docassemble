"""Disposable all-role 05c2 proof. Explicit fixture provisioning, never adoption."""
import http.cookiejar
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from sqlalchemy import create_engine, select, update
from sqlalchemy.engine import URL

from docassemble.base.managed_preparation import Preparation
from docassemble_executor.config import load_config, database_settings
from docassemble_executor.image_baseline import provision_image_baseline
from docassemble_executor.installation import Installation, InstallationPoller
from docassemble_executor.inventory import inspect_environment
from docassemble_executor.journal import Journal
from docassemble_executor.legacy_adoption import worker_root
from docassemble_executor.paths import ExecutorPaths
from docassemble_executor.registry_builder import verify_retained

ROOT = Path('/tmp/image-bridge-proof')
DESCRIPTOR = Path('/etc/docassemble/qualified-image/baseline.json')
STORE = Path('/var/lib/docassemble-build/qualified-retained')


def database():
    config = load_config(ExecutorPaths.from_env().config_file)
    db = database_settings(config)
    url = URL.create('postgresql+psycopg2', username=db.user, password=db.password,
                     host=db.host, port=db.port, database=db.name)
    return create_engine(url), config.get('db', {}).get('table prefix') or ''


def save(name, value):
    (ROOT / name).write_text(json.dumps(value, sort_keys=True, indent=2))


def run(command, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, timeout=900, **kwargs)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def preparation_command() -> list[str]:
    """Non-root registry proofs must reconcile the worker store, not web ingress."""
    return ['runuser', '-u', 'da-environment', '--', sys.executable, '-m',
            'docassemble_executor.preparation_poller', '--store', str(STORE),
            '--image-baseline', str(DESCRIPTOR),
            '--artifact-root', str(worker_root('DA_EXECUTOR_ARTIFACT_ROOT')), '--once']


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def assert_recovery_status(local):
    assert not local['normal_ready'] and local['recovery_available']
    assert local['runtime_kind'] == 'core-only' and local['attempt_phase'] == 'failed'


def assert_recovery_readiness(request, paths):
    # The established ingress health contract uses 400, not 503, for non-ready.
    assert request('/health_check?ready=1')[0] == 400
    assert not (paths.run_dir / 'ready').exists()


def assert_retry_clearance_matrix(poller, clearance, assert_unchanged):
    """Exercise real executor refusal before consuming pending retry authority."""
    from docassemble_executor.installation import schema_clearance_file
    from docassemble_executor.state import _atomic_write_json

    path = schema_clearance_file(poller.paths)
    assert not path.exists(), 'disposable proof requires no preexisting operator clearance'
    for document in (None, dict(clearance, attempt_id='foreign-attempt'),
                     dict(clearance, schema_identity='foreign-schema-job'),
                     dict(clearance, observed_schema='stale-database-revision')):
        if document is not None:
            _atomic_write_json(path, document)
        assert poller.run_once() == 'schema-clearance-required'
        assert_unchanged()
    _atomic_write_json(path, clearance)
    assert poller.run_once() == 'reported'


def main():
    ROOT.mkdir(exist_ok=True)
    paths = ExecutorPaths.from_env()
    engine, prefix = database()
    service = Preparation(engine, lambda *_: True, prefix)  # proof-only readback, NOT HTTP authorization
    if sys.argv[-1] == 'setup':
        provision_image_baseline(paths, DESCRIPTOR.parent)
        baseline = json.loads(DESCRIPTOR.read_text())['baseline']
        # Explicit disposable-fixture intent, not production initialization or
        # adoption. Keep its immutable baseline revision and singleton coherent.
        snapshot = dict(baseline, desired_inventory=baseline['core'],
                        runtime={'kind': 'unknown', 'inventory': {}})
        with engine.begin() as connection:
            state = connection.execute(select(service.tables['environment_state'])).mappings().one()
            assert state['snapshot'].get('fixture_only') is True
            connection.execute(service.tables['revision'].insert().values(id='qualified-image-fixture-r1',
                parent_id=state['desired_revision_id'], snapshot=snapshot))
            connection.execute(update(service.tables['environment_state']).values(snapshot=snapshot,
                desired_revision_id='qualified-image-fixture-r1',
                publication_generation=state['publication_generation'] + 1,
                target_generation=state['target_generation'] + 1, runtime_generation=state['runtime_generation'] + 1))
        STORE.mkdir(exist_ok=True)
        account = pwd.getpwnam('da-environment')
        os.chown(STORE, account.pw_uid, account.pw_gid)
        save('qualified-baseline.json', baseline)
        print('PASS explicit operator image provisioning: every baseline wheel fixed by image lock/hash; no adoption', flush=True)
        return

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect())
    csrf = None
    def request(path, body=None, key=None, form=False, method=None):
        headers = {}
        if key:
            headers['X-API-Key'] = key
        if body is not None:
            if form:
                encoded = urllib.parse.urlencode(body).encode()
            else:
                headers['Content-Type'] = 'application/json'
                if csrf:
                    headers['X-CSRFToken'] = csrf
                encoded = json.dumps(body).encode()
        else:
            encoded = None
        req = urllib.request.Request('http://127.0.0.1' + path, data=encoded, headers=headers, method=method)
        try:
            response = opener.open(req, timeout=60)
        except urllib.error.HTTPError as error:
            response = error
        return response.code, response.read().decode()

    assert request('/api/package_preview?candidate_id=00000000-0000-0000-0000-000000000000')[0] == 403
    code, html = request('/user/sign-in')
    assert code == 200
    csrf = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
    code, _ = request('/user/sign-in', {'csrf_token': csrf, 'email': 'admin@example.com',
                                     'password': 'FixtureAdmin101!', 'submit': 'Sign in'}, form=True)
    assert code in (302, 303), code
    code, html = request('/package_preview')
    assert code == 200, html
    csrf = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
    # Credential provision is a separate admitted fixture operator action using
    # the real serialized API-key writer. Do not retain the credential in evidence.
    key_code = ('from docassemble.webapp.server import application; '
        'from docassemble.webapp.api.helpers import add_api_key; '
        'ctx=application.app_context(); ctx.push(); '
        'print("PROOF_READ_KEY="+add_api_key(1,"07-read-only","none",[],permissions=["read_packages"])); '
        'print("PROOF_CONSTRAINED_KEY="+add_api_key(1,"07-constrained","ip",["192.0.2.1"],permissions=["manage_packages"])); '
        'print("PROOF_KEY="+add_api_key(1,"05c2-disposable","none",[],permissions=["manage_packages","read_packages"]))')
    key_output = run(['da-executor', 'run-consumer', '--', str(paths.runtime_venv / 'bin/python'), '-c', key_code])
    key = key_output.split('PROOF_KEY=')[-1].strip().splitlines()[0]
    assert len(key) == 32
    read_key = key_output.split('PROOF_READ_KEY=')[-1].strip().splitlines()[0]
    constrained_key = key_output.split('PROOF_CONSTRAINED_KEY=')[-1].strip().splitlines()[0]
    assert len(read_key) == len(constrained_key) == 32
    print('PASS real session login + CSRF and real API credential/permission verification; unauthorized API rejected', flush=True)

    # API compatibility ingress is prepare-only: no committed intent or uv work.
    def intent_snapshot():
        with engine.connect() as connection:
            return {name: [dict(row) for row in connection.execute(select(service.tables[name])).mappings()]
                    for name in ('environment_state', 'revision', 'package_identity',
                                 'requirement_contribution', 'installation_attempt')}

    def rejected(path, body, expected, *, credential=key, method=None):
        code, payload = request(path, body, key=credential, method=method)
        error = json.loads(payload)
        assert code == (403 if expected == 'forbidden' else 409), error
        assert set(error) == {'error', 'code'} and error['code'] == expected, error
        assert all(value not in payload for value in (key, read_key, 'secret-token', 'Traceback', 'SQL', str(STORE)))
        return error

    unchanged = intent_snapshot()
    uv_before_api = Path('/var/log/docassemble-uv-invocations.log').read_text()
    with engine.connect() as connection:
        candidate_count = len(list(connection.execute(select(service.tables['candidate'].c.id))))
    for path, body, method in (
            ('/api/package', {'pip': 'sampleproject==4.0.0'}, 'POST'),
            ('/api/package', {'package': 'sampleproject', 'contribution_id': 'explicit'}, 'DELETE'),
            ('/api/package_preview', {'action': 'prepare', 'requirement': 'sampleproject==4.0.0'}, 'POST'),
            ('/api/package_preview', {'action': 'approve'}, 'POST'),
            ('/api/package_preview', {'action': 'retry'}, 'POST')):
        error = rejected(path, dict(body, restart=False, idempotency_key='api-no-restart'),
                         'restart-required', method=method)
        assert 'separate explicit approval' in error['error']
    with engine.connect() as connection:
        assert len(list(connection.execute(select(service.tables['candidate'].c.id)))) == candidate_count
    shim_body = {'pip': 'sampleproject==4.0.0', 'idempotency_key': 'api-shim'}
    code, payload = request('/api/package', shim_body, key=key)
    assert code == 202, payload
    shim = json.loads(payload)
    assert shim['candidate_id'] == shim['id'] and shim['state'] == 'queued'
    code, payload = request('/api/package', shim_body, key=key)
    assert code == 202 and json.loads(payload)['candidate_id'] == shim['candidate_id']
    rejected('/api/package', dict(shim_body, pip='sampleproject==3.0.0'), 'idempotency-conflict')
    code, payload = request('/api/package_update_status?candidate_id=' + shim['candidate_id'] + '&task_id=old-task', key=read_key)
    assert code == 200 and json.loads(payload)['candidate_id'] == shim['candidate_id']
    for action in ({'action': 'cancel', 'candidate_id': shim['candidate_id']},
                   {'action': 'approve', 'candidate_id': shim['candidate_id'], 'idempotency_key': 'read-approve'},
                   {'requirement': 'sampleproject==4.0.0', 'idempotency_key': 'read-prepare'}):
        rejected('/api/package_preview', action, 'forbidden', credential=read_key)
    rejected('/api/package_update_status?candidate_id=' + shim['candidate_id'], None,
             'forbidden', credential=constrained_key)
    rejected('/api/package', {'github_url': 'https://user:secret-token@example.invalid/repo'}, 'rejected')
    rejected('/api/package_update_status?candidate_id=invalid-secret-token', None, 'rejected')
    rejected('/api/package_update_status?task_id=old-task', None, 'rejected')
    code, payload = request('/api/package_preview', {'action': 'cancel', 'candidate_id': shim['candidate_id']}, key=key)
    assert code == 200 and json.loads(payload)['state'] == 'cancelled'
    assert intent_snapshot() == unchanged
    assert Path('/var/log/docassemble-uv-invocations.log').read_text() == uv_before_api
    save('api-prepare-only.json', {'candidate_id': shim['candidate_id'], 'intent_unchanged': True,
         'uv_unchanged': True, 'restart_required': True, 'idempotent': True, 'read_scope_enforced': True,
         'ip_constraint_enforced': True, 'errors_redacted': True})
    print('PASS legacy API prepare-only shim; restart-required/no candidate, idempotency/conflict, read-only token/IP constraints, redaction and old-task isolation; no intent or uv mutation', flush=True)

    def prepare(requirement, identity):
        code, payload = request('/package_preview', {'requirement': requirement, 'idempotency_key': identity})
        assert code == 202, payload
        candidate = json.loads(payload)['id']
        command = preparation_command()
        os.environ['DA_PREPARATION_DATABASE_URL'] = engine.url.render_as_string(hide_password=False)
        assert run(command).strip() == 'ready'
        code, payload = request('/package_preview?candidate_id=' + candidate)
        preview = json.loads(payload)
        assert code == 200 and preview['approval_supported'], preview
        save(identity + '-preview.json', preview)
        return candidate, preview

    candidate, preview = prepare('sampleproject==4.0.0', 'first')
    result = preview['preview']
    bundle = result['bundle']['digest']
    verify_retained(STORE / bundle, bundle)
    save('attestation.json', result)
    # Retain project/lock/full hash manifest and newly prepared sources/wheels.
    # Image-core wheels are witnessed by the descriptor and exact image lock,
    # not duplicated as hundreds of MB of third-party evidence in Git.
    proof_bundle = ROOT / 'published-bundle'
    proof_bundle.mkdir()
    for filename in ('manifest.json', 'pyproject.toml', 'uv.lock', 'inventory.json', 'tool.json', 'installed-material.json'):
        shutil.copyfile(STORE / bundle / filename, proof_bundle / filename)
    shutil.copytree(STORE / bundle / 'sources', proof_bundle / 'sources')
    (proof_bundle / 'candidate-wheels').mkdir()
    core = json.loads(DESCRIPTOR.read_text())['baseline']['core']
    from packaging.utils import parse_wheel_filename, canonicalize_name
    for wheel in (STORE / bundle / 'wheels').glob('*.whl'):
        if canonicalize_name(parse_wheel_filename(wheel.name)[0]) not in core:
            shutil.copyfile(wheel, proof_bundle / 'candidate-wheels' / wheel.name)
    shutil.copyfile(paths.runtime_bundle('r1') / 'uv.lock', ROOT / 'image-runtime-uv.lock')
    before = Path('/var/log/docassemble-uv-invocations.log').read_text()
    code, payload = request('/api/package_preview', {'action': 'approve', 'candidate_id': candidate,
        'result_digest': preview['result_digest'], 'idempotency_key': 'approve'}, key=key)
    assert code == 202, payload
    approved = json.loads(payload)
    assert approved['installation'] == 'pending'
    code, payload = request('/api/package_preview', {'action': 'approve', 'candidate_id': candidate,
        'result_digest': preview['result_digest'], 'idempotency_key': 'approve'}, key=key)
    assert code == 202 and json.loads(payload) == approved
    rejected('/api/package_preview', {'action': 'approve', 'candidate_id': candidate,
        'result_digest': preview['result_digest'], 'idempotency_key': 'approve'},
        'forbidden', credential=read_key)
    save('published-pending.json', approved)
    assert Path('/var/log/docassemble-uv-invocations.log').read_text() == before
    print('PASS authenticated browser preview -> API approve -> atomic publication/pending (no installation success)', flush=True)
    if sys.argv[-1] == 'pending':
        # Separate 05c3 boot boundary, not a shortened all-role application proof.
        return

    command = [sys.executable, '-m', 'docassemble_executor.cli', 'poll-installation', '--store', str(STORE),
               '--baseline', str(DESCRIPTOR), '--once']
    # A real admitted finite consumer forces a confirmed no-mutation abort.
    release = ROOT / 'release-consumer'
    holder = subprocess.Popen(['da-executor', 'run-consumer', '--', str(paths.runtime_venv / 'bin/python'), '-c',
        'import pathlib,time; p=pathlib.Path("' + str(release) + '"); '
        'exec("while not p.exists(): time.sleep(0.1)"); print("accepted consumer completed")'])
    time.sleep(2)
    try:
        abort = run(command, env=dict(os.environ, DA_DRAIN_TIMEOUT='0.2'))
        assert 'reported' in abort
        journal = Journal.load(paths.journal_file)
        assert journal.attempt_result['outcome'] == 'aborted-before-sync', journal.to_dict()
        code, payload = request('/api/package_preview?attempt_id=' + approved['attempt_id'], key=key)
        status = json.loads(payload)
        assert code == 200 and status['installation'] == 'failed' and status['retry_supported'], status
        save('aborted-status.json', status)
        added = Path('/var/log/docassemble-uv-invocations.log').read_text()[len(before):]
        assert not any('\t' + operation + ' ' in added for operation in ('sync', 'lock', 'build'))
        code, payload = request('/api/package_preview', {'action': 'retry', 'revision_id': approved['revision_id'],
            'idempotency_key': 'safe-retry'}, key=key)
        assert code == 202, payload
        retried = json.loads(payload)
    finally:
        release.touch()
        holder.wait(timeout=30)
    time.sleep(2)
    print('PASS failure-visible confirmed pre-sync drain abort; no sync/solve/build; authenticated explicit safe retry', flush=True)

    installation = Installation(engine, lambda *_: False, prefix)
    poller = InstallationPoller(installation, paths, STORE, DESCRIPTOR)
    original = installation.report_terminal
    def lost_report(receipt):
        if receipt.get('outcome') == 'applied-normal':
            raise RuntimeError('proof reporting outage')
        return original(receipt)
    installation.report_terminal = lost_report
    try:
        poller.run_once()
    except RuntimeError as error:
        assert str(error) == 'proof reporting outage'
    else:
        raise AssertionError('missing normal reporting outage')
    journal = Journal.load(paths.journal_file)
    assert journal.attempt_result['outcome'] == 'applied-normal', journal.to_dict()
    with engine.connect() as connection:
        state = connection.execute(select(service.tables['environment_state'])).mappings().one()
        assert state['active_attempt_id'] == retried['attempt_id'] and state['snapshot']['runtime']['kind'] == 'unknown'
    code, payload = request('/status.json')
    assert code == 200 and json.loads(payload)['normal_ready'] is False
    save('lost-report-local-status.json', json.loads(payload))
    installed = inspect_environment(paths.runtime_venv)
    assert installed == {name: value['version'] for name, value in result['bundle']['inventory'].items()}
    save('installed-inventory.json', installed)
    invocations = Path('/var/log/docassemble-uv-invocations.log').read_text()[len(before):]
    syncs = [line for line in invocations.splitlines() if '\tsync ' in line]
    assert len(syncs) == 1 and '--frozen' in syncs[0] and '--no-build' in syncs[0]
    assert syncs[0].startswith(str(pwd.getpwnam('da-environment').pw_uid) + '\t')
    (ROOT / 'apply-uv-invocations.log').write_text(invocations)
    save('normal-terminal-journal.json', journal.to_dict())
    print('PASS executor claims retry -> exact published digest frozen/no-build/non-root apply -> schema/probe/all-role qualification; durable result before DB acknowledgement', flush=True)
    assert 'reported' in run(command)
    assert 'replayed' in run(command)
    assert Path('/var/log/docassemble-uv-invocations.log').read_text()[len(before):] == invocations
    code, payload = request('/api/package_preview?attempt_id=' + retried['attempt_id'], key=key)
    status = json.loads(payload)
    assert code == 200 and status['installation'] == 'verified-running', status
    assert status['verified_running_revision_id'] == approved['revision_id'] and not status['retry_supported']
    save('verified-running-status.json', status)
    code, payload = request('/status.json')
    local = json.loads(payload)
    assert local['normal_ready'] and local['locked_converged']
    assert local['applied_revision'] == approved['revision_id']
    assert all(check['ok'] for check in local['role_health']) and len(local['role_health']) == 12
    save('normal-local-status.json', local)
    print('PASS independent CLI lost-success replay rechecks material and actual roles without reinstall/schema; normal readiness/applied/DB running advancement', flush=True)

    if sys.argv[-1] == 'normal':
        return  # 05c4 cold resumption fixture; default full-flow corruption remains unchanged.

    candidate, preview = prepare('pyfiglet==1.0.4', 'second')
    code, payload = request('/api/package_preview', {'action': 'approve', 'candidate_id': candidate,
        'result_digest': preview['result_digest'], 'idempotency_key': 'approve-second'}, key=key)
    assert code == 202, payload
    failed = json.loads(payload)
    directory = STORE / preview['preview']['bundle']['digest']
    if sys.argv[-1] == 'recovery':
        from unittest.mock import patch
        from docassemble_executor.authority import MutationLock
        from docassemble_executor.durable_work import publish_live_status
        from docassemble_executor.uvsync import sync_frozen
        before_failure = Path('/var/log/docassemble-uv-invocations.log').read_text()
        intent = paths.intent_file.read_bytes()
        applied = paths.applied_file.read_bytes()
        schema_log = paths.log_dir / 'create-tables.log'
        schema_before = schema_log.read_bytes() if schema_log.exists() else None
        def failed_sync(*args, **kwargs):
            # Real uv fails opening the operator-faulted runtime lock as the
            # non-root installer. Restore permissions before safety recovery.
            lock = paths.runtime_venv / '.lock'
            original_stat = lock.stat()
            os.chown(lock, 0, 0)
            lock.chmod(0o400)
            try:
                result = sync_frozen(*args, **kwargs)
                assert not result.ok and 'Permission denied' in result.output_tail, result
                return result
            finally:
                os.chown(lock, original_stat.st_uid, original_stat.st_gid)
                lock.chmod(original_stat.st_mode & 0o777)

        def unavailable(receipt):
            assert receipt['outcome'] == 'failed-stopped' and receipt['recovery']['result'] == 'succeeded', receipt
            raise RuntimeError('proof recovery reporting outage')
        installation.report_terminal = unavailable
        with patch('docassemble_executor.installation.sync_frozen', failed_sync):
            try:
                poller.run_once()
            except RuntimeError as error:
                assert str(error) == 'proof recovery reporting outage'
            else:
                raise AssertionError('missing recovery reporting outage')
        recovered = Journal.load(paths.journal_file)
        save('recovery-terminal-journal.json', recovered.to_dict())
        assert recovered.phase == 'recovery_succeeded' and recovered.recovery.attempt == 1
        assert recovered.attempt_result['normal_verified'] is False
        assert paths.intent_file.read_bytes() == intent and paths.applied_file.read_bytes() == applied
        assert (schema_log.read_bytes() if schema_log.exists() else None) == schema_before
        with engine.connect() as connection:
            state = connection.execute(select(service.tables['environment_state'])).mappings().one()
            assert state['active_attempt_id'] == failed['attempt_id']
            assert state['desired_revision_id'] == failed['revision_id']
        recovery_invocations = Path('/var/log/docassemble-uv-invocations.log').read_text()[len(before_failure):]
        (ROOT / 'recovery-uv-invocations.log').write_text(recovery_invocations)
        syncs = [line for line in recovery_invocations.splitlines() if '\tsync ' in line]
        assert len(syncs) == 2 and all('--frozen' in line and '--no-build' in line for line in syncs)
        assert all(line.startswith(str(pwd.getpwnam('da-environment').pw_uid) + '\t') for line in syncs)
        assert '--reinstall' not in syncs[0] and '--reinstall' in syncs[1]
        assert not any('\t' + op + ' ' in recovery_invocations for op in ('lock', 'build'))
        installation.report_terminal = original
        # Replay does not need candidate bytes or management configuration, and
        # cannot turn one completed recovery into another automatic restoration.
        hidden = STORE.with_name('recovery-store-hidden')
        STORE.rename(hidden)
        try:
            assert poller.run_once() == 'reported'
            assert poller.run_once() == 'replayed'
            with MutationLock(paths.state_dir):
                publish_live_status(installation, paths)
        finally:
            hidden.rename(STORE)
        assert Journal.load(paths.journal_file).to_dict() == recovered.to_dict()
        assert Path('/var/log/docassemble-uv-invocations.log').read_text()[len(before_failure):] == recovery_invocations
        status = service.publication_status('1', attempt_id=failed['attempt_id'])
        save('recovery-durable-status.json', status)
        assert status['installation'] == 'failed' and status['attempt_phase'] == 'failed'
        assert status['desired_revision_id'] == failed['revision_id'] and not status['active']
        assert status['runtime_kind'] == 'core-only' and status['recovery_core']['bundle'] == 'core-r1'
        assert status['recovery_id'] == failed['attempt_id'] + ':core:1'
        assert status['verified_running_revision_id'] is None
        assert status['last_normally_verified_revision_id'] == approved['revision_id']
        assert not status['retry_supported']
        local = json.loads(request('/status.json')[1])
        save('recovery-local-status.json', local)
        assert_recovery_status(local)
        code, page = request('/recovery')
        assert code == 200 and 'Restricted recovery administration' in page, (code, page)
        assert request('/interview')[0] in (403, 404, 503), 'normal interview admitted during recovery'
        assert_recovery_readiness(request, paths)
        installed = inspect_environment(paths.runtime_venv)
        manifest = json.loads(paths.manifest_file.read_text())
        assert installed == manifest['inventories']['core-r1']['distributions']
        save('recovery-installed-inventory.json', installed)
        # Recovery evidence authorizes an exact retry; it does not clear uncertain
        # setup. The runtime helper uses real current administrator authorization.
        from core_recovery_retry_proof import retry_state
        from docassemble_executor.authority import run_runtime
        with MutationLock(paths.state_dir):
            retry = run_runtime([str(paths.runtime_venv / 'bin/python'),
                '/repo/uv-experiment/preparation/core_recovery_retry_proof.py', prefix, failed['revision_id']],
                env=dict(os.environ), timeout=60)
        assert retry.returncode == 0, retry.stdout + retry.stderr
        (ROOT / 'recovery-retry.log').write_text(retry.stdout + retry.stderr)
        retried = json.loads(next(line.removeprefix('PROOF_RETRY=') for line in retry.stdout.splitlines()
                                 if line.startswith('PROOF_RETRY=')))
        pending = retry_state(installation)
        assert retried['revision_id'] == failed['revision_id']
        assert pending['environment_state'][0]['active_attempt_id'] == retried['attempt_id']
        assert installation.next_after_terminal(recovered.attempt_result)
        assert not installation.next_after_terminal(dict(recovered.attempt_result, failure=None))

        # Explicit disposable fault injection: simulate interrupted setup using
        # production durable schema-work records and the published schema identity.
        # This is NOT a reproduced real setup interruption: genuine sync failed
        # before setup, as the unchanged schema log above proves.
        from types import SimpleNamespace
        from docassemble_executor.alembic_probe import read_database_revision
        from docassemble_executor.controller import load_context
        from docassemble_executor.published import schema_identity
        with MutationLock(paths.state_dir):
            journal = Journal.load(paths.journal_file)
            assert not journal.schema_uncertain
            snapshot = next(row['snapshot'] for row in pending['revision'] if row['id'] == failed['revision_id'])
            binding = SimpleNamespace(attempt_id=failed['attempt_id'], revision_id=failed['revision_id'], snapshot=snapshot)
            context = load_context(paths, start_infrastructure=False)
            journal.begin_schema(journal.operation, schema_identity(binding, context))
            journal.save(paths.journal_file)
        print('FAULT INJECTION: simulated interrupted setup for clearance matrix; real sync failure did not run setup', flush=True)
        uncertain = Journal.load(paths.journal_file).to_dict()
        history = paths.state_dir / 'attempt-history' / (failed['attempt_id'] + '.json')
        assert not history.exists()
        observed = read_database_revision(database_settings(load_config(paths.config_file)))
        assert observed
        clearance = {'format': 1, 'attempt_id': journal.schema.attempt_id,
                     'schema_identity': journal.schema.identity, 'observed_schema': observed}
        uv_before_retry = Path('/var/log/docassemble-uv-invocations.log').read_text()
        def assert_unchanged():
            assert retry_state(installation) == pending  # Prior terminal and pending slot survive every refusal.
            assert Journal.load(paths.journal_file).to_dict() == uncertain
            assert not history.exists()
            assert Path('/var/log/docassemble-uv-invocations.log').read_text() == uv_before_retry
            assert paths.intent_file.read_bytes() == intent and paths.applied_file.read_bytes() == applied
            assert (schema_log.read_bytes() if schema_log.exists() else None) == schema_before
            assert_recovery_status(json.loads(request('/status.json')[1]))
            assert_recovery_readiness(request, paths)
        assert_retry_clearance_matrix(poller, clearance, assert_unchanged)
        completed = Journal.load(paths.journal_file)
        assert completed.attempt_result['identity']['attempt_id'] == retried['attempt_id']
        assert completed.attempt_result['identity']['revision_id'] == failed['revision_id']
        assert completed.attempt_result['outcome'] == 'applied-normal'
        assert completed.schema_succeeded and completed.schema.attempt_id == retried['attempt_id']
        archived = json.loads(history.read_text())
        assert archived['clearance']['attempt_id'] == failed['attempt_id']
        assert archived['clearance']['schema_identity'] == clearance['schema_identity']
        assert archived['clearance']['observed_schema'] == observed
        assert archived == dict(uncertain, clearance=archived['clearance'])
        after_retry = retry_state(installation)
        for name in ('revision', 'package_identity', 'requirement_contribution'):
            assert after_retry[name] == pending[name]
        assert [row for row in after_retry['installation_attempt'] if row['id'] != retried['attempt_id']] == [
            row for row in pending['installation_attempt'] if row['id'] != retried['attempt_id']]
        assert after_retry['environment_state'][0]['active_attempt_id'] is None
        invocations = Path('/var/log/docassemble-uv-invocations.log').read_text()[len(uv_before_retry):]
        syncs = [line for line in invocations.splitlines() if '\tsync ' in line]
        assert len(syncs) == 1 and '--frozen' in syncs[0] and '--no-build' in syncs[0]
        assert syncs[0].startswith(str(pwd.getpwnam('da-environment').pw_uid) + '\t')
        assert not any('\t' + operation + ' ' in invocations for operation in ('lock', 'build'))
        assert installed != inspect_environment(paths.runtime_venv)
        assert inspect_environment(paths.runtime_venv) == {
            name: value['version'] for name, value in snapshot['publication']['result']['bundle']['inventory'].items()}
        assert poller.run_once() == 'replayed'
        assert Path('/var/log/docassemble-uv-invocations.log').read_text()[len(uv_before_retry):] == invocations
        local = json.loads(request('/status.json')[1])
        assert local['normal_ready'] and local['locked_converged'] and local['applied_revision'] == failed['revision_id']
        assert request('/health_check?ready=1')[0] == 200 and (paths.run_dir / 'ready').exists()
        save('recovery-exact-retry.json', {'retry': retried, 'simulated_interrupted_setup': True,
             'missing_foreign_stale_clearance_refused': True, 'pending_authority_preserved': True,
             'matching_clearance': clearance, 'archived_failure': archived, 'normal_status': local})
        print('PASS failed frozen apply -> ONE non-root offline core-only recovery; durable bound failure before DB reporting; desired/applied/schema unchanged', flush=True)
        print('PASS lost recovery report replay without retained candidate bytes or another sync; exact-revision administrator retry preserves immutable intent and prior terminal', flush=True)
        print('PASS simulated interrupted setup: missing/foreign/stale clearance preserves pending authority; matching DB-observed clearance permits frozen non-root retry and normal qualification', flush=True)
        return
    (directory / 'inventory.json').write_text('{}')
    assert 'reported' in run(command)
    status = service.publication_status('1', attempt_id=failed['attempt_id'])
    assert status['installation'] == 'failed' and status['verified_running_revision_id'] is None
    assert status['last_normally_verified_revision_id'] == approved['revision_id']
    assert status['desired_revision_id'] == failed['revision_id']
    save('failed-durable-status.json', status)
    code, payload = request('/status.json')
    status = json.loads(payload)
    assert code == 200 and not status['normal_ready'] and status['publication']['failure']['reason'] == 'bundle-invalid', status
    save('failed-visible-status.json', status)
    assert request('/user/register')[0] == 403
    assert not any(name == 'flask' or name.startswith('docassemble.webapp') for name in sys.modules)
    print('PASS corrupt second publication fails before sync, desired retained/history separate/no running claim; independent failure-visible status; no runtime imports in executor', flush=True)


if __name__ == '__main__':
    main()
