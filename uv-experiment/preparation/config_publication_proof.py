"""Standalone pinned-uv/real-PostgreSQL config publication proof, no live install.

Uses an isolated schema and disposable builder venvs. The first pending slot is
released by the proof fixture, NOT by reporting a successful installation.
"""
from docassemble.base.managed_python import python_target
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import urllib.request

from sqlalchemy import create_engine, select, text, update

from docassemble.base.managed_records import preparation_tables
from docassemble_executor.registry_builder import RegistryBuilder, verify_retained

ROOT = Path(__file__).resolve().parents[2]


def main():
    spec = importlib.util.spec_from_file_location('config_proof_adapter', ROOT /
        'docassemble_webapp/docassemble/webapp/packages/managed.py')
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    # Fetch the public source identity before opening any publication transaction.
    with urllib.request.urlopen('https://pypi.org/pypi/packaging/25.0/json', timeout=60) as response:
        metadata = json.load(response)
    core_hash = next(row['digests']['sha256'] for row in metadata['urls'] if row['packagetype'] == 'sdist')
    core = {'packaging': {'version': '25.0', 'source_digest': core_hash}}
    baseline = {'core': core, 'target': python_target(), 'tool': {'uv': '0.12.21'},
                'config': {'index_url': 'https://pypi.org/simple'}, 'desired_inventory': core,
                'runtime': {'kind': 'normal', 'inventory': core}}
    url = os.environ['DA_PREPARATION_TEST_URL']
    schema = 'config_publication_proof'
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text('DROP SCHEMA IF EXISTS ' + schema + ' CASCADE'))
        connection.execute(text('CREATE SCHEMA ' + schema))
    engine = create_engine(url, connect_args={'options': '-csearch_path=' + schema})
    tables = preparation_tables('proof_')
    evidence = {'uv': '0.12.21', 'live_installation': False, 'publications': []}
    try:
        with engine.begin() as connection:
            tables['revision'].metadata.create_all(connection)
            for statement in (
                'CREATE TABLE proof_user (id integer PRIMARY KEY, active boolean, social_id text)',
                'CREATE TABLE proof_role (id integer PRIMARY KEY, name text)',
                'CREATE TABLE proof_user_roles (user_id integer, role_id integer)',
                'CREATE TABLE proof_package (id serial PRIMARY KEY, name text, type text, core boolean, '
                'active boolean, dependency boolean, limitation text)',
                'CREATE TABLE proof_package_auth (package_id integer, user_id integer, authtype text)',
                "INSERT INTO proof_user VALUES (1,true,'interactive'),(2,true,'admin')",
                "INSERT INTO proof_role VALUES (1,'developer'),(2,'admin')",
                'INSERT INTO proof_user_roles VALUES (1,1),(2,2)',
                "INSERT INTO proof_package (name,type,core,active,dependency,limitation) "
                "VALUES ('idna','pip',false,true,false,'==3.10')",
                "INSERT INTO proof_package_auth VALUES (1,1,'owner')",
            ):
                connection.execute(text(statement))
            connection.execute(tables['revision'].insert().values(id='baseline', snapshot=baseline))
            connection.execute(tables['environment_state'].insert().values(id=1, managed=True,
                desired_revision_id='baseline', publication_generation=1, input_generation=1,
                target_generation=1, runtime_generation=1, snapshot=baseline))
            connection.execute(tables['package_identity'].insert().values(id='interactive-idna',
                normalized_name='idna', display_name='idna', legacy_package_id=1, owner_id='1'))
            connection.execute(tables['requirement_contribution'].insert().values(id='interactive-requirement',
                identity_id='interactive-idna', origin='interactive', declaration='idna==3.10', active=True))
        policy = {'allowed': {}, 'allow_updates': True, 'developer_can_install': True}
        service = adapter.QualifiedPublication(engine, adapter.PreviewAuthorizer('proof_', lambda: policy),
            adapter.PackageProjection('proof_'), 'proof_', baseline)
        def interactive_state():
            with engine.connect() as connection:
                return {
                    'package': dict(connection.execute(text('SELECT * FROM proof_package WHERE id=1')).mappings().one()),
                    'grants': [dict(row) for row in connection.execute(text('SELECT * FROM proof_package_auth')).mappings()],
                    'identity': dict(connection.execute(select(tables['package_identity']).where(
                        tables['package_identity'].c.id == 'interactive-idna')).mappings().one()),
                    'contribution': dict(connection.execute(select(tables['requirement_contribution']).where(
                        tables['requirement_contribution'].c.id == 'interactive-requirement')).mappings().one())}
        interactive = interactive_state()
        evidence['interactive_before'] = interactive
        with tempfile.TemporaryDirectory(prefix='config-publication-proof-') as directory:
            builder = RegistryBuilder(Path(directory), shutil.which('uv'))
            previous = None
            for version in ('1.16.0', '1.17.0'):
                observation = service.observe_config({'python packages': ['six==' + version]}, environ={})
                candidate = service.prepare('2', None, 'prepare-' + version, operation='config')
                claim = service.claim(candidate, lease_seconds=300)
                # Real solve/build/cold frozen sync, outside any publication transaction.
                result = builder.build(claim.inputs)
                verify_retained(Path(directory) / result['bundle']['digest'], result['bundle']['digest'])
                assert result['bundle']['inventory']['six']['version'] == version
                assert result['bundle']['inventory']['idna']['version'] == '3.10'
                assert result['bundle']['inventory']['packaging'] == core['packaging']
                if previous:
                    assert result['desired_diff']['six']['before']['version'] == '1.16.0'
                    assert result['desired_diff']['six']['after']['version'] == '1.17.0'
                    assert 'idna' not in result['desired_diff']
                assert service.publish_ready(claim, result)
                status = service.status('2', candidate)
                assert status['approval_supported'] and status['config_drift']['pending']
                published = service.approve('2', candidate, status['result_digest'], 'approve-' + version)
                assert service.approve('2', candidate, status['result_digest'], 'approve-' + version) == published
                with engine.connect() as connection:
                    revision = dict(connection.execute(select(tables['revision']).where(
                        tables['revision'].c.id == published['revision_id'])).mappings().one())
                    environment = dict(connection.execute(select(tables['environment_state'])).mappings().one())
                    config_identity = dict(connection.execute(select(tables['package_identity']).where(
                        tables['package_identity'].c.normalized_name == 'six')).mappings().one())
                    config_package = dict(connection.execute(text('SELECT * FROM proof_package WHERE id=:id'),
                        {'id': config_identity['legacy_package_id']}).mappings().one())
                assert config_identity['owner_id'] is None
                assert config_package['active'] is False and config_package['limitation'] is None
                assert environment['active_attempt_id'] == published['attempt_id']
                assert environment['desired_revision_id'] == revision['id']
                assert revision['snapshot']['observed_config_generation'] == observation['generation']
                assert revision['snapshot']['desired_inventory'] == result['bundle']['inventory']
                assert revision['snapshot']['publication']['result'] == result
                assert revision['snapshot']['observed_config'] == claim.inputs['config_import']['snapshot']
                drift = service.publication_status('2', revision_id=revision['id'])['config_drift']
                assert drift['pending'] is False
                assert interactive_state() == interactive
                evidence['publications'].append({'publication': published, 'revision': revision,
                    'config_package': config_package, 'config_identity': config_identity, 'drift': drift})
                previous = published
                if version == '1.16.0':
                    # Test-fixture-only slot release; no runtime success is asserted.
                    with service.authority_write() as connection:
                        connection.execute(update(tables['environment_state']).values(active_attempt_id=None))
            evidence['interactive_after'] = interactive_state()
            evidence['revision_count'] = 3
            evidence['attempt_count'] = 2
            with engine.connect() as connection:
                assert len(list(connection.execute(select(tables['revision'])))) == 3
                assert len(list(connection.execute(select(tables['installation_attempt'])))) == 2
            # A subsequent observation is new drift and does not rewrite the revision.
            service.observe_config({'python packages': ['six==1.16.0']}, environ={})
            evidence['later_drift'] = service.publication_status('2', revision_id=previous['revision_id'])['config_drift']
            assert evidence['later_drift'] == {'pending': True, 'observed_generation': 3, 'committed_generation': 2}
        print(json.dumps(evidence, indent=2, sort_keys=True))
        print('PASS: pinned uv config 1.16.0 -> 1.17.0, atomic PostgreSQL publication, interactive state unchanged')
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text('DROP SCHEMA ' + schema + ' CASCADE'))
        admin.dispose()


if __name__ == '__main__':
    main()
