"""Real pinned-uv preparation -> internal approval -> stopped frozen apply proof.

No Flask/Celery/Redis runtime in this process or disposable container. This is
NOT normal service/recovery or public browser/API qualification.
"""
from docassemble.base.managed_python import python_target
import copy
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import urllib.request

from sqlalchemy import create_engine, select, text, update

from docassemble.base.managed_publication import Publication
from docassemble_executor.installation import Installation, InstallationPoller
from docassemble_executor.inventory import inspect_environment
from docassemble_executor.journal import Journal, Operation
from docassemble_executor.paths import ExecutorPaths
from docassemble_executor.preparation import PreparationError
from docassemble_executor.registry_builder import verify_retained


def main():
    root = Path('/tmp/proof')
    root.mkdir()
    account = pwd.getpwnam('da-environment')
    store = root / 'retained'
    store.mkdir()
    os.chown(store, account.pw_uid, account.pw_gid)
    engine = create_engine(os.environ['DA_PREPARATION_DATABASE_URL'])
    with engine.begin() as connection:
        connection.execute(text('DROP SCHEMA IF EXISTS installation_proof CASCADE'))
        connection.execute(text('CREATE SCHEMA installation_proof'))
    engine.dispose()
    os.environ['PGOPTIONS'] = '-csearch_path=installation_proof'
    engine = create_engine(os.environ['DA_PREPARATION_DATABASE_URL'])
    publisher = Publication(engine, lambda *_: True,
                            lambda connection, actor, candidate: {'owner_id': actor, 'legacy_package_id': 1})
    tables = publisher.tables
    tables['environment_state'].metadata.create_all(engine)
    with urllib.request.urlopen('https://pypi.org/pypi/packaging/25.0/json') as response:
        source = next(value['digests']['sha256'] for value in json.load(response)['urls'] if value['packagetype'] == 'sdist')
    core = {'packaging': {'version': '25.0', 'source_digest': source}}
    baseline = {'core': core, 'target': python_target(), 'tool': {'uv': '0.12.21'},
                'config': {'index_url': 'https://pypi.org/simple'}, 'desired_inventory': core,
                'runtime': {'kind': 'normal', 'inventory': core, 'revision_id': 'baseline'}}
    with engine.begin() as connection:
        connection.execute(tables['revision'].insert().values(id='baseline', snapshot=baseline))
        connection.execute(tables['environment_state'].insert().values(id=1, managed=True,
            desired_revision_id='baseline', publication_generation=1, input_generation=1,
            target_generation=1, runtime_generation=1, snapshot=baseline))
    candidate = publisher.prepare('alice', 'six==1.17.0', 'prepare')
    command = ['runuser', '-u', 'da-environment', '--', sys.executable, '-m',
               'docassemble_executor.preparation_poller', '--store', str(store), '--once']
    prepared = subprocess.run(command, capture_output=True, text=True, timeout=600)
    assert prepared.returncode == 0 and prepared.stdout.strip() == 'ready', prepared.stderr
    status = publisher.status('alice', candidate)
    result = status['preview']
    approved = publisher.approve('alice', candidate, status['result_digest'], 'approve')
    digest = result['bundle']['digest']
    verify_retained(store / digest, digest)
    (root / 'attestation.json').write_text(json.dumps(result, indent=2, sort_keys=True))
    (root / 'baseline.json').write_text(json.dumps(baseline))
    print('PASS independent DB preparation -> internal atomic approval with exact retained digest ' + digest, flush=True)
    audit = Path('/var/log/docassemble-uv-invocations.log')
    (root / 'preparation-uv-invocations.log').write_text(audit.read_text())
    audit.write_text('')

    os.environ.update(DA_UV_MANAGED='true', DA_ENVIRONMENT_USER='da-environment',
        DA_ROOT=str(root), DA_MANAGED_STATE_DIR=str(root / 'state'), DA_RUN_DIR=str(root / 'run'),
        DA_RUNTIME_VENV=str(root / 'runtime'), UV_CACHE_DIR=str(root / 'cache'),
        DA_CONFIG_FILE=str(root / 'config.yml'), DA_LOG_DIR=str(root / 'logs'),
        UV_BINARY='/usr/local/bin/uv')
    (root / 'config.yml').write_text('allow updates: true\n')
    # uv can create the single test runtime, but cannot write controller authority.
    runtime = root / 'runtime'
    runtime.mkdir()
    os.chown(runtime, account.pw_uid, account.pw_gid)
    paths = ExecutorPaths.from_env()
    installation = Installation(engine, lambda *_: False)
    poller = InstallationPoller(installation, paths, store, root / 'baseline.json')
    original_report = installation.report_terminal
    def lost_report(receipt):
        raise RuntimeError('simulated reporting outage before commit')
    installation.report_terminal = lost_report
    try:
        poller.run_once()
    except RuntimeError:
        pass
    else:
        raise AssertionError('missing reporting fault')
    journal = Journal.load(paths.journal_file)
    assert journal.attempt_result['outcome'] == 'materialized-stopped', journal.to_dict()
    assert journal.operation.binding['bundle_digest'] == digest
    (root / 'terminal-journal.json').write_text(paths.journal_file.read_text())
    with engine.connect() as connection:
        state = connection.execute(select(tables['environment_state'])).mappings().one()
        assert state['active_attempt_id'] == approved['attempt_id']
        assert state['desired_revision_id'] == approved['revision_id']
    inventory = inspect_environment(runtime)
    assert inventory == {name: value['version'] for name, value in result['bundle']['inventory'].items()}
    (root / 'installed-inventory.json').write_text(json.dumps(inventory, sort_keys=True))
    invocations = Path('/var/log/docassemble-uv-invocations.log').read_text()
    syncs = [line for line in invocations.splitlines() if '\tsync ' in line]
    assert len(syncs) == 1 and syncs[0].startswith(str(account.pw_uid) + '\t')
    assert '--frozen' in syncs[0] and '--no-build' in syncs[0]
    (root / 'uv-invocations.log').write_text(invocations)
    print('PASS non-root offline frozen --no-build materialization; terminal durable before DB reporting; normal NOT verified', flush=True)

    # A live external child lease forbids replay and slot release.
    holder = subprocess.Popen([sys.executable, '-c',
        'import fcntl,sys,time; f=open(sys.argv[1]); fcntl.flock(f,fcntl.LOCK_SH); print("held",flush=True); time.sleep(60)',
        str(paths.state_dir / 'runtime.lock')], stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == 'held'
    installation.report_terminal = original_report
    try:
        poller.run_once()
    except BlockingIOError:
        pass
    else:
        raise AssertionError('child did not block replay')
    holder.terminate()
    holder.wait(timeout=10)
    print('PASS live child read authority forbids terminal replay/slot release', flush=True)

    # Run replay through the real installed CLI process; no install is possible
    # because the retained bytes are temporarily unavailable.
    store.rename(root / 'hidden-retained')
    command = [sys.executable, '-m', 'docassemble_executor.cli', 'poll-installation',
               '--store', str(store), '--baseline', str(root / 'baseline.json'), '--once']
    replay = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert replay.returncode == 0 and replay.stdout.strip() == 'reported', replay.stdout + replay.stderr
    replay = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert replay.returncode == 0 and replay.stdout.strip() == 'replayed'
    (root / 'hidden-retained').rename(store)
    with engine.connect() as connection:
        state = connection.execute(select(tables['environment_state'])).mappings().one()
        assert state['active_attempt_id'] is None and state['desired_revision_id'] == approved['revision_id']
        assert state['snapshot']['runtime']['kind'] == 'unknown'
        assert state['snapshot']['last_normally_verified']['revision_id'] == 'baseline'
    assert audit.read_text() == invocations
    print('PASS lost DB reporting replay in independent CLI WITHOUT retained store/reinstallation/recovery; desired unchanged', flush=True)

    # Gate checks operate on the same approved identity and retained bytes.
    claim_record = journal.attempt_result['identity']
    from docassemble_executor.installation import InstallationClaim
    with engine.connect() as connection:
        revision = connection.execute(select(tables['revision']).where(tables['revision'].c.id == approved['revision_id'])).mappings().one()
    claim = InstallationClaim(approved['attempt_id'], approved['revision_id'], claim_record['fence'],
                              claim_record['host_id'], claim_record['runtime_generation'], revision['snapshot'])
    for case in ('updates-config', 'updates-env', 'target', 'corruption'):
        trusted = copy.deepcopy(baseline)
        if case == 'updates-config':
            (root / 'config.yml').write_text('allow updates: false\n')
        if case == 'updates-env':
            os.environ['DAALLOWUPDATES'] = 'false'
        if case == 'target':
            trusted['target'] = {'python': '3.13'}
            (root / 'baseline.json').write_text(json.dumps(trusted))
        if case == 'corruption':
            file = store / digest / 'inventory.json'
            original = file.read_bytes()
            file.write_bytes(b'{}')
        try:
            poller._gates(claim)
        except PreparationError:
            pass
        else:
            raise AssertionError('gate did not reject ' + case)
        (root / 'config.yml').write_text('allow updates: true\n')
        os.environ.pop('DAALLOWUPDATES', None)
        (root / 'baseline.json').write_text(json.dumps(baseline))
        if case == 'corruption':
            file.write_bytes(original)
    verify_retained(store / digest, digest)
    print('PASS config/env update-disable, target mismatch and retained corruption gates before mutation', flush=True)

    # Crash boundaries: running DB claim without receipt, and journal mutation
    # marker without terminal result. Neither authorizes lease reclaim/reapply.
    for phase in ('before-journal', 'mutation-started'):
        with engine.begin() as connection:
            connection.execute(tables['installation_attempt'].insert().values(id=phase,
                revision_id=approved['revision_id'], record={'phase': 'pending', 'reason': 'approval', 'fence': 0}))
            connection.execute(update(tables['environment_state']).values(active_attempt_id=phase))
        interrupted = installation.claim_installation(claim.host_id)
        paths.journal_file.unlink(missing_ok=True)
        if phase == 'mutation-started':
            started = Journal()
            started.begin_mutation(Operation(id=phase, kind='published-materialization',
                revision=approved['revision_id'], binding=interrupted.identity))
            started.save(paths.journal_file)
        refused = subprocess.run(command, capture_output=True, text=True, timeout=30)
        assert refused.returncode == 1
        with engine.connect() as connection:
            assert connection.scalar(select(tables['environment_state'].c.active_attempt_id)) == phase
        # Test-only explicit reset for the next isolated fault, not production retry.
        with engine.begin() as connection:
            connection.execute(update(tables['environment_state']).values(active_attempt_id=None))
    print('PASS crash before local journal and after mutation-start remain fenced; no installation/recovery loop', flush=True)
    assert not any(name == 'flask' or name.startswith(('celery', 'redis', 'docassemble.webapp')) for name in sys.modules)
    print('PASS no Flask/docassemble runtime imports; Redis/Celery absent from execution container', flush=True)
    (root / 'publication-status.json').write_text(json.dumps(publisher.publication_status('alice', attempt_id=approved['attempt_id']), indent=2))
    with engine.begin() as connection:
        connection.execute(text('DROP SCHEMA installation_proof CASCADE'))
    engine.dispose()
    shutil.rmtree(runtime)
    shutil.rmtree(paths.cache_dir)


if __name__ == '__main__':
    main()
