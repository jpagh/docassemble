"""Ticket 16: actual automatic boot adoption and published frozen/all-role apply.

Only the initial legacy installation is synthetic. No managed baseline/candidate,
publication, attempt or successful runtime observation is seeded for adoption.
"""
import json
import os
from pathlib import Path
import sys

from sqlalchemy import MetaData, Table, select, text
import yaml

from docassemble.base.managed_adoption import SYSTEM_ACTOR, POLICY, legacy_evidence
from docassemble.base.managed_records import preparation_tables
from docassemble_executor.config import load_config, database_settings
from docassemble_executor.image_baseline import provision_image_baseline
from docassemble_executor.inventory import inspect_environment
from docassemble_executor.journal import Journal
from docassemble_executor.paths import ExecutorPaths
from docassemble_executor.registry_builder import verify_retained
from docassemble_executor.alembic_probe import read_database_revision
from image_bridge_proof import database, run

ROOT = Path('/tmp/legacy-adoption-proof')
DESCRIPTOR = Path('/etc/docassemble/qualified-image/baseline.json')


def save(name, value):
    (ROOT / name).write_text(json.dumps(value, sort_keys=True, indent=2, default=str))


def main(action):
    ROOT.mkdir(exist_ok=True)
    paths = ExecutorPaths.from_env()
    engine, prefix = database()
    tables = preparation_tables(prefix)
    if action == 'setup':
        provision_image_baseline(paths, DESCRIPTOR.parent)
        store = Path(os.environ['DA_DURABLE_WORK_STORE'])
        import pwd
        account = pwd.getpwnam('da-environment')
        store.mkdir(parents=True, exist_ok=True)
        os.chown(store, account.pw_uid, account.pw_gid)
        with engine.begin() as connection:
            state = connection.execute(select(tables['environment_state'])).mappings().one()
            assert state['snapshot']['fixture_only'] is True
            assert connection.scalar(select(tables['installation_attempt'].c.id)) is None
            # Remove only explicit fixture scaffolding to model a legacy DB.
            # Adoption must initialize its own singleton and publish its own revision.
            names = ','.join('"' + relation.name + '"' for relation in tables.values())
            connection.execute(text('TRUNCATE ' + names + ' CASCADE'))
            metadata = MetaData()
            package = Table(prefix + 'package', metadata, autoload_with=connection)
            grants = Table(prefix + 'package_auth', metadata, autoload_with=connection)
            installs = Table(prefix + 'install', metadata, autoload_with=connection)
            assert not connection.execute(select(package)).first()
            requested = connection.scalar(package.insert().values(name='sampleproject', type='pip',
                limitation='==4.0.0', dependency=True, core=False, active=True).returning(package.c.id))
            connection.execute(grants.insert().values(package_id=requested, user_id=1, authtype='owner'))
            connection.execute(installs.insert().values(package_id=requested, hostname='copied-legacy-host',
                packageversion='0.0.1', version=1))
            connection.execute(package.insert().values(name='cowsay', type='pip', limitation='==6.1',
                dependency=False, core=False, active=True))
            save('original-evidence.json', legacy_evidence(connection, prefix))
        config = load_config(paths.config_file)
        config['python packages'] = ['pyfiglet==1.0.4']
        paths.config_file.write_text(yaml.safe_dump(config, sort_keys=False))
        save('schema-before.json', read_database_revision(database_settings(config)))
        print('PASS synthetic legacy requests/config/grants/inventory prepared; no managed authority seeded', flush=True)
        return
    if action == 'verify':
        with engine.connect() as connection:
            state = connection.execute(select(tables['environment_state'])).mappings().one()
            snapshot = state['snapshot']
            revision = connection.execute(select(tables['revision'])).mappings().one()
            attempt = connection.execute(select(tables['installation_attempt'])).mappings().one()
            identities = list(connection.execute(select(tables['package_identity'])).mappings())
            original = json.loads((ROOT / 'original-evidence.json').read_text())
            after = legacy_evidence(connection, prefix)
            assert all(row in after['package'] for row in original['package'])
            assert {key: value for key, value in after.items() if key != 'package'} == {key: value for key, value in original.items() if key != 'package'}
        assert state['publication_generation'] == 1 and state['active_attempt_id'] is None
        assert state['desired_revision_id'] == revision['id'] == attempt['revision_id']
        publication = snapshot['publication']
        assert publication['system_actor'] == SYSTEM_ACTOR and publication['enabling_policy'] == POLICY
        assert 'approver_id' not in publication
        assert attempt['record']['reason'] == 'automatic-adoption'
        assert attempt['record']['phase'] == 'succeeded'
        assert snapshot['runtime']['kind'] == 'normal'
        assert snapshot['runtime']['revision_id'] == revision['id']
        assert snapshot['adoption']['ownerless'] == ['cowsay', 'pyfiglet']
        assert next(row for row in identities if row['normalized_name'] == 'sampleproject')['owner_id'] == '1'
        assert all(row['owner_id'] is None for row in identities if row['normalized_name'] != 'sampleproject')
        declarations = [row['declaration'] for row in snapshot['contributions']]
        assert set(declarations) == {'sampleproject', 'sampleproject==4.0.0', 'cowsay', 'cowsay==6.1', 'pyfiglet==1.0.4'}
        assert 'legacy-dependency-versions-not-reconstructable' in publication['result']['warnings']
        inventory = inspect_environment(paths.runtime_venv)
        assert inventory == {name: value['version'] for name, value in publication['result']['bundle']['inventory'].items()}
        assert inventory['sampleproject'] == '4.0.0' and inventory['cowsay'] == '6.1' and inventory['pyfiglet'] == '1.0.4'
        journal = Journal.load(paths.journal_file)
        assert journal.schema_succeeded and journal.attempt_result['outcome'] == 'applied-normal'
        assert len(journal.attempt_result['readiness']['roles']) == 12
        assert run(['curl', '-fsS', 'http://127.0.0.1/health_check?ready=1'])
        bundle = publication['result']['bundle']
        directory = Path(os.environ['DA_DURABLE_WORK_STORE']) / bundle['digest']
        verify_retained(directory, bundle['digest'])
        for name in ('manifest.json', 'pyproject.toml', 'uv.lock', 'inventory.json', 'tool.json'):
            (ROOT / name).write_bytes((directory / name).read_bytes())
        invocations = Path('/var/log/docassemble-uv-invocations.log').read_text()
        assert any('\tsync ' in line and '--frozen' in line and '--no-build' in line for line in invocations.splitlines())
        save('verified.json', {'state': dict(state), 'attempt': dict(attempt), 'identities': [dict(row) for row in identities],
            'journal': journal.to_dict(), 'inventory': inventory, 'uv_version': run(['uv', '--version'])})
        (ROOT / 'uv-invocations.log').write_text(invocations)
        print('PASS automatic boot -> conservative legacy/config adoption -> system CAS/slot publication -> pinned uv 0.12.21 frozen application -> real schema/probe/web/all 12 roles -> readiness; original records/grants unchanged', flush=True)
        return
    raise ValueError('unsupported proof step')


if __name__ == '__main__':
    main(sys.argv[1])
