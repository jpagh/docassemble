"""Recovery proof form/HTTP regressions; no real app authentication or mutation."""
from docassemble.base.managed_python import python_target
import builtins
import http.server
import importlib.util
import io
import json
from pathlib import Path
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest


PREPARATION = Path(__file__).resolve().parents[2] / 'preparation'
SIGN_IN_HTML = ('<input name="csrf_token" type="hidden" value="fixture-csrf">'
                '<input name="next" type="hidden" value="/recovery">')


@pytest.fixture
def recovery_repair_proof(monkeypatch):
    monkeypatch.syspath_prepend(str(PREPARATION))
    spec = importlib.util.spec_from_file_location(
        'recovery_repair_proof', PREPARATION / 'recovery_repair_proof.py')
    proof = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proof)
    return proof


@pytest.fixture
def recovery_http_only(recovery_repair_proof):
    return recovery_repair_proof


@pytest.mark.parametrize('next_value', ['/recovery', '../recovery', 'http://127.0.0.1/recovery',
                                       'http://127.0.0.1:80/recovery'])
def test_recovery_repair_proof_sign_in_form_replays_rendered_fields(
        recovery_repair_proof, next_value):
    html = SIGN_IN_HTML.replace('value="/recovery"', 'value="' + next_value + '"')
    assert recovery_repair_proof.sign_in_form(html) == {
        'csrf_token': 'fixture-csrf', 'email': 'admin@example.com',
        'password': 'FixtureAdmin101!', 'submit': 'Sign in', 'next': next_value}


def test_recovery_repair_proof_sign_in_form_unescapes_next_before_urlencode(recovery_repair_proof):
    rendered = '/recovery?first=one&amp;second=two&#43;three&amp;quote=&quot;four&quot;'
    html = SIGN_IN_HTML.replace('value="/recovery"', 'value="' + rendered + '"')
    form = recovery_repair_proof.sign_in_form(html)
    expected = '/recovery?first=one&second=two+three&quote="four"'
    assert form['next'] == expected
    assert urllib.parse.parse_qs(urllib.parse.urlencode(form))['next'] == [expected]


@pytest.mark.parametrize('next_field', [
    '', '<input name="next" value="">',
    '<input name="next" value="https://foreign.example/recovery">',
    '<input name="next" value="//foreign.example/recovery">',
    '<input name="next" value="https://127.0.0.1/recovery">',
    '<input name="next" value="http://127.0.0.1:8080/recovery">',
    '<input name="next" value="http://127.0.0.1:0/recovery">',
    '<input name="next" value="/">',
    '<input name="next" value="/interview">',
    '<input name="next" value="/recovery/publication">',
])
def test_recovery_repair_proof_sign_in_form_refuses_invalid_next(
        recovery_repair_proof, next_field):
    html = '<input name="csrf_token" value="fixture-csrf">' + next_field
    with pytest.raises(ValueError, match='Sign-in form next'):
        recovery_repair_proof.sign_in_form(html)


@pytest.fixture
def recovery_http_server():
    """Only model rendered fields, cookie transport, redirect and root refusal."""
    events = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(2)

        def log_message(self, *args):
            pass

        def respond(self, event, status, body=b'', headers=()):
            event['status'] = status
            self.send_response(status)
            self.send_header('Content-Length', str(len(body)))
            for name, value in headers:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            event = {'method': 'GET', 'path': self.path, 'cookie': self.headers.get('Cookie', '')}
            events.append(event)
            if self.path == '/user/sign-in':
                self.respond(event, 200, SIGN_IN_HTML.encode(),
                             [('Set-Cookie', 'csrf=fixture-csrf; Path=/')])
            elif self.path == '/recovery' and 'session=fixture-session' in event['cookie']:
                self.respond(event, 200, b'Restricted recovery administration')
            elif self.path == '/':
                self.respond(event, 403, b'unsupported experimental image-bridge route')
            else:
                self.respond(event, 403, b'fixture session required')

        def do_POST(self):
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4096:
                self.send_error(400)
                return
            form = urllib.parse.parse_qs(self.rfile.read(length).decode())
            event = {'method': 'POST', 'path': self.path,
                     'cookie': self.headers.get('Cookie', ''), 'form': form}
            events.append(event)
            expected = {'csrf_token': ['fixture-csrf'], 'email': ['admin@example.com'],
                        'password': ['FixtureAdmin101!'], 'submit': ['Sign in']}
            if (self.path != '/user/sign-in' or 'csrf=fixture-csrf' not in event['cookie']
                    or {key: value for key, value in form.items() if key != 'next'} != expected):
                self.respond(event, 400)
                return
            self.respond(event, 302, headers=[
                ('Set-Cookie', 'session=fixture-session; Path=/'),
                ('Location', form.get('next', ['/'])[0]),
            ])

    server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
    thread.start()
    try:
        yield server.server_port, events
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def route_cookie_opener_to_fixture(proof, monkeypatch, port):
    build_opener = urllib.request.build_opener

    def fixture_opener(*handlers):
        assert any(isinstance(handler, urllib.request.HTTPCookieProcessor) for handler in handlers)
        opener = build_opener(*handlers)
        open_url = opener.open

        def open_fixture(url, data=None, timeout=None):
            if isinstance(url, urllib.request.Request):
                assert url.full_url.startswith(f'http://127.0.0.1:{port}/')
                return open_url(url, data=data, timeout=2)
            assert url.startswith('http://127.0.0.1/')
            return open_url(url.replace('http://127.0.0.1/', f'http://127.0.0.1:{port}/', 1),
                            data=data, timeout=2)

        monkeypatch.setattr(opener, 'open', open_fixture)
        return opener

    monkeypatch.setattr(proof.urllib.request, 'build_opener', fixture_opener)


def test_recovery_repair_proof_follows_rendered_next_with_session_cookie(
        recovery_http_only, recovery_http_server, monkeypatch, capsys):
    port, events = recovery_http_server
    route_cookie_opener_to_fixture(recovery_http_only, monkeypatch, port)
    recovery_http_only.BrowserHTTP().login(recovery_http_only.time.monotonic() + 120)
    assert [(event['method'], event['path'], event['status']) for event in events] == [
        ('GET', '/user/sign-in', 200), ('POST', '/user/sign-in', 302),
        ('GET', '/recovery', 200), ('GET', '/recovery', 200)]
    assert events[1]['form']['next'] == ['/recovery']
    assert 'csrf=fixture-csrf' in events[1]['cookie']
    assert all('session=fixture-session' in event['cookie'] for event in events[2:])
    assert capsys.readouterr() == ('', '')
    # A fresh cookie-aware client still cannot fetch the fixture recovery page.
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    with pytest.raises(urllib.error.HTTPError) as raised:
        opener.open('http://127.0.0.1/recovery', timeout=2)
    assert raised.value.code == 403
    assert events[-1]['cookie'] == ''


def test_recovery_repair_proof_omitting_next_reproduces_redirect_to_root_refusal(
        recovery_http_only, recovery_http_server, monkeypatch, capsys):
    proof = recovery_http_only
    port, events = recovery_http_server
    route_cookie_opener_to_fixture(proof, monkeypatch, port)
    sign_in_form = proof.sign_in_form

    def omit_next(html):
        form = sign_in_form(html)
        del form['next']
        return form

    monkeypatch.setattr(proof, 'sign_in_form', omit_next)
    with pytest.raises(urllib.error.HTTPError) as raised:
        proof.BrowserHTTP().login(proof.time.monotonic() + 120)
    assert raised.value.code == 403
    assert urllib.parse.urlsplit(raised.value.geturl()).path == '/'
    assert [(event['method'], event['path'], event['status']) for event in events] == [
        ('GET', '/user/sign-in', 200), ('POST', '/user/sign-in', 302), ('GET', '/', 403)]
    assert 'next' not in events[1]['form']
    assert 'session=fixture-session' in events[2]['cookie']
    captured = capsys.readouterr()
    assert captured.out == ''
    assert json.loads(captured.err) == {
        'initial_method': 'POST', 'initial_path': '/user/sign-in', 'terminal_path': '/',
        'status': 403, 'body': 'unsupported experimental image-bridge route'}


@pytest.mark.parametrize('method', ['GET', 'POST'])
@pytest.mark.parametrize('status', [403, 401, 404, 500, 503])
def test_recovery_repair_proof_request_reports_only_bounded_403_and_reraises(
        recovery_http_only, monkeypatch, capsys, method, status):
    proof = recovery_http_only
    body = b'\xff' + b'x' * 400
    body_stream = io.BytesIO(body)
    error = urllib.error.HTTPError(
        'http://127.0.0.1/denied?private-query#private-fragment', status, 'refused',
        {'Set-Cookie': 'private-cookie', 'Authorization': 'private-authorization'}, body_stream)
    calls = []
    prints = []

    class Opener:
        def open(self, url, data=None, timeout=None):
            calls.append((url, data, timeout))
            if method == 'POST' and data is None:
                return io.BytesIO(SIGN_IN_HTML.encode())
            raise error

    def print_with_record(*args, **kwargs):
        prints.append(kwargs)
        return builtins.print(*args, **kwargs)

    monkeypatch.setattr(proof.urllib.request, 'build_opener', lambda *args: Opener())
    monkeypatch.setattr(proof, 'print', print_with_record, raising=False)
    monkeypatch.setattr(proof.time, 'monotonic', lambda: 0)
    with pytest.raises(urllib.error.HTTPError) as raised:
        proof.BrowserHTTP().request('/user/sign-in', 120, {} if method == 'POST' else None)
    assert raised.value is error
    assert len(calls) == 1
    captured = capsys.readouterr()
    assert captured.out == ''
    if status == 403:
        assert len(captured.err.splitlines()) == 1
        assert json.loads(captured.err) == {
            'initial_method': method, 'initial_path': '/user/sign-in',
            'terminal_path': '/denied', 'status': 403,
            'body': body[:256].decode(errors='replace')}
        assert prints == [{'file': proof.sys.stderr, 'flush': True}]
        assert body_stream.tell() == 256
        for secret in ('private-query', 'private-fragment', 'private-cookie',
                       'private-authorization', 'fixture-csrf', 'admin@example.com', 'FixtureAdmin101!'):
            assert secret not in captured.err
    else:
        assert captured.err == ''
        assert prints == []
        assert body_stream.tell() == 0


@pytest.fixture
def bound_repair(recovery_repair_proof):
    from copy import deepcopy
    proof = recovery_repair_proof
    identity = {'attempt_id': 'FA', 'revision_id': 'F', 'fence': 1, 'host_id': 'H',
                'runtime_generation': 2, 'bundle_digest': 'failed-bundle', 'target': python_target()}
    recovery = {'id': 'FA:core:1', 'attempt': 1, 'result': 'succeeded',
                'inventory': {'docassemble-webapp': '1.10.14'}, 'core_baseline': {'bundle_digest': 'prior-core'}}
    failed_receipt = {'identity': identity, 'outcome': 'failed-stopped', 'mutation_started': True,
                      'child_safe': True, 'normal_verified': False,
                      'failure': {'stage': 'normal', 'reason': 'qualification-failed'}, 'recovery': recovery}
    schema = {'attempt_id': 'FA', 'revision': 'F', 'identity': 'old-schema', 'result': None, 'completed_at': None}
    clearance = {'attempt_id': 'FA', 'schema_identity': 'old-schema', 'observed_schema': 'alembic-revision',
                 'source_digest': 'source-hash', 'cleared_at': None}
    journal = {'operation': {'id': 'FA', 'revision': 'F', 'kind': 'published-apply', 'binding': identity},
               'phase': 'recovery_succeeded', 'attempt_result': failed_receipt, 'schema': schema,
               'clearance': clearance, 'failure': {'revision': 'F', 'message': 'failure'},
               'recovery': {'attempt': 1, 'result': 'succeeded', 'finished_at': 'earlier'},
               'verified_revision': 'old-success'}
    before = {'sequence': 1, 'read_errors': [], 'environment': {'desired_revision_id': 'F', 'active_attempt_id': None,
        **dict.fromkeys(proof.GENERATIONS, 3), 'snapshot': {'target': python_target(),
            'runtime': {'kind': 'core-only', 'attempt_id': 'FA', 'recovery_id': recovery['id'],
                        'inventory': recovery['inventory'], 'core_baseline': recovery['core_baseline']}}},
        'journal': journal, 'applied': {'revision': 'old-success'}, 'desired': {'revision': 'F'},
        'revisions': {'F': {'id': 'F', 'parent_id': 'old-success', 'snapshot': {'immutable': 'failed'}}},
        'attempts': {'FA': {'id': 'FA', 'revision_id': 'F', 'record': {'phase': 'failed', 'terminal': failed_receipt}}},
        'history': {'old-attempt': {'unchanged': 'history'}}, 'candidates': {}, 'publications': [], 'deduplication': [],
        'audit': b'1001\t--version\n', 'sync_logs': ['old-sync.log'], 'contributions': [],
        'installation_host': {'id': 'H'}, 'host': {'id': 'H', 'generation': 5, 'observation': {'host_generation': 5}},
        'schema_clearance': clearance, 'schema_clearance_source_digest': 'source-hash',
        'operator_clearance': {'missing_stale_foreign_clearance_denied': True,
                              'observed_database_revision': 'alembic-revision', 'journal': deepcopy(journal)},
        'alembic_revision': 'alembic-revision', 'status': {'mode': 'recovery', 'recovery_available': True, 'normal_ready': False},
        'admission': {'mode': 'open', 'consumers': False}, 'web_processes': {'recovery': {'pid': '4', 'starttime': '10'}}}
    result = {'bundle': {'digest': 'repair-bundle', 'inventory': {'docassemble-webapp': {'version': '1.10.15'}}}}
    inputs = {'change_operation': 'repair', 'parent_id': 'F', 'generations': dict.fromkeys(proof.GENERATIONS, 3),
              'baseline': deepcopy(before['environment']['snapshot']), 'contributions': [], 'committed_contributions': []}
    result_digest = proof.digest({'inputs': inputs, 'result': result})
    binding = {'candidate_id': 'C', 'revision_id': 'R', 'attempt_id': 'A', 'inputs': inputs, 'result': result,
               'result_digest': result_digest, 'bundle_digest': 'repair-bundle', 'target': python_target(),
               'preapprove_audit': before['audit'] + b'1001\tlock --no-config\n1001\tsync --frozen --no-config\n',
               'preapprove_host_generation': 10}
    candidate = {'id': 'C', 'operation': 'repair', 'requester_id': '1', 'state': 'published', 'parent_id': 'F',
                 'result_digest': result_digest, 'result': result, 'inputs': inputs, 'fence': 1,
                 'request_digest': proof.digest({'operation': 'repair', 'declaration': None, 'restart': True}),
                 **dict.fromkeys(proof.GENERATIONS, 3), 'expires_at': '2030-01-01T00:00:00+00:00'}
    identity = dict(identity, attempt_id='A', revision_id='R', runtime_generation=4, bundle_digest='repair-bundle')
    receipt = {'identity': identity, 'outcome': 'applied-normal', 'mutation_started': True, 'child_safe': True,
               'normal_verified': True, 'failure': None, 'inventory': {'docassemble-webapp': '1.10.15'},
               'readiness': {'schema_verified': True, 'roles': proof.ROLES}}
    observation = deepcopy(before)
    observation['environment'].update(desired_revision_id='R', publication_generation=4, runtime_generation=5)
    observation['environment']['snapshot']['runtime'] = {'kind': 'normal', 'revision_id': 'R', 'attempt_id': 'A',
                                                       'inventory': result['bundle']['inventory']}
    observation['candidates'] = {'C': candidate}
    observation['revisions']['R'] = {'id': 'R', 'parent_id': 'F', 'snapshot': {'target': binding['target'],
        'publication': {'candidate_id': 'C', 'approver_id': '1', 'inputs': inputs, 'result': result, 'result_digest': result_digest}}}
    observation['attempts']['A'] = {'id': 'A', 'revision_id': 'R', 'record': {
        'phase': 'succeeded', 'reason': 'approval', 'actor_id': '1', 'fence': 1, 'claim': identity, 'terminal': receipt}}
    observation['publications'] = [{'actor_id': '1', 'key': proof.APPROVE_KEY, 'candidate_id': 'C',
        'revision_id': 'R', 'attempt_id': 'A', 'request_digest': proof.digest({'candidate_id': 'C', 'result_digest': result_digest})}]
    observation['deduplication'] = [{'actor_id': '1', 'operation': 'repair', 'key': proof.PREPARE_KEY,
                                   'candidate_id': 'C', 'request_digest': candidate['request_digest']}]
    observation['journal'] = {'phase': 'verified', 'operation': {'id': 'A', 'revision': 'R', 'kind': 'published-apply',
        'binding': identity}, 'verified_revision': 'R', 'attempt_result': receipt, 'clearance': None,
        'schema': {'attempt_id': 'A', 'revision': 'R', 'result': 'succeeded', 'completed_at': 'now'}}
    observation['history']['FA'] = deepcopy(before['journal'])
    observation['applied'] = observation['desired'] = {'revision': 'R'}
    observation['publication_reported'] = identity
    observation['audit'] = binding['preapprove_audit'] + b'1001\tsync --frozen --no-build --project /var/lib/docassemble-build/frozen-repair\n'
    observation['sync_logs'].append('repair-sync.log')
    observation['host'] = {'id': 'H', 'generation': 11, 'observation': {'host_generation': 11}}
    processes = {name: [[str(index + 10), '100']] for index, name in enumerate(proof.ROLES)}
    observation['role_identities'] = processes
    observation['status'] = {'mode': 'normal', 'normal_ready': True, 'locked_converged': True, 'runtime_state': 'locked',
        'desired_revision': 'R', 'applied_revision': 'R', 'verified_running_revision_id': 'R', 'active': False,
        'attempt_id': 'A', 'publication': {'desired_revision_id': 'R', 'attempt_id': 'A', 'active': False,
            'attempt_phase': 'succeeded', 'terminal_outcome': 'applied-normal'},
        'role_health': [{'name': name, 'ok': True} for name in proof.ROLES], 'role_processes': processes,
        'host_generation': 11}
    observation['admission'] = {'mode': 'open', 'consumers': True}
    observation['ready_http_status'] = 200
    return deepcopy((before, binding, observation))


def test_recovery_repair_proof_accepts_only_bound_runtime_terminal(recovery_repair_proof, bound_repair):
    before, binding, observation = bound_repair
    proof = recovery_repair_proof
    proof.assert_baseline(before)
    assert proof.assert_repair_terminal(before, binding, observation)['outcome'] == 'applied-normal'
    assert proof.repair_terminal_state(before, binding, observation) == 'success'
    assert observation['journal']['clearance'] is None  # New schema work intentionally cleared it.
    assert proof.assert_prior_schema_clearance(before)['source_digest'] == 'source-hash'


# Literal cross-plane corruption matrix: the same certifier main calls rejects each.
TERMINAL_CORRUPTIONS = [
    ('environment.desired_revision_id', 'old-success'), ('environment.active_attempt_id', 'foreign'),
    ('environment.publication_generation', 5), ('environment.snapshot.runtime.kind', 'core-only'),
    ('environment.snapshot.runtime.revision_id', 'old-success'), ('environment.snapshot.runtime.attempt_id', 'FA'),
    ('environment.snapshot.runtime.inventory', {}), ('candidates.C.state', 'ready'),
    ('candidates.C.operation', 'adoption'), ('candidates.C.requester_id', '2'), ('candidates.C.parent_id', 'foreign'),
    ('candidates.C.result_digest', 'wrong'), ('candidates.C.inputs.change_operation', 'upgrade'),
    ('publications.0.actor_id', '2'), ('publications.0.key', 'auto-approve'), ('publications.0.candidate_id', 'foreign'),
    ('publications.0.request_digest', 'wrong'), ('publications', []),
    ('revisions.R.parent_id', None), ('revisions.R.snapshot.publication.approver_id', 'system'),
    ('revisions.R.snapshot.publication.candidate_id', 'foreign'), ('revisions.R.snapshot.publication.result_digest', 'wrong'),
    ('attempts.A.record.reason', 'retry'), ('attempts.A.record.actor_id', '2'), ('attempts.A.record.fence', 2),
    ('attempts.A.record.terminal.outcome', 'materialized-stopped'), ('attempts.A.record.terminal.mutation_started', False),
    ('attempts.A.record.terminal.child_safe', False), ('attempts.A.record.terminal.normal_verified', False),
    ('attempts.A.record.terminal.failure', {'stage': 'normal', 'reason': 'roles'}),
    ('attempts.A.record.claim.host_id', 'foreign'), ('attempts.A.record.claim.fence', 2),
    ('attempts.A.record.terminal.identity.attempt_id', 'FA'), ('attempts.A.record.terminal.identity.revision_id', 'F'),
    ('attempts.A.record.terminal.identity.bundle_digest', 'failed-bundle'),
    ('attempts.A.record.terminal.identity.target.python', '3.11'),
    ('attempts.A.record.terminal.identity.runtime_generation', 2),
    ('attempts.A.record.terminal.inventory', {}), ('attempts.A.record.terminal.readiness.roles', []),
    ('attempts.A.record.terminal.readiness.schema_verified', False),
    ('journal.phase', 'recovery_succeeded'), ('journal.operation.kind', 'published-materialization'),
    ('journal.operation.id', 'FA'), ('journal.operation.revision', 'F'), ('journal.operation.binding.host_id', 'foreign'),
    ('journal.verified_revision', 'old-success'), ('applied.revision', 'old-success'), ('desired.revision', 'F'),
    ('publication_reported.attempt_id', 'FA'), ('journal.schema.attempt_id', 'FA'), ('journal.schema.revision', 'F'),
    ('journal.schema.result', None), ('journal.schema.completed_at', None),
    ('status.mode', 'maintenance'), ('status.normal_ready', False), ('status.locked_converged', False),
    ('status.runtime_state', 'unknown'), ('status.desired_revision', 'F'), ('status.applied_revision', 'old-success'),
    ('status.verified_running_revision_id', 'old-success'), ('status.attempt_id', 'FA'), ('status.active', True),
    ('status.publication.attempt_phase', 'running'), ('status.publication.terminal_outcome', 'failed-stopped'),
    ('status.role_health', []), ('status.role_health.0.ok', False), ('status.role_health.0.name', 'web'),
    ('status.role_processes', {}), ('role_identities.web', []), ('role_identities.web', [['stale', '999']]),
    ('admission.mode', 'closed'), ('admission.consumers', False), ('ready_http_status', 400),
    ('host.generation', 10), ('status.host_generation', 10), ('installation_host.id', 'foreign'),
    ('revisions.F.snapshot', {}), ('attempts.FA.record.terminal.outcome', 'applied-normal'),
    ('history.FA.clearance', None), ('history.FA.schema.attempt_id', 'foreign'),
    ('history.FA.recovery.result', 'failed'), ('history.FA.failure', None), ('history.old-attempt', {}),
    ('audit', b'1001\tsync --frozen --no-build --project /var/lib/docassemble-build/frozen-repair\n'),
    ('sync_logs', ['old-sync.log']),
]


def set_snapshot_field(snapshot, path, value):
    keys = path.split('.')
    for key in keys[:-1]:
        snapshot = snapshot[int(key)] if isinstance(snapshot, list) else snapshot[key]
    snapshot[int(keys[-1]) if isinstance(snapshot, list) else keys[-1]] = value


@pytest.mark.parametrize('path,value', TERMINAL_CORRUPTIONS, ids=[item[0] for item in TERMINAL_CORRUPTIONS])
def test_recovery_repair_proof_rejects_corrupt_runtime_terminal(recovery_repair_proof, bound_repair, path, value):
    # Break aliasing so corruption does not update another plane's evidence too.
    import copy
    before, binding, observation = bound_repair
    observation = json.loads(json.dumps({key: value for key, value in observation.items() if key != 'audit'}))
    observation['audit'] = bound_repair[2]['audit']
    set_snapshot_field(observation, path, copy.deepcopy(value))
    with pytest.raises(AssertionError):
        recovery_repair_proof.assert_repair_terminal(before, binding, observation)


@pytest.mark.parametrize('path,value', [
    ('schema_clearance', None), ('schema_clearance_source_digest', 'wrong'),
    ('alembic_revision', 'foreign'), ('journal.schema.attempt_id', 'foreign'), ('journal.schema.identity', 'wrong'),
    ('journal.schema.result', 'succeeded'), ('journal.clearance', None),
    ('operator_clearance.missing_stale_foreign_clearance_denied', False),
    ('operator_clearance.observed_database_revision', 'stale')])
def test_recovery_repair_proof_requires_prior_clearance(recovery_repair_proof, bound_repair, path, value):
    before, _, _ = bound_repair
    set_snapshot_field(before, path, value)
    with pytest.raises(AssertionError):
        recovery_repair_proof.assert_prior_schema_clearance(before)


@pytest.mark.parametrize('outcome', ['blocked', 'failed-stopped', 'aborted-before-sync', 'materialized-stopped'])
def test_recovery_repair_proof_rejects_non_normal_terminal_immediately(recovery_repair_proof, bound_repair, outcome):
    before, binding, observation = bound_repair
    observation['attempts']['A']['record']['terminal']['outcome'] = outcome
    with pytest.raises(AssertionError, match='non-applied-normal'):
        recovery_repair_proof.repair_terminal_state(before, binding, observation)


@pytest.mark.parametrize('argv', [
    'sync --no-build --project /var/lib/docassemble-build/frozen-one',
    'sync --frozen --project /var/lib/docassemble-build/frozen-one',
    'sync --frozen --no-build --reinstall --project /var/lib/docassemble-build/frozen-one',
    'sync --frozen --no-build --project /wrong/target',
    'lock', 'build', 'pip install sample',
])
def test_recovery_repair_proof_rejects_unfrozen_or_solving_apply_audit(recovery_repair_proof, bound_repair, argv):
    before, binding, observation = bound_repair
    observation['audit'] = binding['preapprove_audit'] + ('1001\t' + argv + '\n').encode()
    with pytest.raises(AssertionError):
        recovery_repair_proof.assert_repair_terminal(before, binding, observation)


@pytest.mark.parametrize('broken', ['root', 'second-sync', 'second-attempt', 'second-log', 'no-sync'])
def test_recovery_repair_proof_rejects_double_or_absent_apply(recovery_repair_proof, bound_repair, broken):
    before, binding, observation = bound_repair
    if broken == 'root':
        observation['audit'] = observation['audit'].replace(b'1001\tsync --frozen --no-build', b'0\tsync --frozen --no-build')
    elif broken == 'second-sync':
        observation['audit'] += b'1001\tsync --frozen --no-build --project /var/lib/docassemble-build/frozen-two\n'
    elif broken == 'second-attempt':
        observation['attempts']['second'] = observation['attempts']['A']
    elif broken == 'second-log':
        observation['sync_logs'].append('second.log')
    else:
        observation['audit'] = binding['preapprove_audit']
    with pytest.raises(AssertionError):
        recovery_repair_proof.assert_repair_terminal(before, binding, observation)


def test_recovery_repair_proof_runtime_syncs_excludes_isolated_registry_builder(recovery_repair_proof):
    audit = (b'1001\tlock --no-config\n1001\tsync --frozen --no-config\n1001\tbuild --wheel .\n'
             b'1001\tsync --frozen --no-build --project=/var/lib/docassemble-build/frozen-one\n')
    assert len(recovery_repair_proof.runtime_syncs(audit)) == 1
    assert recovery_repair_proof.runtime_syncs(b'1001\tsync --frozen --no-config\n') == []
    for invalid in (b'no-uid sync', b'1001\t"broken', b'1001\t'):
        with pytest.raises(AssertionError):
            recovery_repair_proof.runtime_syncs(invalid)


def ready_observation(before, binding, terminal):
    from copy import deepcopy
    observation = deepcopy(before)
    observation['candidates'] = deepcopy(terminal['candidates'])
    observation['candidates']['C']['state'] = 'ready'
    observation['deduplication'] = deepcopy(terminal['deduplication'])
    observation['database_now'] = '2029-01-01T00:00:00+00:00'
    observation['audit'] = binding['preapprove_audit']
    observation['workers'] = [{'candidate_id': 'C', 'fence': 1, 'id': 'worker'}]
    return observation


def test_recovery_repair_proof_keeps_ready_unapproved_without_runtime_mutation(recovery_repair_proof, bound_repair, monkeypatch):
    proof = recovery_repair_proof
    before, binding, terminal = bound_repair
    observation = ready_observation(before, binding, terminal)
    clock = [0]
    monkeypatch.setattr(proof.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(proof.time, 'sleep', lambda duration: clock.__setitem__(0, clock[0] + duration))
    samples = []
    def sample():
        observation['host']['generation'] += 1
        samples.append(clock[0])
        return observation
    result = proof.observe_window(sample, lambda value: proof.assert_preapproval_unchanged(
        before, value, 'C', binding['result_digest']), 120)
    assert samples == [0, 2, 4, 6, 8, 10]
    assert result['host']['generation'] > before['host']['generation']


@pytest.mark.parametrize('path,value', [
    ('environment.desired_revision_id', 'R'), ('environment.active_attempt_id', 'A'),
    ('journal.phase', 'mutation_started'), ('journal.schema.result', 'succeeded'),
    ('applied.revision', 'R'), ('candidates.C.state', 'published'),
    ('publications', [{'candidate_id': 'C'}]),
    ('audit', b'1001\t--version\n1001\tsync --project /var/lib/docassemble-build/frozen-early\n')])
def test_recovery_repair_proof_detects_approval_before_browser_action(recovery_repair_proof, bound_repair, path, value):
    before, binding, terminal = bound_repair
    observation = ready_observation(before, binding, terminal)
    set_snapshot_field(observation, path, value)
    with pytest.raises(AssertionError, match='approval-before-browser-action'):
        recovery_repair_proof.assert_preapproval_unchanged(before, observation, 'C', binding['result_digest'])


@pytest.mark.parametrize('path,value', [('candidates.C.operation', 'upgrade'), ('deduplication', []),
    ('candidates.C.requester_id', '2'), ('candidates.C.parent_id', 'foreign'),
    ('candidates.C.expires_at', '2020-01-01T00:00:00+00:00'), ('candidates.C.result_digest', 'wrong'),
    ('candidates.C.runtime_generation', 4), ('candidates.C.state', 'failed')])
def test_recovery_repair_proof_rejects_broken_prepare_or_ready(recovery_repair_proof, bound_repair, path, value):
    before, binding, terminal = bound_repair
    observation = ready_observation(before, binding, terminal)
    set_snapshot_field(observation, path, value)
    with pytest.raises(AssertionError):
        recovery_repair_proof.assert_preapproval_unchanged(before, observation, 'C', binding['result_digest'])


def test_recovery_repair_proof_waits_for_report_and_readiness_after_local_terminal(recovery_repair_proof, bound_repair):
    import copy
    proof = recovery_repair_proof
    before, binding, terminal = bound_repair
    for phase in ('pending', 'running'):
        observation = copy.deepcopy(terminal)
        record = observation['attempts']['A']['record']
        record.update(phase=phase, terminal=None, fence=0 if phase == 'pending' else 1)
        observation['environment']['active_attempt_id'] = 'A'
        assert proof.repair_terminal_state(before, binding, observation) == 'pending'
    observation = copy.deepcopy(terminal)
    observation['status']['normal_ready'] = False
    assert proof.repair_terminal_state(before, binding, observation) == 'pending'
    assert proof.repair_terminal_state(before, binding, terminal) == 'success'


@pytest.mark.parametrize('code,method,destination,accepted', [
    (302, 'POST', '/recovery', True), (303, 'POST', '/recovery', True),
    (307, 'POST', '/recovery', False), (302, 'GET', '/recovery', False),
    (302, 'POST', '/', False), (302, 'POST', 'http://foreign.example/recovery', False),
    (302, 'POST', '/recovery?token=private', False)])
def test_recovery_repair_proof_redirect_recorder_classification(recovery_repair_proof, code, method, destination, accepted):
    proof = recovery_repair_proof
    recorder = proof.ApprovalRedirectRecorder()
    recorder.approving = True
    recorder.deadline = proof.time.monotonic() + 30
    request = urllib.request.Request('http://127.0.0.1/recovery/publication', data=b'action=approve', method=method)
    request.timeout = 30
    destination = urllib.parse.urljoin(request.full_url, destination)
    if not accepted:
        with pytest.raises(AssertionError, match='approval-http'):
            recorder.redirect_request(request, None, code, '', {}, destination)
    else:
        redirected = recorder.redirect_request(request, None, code, '', {}, destination)
        assert redirected.get_method() == 'GET'
        error = urllib.error.HTTPError(destination, 503, 'not ready', {}, io.BytesIO())
        assert recorder.approve_followup_503(error)
        recorder.approving = False
        assert not recorder.approve_followup_503(error)
    initial = urllib.error.HTTPError(request.full_url, 503, 'not ready', {}, io.BytesIO())
    assert not recorder.approve_followup_503(initial)


def test_recovery_repair_proof_bounds_preapproval_503_without_retrying_post(recovery_repair_proof, monkeypatch):
    proof = recovery_repair_proof
    clock = [0]
    monkeypatch.setattr(proof.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(proof.time, 'sleep', lambda duration: clock.__setitem__(0, clock[0] + duration))
    client = proof.BrowserHTTP()
    requests, snapshots = [], []
    def request(path, deadline, form=None, **kwargs):
        requests.append((path, form))
        raise urllib.error.HTTPError(proof.ORIGIN + path, 503, 'unavailable', {}, io.BytesIO())
    monkeypatch.setattr(client, 'request', request)
    with pytest.raises(AssertionError, match='recovery-unavailable-before-approval'):
        client.get_page('/recovery', 120, lambda *args: snapshots.append(clock[0]))
    assert clock[0] == 30
    assert all(form is None for _, form in requests)
    assert snapshots[0] == 0 and snapshots[-1] == 30


def test_recovery_repair_proof_missing_approve_publication_is_not_redirect_success(recovery_repair_proof, bound_repair):
    before, binding, observation = bound_repair
    observation = ready_observation(before, binding, observation)
    assert recovery_repair_proof.binding_from_publication(before, binding, observation) is None


@pytest.fixture
def repair_driver(monkeypatch):
    directory = PREPARATION.parent
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location('run_core_upgrade_proof', directory / 'run-core-upgrade-proof.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('stdout,code,expected', [
    ('durable-preparation RUNNING pid 1\ndurable-installation RUNNING pid 2', 0, True),
    ('', 0, False), ('supervisor unavailable', 4, False),
    ('durable-preparation RUNNING\ndurable-installation RUNNING', 4, False),
    ('durable-preparation RUNNING\ndurable-preparation RUNNING', 0, False),
    ('durable-preparation STARTING\ndurable-installation RUNNING', 0, False),
    ('durable-preparation\ndurable-installation RUNNING', 0, False),
    ('durable-preparation RUNNING\ndurable-installation RUNNING\nextra RUNNING', 0, False)])
def test_run_core_upgrade_proof_repair_workers_requires_positive_exact_running(repair_driver, stdout, code, expected):
    assert repair_driver.repair_workers_running(stdout, code) is expected


def test_run_core_upgrade_proof_wait_retains_status_without_worker_scheduling(repair_driver, monkeypatch, tmp_path):
    import subprocess
    clock = [0]
    monkeypatch.setattr(repair_driver.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(repair_driver.time, 'sleep', lambda duration: clock.__setitem__(0, clock[0] + duration))
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        stdout = 'durable-preparation STARTING\ndurable-installation RUNNING' if len(calls) == 1 else (
            'durable-preparation RUNNING\ndurable-installation RUNNING')
        return subprocess.CompletedProcess(argv, 0, stdout, 'retained diagnostic')
    repair_driver.wait_for_repair_workers(run, 'fixture', tmp_path)
    assert len(calls) == 2
    assert all(argv[-3:] == ['status', 'durable-preparation', 'durable-installation'] for argv in calls)
    assert json.loads((tmp_path / 'browser-repair-workers.json').read_text())[0]['stderr'] == 'retained diagnostic'


def test_run_core_upgrade_proof_missing_workers_time_out(repair_driver, monkeypatch, tmp_path):
    import subprocess
    clock = [0]
    monkeypatch.setattr(repair_driver.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(repair_driver.time, 'sleep', lambda duration: clock.__setitem__(0, clock[0] + duration))
    with pytest.raises(RuntimeError, match='120s'):
        repair_driver.wait_for_repair_workers(lambda argv, **kwargs: subprocess.CompletedProcess(argv, 4, '', 'unavailable'),
                                             'fixture', tmp_path)
    assert clock[0] == 120
    assert json.loads((tmp_path / 'browser-repair-workers.json').read_text())[-1]['exit'] == 4


@pytest.mark.parametrize('approval_http', ['normal', 'redirect-503', 'initial-503', '403', 'foreign-redirect'])
def test_recovery_repair_proof_never_drives_mutation_and_binds_browser_action(
        recovery_repair_proof, bound_repair, monkeypatch, tmp_path, capsys, approval_http):
    import copy
    import subprocess
    proof = recovery_repair_proof
    before, binding, terminal = bound_repair
    clock = [0]
    monkeypatch.setattr(proof.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(proof.time, 'sleep', lambda duration: clock.__setitem__(0, clock[0] + duration))
    from types import SimpleNamespace
    monkeypatch.setattr(proof.ExecutorPaths, 'from_env', lambda: SimpleNamespace(
        state_dir=tmp_path / 'managed', journal_file=tmp_path / 'managed/lifecycle.json'))
    monkeypatch.setattr(proof.core, 'ROOT', tmp_path)
    def forbid(*args, **kwargs):
        pytest.fail('browser certifier must never invoke a mutation API or subprocess')
    monkeypatch.setattr(subprocess, 'run', forbid)
    monkeypatch.setattr(proof.core, 'publisher_at', forbid)
    monkeypatch.setattr(proof.core.InstallationPoller, 'run_once', forbid)
    monkeypatch.setattr(proof.core.RegistryBuilder, 'build', forbid)
    class Engine:
        def dispose(self):
            pass
    monkeypatch.setattr(proof, 'observation_database', lambda paths: (Engine(), ''))
    events = []
    mode = ['before']
    reads = [0]
    def observe(paths, engine, prefix, deadline, sequence):
        reads[0] += 1
        if mode[0] == 'before':
            observation = copy.deepcopy(before)
        elif mode[0] == 'ready':
            observation = ready_observation(before, binding, terminal)
        else:
            observation = copy.deepcopy(terminal)
        observation.update(sequence=sequence, monotonic=clock[0], utc='fixture-time')
        observation['host']['generation'] = 20 + reads[0]
        observation['host']['observation']['host_generation'] = observation['host']['generation']
        observation['status']['host_generation'] = observation['host']['generation']
        return observation
    monkeypatch.setattr(proof, 'read_repair_observation', observe)
    client = proof.BrowserHTTP()
    class Response(io.BytesIO):
        status = 200
    class Opener:
        def open(self, url, data=None, timeout=None):
            form = urllib.parse.parse_qs(data.decode()) if data is not None else None
            path = urllib.parse.urlsplit(url).path
            events.append(('POST' if form is not None else 'GET', path, form))
            if path == '/user/sign-in':
                return Response(SIGN_IN_HTML.encode() if form is None else b'logged in')
            if path == '/recovery':
                return Response(('Restricted recovery administration <form method="post">'
                    '<input name="csrf_token" value="actual-ready-csrf">'
                    '<input name="candidate_id" value="C"><input name="action" value="approve">'
                    '<button>Approve the prepared repair</button></form>').encode())
            if path == '/health_check':
                assert url.endswith('?ready=1')
                return Response(b'OK')
            assert path == '/recovery/publication'
            if form['action'] == ['repair']:
                mode[0] = 'ready'
                return Response(b'Repair preview prepared')
            assert form == {'csrf_token': ['actual-ready-csrf'], 'action': ['approve'],
                            'candidate_id': ['C'], 'idempotency_key': [proof.APPROVE_KEY]}
            if approval_http in ('initial-503', '403'):
                raise urllib.error.HTTPError(url, 503 if approval_http == 'initial-503' else 403, '', {}, io.BytesIO())
            request = urllib.request.Request(url, data=data)
            request.timeout = timeout
            destination = 'http://foreign.example/recovery' if approval_http == 'foreign-redirect' else proof.ORIGIN + '/recovery'
            client.recorder.redirect_request(request, None, 302, '', {}, destination)
            mode[0] = 'terminal'
            events.append(('GET', '/recovery', None))
            if approval_http == 'redirect-503':
                raise urllib.error.HTTPError(destination, 503, '', {}, io.BytesIO())
            return Response(b'Approval published revision')
    client.opener = Opener()
    monkeypatch.setattr(proof, 'BrowserHTTP', lambda: client)
    if approval_http in ('normal', 'redirect-503'):
        proof.main('browser-repair')
        result = json.loads((tmp_path / 'browser-repair-result.json').read_text())
        assert result['attempt_id'] == 'A' and result['new_desired_revision'] == 'R'
        assert result['runtime_apply_count'] == 1 and result['terminal_identity'] == terminal['publication_reported']
        assert result['roles'] == proof.ROLES
        assert 'runtime-owned frozen apply' in capsys.readouterr().out
        http = json.loads((tmp_path / 'browser-repair-approve-http.json').read_text())
        assert http['followup_503'] is (approval_http == 'redirect-503')
        assert clock[0] >= 20
        approve_index = next(index for index, event in enumerate(events)
                             if event[2] and event[2].get('action') == ['approve'])
        assert sum(event[:2] == ('GET', '/recovery') for event in events[approve_index + 1:]) == 1
    else:
        with pytest.raises(AssertionError):
            proof.main('browser-repair')
        assert not (tmp_path / 'browser-repair-result.json').exists()
        assert (tmp_path / 'browser-repair-failure.json').is_file()
    mutations = [event for event in events if event[2] and 'action' in event[2]]
    assert [event[2]['action'] for event in mutations] == [['repair'], ['approve']]
    assert (tmp_path / 'browser-repair-final.json').is_file()
    assert (tmp_path / 'browser-repair-final-uv.log').is_file()


@pytest.mark.parametrize('timeout', [False, True])
def test_run_core_upgrade_proof_retains_browser_failure_and_final_evidence_before_cleanup(
        repair_driver, monkeypatch, tmp_path, timeout):
    import subprocess
    commands = []
    monkeypatch.setenv('PROOF_EVIDENCE_DIR', str(tmp_path))
    monkeypatch.setenv('PROOF_REPAIR_FLOW', '1')
    monkeypatch.setattr(repair_driver, 'refuse_existing', lambda *args, **kwargs: None)
    monkeypatch.setattr(repair_driver, 'cleanup', lambda *args, **kwargs: commands.append(['cleanup']))
    def run(argv, **kwargs):
        commands.append(argv)
        if argv[-1] == 'browser-repair':
            if timeout:
                raise subprocess.TimeoutExpired(argv, 1800, b'partial browser stdout', b'partial browser stderr')
            return subprocess.CompletedProcess(argv, 1, 'browser failed stdout', 'browser failed stderr')
        stdout = ('durable-preparation RUNNING\ndurable-installation RUNNING' if 'status' in argv else '')
        return subprocess.CompletedProcess(argv, 0, stdout, 'retained stderr')
    monkeypatch.setattr(repair_driver.subprocess, 'run', run)
    with pytest.raises((RuntimeError, subprocess.TimeoutExpired)):
        repair_driver.main()
    assert 'browser' in (tmp_path / 'browser-repair.log').read_text()
    assert json.loads((tmp_path / 'final-repair-workers.json').read_text())['stderr'] == 'retained stderr'
    copies = json.loads((tmp_path / 'browser-repair-evidence-copies.json').read_text())
    assert len(copies) == 5
    assert all(item['exit'] == 0 for item in copies)
    assert commands[-1] == ['cleanup']
    copied = [argv[2] for argv in commands if argv[:2] == ['docker', 'cp']]
    assert any(path.endswith('/recovery/status.json') for path in copied)
    assert any(path.endswith('/durable-installation.log') for path in copied)
    assert any(path.endswith('/docassemble-uv-invocations.log') for path in copied)


@pytest.mark.parametrize('limit', [120, 600, 900, 1740])
def test_recovery_repair_proof_deadline_never_resets(recovery_repair_proof, monkeypatch, limit):
    proof = recovery_repair_proof
    clock = [limit - 1]
    monkeypatch.setattr(proof.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(proof.time, 'sleep', lambda duration: clock.__setitem__(0, clock[0] + duration))
    assert proof.remaining(limit, 10) == 1
    proof.pause(limit)
    assert clock[0] == limit
    with pytest.raises(TimeoutError):
        proof.remaining(limit, 30)


def test_recovery_repair_proof_unavailable_database_still_retains_safe_local_failure(
        recovery_repair_proof, monkeypatch, tmp_path):
    proof = recovery_repair_proof
    clock = [0]
    monkeypatch.setattr(proof.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(proof.time, 'sleep', lambda duration: clock.__setitem__(0, clock[0] + duration))
    monkeypatch.setattr(proof.core, 'ROOT', tmp_path)
    from types import SimpleNamespace
    monkeypatch.setattr(proof.ExecutorPaths, 'from_env', lambda: SimpleNamespace(
        state_dir=tmp_path / 'managed', journal_file=tmp_path / 'managed/lifecycle.json'))
    monkeypatch.setattr(proof, 'observation_database', lambda paths: (SimpleNamespace(dispose=lambda: None), ''))
    reads = []
    def read(*args):
        reads.append(clock[0])
        return {'sequence': len(reads), 'journal': {'phase': 'recovery_succeeded'},
                'audit': b'1001\t--version\n', 'read_errors': [{'plane': 'database', 'class': 'OperationalError'}]}
    monkeypatch.setattr(proof, 'read_repair_observation', read)
    with pytest.raises(AssertionError, match='deadline'):
        proof.main('browser-repair')
    assert clock[0] == 120
    failure = json.loads((tmp_path / 'browser-repair-failure.json').read_text())
    assert failure['last_observation']['journal']['phase'] == 'recovery_succeeded'
    assert failure['last_observation']['read_errors'] == [{'plane': 'database', 'class': 'OperationalError'}]
    assert (tmp_path / 'browser-repair-final.json').is_file()


def test_recovery_repair_proof_steady_window_rejects_stalled_live_worker(recovery_repair_proof, monkeypatch):
    proof = recovery_repair_proof
    clock = [0]
    monkeypatch.setattr(proof.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(proof.time, 'sleep', lambda duration: clock.__setitem__(0, clock[0] + duration))
    with pytest.raises(AssertionError, match='did not advance'):
        proof.observe_window(lambda: {'host': {'generation': 10}}, lambda value: None, 120)
    assert clock[0] == 120


@pytest.mark.parametrize('initial_status,location,followup_status,transition', [
    (302, '/recovery', 503, True), (303, '/recovery', 503, True),
    (503, None, 200, False), (403, None, 200, False),
    (302, '/denied', 503, False)])
def test_recovery_repair_proof_real_http_approve_redirect_503_is_only_a_transition(
        recovery_repair_proof, initial_status, location, followup_status, transition):
    proof = recovery_repair_proof
    events = []
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            length = int(self.headers['Content-Length'])
            form = urllib.parse.parse_qs(self.rfile.read(length).decode())
            events.append(('POST', self.path, form))
            self.send_response(initial_status)
            if location:
                self.send_header('Location', location)
            self.send_header('Content-Length', '0')
            self.end_headers()
        def do_GET(self):
            events.append(('GET', self.path, None))
            self.send_response(followup_status)
            self.send_header('Content-Length', '0')
            self.end_headers()
    server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
    thread.start()
    original = proof.ORIGIN
    proof.ORIGIN = 'http://127.0.0.1:' + str(server.server_port)
    try:
        browser = proof.BrowserHTTP()
        form = {'csrf_token': 'actual-csrf', 'action': 'approve', 'candidate_id': 'C',
                'idempotency_key': proof.APPROVE_KEY}
        if transition:
            assert browser.request('/recovery/publication', proof.time.monotonic() + 30,
                                   form, approve=True, sequence=5) is None
            assert [(method, path) for method, path, _ in events] == [
                ('POST', '/recovery/publication'), ('GET', '/recovery')]
            assert browser.recorder.events[0]['redirect_method'] == 'GET'
            assert browser.recorder.events[1]['status'] == 503
            assert browser.approve_boundary['sequence'] == 5
            assert 'actual-csrf' not in json.dumps(browser.approve_boundary)
        else:
            with pytest.raises((AssertionError, urllib.error.HTTPError)):
                browser.request('/recovery/publication', proof.time.monotonic() + 30, form, approve=True, sequence=5)
        assert sum(event[0] == 'POST' for event in events) == 1
    finally:
        proof.ORIGIN = original
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_recovery_repair_proof_prepare_redirect_503_is_not_approve_transition(recovery_repair_proof, monkeypatch):
    proof = recovery_repair_proof
    browser = proof.BrowserHTTP()
    def open_url(url, data=None, timeout=None):
        request = urllib.request.Request(url, data=data)
        request.timeout = timeout
        browser.recorder.redirect_request(request, None, 302, '', {}, proof.ORIGIN + '/recovery')
        raise urllib.error.HTTPError(proof.ORIGIN + '/recovery', 503, '', {}, io.BytesIO())
    monkeypatch.setattr(browser.opener, 'open', open_url)
    with pytest.raises(urllib.error.HTTPError):
        browser.request('/recovery/publication', proof.time.monotonic() + 30,
                        {'action': 'repair', 'csrf_token': 'real-token', 'idempotency_key': proof.PREPARE_KEY})
    assert browser.approve_boundary is None


def test_recovery_repair_proof_trickle_response_cannot_extend_http_deadline(recovery_repair_proof, monkeypatch):
    proof = recovery_repair_proof
    clock = [0]
    monkeypatch.setattr(proof.time, 'monotonic', lambda: clock[0])
    class Response:
        def read1(self, maximum):
            clock[0] += 2
            return b'x'
    with pytest.raises(TimeoutError):
        proof.read_http_body(Response(), 10)
    assert clock[0] == 10


@pytest.fixture
def repair_main_memory(recovery_repair_proof, bound_repair, monkeypatch, capsys):
    """Actual main/browser/predicates; only storage, clock, reader and transport are fake."""
    from copy import deepcopy
    from types import SimpleNamespace
    proof = recovery_repair_proof
    before, binding, terminal = bound_repair
    files, clock, mode, reads, health_reads, evaluations = {}, [0], ['before'], [], [], []

    class MemoryPath:
        def __init__(self, name=''):
            self.name = name
        def __truediv__(self, name):
            return MemoryPath(self.name + '/' + name)
        def __str__(self):
            return self.name
        def write_bytes(self, value):
            files[self.name] = value
            return len(value)
        def write_text(self, value):
            return self.write_bytes(value.encode())
        def read_text(self):
            return files[self.name].decode()
        def open(self, mode):
            path = self
            class Output(io.StringIO):
                def close(self):
                    path.write_text(self.getvalue())
                    super().close()
            output = Output(files.get(self.name, b'').decode() if mode == 'a' else '')
            output.seek(0, io.SEEK_END)
            return output

    monkeypatch.setattr(proof.core, 'ROOT', MemoryPath())
    monkeypatch.setattr(proof.ExecutorPaths, 'from_env', lambda: SimpleNamespace(
        state_dir=MemoryPath('managed'), journal_file=MemoryPath('managed/lifecycle.json')))
    monkeypatch.setattr(proof.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(proof.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    engine = SimpleNamespace(dispose=lambda: None)
    monkeypatch.setattr(proof, 'observation_database', lambda paths: (engine, ''))
    faults, generations = {}, {}
    def observe(paths, engine, prefix, deadline, sequence):
        value = (deepcopy(before) if mode[0] == 'before' else
                 ready_observation(before, binding, terminal) if mode[0] == 'ready' else deepcopy(terminal))
        generation = (generations[mode[0]](clock[0], sequence) if mode[0] in generations else
                      20 + sequence)
        value.update(sequence=sequence, monotonic=clock[0], utc='fixture-time')
        value['host'].update(generation=generation, observation={'host_generation': generation})
        value['status']['host_generation'] = generation
        if mode[0] == 'terminal' and clock[0] > 10:
            fault = faults.get(clock[0], faults.get('persistent'))
            if fault:
                fault(value)
        reads.append((clock[0], deepcopy(value)))
        return value
    monkeypatch.setattr(proof, 'read_repair_observation', observe)
    predicate = proof.repair_terminal_state
    def checked(*args):
        evaluations.append(clock[0])
        return predicate(*args)
    monkeypatch.setattr(proof, 'repair_terminal_state', checked)
    client = proof.BrowserHTTP()
    class Response(io.BytesIO):
        status = 200
    class Opener:
        def open(self, url, data=None, timeout=None):
            path = urllib.parse.urlsplit(url).path
            form = urllib.parse.parse_qs(data.decode()) if data is not None else None
            if path == '/user/sign-in':
                return Response(SIGN_IN_HTML.encode() if form is None else b'logged in')
            if path == '/recovery':
                return Response(('Restricted recovery administration <form>'
                    '<input name="csrf_token" value="ready-csrf"><input name="candidate_id" value="C">'
                    '<input name="action" value="approve"><button>Approve the prepared repair</button></form>').encode())
            if path == '/health_check':
                code = (400 if reads[-1][1]['status'].get('normal_ready') is False else
                        reads[-1][1].get('ready_http_status', 200))
                health_reads.append((clock[0], code))
                if code != 200:
                    raise urllib.error.HTTPError(url, code, 'unhealthy', {}, io.BytesIO())
                return Response(b'OK')
            assert path == '/recovery/publication'
            if form['action'] == ['repair']:
                mode[0] = 'ready'
                return Response(b'Repair preview prepared')
            assert form['action'] == ['approve']
            request = urllib.request.Request(url, data=data)
            request.timeout = timeout
            client.recorder.redirect_request(request, None, 302, '', {}, proof.ORIGIN + '/recovery')
            mode[0] = 'terminal'
            return Response(b'Approval published revision')
    client.opener = Opener()
    monkeypatch.setattr(proof, 'BrowserHTTP', lambda: client)
    return SimpleNamespace(proof=proof, files=files, clock=clock, faults=faults, generations=generations,
                           reads=reads, health_reads=health_reads, evaluations=evaluations, capsys=capsys)


def mismatched_generation(value):
    value['status']['host_generation'] -= 1


@pytest.mark.parametrize('read_error', [False, True])
def test_recovery_repair_proof_main_rejects_unhealthy_discarded_steady_samples(repair_main_memory, read_error):
    run = repair_main_memory
    def unhealthy(value):
        mismatched_generation(value)
        value['status']['normal_ready'] = False
        value['status']['role_health'][0]['ok'] = False
        if read_error:
            value['read_errors'] = [{'plane': 'database', 'class': 'OperationalError'}]
    run.faults.update(dict.fromkeys((12, 14, 16, 18), unhealthy))
    with pytest.raises(AssertionError, match='runtime-steady'):
        run.proof.main('browser-repair')
    assert '/browser-repair-result.json' not in run.files
    assert 'PASS' not in run.capsys.readouterr().out
    assert run.clock[0] == 12
    # A read error must not suppress the available negative status/role evidence.
    assert (12, 400) in run.health_reads


@pytest.mark.parametrize('fault_kind', ['mismatch', 'read-error'])
def test_recovery_repair_proof_main_retries_from_fresh_successful_window(repair_main_memory, fault_kind):
    run = repair_main_memory
    def unvalidatable(value):
        if fault_kind == 'mismatch':
            mismatched_generation(value)
        else:
            value['read_errors'] = [{'plane': 'database', 'class': 'OperationalError'}]
    run.faults.update(dict.fromkeys((12, 14, 16, 18), unvalidatable))
    run.proof.main('browser-repair')
    assert '/browser-repair-result.json' in run.files
    assert 'PASS' in run.capsys.readouterr().out
    assert run.clock[0] == 30  # First coherent success at 20 starts a NEW ten-second window.
    assert [time for time in run.evaluations if time >= 12] == [12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 30]


def test_recovery_repair_proof_main_persistent_mismatch_exhausts_unchanged_deadline(repair_main_memory):
    run = repair_main_memory
    run.faults['persistent'] = mismatched_generation
    with pytest.raises(AssertionError, match='deadline'):
        run.proof.main('browser-repair')
    assert run.clock[0] == 910  # 900-second terminal/steady cap anchored at t=10.
    assert '/browser-repair-result.json' not in run.files


@pytest.mark.parametrize('read_error', [False, True])
@pytest.mark.parametrize('path,value', [
    ('status.role_health', []), ('status.role_health.0.ok', False),
    ('status.role_health.0.name', 'duplicate'), ('status.desired_revision', 'foreign'),
    ('attempts.A.record.phase', 'failed'), ('attempts.A.record.terminal.outcome', 'failed-stopped'),
    ('sync_logs', ['extra-sync.log']), ('audit', b'changed audit'),
    ('ready_http_status', 400),
])
def test_recovery_repair_proof_main_negative_evidence_precedes_steady_retry(
        repair_main_memory, read_error, path, value):
    run = repair_main_memory
    def negative(observation):
        mismatched_generation(observation)
        set_snapshot_field(observation, path, value)
        if read_error:
            observation['read_errors'] = [{'plane': 'database', 'class': 'OperationalError'}]
    run.faults[12] = negative
    with pytest.raises(AssertionError):
        run.proof.main('browser-repair')
    assert run.clock[0] == 12
    assert '/browser-repair-result.json' not in run.files
    assert 'PASS' not in run.capsys.readouterr().out


def test_recovery_repair_proof_main_partial_database_read_restarts_window(repair_main_memory):
    run = repair_main_memory
    def missing_database(observation):
        for key in ('host', 'environment', 'candidates', 'attempts', 'revisions', 'publications'):
            del observation[key]
        observation['read_errors'] = [{'plane': 'database', 'class': 'OperationalError'}]
    run.faults.update(dict.fromkeys((12, 14, 16, 18), missing_database))
    run.proof.main('browser-repair')
    assert run.clock[0] == 30
    assert '/browser-repair-result.json' in run.files
    assert [time for time in run.evaluations if time >= 12] == [12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 30]


@pytest.mark.parametrize('read_error', [False, True])
def test_recovery_repair_proof_main_final_recheck_cannot_skip_window_reset(repair_main_memory, read_error):
    run = repair_main_memory
    checks = [0]
    def final_only(value):
        checks[0] += 1
        if checks[0] == 2:
            if read_error:
                value['read_errors'] = [{'plane': 'database', 'class': 'OperationalError'}]
            else:
                mismatched_generation(value)
    run.faults[20] = final_only
    run.proof.main('browser-repair')
    assert run.clock[0] == 32
    assert '/browser-repair-result.json' in run.files
    assert run.evaluations[-7:] == [22, 24, 26, 28, 30, 32, 32]


@pytest.mark.parametrize('read_error', [False, True])
@pytest.mark.parametrize('negative', ['missing-health', 'extra-attempt'])
def test_recovery_repair_proof_main_missing_health_or_extra_attempt_is_fatal(repair_main_memory, read_error, negative):
    run = repair_main_memory
    def corrupt(value):
        mismatched_generation(value)
        if negative == 'missing-health':
            del value['status']['role_health']
        else:
            value['attempts']['extra-attempt'] = value['attempts']['A']
        if read_error:
            value['read_errors'] = [{'plane': 'database', 'class': 'OperationalError'}]
    run.faults[12] = corrupt
    with pytest.raises(AssertionError):
        run.proof.main('browser-repair')
    assert run.clock[0] == 12
    assert '/browser-repair-result.json' not in run.files


def test_recovery_repair_proof_main_waits_for_delayed_steady_generation(repair_main_memory):
    run = repair_main_memory
    # The steady window begins at t=10: ten healthy seconds without a publication
    # are incomplete liveness evidence, not failure; the next turn publishes at +14s.
    run.generations['terminal'] = lambda time, sequence: 100 if time < 24 else 101
    run.proof.main('browser-repair')
    assert run.clock[0] == 24
    assert run.evaluations[-9:] == [10, 12, 14, 16, 18, 20, 22, 24, 24]
    assert all(value['host']['generation'] == 100 for time, value in run.reads
               if 10 <= time < 24 and value['status']['mode'] == 'normal')
    result = json.loads(run.files['/browser-repair-result.json'])
    steady = json.loads(run.files['/browser-repair-steady.json'])
    assert steady['host']['generation'] == 101
    assert result['terminal_identity']['attempt_id'] == 'A'
    assert result['runtime_apply_count'] == 1
    assert 'PASS' in run.capsys.readouterr().out


def test_recovery_repair_proof_main_stalled_steady_generation_exhausts_deadline(repair_main_memory):
    run = repair_main_memory
    run.generations['terminal'] = lambda time, sequence: 100
    with pytest.raises(AssertionError, match='host generation did not advance during observation window'):
        run.proof.main('browser-repair')
    assert run.clock[0] == 910  # Original 900-second deadline, anchored at t=10.
    failure = json.loads(run.files['/browser-repair-failure.json'])
    assert failure['stage'] == 'runtime-terminal' and failure['limit'] == failure['elapsed'] == 900
    assert failure['class'] == 'AssertionError'
    assert failure['reason'] == 'durable installation host generation did not advance during observation window'
    assert '/browser-repair-result.json' not in run.files
    assert 'PASS' not in run.capsys.readouterr().out


@pytest.mark.parametrize('path,value', [
    ('status.normal_ready', False), ('status.role_health.0.ok', False),
    ('admission.consumers', False), ('ready_http_status', 400),
    ('attempts.A.record.terminal.identity.attempt_id', 'foreign'),
    ('audit', b'changed audit'), ('sync_logs', ['extra-sync.log']),
])
def test_recovery_repair_proof_main_negative_evidence_fails_during_generation_wait(
        repair_main_memory, path, value):
    run = repair_main_memory
    run.generations['terminal'] = lambda time, sequence: 100
    run.faults[22] = lambda observation: set_snapshot_field(observation, path, value)
    with pytest.raises(AssertionError):
        run.proof.main('browser-repair')
    assert run.clock[0] == 22  # Still immediate, even after the minimum interval elapsed.
    failure = json.loads(run.files['/browser-repair-failure.json'])
    assert 'did not advance' not in failure['reason'] and 'deadline' not in failure['reason']
    assert '/browser-repair-result.json' not in run.files
    assert 'PASS' not in run.capsys.readouterr().out


@pytest.mark.parametrize('fault_kind', ['mismatch', 'read-error'])
@pytest.mark.parametrize('post_reset_advances', [False, True])
def test_recovery_repair_proof_main_generation_before_reset_cannot_certify_new_window(
        repair_main_memory, fault_kind, post_reset_advances):
    run = repair_main_memory
    def generation(time, sequence):
        if time < 12:
            return 100
        return 102 if post_reset_advances and time >= 32 else 101
    run.generations['terminal'] = generation
    def unvalidatable(value):
        if fault_kind == 'mismatch':
            mismatched_generation(value)
        else:
            value['read_errors'] = [{'plane': 'database', 'class': 'OperationalError'}]
    run.faults[18] = unvalidatable  # Advancement at t=12 belongs only to the discarded interval.
    if post_reset_advances:
        run.proof.main('browser-repair')
        assert run.clock[0] == 32  # Fresh success starts at 20; cannot return at 30 without new progress.
        assert run.evaluations[-8:] == [20, 22, 24, 26, 28, 30, 32, 32]
        assert '/browser-repair-result.json' in run.files
        assert 'PASS' in run.capsys.readouterr().out
    else:
        with pytest.raises(AssertionError, match='host generation did not advance during observation window'):
            run.proof.main('browser-repair')
        assert run.clock[0] == 910
        assert '/browser-repair-result.json' not in run.files
        assert 'PASS' not in run.capsys.readouterr().out


@pytest.mark.parametrize('advances', [False, True])
def test_recovery_repair_proof_main_preapproval_still_requires_generation(repair_main_memory, advances):
    run = repair_main_memory
    run.generations['ready'] = lambda time, sequence: 100 if not advances or time < 14 else 101
    run.generations['terminal'] = lambda time, sequence: 102 + sequence
    if advances:
        run.proof.main('browser-repair')
        approve = json.loads(run.files['/browser-repair-approve-http.json'])
        assert approve['boundary']['monotonic'] == 14
        assert run.clock[0] == 24
        assert '/browser-repair-result.json' in run.files
    else:
        with pytest.raises(AssertionError, match='host generation did not advance during observation window'):
            run.proof.main('browser-repair')
        assert run.clock[0] == 120
        failure = json.loads(run.files['/browser-repair-failure.json'])
        assert failure['stage'] == 'ready-review' and failure['limit'] == failure['elapsed'] == 120
        assert '/browser-repair-approve-http.json' not in run.files
        assert '/browser-repair-result.json' not in run.files
