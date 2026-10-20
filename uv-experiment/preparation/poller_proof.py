"""Disposable PostgreSQL + pinned-uv independent preparation dispatch proof."""
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

from sqlalchemy import create_engine, select, text

from docassemble_executor.preparation import Preparation
from docassemble_executor.registry_builder import verify_retained


def main():
    engine = create_engine(os.environ['DA_PREPARATION_DATABASE_URL'])
    with engine.begin() as connection:
        connection.execute(text('DROP SCHEMA IF EXISTS poller_proof CASCADE'))
        connection.execute(text('CREATE SCHEMA poller_proof'))
    engine.dispose()
    os.environ['PGOPTIONS'] = '-csearch_path=poller_proof'
    engine = create_engine(os.environ['DA_PREPARATION_DATABASE_URL'])
    preparation = Preparation(engine, lambda *_: True)
    tables = preparation.tables
    tables['environment_state'].metadata.create_all(engine)
    with urllib.request.urlopen('https://pypi.org/pypi/packaging/25.0/json') as response:
        metadata = json.load(response)
    source_hash = next(v['digests']['sha256'] for v in metadata['urls'] if v['packagetype'] == 'sdist')
    core = {'packaging': {'version': '25.0', 'source_digest': source_hash}}
    baseline = {'core': core, 'target': {'python': '3.12'}, 'tool': {'uv': '0.12.21'},
                'config': {'index_url': 'https://pypi.org/simple'}, 'desired_inventory': core,
                'runtime': {'kind': 'normal', 'inventory': core}}
    with engine.begin() as connection:
        connection.execute(tables['revision'].insert().values(id='baseline', snapshot=baseline))
        connection.execute(tables['environment_state'].insert().values(id=1, managed=True,
            desired_revision_id='baseline', publication_generation=1, input_generation=1,
            target_generation=1, runtime_generation=1, snapshot=baseline))
    candidate = preparation.prepare('alice', 'six==1.17.0', 'independent-poller')
    store = Path('/tmp/proof/retained')
    # Independent process: no Flask, Celery or Redis, and no transaction held.
    command = [sys.executable, '-m', 'docassemble_executor.preparation_poller',
               '--store', str(store), '--once']
    completed = subprocess.run(command, capture_output=True, text=True, timeout=600)
    assert completed.returncode == 0, 'independent poller failed'
    assert completed.stdout.strip() == 'ready', 'candidate not ready'
    status = preparation.status('alice', candidate)
    assert not status['installation_requested'] and not status['approval_supported']
    result = status['preview']
    verify_retained(store / result['bundle']['digest'], result['bundle']['digest'])
    with engine.connect() as connection:
        assert connection.scalar(select(tables['environment_state'].c.desired_revision_id)) == 'baseline'
        assert not connection.execute(select(tables['installation_attempt'])).first()
        assert not connection.execute(select(tables['package_identity'])).first()
        assert not connection.execute(select(tables['preparation_worker'].c.exited)).scalar_one()
    (Path('/tmp/proof') / 'attestation.json').write_text(json.dumps(result, indent=2, sort_keys=True))
    print('PASS independent PostgreSQL preparation dispatch: pinned uv solve/build/retain -> fenced ready; no intent, runtime or installation mutation', flush=True)
    completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0 and completed.stdout.strip() == 'idle'
    print('PASS independent poller idle after readiness; worker exit not inferred from builder return', flush=True)
    with engine.begin() as connection:
        connection.execute(text('DROP SCHEMA poller_proof CASCADE'))
    engine.dispose()


if __name__ == '__main__':
    main()
