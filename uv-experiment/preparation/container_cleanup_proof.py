"""Host Docker + disposable PostgreSQL proof; never a production database."""
from docassemble.base.managed_python import python_target
import json
import os
from pathlib import Path
import urllib.request

from sqlalchemy import create_engine, select, text, update

from docassemble_executor.preparation import Preparation
from docassemble_executor.preparation_containers import ContainerWorkers
from docassemble_executor.preparation_poller import PreparationPoller
from docassemble_executor.registry_builder import RegistryBuilder, verify_retained


def main():
    root = Path(os.environ['PROOF_ROOT']).resolve()
    repository = Path(__file__).resolve().parents[2]
    image = os.environ.get('PROOF_IMAGE', 'da-uv-full-maintenance:local')
    engine = create_engine(os.environ['DA_PREPARATION_TEST_URL'])
    with engine.begin() as connection:
        connection.execute(text('DROP SCHEMA IF EXISTS container_cleanup_proof CASCADE'))
        connection.execute(text('CREATE SCHEMA container_cleanup_proof'))
    engine.dispose()
    engine = create_engine(os.environ['DA_PREPARATION_TEST_URL'],
                           connect_args={'options': '-csearch_path=container_cleanup_proof'})
    preparation = Preparation(engine, lambda *_: True)
    tables = preparation.tables
    tables['environment_state'].metadata.create_all(engine)
    with urllib.request.urlopen('https://pypi.org/pypi/packaging/25.0/json') as response:
        metadata = json.load(response)
    source_hash = next(v['digests']['sha256'] for v in metadata['urls'] if v['packagetype'] == 'sdist')
    core = {'packaging': {'version': '25.0', 'source_digest': source_hash}}
    baseline = {'core': core, 'target': python_target(), 'tool': {'uv': '0.12.21'},
                'config': {'index_url': 'https://pypi.org/simple'}, 'desired_inventory': core,
                'runtime': {'kind': 'normal', 'inventory': core}}
    with engine.begin() as connection:
        connection.execute(tables['revision'].insert().values(id='baseline', snapshot=baseline))
        connection.execute(tables['environment_state'].insert().values(id=1, managed=True,
            desired_revision_id='baseline', publication_generation=1, input_generation=1,
            target_generation=1, runtime_generation=1, snapshot=baseline))
    material_root = root / 'material'
    material_root.mkdir(mode=0o700)
    store = root / 'retained'
    workers = ContainerWorkers(preparation, root / 'receipts', repository,
                               image, store)
    candidate = preparation.prepare('alice', 'six==1.17.0', 'container-build')
    poller = PreparationPoller(preparation, RegistryBuilder(store), containers=workers,
                               material_root=material_root)
    assert poller.run_once() == 'ready'
    result = preparation.status('alice', candidate)['preview']
    verify_retained(store / result['bundle']['digest'], result['bundle']['digest'])
    with engine.connect() as connection:
        assert connection.scalar(select(tables['preparation_worker'].c.exited))
    print('PASS real pinned-uv container dispatch: retained ready bytes; namespace destroyed before worker exit', flush=True)

    candidate = preparation.prepare('alice', 'six==1.17.0', 'orphan')
    first = preparation.claim(candidate)
    directory = workers.receipts / first.worker_id
    directory.mkdir(mode=0o700)
    container = workers._docker('create', '--user', '1000:1000', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges', '--restart', 'no',
        '--label', 'da.preparation.worker=' + first.worker_id,
        '--entrypoint', 'sh', image, '-c', 'sleep 300 & wait')
    workers._write(directory / 'receipt.json', {'container': container,
                   'worker': first.worker_id, 'candidate': candidate,
                   'daemon': workers._docker('info', '--format', '{{.ID}}')})
    workers._docker('start', container)
    top = workers._docker('top', container)
    assert 'sleep 300' in top
    with engine.begin() as connection:
        connection.execute(update(tables['candidate']).where(tables['candidate'].c.id == candidate).values(
            claim_until=text("clock_timestamp() - interval '1 second'")))
    second = preparation.claim(candidate)
    secret = 'DISPOSABLE_SECRET_TOKEN'
    values = [f'https://user:{secret}@example.invalid/repo',
              f'https://{secret}@example.invalid/repo',
              f'https://example.invalid/repo?token={secret}']
    material_ids = [preparation.stage_candidate_secret(second, material_root, value.encode())
                    for value in values]
    for material_id, value in zip(material_ids, values):
        assert preparation.read_candidate_secret(second, material_root, material_id) == value.encode()
        assert (material_root / material_id).stat().st_mode & 0o777 == 0o600
    print('PASS restricted candidate-secret staging and claim-scoped consumption: userinfo/token/query fixtures only; no source enabled', flush=True)
    preparation.cancel('alice', candidate)
    preparation.worker_exited(second.worker_id)  # Fixture worker has never launched OS work.
    workers.reconcile()  # Orphan remains live even though its lease expired.
    assert preparation.reconcile_material(material_root) == 0
    assert all((material_root / identity).exists() for identity in material_ids)
    assert all(not preparation.material_authorized(second, identity) for identity in material_ids)
    assert not preparation.material_authorized(first, material_ids[0])
    assert secret not in str(preparation.status('alice', candidate))
    print('PASS live orphan with build descendant blocks deletion after lease reclaim and newer worker exit; status redacted', flush=True)
    workers._docker('kill', container)
    workers.reconcile()
    assert container not in workers._docker('ps', '-aq', '--no-trunc').splitlines()
    assert preparation.reconcile_material(material_root) == 3
    assert not any((material_root / identity).exists() for identity in material_ids)
    assert preparation.reconcile_material(material_root) == 0
    assert not preparation.cleanup_plan(candidate)
    print('PASS trusted daemon orphan reconciliation destroys private PID namespace; ALL-worker exit gates physical deletion; retry idempotent', flush=True)
    with engine.connect() as connection:
        assert connection.scalar(select(tables['environment_state'].c.desired_revision_id)) == 'baseline'
        assert not connection.execute(select(tables['installation_attempt'])).first()
    (root / 'attestation.json').write_text(json.dumps(result, indent=2, sort_keys=True))
    with engine.begin() as connection:
        connection.execute(text('DROP SCHEMA container_cleanup_proof CASCADE'))
    engine.dispose()


if __name__ == '__main__':
    main()
