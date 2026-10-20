"""Real administrator repair, certified from runtime-owned durable evidence.

This observer never claims preparation/installation work or changes admission.
Only the two real browser form submissions authorize the repair.
"""
import hashlib
import http.cookiejar
from html import unescape
import json
from pathlib import Path
import re
import shlex
import sys
import time
from datetime import datetime, timezone
import urllib.error
import urllib.parse
import urllib.request

from sqlalchemy import create_engine, select, text, func, event
from sqlalchemy.pool import NullPool

import core_upgrade_image_proof as core
from docassemble.base.managed_records import preparation_tables
from docassemble.base.managed_preparation import digest, GENERATIONS
from docassemble_executor.durable_work import configuration
from docassemble_executor.installation import load_schema_clearance
from docassemble_executor.journal import Journal
from docassemble_executor.paths import ExecutorPaths
from docassemble_executor.roles import process_identities, RUNTIME_ROLES, INFRASTRUCTURE_ROLES

ROLES = sorted(RUNTIME_ROLES + INFRASTRUCTURE_ROLES)
AUDIT = Path('/var/log/docassemble-uv-invocations.log')
PREPARE_KEY = 'final-browser-repair'
APPROVE_KEY = 'final-browser-repair-approve'
ORIGIN = 'http://127.0.0.1'


def csrf(html):
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    if token is None:
        raise ValueError('Browser form CSRF is missing or empty')
    return unescape(token.group(1))


def sign_in_form(html):
    rendered_next = re.search(r'name="next"[^>]*value="([^"]+)"', html)
    if rendered_next is None:
        raise ValueError('Sign-in form next is missing or empty')
    next_value = unescape(rendered_next.group(1))
    if not next_value:
        raise ValueError('Sign-in form next is missing or empty')
    destination = urllib.parse.urlsplit(urllib.parse.urljoin(
        'http://127.0.0.1/user/sign-in', next_value))
    port = destination.port if destination.port is not None else 80
    if ((destination.scheme, destination.hostname, port)
            != ('http', '127.0.0.1', 80) or destination.path != '/recovery'):
        raise ValueError('Sign-in form next must resolve to same-origin /recovery')
    return {'csrf_token': csrf(html), 'email': 'admin@example.com',
            'password': 'FixtureAdmin101!', 'submit': 'Sign in', 'next': next_value}



class PendingEvidence(AssertionError):
    """A valid bound apply has not yet completed its cross-plane handoff."""


def require(value, message):
    if not value:
        raise AssertionError(message)


def completed(value, message):
    if not value:
        raise PendingEvidence(message)


def audit_entries(audit_suffix):
    if isinstance(audit_suffix, bytes):
        audit_suffix = audit_suffix.decode()
    entries = []
    for line in audit_suffix.splitlines():
        uid, separator, command = line.partition('\t')
        require(separator and uid.isdecimal(), 'uv-audit malformed UID/argv evidence')
        try:
            argv = shlex.split(command)
        except ValueError:
            raise AssertionError('uv-audit malformed argv evidence') from None
        require(bool(argv), 'uv-audit empty argv evidence')
        entries.append({'uid': int(uid), 'argv': argv})
    return entries


def runtime_syncs(audit_suffix):
    """RegistryBuilder isolated cwd/venv calls have no --project runtime target."""
    syncs = []
    for entry in audit_entries(audit_suffix):
        argv = entry['argv']
        projects = [argv[index + 1] for index, arg in enumerate(argv[:-1]) if arg == '--project']
        projects += [arg.split('=', 1)[1] for arg in argv if arg.startswith('--project=')]
        if argv[0] == 'sync' and any(re.fullmatch(r'/var/lib/docassemble-build/frozen-[^/]+',
                                                project) for project in projects):
            syncs.append(entry)
    return syncs


def audit_suffix(prefix, current):
    require(current.startswith(prefix), 'uv-audit truncated/replaced baseline prefix')
    return current[len(prefix):]


def assert_apply_audit(before, binding, observation, *, terminal=False):
    audit = observation['audit']
    audit_suffix(before['audit'], audit)
    preapprove = binding['preapprove_audit']
    require(not runtime_syncs(audit_suffix(before['audit'], preapprove)),
            'approval-before-browser-action: runtime sync before explicit approve')
    suffix = audit_suffix(preapprove, audit)
    entries = audit_entries(suffix)
    require(not any(entry['argv'][0] in ('lock', 'build', 'pip') for entry in entries),
            'uv-audit postapproval lock/build/pip operation')
    syncs = runtime_syncs(suffix)
    # No other sync may be mistaken for the approved frozen runtime apply.
    require(len(syncs) <= 1 and sum(entry['argv'][0] == 'sync' for entry in entries) == len(syncs),
            'double-apply or unbound runtime sync')
    for entry in syncs:
        argv = entry['argv']
        require(entry['uid'] != 0 and '--frozen' in argv and '--no-build' in argv and
                not any(arg.startswith('--reinstall') for arg in argv),
                'uv-audit apply must be non-root frozen/no-build without reinstall')
    new_logs = set(observation['sync_logs']) - set(before['sync_logs'])
    require(len(new_logs) <= 1, 'double-apply: extra runtime uv-sync log')
    if terminal:
        completed(len(syncs) == len(new_logs) == 1, 'runtime apply/audit log not yet observed')
    return len(syncs)


def assert_prior_schema_clearance(before):
    journal = before['journal']
    schema, clearance = journal['schema'], journal['clearance']
    operator, loaded = before['operator_clearance'], before['schema_clearance']
    attempt_id = journal['operation']['id']
    desired = before['environment']['desired_revision_id']
    require(isinstance(schema, dict) and schema['result'] != 'succeeded' and
            schema['attempt_id'] == attempt_id and schema['revision'] == desired,
            'prior-schema-clearance: missing/foreign uncertain schema')
    require(isinstance(clearance, dict) and isinstance(loaded, dict),
            'prior-schema-clearance: explicit restricted operator clearance missing')
    require(clearance == loaded and clearance['attempt_id'] == attempt_id and
            clearance['schema_identity'] == schema['identity'] and
            clearance['source_digest'] == before['schema_clearance_source_digest'],
            'prior-schema-clearance: stale/foreign schema or source digest')
    require(operator['missing_stale_foreign_clearance_denied'] is True and
            operator['observed_database_revision'] == clearance['observed_schema'] == before['alembic_revision'] and
            operator['journal']['schema'] == schema and operator['journal']['clearance'] == clearance,
            'prior-schema-clearance: earlier negatives/observed database revision disagree')
    return {'attempt_id': attempt_id, 'revision_id': desired, 'schema_identity': schema['identity'],
            'observed_schema': clearance['observed_schema'], 'source_digest': clearance['source_digest'],
            'observation_sequence': before['sequence']}


def assert_baseline(before):
    require(not before['read_errors'], 'browser-precondition: required baseline evidence unavailable')
    env, journal = before['environment'], before['journal']
    desired = env['desired_revision_id']
    operation = journal['operation']
    require(operation and operation['revision'] == desired and operation['kind'] == 'published-apply',
            'browser-precondition: local failed desired/attempt binding missing')
    attempt = before['attempts'][operation['id']]
    receipt = attempt['record']['terminal']
    require(attempt['revision_id'] == desired and attempt['record']['phase'] == 'failed' and
            receipt == journal['attempt_result'] and receipt['outcome'] == 'failed-stopped' and
            receipt['child_safe'] is True and receipt['mutation_started'] is True and
            receipt['normal_verified'] is False and receipt['failure'] is not None and
            operation['binding'] == receipt['identity'] and journal['phase'] == 'recovery_succeeded',
            'browser-precondition: expected bound failed-stopped terminal')
    recovery = receipt['recovery']
    require(recovery['id'] == operation['id'] + ':core:1' and recovery['attempt'] == 1 and
            recovery['result'] == 'succeeded' and bool(recovery['inventory']) and
            journal['recovery']['result'] == 'succeeded' and bool(journal['recovery']['finished_at']),
            'browser-precondition: incomplete child-safe prior-core recovery')
    runtime = env['snapshot']['runtime']
    require(env['active_attempt_id'] is None and runtime['kind'] == 'core-only' and
            runtime['attempt_id'] == operation['id'] and runtime['recovery_id'] == recovery['id'] and
            runtime['inventory'] == recovery['inventory'] and runtime['core_baseline'] == recovery['core_baseline'],
            'browser-precondition: DB runtime is not bound core-only recovery')
    require(all(candidate['state'] not in ('queued', 'preparing', 'ready')
                for candidate in before['candidates'].values()) and
            all(row['record']['phase'] not in ('pending', 'running') for row in before['attempts'].values()),
            'browser-precondition: pending automatic or unrelated candidate/installation work')
    require(before['status']['mode'] == 'recovery' and before['status']['recovery_available'] is True and
            before['status']['normal_ready'] is False and before['admission']['mode'] == 'open' and
            before['admission'].get('consumers') is not True and bool(before['web_processes']['recovery']) and
            before['web_processes']['recovery'].get('state') not in ('Z', 'T', 't'),
            'browser-precondition: live restricted recovery unavailable')
    require(not any(row['key'] in (PREPARE_KEY, APPROVE_KEY)
                    for row in before['deduplication'] + before['publications']),
            'browser-precondition: browser idempotency keys already used')
    assert_prior_schema_clearance(before)


def assert_failed_history(before, observation, *, archived=False):
    desired = before['environment']['desired_revision_id']
    failed_attempt = before['journal']['operation']['id']
    require(observation['revisions'].get(desired) == before['revisions'][desired],
            'failed-history-erased: original immutable failed revision changed/missing')
    require(observation['attempts'].get(failed_attempt) == before['attempts'][failed_attempt],
            'failed-history-erased: failed attempt/terminal changed/missing')
    require(set(before['revisions']).issubset(observation['revisions']) and
            set(before['attempts']).issubset(observation['attempts']),
            'failed-history-erased: prior revision/attempt disappeared')
    for identity, history in before['history'].items():
        require(observation['history'].get(identity) == history,
                'failed-history-erased: pre-existing attempt history changed/missing')
    if archived:
        completed(failed_attempt in observation['history'], 'failed attempt rollover archive not yet observed')
        require(observation['history'][failed_attempt] == before['journal'],
                'failed-history-erased: failed desired/recovery/schema/clearance archive changed')


def assert_preapproval_unchanged(before, observation, candidate_id=None, ready_digest=None):
    require(not observation['read_errors'], 'preapproval evidence unavailable')
    env = observation['environment']
    require(all(env[key] == before['environment'][key] for key in
                ('desired_revision_id', 'active_attempt_id', *GENERATIONS)) and
            env['snapshot'] == before['environment']['snapshot'] and
            observation['revisions'] == before['revisions'] and observation['attempts'] == before['attempts'] and
            observation['publications'] == before['publications'] and
            observation['journal'] == before['journal'] and observation['applied'] == before['applied'] and
            observation['desired'] == before['desired'] and
            observation['schema_clearance'] == before['schema_clearance'] and
            observation['history'] == before['history'],
            'approval-before-browser-action: publication/desired/attempt/journal/schema/applied changed')
    require(not runtime_syncs(audit_suffix(before['audit'], observation['audit'])) and
            observation['sync_logs'] == before['sync_logs'],
            'approval-before-browser-action: runtime sync before explicit approve')
    require(observation['contributions'] == before['contributions'],
            'approval-before-browser-action: committed contributions changed')
    if candidate_id:
        require(all(observation['candidates'].get(identity) == row for identity, row in before['candidates'].items()),
                'approval-before-browser-action: prior candidate history changed')
        require(set(observation['candidates']) - set(before['candidates']) == {candidate_id},
                'repair-prepare: no unique new repair candidate')
        candidate = observation['candidates'][candidate_id]
        require(candidate['state'] != 'published', 'approval-before-browser-action: candidate published')
        require(candidate['state'] in ('queued', 'preparing', 'ready'), 'repair-ready: failed/cancelled/expired candidate')
        require(candidate['operation'] == 'repair' and candidate['requester_id'] == '1' and
                candidate['parent_id'] == before['environment']['desired_revision_id'],
                'repair-prepare: wrong operation/requester/parent')
        require(all(candidate[key] == env[key] == candidate['inputs']['generations'][key] for key in GENERATIONS) and
                'config_save_unresolved' not in env['snapshot'], 'repair-ready: stale candidate generations')
        inputs = candidate['inputs']
        require(inputs['change_operation'] == 'repair' and inputs['parent_id'] == candidate['parent_id'] and
                inputs['baseline'] == before['environment']['snapshot'] and
                inputs['contributions'] == inputs['committed_contributions'] == before['contributions'],
                'repair-prepare: changed desired intent/committed contributions')
        expected_digest = digest({'operation': 'repair', 'declaration': None, 'restart': True})
        dedup = [row for row in observation['deduplication']
                 if row['actor_id'] == '1' and row['operation'] == 'repair' and row['key'] == PREPARE_KEY]
        require(dedup == [{'actor_id': '1', 'operation': 'repair', 'key': PREPARE_KEY,
                          'request_digest': expected_digest, 'candidate_id': candidate_id}] and
                candidate['request_digest'] == expected_digest, 'repair-prepare: missing/wrong repair deduplication')
        if candidate['state'] == 'ready':
            require(candidate['expires_at'] > observation['database_now'], 'repair-ready: expired candidate')
            require(candidate['result'] and candidate['result_digest'] == digest({
                'inputs': inputs, 'result': candidate['result']}), 'repair-ready: invalid result digest')
        if ready_digest is not None:
            require(candidate['state'] == 'ready' and candidate['result_digest'] == ready_digest,
                    'repair-ready: ready candidate state/digest changed')


def assert_publication_binding(before, binding, observation):
    c, r, a = (binding[key] for key in ('candidate_id', 'revision_id', 'attempt_id'))
    require(r != before['environment']['desired_revision_id'] and
            r not in before['revisions'] and a not in before['attempts'], 'repair-binding: old success is not repair')
    require(set(observation['revisions']) - set(before['revisions']) == {r} and
            set(observation['attempts']) - set(before['attempts']) == {a},
            'double-apply: expected exactly one new pinned revision/attempt')
    require(set(observation['candidates']) - set(before['candidates']) == {c},
            'repair-binding: wrong/extra candidate')
    new = [row for row in observation['publications'] if row not in before['publications']]
    expected = {'actor_id': '1', 'key': APPROVE_KEY, 'candidate_id': c, 'revision_id': r,
                'attempt_id': a, 'request_digest': digest({'candidate_id': c, 'result_digest': binding['result_digest']})}
    require(new == [expected] and all(row in observation['publications'] for row in before['publications']),
            'approval-not-published: exact actor/key/candidate/digest publication missing/wrong')
    require(all(observation['candidates'].get(identity) == row for identity, row in before['candidates'].items()),
            'failed-history-erased: original candidate changed/missing')
    candidate = observation['candidates'][c]
    require(candidate['state'] == 'published' and candidate['operation'] == 'repair' and
            candidate['requester_id'] == '1' and candidate['parent_id'] == before['environment']['desired_revision_id'] and
            candidate['result_digest'] == binding['result_digest'] and candidate['inputs'] == binding['inputs'] and
            candidate['result'] == binding['result'] and
            all(candidate[key] == before['environment'][key] == binding['inputs']['generations'][key] for key in GENERATIONS) and digest({'inputs': candidate['inputs'],
                'result': candidate['result']}) == binding['result_digest'], 'repair-binding: published candidate changed')
    revision = observation['revisions'][r]
    publication = revision['snapshot']['publication']
    require(revision['parent_id'] == before['environment']['desired_revision_id'] and
            publication['candidate_id'] == c and publication['approver_id'] == '1' and
            publication['result_digest'] == binding['result_digest'] and publication['inputs'] == binding['inputs'] and
            publication['result'] == binding['result'] and revision['snapshot']['target'] == binding['target'] and
            publication['result']['bundle']['digest'] == binding['bundle_digest'], 'repair-binding: wrong revision publication')
    env = observation['environment']
    require(env['desired_revision_id'] == r and
            env['publication_generation'] == before['environment']['publication_generation'] + 1 and
            env['active_attempt_id'] in (None, a), 'repair-binding: wrong desired/active/publication generation')
    attempt = observation['attempts'][a]
    require(attempt['revision_id'] == r and attempt['record']['reason'] == 'approval' and
            attempt['record']['actor_id'] == '1', 'repair-binding: wrong attempt/actor/reason')
    return attempt


def assert_repair_terminal(before, binding, observation):
    """Conjunctive pinned publication, single apply, schema/history and live readiness."""
    require(not observation['read_errors'], 'runtime-terminal: required evidence unavailable')
    attempt = assert_publication_binding(before, binding, observation)
    assert_failed_history(before, observation, archived=True)
    assert_prior_schema_clearance(before)
    assert_apply_audit(before, binding, observation, terminal=True)
    record = attempt['record']
    completed(record['phase'] == 'succeeded' and observation['environment']['active_attempt_id'] is None,
              'runtime-terminal: terminal report not yet complete')
    receipt = record.get('terminal')
    completed(receipt is not None, 'runtime-terminal: receipt not reported')
    require(record['fence'] == 1 and receipt['outcome'] == 'applied-normal' and
            receipt['mutation_started'] is True and receipt['child_safe'] is True and
            receipt['normal_verified'] is True and receipt['failure'] is None,
            'runtime-terminal: non-applied-normal terminal or wrong fence')
    identity = receipt['identity']
    require(set(identity) == {'attempt_id', 'revision_id', 'fence', 'host_id', 'runtime_generation', 'bundle_digest', 'target'} and
            identity['attempt_id'] == binding['attempt_id'] and identity['revision_id'] == binding['revision_id'] and
            identity['fence'] == 1 and identity['host_id'] == observation['installation_host']['id'] == before['installation_host']['id'] and
            isinstance(identity['runtime_generation'], int) and identity['runtime_generation'] > before['environment']['runtime_generation'] and
            identity['bundle_digest'] == binding['bundle_digest'] and identity['target'] == binding['target'] and
            record['claim'] == identity, 'repair-binding: wrong attempt/revision/fence/host/generation/digest/target')
    journal = observation['journal']
    completed(journal['operation'] and journal['operation']['id'] == binding['attempt_id'],
              'runtime-terminal: local rollover not yet observed')
    require(journal['operation']['kind'] == 'published-apply' and
            journal['operation']['revision'] == binding['revision_id'] and journal['operation']['binding'] == identity and
            journal['attempt_result'] == receipt, 'repair-binding: local operation/receipt identity mismatch')
    reported = observation['publication_reported']
    if reported and reported.get('attempt_id') == binding['attempt_id']:
        require(reported == identity, 'repair-binding: publication-reported full identity mismatch')
    require(observation['applied']['revision'] in (before['applied']['revision'], binding['revision_id']) and
            observation['desired']['revision'] in (before['desired']['revision'], binding['revision_id']),
            'repair-binding: foreign local desired/applied revision')
    completed(journal['phase'] == 'verified' and journal['verified_revision'] == binding['revision_id'] and
              observation['applied']['revision'] == observation['desired']['revision'] == binding['revision_id'] and
              observation['publication_reported'] == identity, 'runtime-terminal: local verified/applied/report handoff incomplete')
    inventory = binding['result']['bundle']['inventory']
    require(bool(inventory) and receipt['inventory'] == {name: value['version'] for name, value in inventory.items()},
            'runtime-terminal: receipt inventory differs from approved bundle')
    runtime = observation['environment']['snapshot']['runtime']
    require(runtime.get('kind') == 'normal' and runtime.get('revision_id') == binding['revision_id'] and
            runtime.get('attempt_id') == binding['attempt_id'], 'repair-binding: reported DB runtime is not normal/bound')
    require(runtime['inventory'] == inventory, 'runtime-terminal: DB runtime inventory differs from publication')
    require(receipt['readiness'] == {'schema_verified': True, 'roles': ROLES},
            'runtime-terminal: exact 12-role/schema receipt readiness required')
    schema = journal['schema']
    require(schema and schema['attempt_id'] == binding['attempt_id'] and schema['revision'] == binding['revision_id'] and
            schema['result'] == 'succeeded' and bool(schema['completed_at']), 'runtime-terminal: new bound schema success missing')
    status = observation['status']
    completed(status.get('mode') == 'normal' and status.get('normal_ready') is True and
              status.get('locked_converged') is True and status.get('runtime_state') == 'locked',
              'runtime-terminal: independent locked normal readiness not yet observed')
    require(status['desired_revision'] == status['applied_revision'] == status['verified_running_revision_id'] == binding['revision_id'] and
            status['active'] is False and status['attempt_id'] == binding['attempt_id'],
            'repair-binding: stale independent desired/applied/verified/attempt identity')
    published = status['publication']
    require(published and published['desired_revision_id'] == binding['revision_id'] and
            published['attempt_id'] == binding['attempt_id'] and published['active'] is False and
            published['attempt_phase'] == 'succeeded' and published['terminal_outcome'] == 'applied-normal',
            'runtime-terminal: independent published terminal mismatch')
    completed(observation['admission']['mode'] == 'open' and observation['admission'].get('consumers') is True and
              observation['ready_http_status'] == 200, 'runtime-terminal: consumer admission/real ready HTTP not yet ready')
    health = status['role_health']
    require(len(health) == len(ROLES) and sorted(item['name'] for item in health) == ROLES and
            all(item['ok'] is True for item in health), 'runtime-terminal: exact live all-role health required')
    processes = status['role_processes']
    require(sorted(processes) == ROLES and all(processes[name] for name in ROLES) and
            processes == observation['role_identities'], 'runtime-terminal: stale/missing live role incarnations')
    require(observation['host']['id'] == identity['host_id'] and
            observation['host']['generation'] > binding['preapprove_host_generation'] and
            observation['host']['observation']['host_generation'] == observation['host']['generation'],
            'runtime-terminal: durable installation host observation not advancing')
    completed(status['host_generation'] == observation['host']['generation'],
              'runtime-terminal: DB/status host observation handoff not yet coherent')
    return receipt


def repair_terminal_state(before, binding, observation):
    if observation['read_errors']:
        return 'pending'
    attempt = assert_publication_binding(before, binding, observation)
    assert_failed_history(before, observation)
    assert_apply_audit(before, binding, observation)
    for receipt in (attempt['record'].get('terminal'), observation['journal'].get('attempt_result')):
        if receipt and receipt['identity']['attempt_id'] == binding['attempt_id']:
            require(receipt['outcome'] == 'applied-normal', 'runtime-terminal: non-applied-normal outcome ' + receipt['outcome'])
    if attempt['record']['phase'] in ('blocked', 'failed', 'aborted', 'stopped'):
        raise AssertionError('runtime-terminal: negative terminal ' + attempt['record']['phase'])
    operation = observation['journal'].get('operation')
    require(not operation or operation['id'] in (before['journal']['operation']['id'], binding['attempt_id']),
            'repair-binding: another local attempt applied')
    record = attempt['record']
    if record.get('terminal'):
        require(record['terminal']['identity'] == record.get('claim'),
                'repair-binding: invalid DB terminal identity')
    if record.get('claim'):
        identity = record['claim']
        require(identity['attempt_id'] == binding['attempt_id'] and identity['revision_id'] == binding['revision_id'] and
                identity['fence'] == 1 and identity['host_id'] == before['installation_host']['id'] and
                identity['bundle_digest'] == binding['bundle_digest'] and identity['target'] == binding['target'],
                'repair-binding: invalid running claim identity')
    if operation and operation['id'] == binding['attempt_id']:
        require(operation['revision'] == binding['revision_id'] and operation['kind'] == 'published-apply' and
                operation['binding'] == record.get('claim'), 'repair-binding: invalid live local binding')
    require(record['phase'] in ('pending', 'running', 'succeeded'),
            'runtime-terminal: unsupported attempt phase')
    if record['phase'] in ('pending', 'running'):
        require(record['fence'] in (0, 1), 'double-apply: attempt fence changed')
        return 'pending'
    if operation and operation['id'] == binding['attempt_id'] and observation['journal'].get('attempt_result'):
        require(observation['journal']['attempt_result']['identity'] == record.get('claim'),
                'repair-binding: invalid local terminal identity')
    try:
        assert_repair_terminal(before, binding, observation)
    except PendingEvidence:
        return 'pending'
    return 'success'


def observation_database(paths):
    url, prefix = configuration(paths)
    engine = create_engine(url, poolclass=NullPool, connect_args={
        'connect_timeout': 5, 'options': '-c statement_timeout=5000'})

    @event.listens_for(engine, 'do_connect')
    def clamp_connection(dialect, record, arguments, parameters):
        budget = remaining(engine.proof_deadline, 5)
        parameters['connect_timeout'] = max(1, int(budget))
        parameters['options'] = '-c statement_timeout=' + str(max(1, int(budget * 1000)))

    return engine, prefix


def remaining(deadline, maximum):
    budget = min(maximum, deadline - time.monotonic())
    if budget <= 0:
        raise TimeoutError('browser-repair stage deadline exhausted')
    return budget


def pause(deadline):
    time.sleep(remaining(deadline, 2))


def read_repair_observation(paths, engine, prefix, deadline, sequence):
    """One short REPEATABLE READ, READ ONLY transaction; no product mutation API."""
    observation = {'sequence': sequence, 'utc': datetime.now(timezone.utc).isoformat(),
                   'monotonic': time.monotonic(), 'read_errors': []}

    def read(name, path, loader=None):
        # After an exhausted budget, retain local diagnostics but do not query DB.
        if time.monotonic() >= deadline:
            observation['read_errors'].append({'plane': 'deadline', 'path': str(path), 'class': 'TimeoutError'})
        try:
            require(path.is_file() and not path.is_symlink(), 'required regular evidence missing')
            observation[name] = loader(path) if loader else json.loads(path.read_text())
        except (OSError, ValueError, KeyError, TypeError, AssertionError) as error:
            observation['read_errors'].append({'plane': 'local', 'path': str(path), 'class': type(error).__name__})

    read('journal', paths.journal_file, lambda path: Journal.load(path).to_dict())
    for key, path in [('applied', paths.applied_file), ('desired', paths.intent_file),
                      ('admission', paths.state_dir / 'admission.json'), ('status', paths.status_file),
                      ('installation_host', paths.state_dir / 'installation-host.json'),
                      ('publication_reported', paths.state_dir / 'publication-reported.json'),
                      ('operator_clearance', core.ROOT / 'operator-clearance.json')]:
        read(key, path)
    clearance_path = paths.state_dir / 'schema-clearance.json'
    read('schema_clearance_source_digest', clearance_path, lambda path: hashlib.sha256(path.read_bytes()).hexdigest())
    observation['schema_clearance'] = load_schema_clearance(paths)
    read('audit', AUDIT, lambda path: path.read_bytes())
    observation['history'] = {}
    for directory in (paths.state_dir / 'attempt-history', paths.log_dir):
        if not directory.is_dir():
            observation['read_errors'].append({'plane': 'local', 'path': str(directory), 'class': 'MissingDirectory'})
    for path in sorted((paths.state_dir / 'attempt-history').glob('*.json')):
        try:
            observation['history'][path.stem] = Journal.load(path).to_dict()
        except (OSError, ValueError, TypeError) as error:
            observation['read_errors'].append({'plane': 'local', 'path': str(path), 'class': type(error).__name__})
    observation['sync_logs'] = sorted(str(path) for path in paths.log_dir.glob('uv-sync-*.log') if path.is_file())
    observation['role_identities'] = process_identities(paths)
    observation['web_processes'] = {}
    for kind in ('recovery', 'normal'):
        try:
            pid = (paths.run_dir / ('web-' + kind + '.pid')).read_text().strip()
            stat = (Path('/proc') / str(int(pid)) / 'stat').read_text().rsplit(')', 1)[-1].split()
            observation['web_processes'][kind] = {'pid': pid, 'starttime': stat[19], 'state': stat[0]}
        except (OSError, ValueError, IndexError):
            observation['web_processes'][kind] = None
    if engine is None:
        observation['read_errors'].append({'plane': 'database', 'class': 'Unavailable'})
        return observation
    tables = preparation_tables(prefix)
    try:
        # NullPool avoids a pooled connection retaining transaction/session state.
        remaining(deadline, 5)
        engine.proof_deadline = deadline
        with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
            with connection.begin():
                connection.execute(text('SET TRANSACTION READ ONLY'))

                def query(statement):
                    milliseconds = max(1, int(remaining(deadline, 5) * 1000))
                    connection.execute(text('SET LOCAL statement_timeout = ' + str(milliseconds)))
                    return connection.execute(statement)

                observation['database_now'] = query(select(func.now())).scalar_one().isoformat()
                versions = query(text('SELECT version_num FROM alembic_version')).scalars().all()
                require(len(versions) == 1 and bool(versions[0]), 'ambiguous/missing Alembic revision')
                observation['alembic_revision'] = versions[0]
                observation['environment'] = dict(query(select(tables['environment_state']).where(
                    tables['environment_state'].c.id == 1)).mappings().one())
                for key, table_name in [('candidates', 'candidate'), ('revisions', 'revision'),
                                        ('attempts', 'installation_attempt')]:
                    observation[key] = {row['id']: dict(row) for row in query(select(tables[table_name])).mappings()}
                for key, table_name in [('publications', 'publication_result'), ('deduplication', 'request_deduplication'),
                                        ('workers', 'preparation_worker')]:
                    observation[key] = [dict(row) for row in query(select(tables[table_name]).order_by(
                        *tables[table_name].primary_key.columns)).mappings()]
                contributions = tables['requirement_contribution']
                observation['contributions'] = [dict(row) for row in query(select(contributions).where(
                    contributions.c.active.is_(True)).order_by(contributions.c.id)).mappings()]
                host = tables['host_environment']
                observation['host'] = dict(query(select(host).where(
                    host.c.id == observation['installation_host']['id'])).mappings().one())
    except Exception as error:
        # DB exceptions may include connection parameters or complete input rows.
        observation['read_errors'].append({'plane': 'database', 'class': type(error).__name__})
    for candidate in observation.get('candidates', {}).values():
        for key in ('claim_until', 'ready_at', 'expires_at'):
            if candidate.get(key) is not None:
                candidate[key] = candidate[key].isoformat()
    return observation


def safe_observation(observation):
    """Retain named identities and hashes, never secret-bearing config/snapshots."""
    result = {key: observation.get(key) for key in ('sequence', 'utc', 'monotonic', 'read_errors',
        'database_now', 'alembic_revision', 'schema_clearance_source_digest', 'admission', 'installation_host', 'publication_reported',
        'web_processes', 'role_identities', 'sync_logs', 'ready_http_status')}
    env = observation.get('environment', {})
    result['environment'] = {key: env.get(key) for key in ('desired_revision_id', 'active_attempt_id', *GENERATIONS)}
    runtime = env.get('snapshot', {}).get('runtime', {})
    result['runtime'] = {key: runtime.get(key) for key in ('kind', 'revision_id', 'attempt_id', 'recovery_id')}
    result['snapshot_digest'] = digest(env.get('snapshot'))
    result['candidates'] = {identity: {**{key: row.get(key) for key in ('state', 'operation', 'requester_id',
        'parent_id', 'result_digest', 'ready_at', 'expires_at', *GENERATIONS)},
        'inputs_digest': digest(row.get('inputs')), 'result_hash': digest(row.get('result'))}
        for identity, row in observation.get('candidates', {}).items()}
    result['revisions'] = {identity: {'parent_id': row['parent_id'], 'snapshot_digest': digest(row['snapshot'])}
                           for identity, row in observation.get('revisions', {}).items()}
    result['attempts'] = {identity: {'revision_id': row['revision_id'],
        'phase': row['record'].get('phase'), 'reason': row['record'].get('reason'), 'fence': row['record'].get('fence'),
        'claim': row['record'].get('claim'), 'terminal': row['record'].get('terminal'), 'record_digest': digest(row['record'])}
        for identity, row in observation.get('attempts', {}).items()}
    for key in ('journal', 'applied', 'desired', 'schema_clearance', 'publications', 'deduplication', 'workers'):
        # desired.json can include configuration; keep just its identity + digest.
        value = observation.get(key)
        result[key] = ({'revision': value.get('revision'), 'digest': digest(value)}
                       if key == 'desired' and isinstance(value, dict) else value)
    result['history'] = {identity: {'digest': digest(value), 'journal': value}
                         for identity, value in observation.get('history', {}).items()}
    result['contributions_digest'] = digest(observation.get('contributions'))
    status = observation.get('status', {})
    result['status'] = {key: status.get(key) for key in ('mode', 'normal_ready', 'recovery_available',
        'desired_revision', 'applied_revision', 'verified_running_revision_id', 'attempt_id', 'active',
        'publication', 'locked_converged', 'runtime_state', 'role_health', 'role_processes', 'host_id', 'host_generation')}
    host = observation.get('host', {})
    result['host'] = {'id': host.get('id'), 'generation': host.get('generation')}
    audit = observation.get('audit')
    result['audit'] = {'bytes': len(audit), 'sha256': hashlib.sha256(audit).hexdigest()} if audit is not None else None
    if audit is not None:
        try:
            result['audit']['runtime_sync_count'] = len(runtime_syncs(audit))
        except (AssertionError, UnicodeError) as error:
            result['audit']['parse_error'] = type(error).__name__
    return result


def same_recovery_destination(source, destination):
    left, right = urllib.parse.urlsplit(source), urllib.parse.urlsplit(destination)
    def origin(url):
        return url.scheme, url.hostname, url.port if url.port is not None else (80 if url.scheme == 'http' else 443)
    return origin(left) == origin(right) and right.path == '/recovery' and not right.query and not right.fragment


class ApprovalRedirectRecorder(urllib.request.HTTPRedirectHandler):
    """Record only; standard urllib owns redirect method conversion and following."""
    def __init__(self):
        self.events = []
        self.approving = False
        self.deadline = None

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if self.deadline is not None:
            req.timeout = remaining(self.deadline, 10 if req.get_method() == 'GET' else 30)
        if self.approving:
            require(req.get_method() == 'POST' and urllib.parse.urlsplit(req.full_url).path == '/recovery/publication' and
                    code in (302, 303) and same_recovery_destination(req.full_url, newurl),
                    'approval-http: foreign/disallowed redirect or method/status')
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        self.events.append({'method': req.get_method(), 'path': urllib.parse.urlsplit(req.full_url).path,
                            'status': code, 'destination_path': urllib.parse.urlsplit(newurl).path,
                            'same_origin_recovery': same_recovery_destination(req.full_url, newurl),
                            'redirect_method': redirected.get_method() if redirected else None})
        if self.approving:
            req.timeout = remaining(self.deadline, 10)
        return redirected

    def valid_approve_redirect(self):
        return (self.approving and len(self.events) == 1 and
                self.events[0]['method'] == 'POST' and self.events[0]['path'] == '/recovery/publication' and
                self.events[0]['status'] in (302, 303) and self.events[0]['same_origin_recovery'] and
                self.events[0]['redirect_method'] == 'GET')

    def approve_followup_503(self, error):
        return (self.valid_approve_redirect() and error.code == 503 and
                urllib.parse.urlsplit(error.geturl()).path == '/recovery')


def read_http_body(response, deadline, maximum=1024 * 1024):
    chunks = []
    size = 0
    while size < maximum:
        budget = remaining(deadline, 10)
        raw = getattr(getattr(response, 'fp', None), 'raw', None)
        socket = getattr(raw, '_sock', None)
        if socket is not None:
            socket.settimeout(budget)
        chunk = response.read1(min(65536, maximum - size))
        if not chunk:
            return b''.join(chunks).decode()
        chunks.append(chunk)
        size += len(chunk)
    raise AssertionError('browser-http: bounded response body exceeded')


class BrowserHTTP:
    def __init__(self):
        self.recorder = ApprovalRedirectRecorder()
        self.opener = urllib.request.build_opener(self.recorder,
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.approve_boundary = None

    def request(self, path, deadline, form=None, *, approve=False, sequence=None):
        self.recorder.events = []
        self.recorder.approving = approve
        self.recorder.deadline = deadline
        method = 'POST' if form is not None else 'GET'
        request_deadline = min(deadline, time.monotonic() + (30 if form is not None else 10))
        self.recorder.deadline = request_deadline
        encoded = urllib.parse.urlencode(form).encode() if form is not None else None
        if approve:
            require(path == '/recovery/publication' and form['action'] == 'approve' and
                    form['csrf_token'] and form['candidate_id'] and form['idempotency_key'] == APPROVE_KEY and
                    'result_digest' not in form, 'approval-http: invalid browser approve boundary')
            self.approve_boundary = {'sequence': sequence, 'monotonic': time.monotonic(),
                'utc': datetime.now(timezone.utc).isoformat(), 'method': method, 'path': path,
                'candidate_id': form['candidate_id'], 'idempotency_key': form['idempotency_key']}
        try:
            with self.opener.open(ORIGIN + path, data=encoded,
                                  timeout=remaining(request_deadline, 30 if form is not None else 10)) as response:
                html = read_http_body(response, request_deadline)
                remaining(request_deadline, 10)
                if approve:
                    require(self.recorder.valid_approve_redirect(),
                            'approval-http: successful response without valid explicit approve redirect')
                self.recorder.events.append({'method': 'GET' if self.recorder.events else method,
                    'path': urllib.parse.urlsplit(response.geturl()).path if hasattr(response, 'geturl') else path,
                    'status': getattr(response, 'status', 200)})
                return html
        except urllib.error.HTTPError as error:
            if error.code == 403:
                print(json.dumps({'initial_method': method, 'initial_path': urllib.parse.urlsplit(path).path,
                    'terminal_path': urllib.parse.urlsplit(error.geturl()).path, 'status': error.code,
                    'body': error.read(256).decode(errors='replace')}), file=sys.stderr, flush=True)
            transition = approve and self.recorder.approve_followup_503(error)
            self.recorder.events.append({'method': 'GET' if self.recorder.events else method,
                'path': urllib.parse.urlsplit(error.geturl()).path, 'status': error.code})
            if transition:
                return None  # Identified transition only; DB commit is still required.
            raise

    def get_page(self, path, deadline, observe=None, *, initial=False):
        outage_started = None
        while True:
            request_deadline = deadline
            if outage_started is not None and not initial:
                request_deadline = min(deadline, outage_started + 30)
                if time.monotonic() >= request_deadline:
                    if observe:
                        observe('outage-deadline')
                    raise AssertionError('recovery-unavailable-before-approval: 30s continuous GET 503')
            if observe:
                observe()
            try:
                return self.request(path, request_deadline)
            except urllib.error.HTTPError as error:
                if error.code != 503:
                    raise
                if observe:
                    observe('first-503' if outage_started is None else None)
                now = time.monotonic()
                if outage_started is None:
                    outage_started = now
                if not initial and now - outage_started >= 30:
                    raise AssertionError('recovery-unavailable-before-approval: 30s continuous GET 503') from None
                if now >= deadline:
                    if initial:
                        raise
                    raise AssertionError('recovery-unavailable-before-approval: ready review deadline') from None
                pause(min(deadline, outage_started + 30) if not initial else deadline)

    def login(self, deadline):
        html = self.get_page('/user/sign-in', deadline, initial=True)
        self.request('/user/sign-in', deadline, sign_in_form(html))
        html = self.get_page('/recovery', deadline, initial=True)
        require('Restricted recovery administration' in html, 'browser-login: real restricted recovery page missing')
        return html

    def ready_health(self, deadline):
        try:
            with self.opener.open(ORIGIN + '/health_check?ready=1', timeout=remaining(deadline, 10)) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code


def observe_window(sample, predicate, deadline, *, seconds=10):
    """Require successful duration AND generation progress; False resets the interval."""
    started = None
    first_generation = None
    last_generation = None
    last = None
    try:
        while True:
            remaining(deadline, 2)
            last = sample()
            if predicate(last) is False:
                # A cross-plane retry is not successful service, including its elapsed time.
                started = first_generation = None
                pause(deadline)
                continue
            generation = last['host']['generation']
            if first_generation is None:
                started = time.monotonic()
                first_generation = generation
            require(generation >= first_generation and (last_generation is None or generation >= last_generation),
                    'durable installation host generation regressed')
            last_generation = generation
            if time.monotonic() - started >= seconds and generation > first_generation:
                return last
            pause(deadline)
    except TimeoutError:
        if time.monotonic() >= deadline:
            # Stagnation is incomplete liveness evidence until the existing deadline.
            require(first_generation is None or last_generation > first_generation,
                    'durable installation host generation did not advance during observation window')
        raise


def binding_from_publication(before, ready, observation):
    rows = [row for row in observation['publications'] if row['actor_id'] == '1' and row['key'] == APPROVE_KEY]
    if not rows:
        require(observation['publications'] == before['publications'] and
                observation['environment']['desired_revision_id'] == before['environment']['desired_revision_id'] and
                observation['environment']['active_attempt_id'] is None and
                observation['revisions'] == before['revisions'] and observation['attempts'] == before['attempts'],
                'approval-not-published: unrelated/wrong publication authority changed')
        return None
    require(len(rows) == 1, 'approval-not-published: duplicate actor/key publication')
    row = rows[0]
    binding = dict(ready, revision_id=row['revision_id'], attempt_id=row['attempt_id'])
    assert_publication_binding(before, binding, observation)
    return binding


def main(action):
    require(action == 'browser-repair', 'only browser-repair is permitted; no local build/apply action')
    paths = ExecutorPaths.from_env()
    overall = time.monotonic() + 1740
    started = time.monotonic()
    stage, stage_limit = 'browser-precondition/login', 120
    stage_deadline = min(overall, started + stage_limit)
    engine = None
    prefix = ''
    before = binding = last = None
    ready = {}
    sequence = 0
    last_saved = -float('inf')
    last_stage = None
    browser = BrowserHTTP()
    approval_http_saved = False
    evidence_paths = []

    def save_observation(name, observation):
        core.save(name, safe_observation(observation))
        if name not in evidence_paths:
            evidence_paths.append(name)

    def sample(label=None, *, health=False):
        nonlocal sequence, last, last_saved, last_stage
        sequence += 1
        last = read_repair_observation(paths, engine, prefix, stage_deadline, sequence)
        if health:
            last['ready_http_status'] = browser.ready_health(stage_deadline)
        if label or stage != last_stage or time.monotonic() - last_saved >= 10:
            projection = safe_observation(last)
            projection.update(stage=stage, event=label or 'poll')
            with (core.ROOT / 'browser-repair-observations.jsonl').open('a') as handle:
                handle.write(json.dumps(projection, default=str, sort_keys=True) + '\n')
            last_saved, last_stage = time.monotonic(), stage
        if label:
            save_observation('browser-repair-' + label + '.json', last)
        return last

    def phase(name, limit, anchor=None):
        nonlocal stage, stage_limit, stage_deadline, started
        stage, stage_limit = name, limit
        started = time.monotonic() if anchor is None else anchor
        stage_deadline = min(overall, started + limit)

    def preapproval(label=None):
        observation = sample(label)
        assert_preapproval_unchanged(before, observation, ready['candidate_id'], ready.get('result_digest'))
        return observation

    try:
        engine, prefix = observation_database(paths)
        while True:
            observation = sample()
            if not observation['read_errors']:
                # Startup may not yet have projected live recovery; no stale health PASS.
                status = observation['status']
                env = observation['environment']
                if (status.get('mode') == 'recovery' and status.get('recovery_available') is True and
                        env['snapshot'].get('runtime', {}).get('kind') == 'core-only' and
                        env['active_attempt_id'] is None):
                    assert_baseline(observation)
                    before = observation
                    break
            remaining(stage_deadline, 2)
            pause(stage_deadline)
        clearance = assert_prior_schema_clearance(before)
        save_observation('browser-repair-baseline.json', before)
        core.save('browser-repair-prior-clearance.json', dict(clearance,
            sources=['operator-clearance.json', str(paths.state_dir / 'schema-clearance.json'),
                     str(paths.journal_file)]))
        html = browser.login(stage_deadline)
        # Login has no installation authority; catch any intervening work before prepare.
        assert_preapproval_unchanged(before, sample('before-prepare'))
        html = browser.request('/recovery/publication', stage_deadline, {
            'csrf_token': csrf(html), 'action': 'repair', 'idempotency_key': PREPARE_KEY})
        accepted_prepare = time.monotonic()
        require('Repair preview prepared' in html, 'repair-prepare: real prepare feedback missing')
        core.save('browser-repair-prepare-http.json', {'sequence': sequence + 1, 'monotonic': accepted_prepare,
            'utc': datetime.now(timezone.utc).isoformat(), 'method': 'POST', 'path': '/recovery/publication',
            'idempotency_key': PREPARE_KEY, 'redirects': browser.recorder.events})
        phase('candidate-ready', 600, accepted_prepare)
        observation = sample('prepared')
        require(not observation['read_errors'], 'repair-prepare: required evidence unavailable')
        candidates = set(observation['candidates']) - set(before['candidates'])
        require(len(candidates) == 1, 'repair-prepare: no unique new repair candidate')
        candidate_id, = candidates
        ready = {'candidate_id': candidate_id}
        while True:
            assert_preapproval_unchanged(before, observation, candidate_id)
            candidate = observation['candidates'][candidate_id]
            if candidate['state'] == 'ready':
                first_ready = time.monotonic()
                workers = [row for row in observation['workers'] if row['candidate_id'] == candidate_id and
                           row['fence'] == candidate['fence']]
                builder_entries = audit_entries(audit_suffix(before['audit'], observation['audit']))
                require(workers and candidate['fence'] > 0 and any(entry['argv'][0] in ('sync', 'lock', 'build')
                        for entry in builder_entries), 'repair-ready: candidate-bound runtime worker/builder audit missing')
                ready.update(inputs=candidate['inputs'], result=candidate['result'],
                             result_digest=candidate['result_digest'], bundle_digest=candidate['result']['bundle']['digest'],
                             target=before['environment']['snapshot']['target'])
                save_observation('browser-repair-ready.json', observation)
                core.save('browser-repair-ready-material.json', {
                    'candidate_id': candidate_id, 'result_digest': ready['result_digest'],
                    'bundle_digest': ready['bundle_digest'], 'inputs_digest': digest(ready['inputs']),
                    'result': ready['result']})
                break
            pause(stage_deadline)
            observation = sample()
        phase('ready-review', 120, first_ready)
        observation = observe_window(preapproval, lambda value: assert_preapproval_unchanged(
            before, value, candidate_id, ready['result_digest']), stage_deadline)
        save_observation('browser-repair-unapproved-window.json', observation)
        html = browser.get_page('/recovery', stage_deadline, preapproval)
        # A real candidate-specific form supplies its own current CSRF, not an arbitrary page token.
        forms = re.findall(r'<form\b[^>]*>.*?</form>', html, flags=re.S | re.I)
        forms = [form for form in forms if 'Approve the prepared repair' in form and
                 re.search(r'name="candidate_id"[^>]*value="' + re.escape(candidate_id) + '"', form) and
                 re.search(r'name="action"[^>]*value="approve"', form)]
        require(len(forms) == 1, 'ready-review: real Approve the prepared repair form for candidate missing')
        approve_csrf = csrf(forms[0])
        observation = preapproval('preapprove')
        ready.update(preapprove_audit=observation['audit'], preapprove_host_generation=observation['host']['generation'])
        (core.ROOT / 'browser-repair-preapprove-uv.log').write_bytes(audit_suffix(before['audit'], observation['audit']))
        # The POST and all redirects share this fixed 30s commit boundary.
        phase('resolve-approve', 30)
        html = browser.request('/recovery/publication', stage_deadline, {
            'csrf_token': approve_csrf, 'action': 'approve', 'candidate_id': candidate_id,
            'idempotency_key': APPROVE_KEY}, approve=True, sequence=sequence + 1)
        core.save('browser-repair-approve-http.json', {
            'boundary': browser.approve_boundary, 'redirects': browser.recorder.events,
            'followup_503': html is None})
        approval_http_saved = True
        if html is None:
            sample('first-503')
        else:
            require('Approval published revision' in html, 'approval-http: real approval feedback missing')
        while True:
            observation = sample()
            if not observation['read_errors']:
                binding = binding_from_publication(before, ready, observation)
                if binding:
                    save_observation('browser-repair-approval.json', observation)
                    break
            if time.monotonic() >= stage_deadline:
                raise AssertionError('approval-not-published: no exact actor/key/digest transaction within 30s')
            pause(stage_deadline)
        phase('runtime-terminal', 900)
        while True:
            observation = sample(health=True)
            if repair_terminal_state(before, binding, observation) == 'success':
                save_observation('browser-repair-terminal.json', observation)
                break
            if time.monotonic() >= stage_deadline:
                raise AssertionError('runtime-terminal: bound runtime/worker control plane did not finish within 900s')
            pause(stage_deadline)
        terminal_host_generation = observation['host']['generation']
        steady_audit = observation['audit']
        steady_logs = observation['sync_logs']
        terminal_observation = observation

        def steady(value):
            # Check available negative evidence BEFORE read-error/coherence retries.
            # A failed DB read can still leave authoritative local loss of service.
            status = value.get('status')
            if status is not None:
                require(status.get('mode') == 'normal' and status.get('normal_ready') is True and
                        status.get('locked_converged') is True and status.get('runtime_state') == 'locked',
                        'runtime-steady: independent normal readiness lost')
                health = status.get('role_health', [])
                require(len(health) == len(ROLES) and sorted(item['name'] for item in health) == ROLES and
                        all(item['ok'] is True for item in health),
                        'runtime-steady: exact live all-role health lost')
            if 'ready_http_status' in value:
                require(value['ready_http_status'] == 200, 'runtime-steady: real ready HTTP lost')
            if 'admission' in value:
                require(value['admission']['mode'] == 'open' and value['admission'].get('consumers') is True,
                        'runtime-steady: consumer admission lost')
            for key, expected in (('audit', steady_audit), ('sync_logs', steady_logs)):
                if key in value:
                    require(value[key] == expected, 'double-apply: audit mutation changed during steady completion')
            # Validate each available plane against all existing terminal predicates.
            # Missing planes use the pinned terminal ONLY for negative checking;
            # read_errors below forbids certifying this composite as a success.
            checked = dict(terminal_observation, **value) if value['read_errors'] else dict(value)
            checked['read_errors'] = []
            state = repair_terminal_state(before, binding, checked)
            if state != 'success':
                # Coherence is the LAST terminal check. Any other pending condition
                # is loss of previously certified completion, not a benign retry.
                try:
                    assert_repair_terminal(before, binding, checked)
                except PendingEvidence as error:
                    require(str(error) == 'runtime-terminal: DB/status host observation handoff not yet coherent',
                            'runtime-steady: bound completion/readiness lost')
                return False
            return not value['read_errors']

        def steady_sample():
            return sample(health=True)

        while True:
            observation = observe_window(steady_sample, steady, stage_deadline)
            # The final re-read also belongs to the interval; retry needs a NEW window.
            observation = steady_sample()
            if steady(observation):
                break
            pause(stage_deadline)
        save_observation('browser-repair-steady.json', observation)
        require(observation['host']['generation'] > terminal_host_generation,
                'runtime-steady: completion host generation did not advance')
        receipt = assert_repair_terminal(before, binding, observation)
        save_observation('browser-repair-failed-history.json', observation)
        apply_count = assert_apply_audit(before, binding, observation, terminal=True)
        core.save('browser-repair-result.json', {
            'candidate_id': candidate_id, 'attempt_id': binding['attempt_id'],
            'approved_result_digest': binding['result_digest'], 'bundle_digest': binding['bundle_digest'],
            'normal_ready': observation['status']['normal_ready'],
            'csrf_login_prepare_ready_approve': bool(browser.approve_boundary and approve_csrf and forms),
            'explicit_schema_clearance_preceded_repair': clearance['observation_sequence'] <
                json.loads((core.ROOT / 'browser-repair-prepare-http.json').read_text())['sequence'],
            'old_failed_desired_preserved_in_history': before['environment']['desired_revision_id'],
            'new_desired_revision': binding['revision_id'], 'terminal_outcome': receipt['outcome'],
            'terminal_identity': receipt['identity'], 'runtime_apply_count': apply_count,
            'roles': receipt['readiness']['roles'], 'prior_clearance': clearance,
            'evidence_paths': evidence_paths + ['browser-repair-observations.jsonl', 'browser-repair-prior-clearance.json',
                'browser-repair-ready-material.json', 'browser-repair-prepare-http.json', 'browser-repair-approve-http.json',
                'browser-repair-preapprove-uv.log', 'browser-repair-final-uv.log',
                'supervisor-logs/durable-preparation.log', 'supervisor-logs/durable-installation.log', 'uv-invocations.log']})

    except Exception as error:
        if browser.approve_boundary is not None and not approval_http_saved:
            core.save('browser-repair-approve-http.json', {
                'boundary': browser.approve_boundary, 'redirects': browser.recorder.events,
                'http_error_class': type(error).__name__})
        failure = {'stage': stage, 'elapsed': time.monotonic() - started, 'limit': stage_limit,
            'class': type(error).__name__, 'candidate_id': (binding or ready).get('candidate_id'),
            'revision_id': binding.get('revision_id') if binding else None,
            'attempt_id': binding.get('attempt_id') if binding else None,
            'evidence_paths': evidence_paths,
                        'reason': str(error)[:300] if isinstance(error, (AssertionError, TimeoutError)) else type(error).__name__,
            'last_observation': safe_observation(last) if last else None}
        core.save('browser-repair-failure.json', failure)
        raise AssertionError('browser-repair ' + stage + ': ' + failure['reason'] +
                             '; see browser-repair-failure.json and browser-repair-observations.jsonl') from None
    finally:
        try:
            # Diagnostic-only grace is inside the overall cap, never an apply/wait extension.
            sequence += 1
            final = read_repair_observation(paths, engine, prefix, min(overall, time.monotonic() + 10), sequence)
            save_observation('browser-repair-final.json', final)
            if final.get('audit') is not None:
                suffix = audit_suffix(before['audit'], final['audit']) if before else final['audit']
                (core.ROOT / 'browser-repair-final-uv.log').write_bytes(suffix)
        except Exception as error:
            core.save('browser-repair-final-read-errors.json', {'class': type(error).__name__})
            if last and last.get('audit') is not None:
                (core.ROOT / 'browser-repair-final-uv.log').write_bytes(last['audit'])
        finally:
            if engine is not None:
                engine.dispose()
    print('PASS real recovery browser login/CSRF, repair prepare -> ready -> approve -> runtime-owned frozen apply/all-role normal service', flush=True)


if __name__ == '__main__':
    main(sys.argv[-1])
