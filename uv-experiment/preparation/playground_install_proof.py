"""Real Playground generation -> SQL prepare/approve -> pinned frozen apply.

Only disposable schemas, files and venvs are modified; no host runtime services.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request

from sqlalchemy import create_engine, select, text

from docassemble.base.managed_records import preparation_tables
from docassemble_executor.registry_builder import RegistryBuilder, verify_retained
from playground_fixture import ROOT, generator, package_info, author_info


def main():
    spec = importlib.util.spec_from_file_location('playground_proof_adapter', ROOT /
        'docassemble_webapp/docassemble/webapp/packages/managed.py')
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    uv = shutil.which('uv')
    assert subprocess.check_output([uv, '--version'], text=True).split()[:2] == ['uv', '0.12.21']
    with urllib.request.urlopen('https://pypi.org/pypi/packaging/25.0/json', timeout=60) as response:
        metadata = json.load(response)
    core_hash = next(row['digests']['sha256'] for row in metadata['urls'] if row['packagetype'] == 'sdist')
    core = {'packaging': {'version': '25.0', 'source_digest': core_hash}}
    baseline = {'core': core, 'target': {'python': '3.12'}, 'tool': {'uv': '0.12.21'},
        'config': {'index_url': 'https://pypi.org/simple'}, 'desired_inventory': core,
        'runtime': {'kind': 'normal', 'inventory': core}}
    url = os.environ['DA_PREPARATION_TEST_URL']
    schema = 'playground_install_proof'
    admin = create_engine(url)
    with admin.begin() as c:
        c.execute(text('DROP SCHEMA IF EXISTS ' + schema + ' CASCADE'))
        c.execute(text('CREATE SCHEMA ' + schema))
    engine = create_engine(url, connect_args={'options': '-csearch_path=' + schema})
    tables = preparation_tables('proof_')
    try:
        with engine.begin() as c:
            tables['revision'].metadata.create_all(c)
            for statement in (
                'CREATE TABLE proof_user (id integer PRIMARY KEY, active boolean, social_id text)',
                'CREATE TABLE proof_role (id integer PRIMARY KEY, name text)',
                'CREATE TABLE proof_user_roles (user_id integer, role_id integer)',
                'CREATE TABLE proof_package (id serial PRIMARY KEY, name text, type text, core boolean, '
                'active boolean, dependency boolean, limitation text)',
                'CREATE TABLE proof_package_auth (package_id integer, user_id integer, authtype text)',
                "INSERT INTO proof_user VALUES (2,true,'admin')",
                "INSERT INTO proof_role VALUES (2,'admin')",
                'INSERT INTO proof_user_roles VALUES (2,2)',
            ):
                c.execute(text(statement))
            c.execute(tables['revision'].insert().values(id='baseline', snapshot=baseline))
            c.execute(tables['environment_state'].insert().values(id=1, managed=True,
                desired_revision_id='baseline', publication_generation=1, input_generation=1,
                target_generation=1, runtime_generation=1, snapshot=baseline))
        policy = {'allowed': {}, 'allow_updates': True, 'developer_can_install': True}
        service = adapter.QualifiedPublication(engine, adapter.PreviewAuthorizer('proof_', lambda: policy),
            adapter.PackageProjection('proof_'), 'proof_', baseline)
        with tempfile.TemporaryDirectory(prefix='playground-install-proof-') as directory:
            root = Path(directory)
            store = root / 'artifacts'
            store.mkdir(mode=0o700)
            previous_artifact_root = os.environ.get('DA_EXECUTOR_ARTIFACT_ROOT')
            # This standalone proof prepares and publishes from one disposable store.
            os.environ['DA_EXECUTOR_ARTIFACT_ROOT'] = str(store)
            try:
                generated = generator(root / 'playground')
                with generated['make_package_zip']('Example', package_info(dependencies=['packaging==25.0']),
                        author_info(), 'UTC', managed=True) as archive:
                    candidate = service.prepare_zip('2', Path(archive.name), 'playground', artifact_root=store,
                                                    declaration='docassemble.Example')
                with engine.connect() as c:
                    assert c.scalar(text('SELECT count(*) FROM proof_package')) == 0
                    assert c.scalar(select(tables['environment_state'].c.desired_revision_id)) == 'baseline'
                claim = service.claim(candidate, lease_seconds=300)
                builder = RegistryBuilder(root / 'bundles', uv, artifact_root=store)
                result = builder.build(claim.inputs)
                assert result['bundle']['inventory']['packaging'] == core['packaging']
                assert result['desired_diff'] == result['runtime_diff'] == {'docassemble-example': {
                    'before': None, 'after': result['bundle']['inventory']['docassemble-example']}}
                assert service.publish_ready(claim, result)
                status = service.status('2', candidate)
                assert status['approval_supported']
                published = service.approve('2', candidate, status['result_digest'], 'approve')
            finally:
                if previous_artifact_root is None:
                    os.environ.pop('DA_EXECUTOR_ARTIFACT_ROOT', None)
                else:
                    os.environ['DA_EXECUTOR_ARTIFACT_ROOT'] = previous_artifact_root
            bundle = root / 'bundles' / result['bundle']['digest']
            verify_retained(bundle, result['bundle']['digest'])
            with engine.connect() as c:
                revision = c.execute(select(tables['revision']).where(
                    tables['revision'].c.id == published['revision_id'])).mappings().one()
                assert revision['snapshot']['desired_inventory'] == result['bundle']['inventory']
                assert c.scalar(select(tables['candidate_material'].c.committed))
            # A fresh, post-approval frozen apply from the published bundle only.
            # Builder's caches/venvs and the generated temporary ZIP are gone.
            env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(root / 'runtime'),
                UV_CACHE_DIR=str(root / 'empty-cache'), UV_PYTHON_DOWNLOADS='never')
            applied = subprocess.run([uv, 'sync', '--frozen', '--no-build', '--offline', '--no-config',
                '--python', '3.12'], cwd=bundle, env=env, text=True, capture_output=True)
            assert applied.returncode == 0, applied.stderr
            check = subprocess.check_output([str(root / 'runtime/bin/python'), '-I', '-c',
                'import docassemble.Example, packaging; print(docassemble.Example.__version__, packaging.__version__)'],
                text=True).strip()
            assert check == '1.0 25.0'
            evidence = {'uv': '0.12.21', 'generated_playground': True, 'prepare_approve': published,
                'bundle': result['bundle']['digest'], 'artifact': claim.inputs['artifact']['digest'],
                'exact_core': True, 'no_early_intent': True, 'cold_post_approval_frozen_apply': True,
                'runtime_versions': check, 'scope': 'disposable-venv-not-live-maintenance'}
            print(json.dumps(evidence, sort_keys=True))
            print('PASS: Playground generation -> prepare/approve -> cold pinned frozen apply')
    finally:
        engine.dispose()
        with admin.begin() as c:
            c.execute(text('DROP SCHEMA ' + schema + ' CASCADE'))
        admin.dispose()


if __name__ == '__main__':
    main()
