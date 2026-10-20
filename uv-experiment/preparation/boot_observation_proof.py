"""Independent readback of real published intent across bounded cold boot."""
import hashlib
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

from sqlalchemy import select, text

from docassemble.base.managed_preparation import Preparation, runtime_status
from docassemble_executor.inventory import inspect_environment
from docassemble_executor.paths import ExecutorPaths
from image_bridge_proof import database, DESCRIPTOR

ROOT = Path('/tmp/boot-observation-proof')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def request(path):
    try:
        response = urllib.request.urlopen('http://127.0.0.1' + path, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    return response.code, response.read().decode()


def main():
    action = sys.argv[1]
    paths = ExecutorPaths.from_env()
    ROOT.mkdir(exist_ok=True)
    if action == 'hide-descriptor':
        DESCRIPTOR.rename(DESCRIPTOR.with_suffix('.hidden'))
        return
    if action == 'restore-descriptor':
        DESCRIPTOR.with_suffix('.hidden').rename(DESCRIPTOR)
        return
    engine, prefix = database()
    protocol = Preparation(engine, lambda *_: False, prefix)
    if action in ('hide-tables', 'restore-tables'):
        source, target = ((prefix + 'managed_environment_state', prefix + 'hidden_environment_state') if action == 'hide-tables'
                          else (prefix + 'hidden_environment_state', prefix + 'managed_environment_state'))
        quote = engine.dialect.identifier_preparer.quote
        with engine.begin() as connection:
            connection.execute(text('ALTER TABLE ' + quote(source) + ' RENAME TO ' + quote(target)))
        return
    with engine.begin() as connection:
        environment = dict(connection.execute(select(protocol.tables['environment_state'])).mappings().one())
        attempts = [dict(row) for row in connection.execute(select(protocol.tables['installation_attempt']).order_by(
            protocol.tables['installation_attempt'].c.id)).mappings()]
        immutable = {name: [dict(row) for row in connection.execute(select(protocol.tables[name]).order_by(
            protocol.tables[name].c.id)).mappings()] for name in ('revision', 'requirement_contribution', 'package_identity')}
        hosts = [dict(row) for row in connection.execute(select(protocol.tables['host_environment'])).mappings()]
    current = {
        'desired_revision_id': environment['desired_revision_id'],
        'publication_generation': environment['publication_generation'],
        'active_attempt_id': environment['active_attempt_id'],
        'attempts_digest': digest(attempts), 'immutable_digest': digest(immutable),
        'inventory_digest': digest(inspect_environment(paths.runtime_venv)),
        'local_files': {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (
            paths.intent_file, paths.applied_file, paths.journal_file,
            Path('/var/log/docassemble-uv-invocations.log'), paths.log_dir / 'create-tables.log')},
    }
    if action == 'capture':
        (ROOT / 'invariants.json').write_text(json.dumps(current, indent=2))
        (ROOT / 'before-runtime.json').write_text(json.dumps(runtime_status(environment), indent=2))
        print('PASS captured actual published DB intent, attempt, immutable intent, material and invocation invariants', flush=True)
        return
    assert action == 'verify'
    before = json.loads((ROOT / 'invariants.json').read_text())
    assert before == current, (before, current)
    code, body = request('/status.json')
    status = json.loads(body)
    assert code == 200 and status['mode'] == 'maintenance' and not status['normal_ready'], status
    assert status['desired_revision_id'] == current['desired_revision_id']
    assert status['runtime_kind'] == 'unknown' and status['verified_running_revision_id'] is None
    assert status['applied_revision'] != status['desired_revision_id'] and status['diverged']
    assert status['publication']['installation'] in ('pending', 'failed')
    for key, value in runtime_status(environment).items():
        assert status[key] == value
    assert len(hosts) == 1 and hosts[0]['observation'] == status['publication']
    assert request('/health_check')[0] == 200
    assert request('/health_check?ready=1')[0] == 400
    assert request('/user/sign-in')[0] == 503
    assert not status['recovery_available'] and not status['locked_converged']
    assert not any(name == 'flask' or name.startswith('docassemble.webapp') for name in sys.modules)
    (ROOT / ('observed-' + str(status['host_generation']) + '.json')).write_text(json.dumps(status, indent=2))
    print('PASS cold boot reconciles desired/applied divergence and ' + status['publication']['installation'] +
          '; durable HostEnvironment generation ' + str(status['host_generation']) + '; no sync/schema/runtime mutation or slot release', flush=True)


if __name__ == '__main__':
    main()
