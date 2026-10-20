"""Real supervised image-bridge work and cold normal resumption; no source injection."""
import hashlib
import http.cookiejar
import json
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from sqlalchemy import select

from docassemble.base.managed_preparation import runtime_status
from docassemble_executor.installation import Installation
from docassemble_executor.journal import Journal
from docassemble_executor.paths import ExecutorPaths
from docassemble_executor.supervision import supervisor_command
from image_bridge_proof import database, STORE

ROOT = Path('/tmp/continuous-work-proof')


def save(name, value):
    (ROOT / name).write_text(json.dumps(value, indent=2, sort_keys=True))


def invariants(paths):
    files = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (
        paths.log_dir / 'create-tables.log', paths.journal_file)}
    # Version checks are expected operational gates, not installation/resolution.
    commands = [line for line in Path('/var/log/docassemble-uv-invocations.log').read_text().splitlines()
                if line.split('\t', 1)[-1] != '--version']
    return dict(files, uv_mutation_commands=commands)


def wait(operation, description, seconds=600):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = operation()
        if result:
            return result
        time.sleep(1)
    raise AssertionError('timed out: ' + description)


def main():
    paths = ExecutorPaths.from_env()
    ROOT.mkdir(exist_ok=True)
    engine, prefix = database()
    installation = Installation(engine, lambda *_: True, prefix)  # proof-only readback, not HTTP authorization
    if sys.argv[-1] == 'capture':
        save('before-restart.json', invariants(paths))
        save('old-receipt.json', Journal.load(paths.journal_file).attempt_result)
        return

    def environment():
        with engine.connect() as connection:
            return dict(connection.execute(select(installation.tables['environment_state'])).mappings().one())

    def status():
        return json.loads(urllib.request.urlopen('http://127.0.0.1/status.json', timeout=20).read())

    def assert_shared(local):
        for key, value in runtime_status(environment()).items():
            assert local[key] == value, (key, local, value)

    local = wait(lambda: (s if (s := status()).get('normal_ready') else None), 'cold normal readiness')
    assert invariants(paths) == json.loads((ROOT / 'before-restart.json').read_text()), 'cold resumption repeated sync/schema/journal mutation'
    assert local['reconciliation'] == 'resumed-normal' and local['locked_converged']
    assert len(local['role_health']) == 12 and all(check['ok'] for check in local['role_health'])
    assert_shared(local)
    save('resumed-status.json', local)
    assert urllib.request.urlopen('http://127.0.0.1/user/sign-in').code == 200
    print('PASS no-pending cold boot freshly qualifies schema usability/core/web/all-12 roles; normal traffic/readiness with no sync/schema/journal replay', flush=True)

    def pid(kind):
        result = supervisor_command(paths, ['pid', 'durable-' + kind])
        assert result.returncode == 0, result.stdout + result.stderr
        return int(result.stdout.strip())

    assert pid('preparation') > 0 and pid('installation') > 0
    import pwd
    account = pwd.getpwnam('da-environment')
    assert ('Uid:\t' + str(account.pw_uid) + '\t') in (Path('/proc') / str(pid('preparation')) / 'status').read_text()
    # Supervisor, not the proof or HTTP worker, owns restart and continued polling.
    import os
    old_pid = pid('preparation')
    os.kill(old_pid, 9)
    wait(lambda: pid('preparation') not in (0, old_pid), 'Supervisor preparation autorestart', seconds=30)
    print('PASS boot starts both independent continuous daemons; Supervisor replaces killed preparation daemon', flush=True)

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def request(path, body=None, form=False):
        data = (urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode()) if body is not None else None
        headers = {} if form else {'Content-Type': 'application/json', 'X-CSRFToken': csrf or ''}
        req = urllib.request.Request('http://127.0.0.1' + path, data=data, headers=headers)
        try:
            response = opener.open(req, timeout=60)
        except urllib.error.HTTPError as error:
            response = error
        return response.code, response.read().decode()
    csrf = None
    code, html = request('/user/sign-in')
    assert code == 200
    csrf = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
    request('/user/sign-in', {'csrf_token': csrf, 'email': 'admin@example.com',
                             'password': 'FixtureAdmin101!', 'submit': 'Sign in'}, form=True)
    code, html = request('/package_preview')
    assert code == 200, html
    csrf = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
    code, payload = request('/package_preview', {'requirement': 'pyfiglet==1.0.4', 'idempotency_key': 'continuous'})
    assert code == 202, payload
    candidate = json.loads(payload)['id']
    def preview_ready():
        code, payload = request('/package_preview?candidate_id=' + candidate)
        assert code == 200, payload
        preview = json.loads(payload)
        assert preview.get('state') != 'failed', preview
        return preview if preview.get('result_digest') else None
    preview = wait(preview_ready, 'supervised durable preparation')
    save('continuous-preview.json', preview)
    assert preview['approval_supported']
    print('PASS authenticated preparation is discovered/built/published ready by the non-root supervised continuous poller; no one-shot CLI', flush=True)
    old_receipt = json.loads((ROOT / 'old-receipt.json').read_text())
    before_apply = Path('/var/log/docassemble-uv-invocations.log').read_text()
    code, payload = request('/package_preview', {'action': 'approve', 'candidate_id': candidate,
        'result_digest': preview['result_digest'], 'idempotency_key': 'approve-continuous'})
    assert code == 202, payload
    approved = json.loads(payload)
    assert approved['installation'] == 'pending'
    save('continuous-pending.json', approved)
    assert environment()['active_attempt_id'] == approved['attempt_id']
    assert installation.report_terminal(old_receipt) == 'replayed'
    assert environment()['active_attempt_id'] == approved['attempt_id'], 'old receipt released another attempt'
    pending = installation.publication_status('1', attempt_id=approved['attempt_id'])
    assert pending['installation'] in ('pending', 'in-progress') and pending['desired_revision_id'] == approved['revision_id']
    save('pending-status.json', pending)
    def complete():
        value = environment()
        return value if value['active_attempt_id'] is None and value['snapshot']['runtime'].get('revision_id') == approved['revision_id'] else None
    wait(complete, 'supervised frozen application')
    local = wait(lambda: (s if (s := status()).get('normal_ready') and s.get('desired_revision_id') == approved['revision_id'] else None), 'new normal live status')
    assert_shared(local)
    assert local['verified_running_revision_id'] == approved['revision_id']
    assert (paths.state_dir / 'attempt-history' / (old_receipt['identity']['attempt_id'] + '.json')).is_file()
    save('continuous-normal-status.json', local)
    calls = Path('/var/log/docassemble-uv-invocations.log').read_text()[len(before_apply):]
    syncs = [line for line in calls.splitlines() if '\tsync ' in line]
    assert len(syncs) == 1 and '--frozen' in syncs[0] and '--no-build' in syncs[0]
    assert syncs[0].startswith(str(account.pw_uid) + '\t')
    (ROOT / 'continuous-apply-invocations.log').write_text(calls)
    print('PASS continuous installation discovers subsequent publication, archives old receipt, applies exact frozen bytes and qualifies normal; late old success never releases pending slot', flush=True)

    # Lost-success recheck must invalidate actual health, not blindly retain DB success.
    before = invariants(paths)
    receipt = Journal.load(paths.journal_file).attempt_result
    (STORE / receipt['identity']['bundle_digest'] / 'inventory.json').write_text('{}')
    local = wait(lambda: (s if not (s := status()).get('normal_ready') and s.get('runtime_kind') == 'unknown' and
                         s.get('last_normally_verified_revision_id') == approved['revision_id'] else None), 'continuous health invalidation')
    assert_shared(local)
    assert local['verified_running_revision_id'] is None and not local['locked_converged']
    assert environment()['desired_revision_id'] == approved['revision_id'] and environment()['active_attempt_id'] is None
    assert invariants(paths) == before, 'health failure repeated sync/schema/journal mutation'
    save('invalidated-status.json', local)
    try:
        urllib.request.urlopen('http://127.0.0.1/health_check?ready=1')
    except urllib.error.HTTPError as error:
        assert error.code == 400
    else:
        raise AssertionError('false readiness after failed health')
    assert not any(name == 'flask' or name.startswith('docassemble.webapp') for name in sys.modules)
    print('PASS continuous retained-byte health failure closes readiness and invalidates current DB normal; desired/history retained, no automatic restore/retry/sync/schema', flush=True)


if __name__ == '__main__':
    main()
