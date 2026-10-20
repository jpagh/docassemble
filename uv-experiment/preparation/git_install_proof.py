"""Pinned public HTTPS Git/subdirectory -> SQL approval -> cold frozen apply.

Disposable schema/venvs only. Public-source availability is an external prerequisite.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request

from sqlalchemy import create_engine, select, text

from docassemble.base.managed_records import preparation_tables
from docassemble.base.managed_preparation import digest, PreparationError
from docassemble.base.managed_git import parse_git_requirement
from docassemble_executor.registry_builder import RegistryBuilder, verify_retained
from frozen_apply_diagnostics import frozen_apply_failure

ROOT = Path(__file__).resolve().parents[2]
DECLARATION = 'flit_core @ git+https://github.com/pypa/flit.git@3.12.0#subdirectory=flit_core'


def main():
    spec = importlib.util.spec_from_file_location('git_proof_adapter', ROOT /
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
    schema = 'git_install_proof'
    admin = create_engine(os.environ['DA_PREPARATION_TEST_URL'])
    with admin.begin() as c:
        c.execute(text('DROP SCHEMA IF EXISTS ' + schema + ' CASCADE'))
        c.execute(text('CREATE SCHEMA ' + schema))
    engine = create_engine(os.environ['DA_PREPARATION_TEST_URL'], connect_args={'options': '-csearch_path=' + schema})
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
        with tempfile.TemporaryDirectory(prefix='git-install-proof-') as directory:
            root = Path(directory)
            candidate = service.prepare('2', DECLARATION, 'git')
            with engine.connect() as c:
                assert c.scalar(text('SELECT count(*) FROM proof_package')) == 0
                assert c.scalar(select(tables['environment_state'].c.desired_revision_id)) == 'baseline'
            claim = service.claim(candidate, lease_seconds=300)
            builder = RegistryBuilder(root / 'bundles', uv)
            result = builder.build(claim.inputs)
            source = result['git_sources']['flit-core']
            assert source == {'repository': 'https://github.com/pypa/flit.git', 'declared_ref': '3.12.0',
                'subdirectory': 'flit_core', 'ref_kind': 'tag', 'resolved_sha': '1c8141722066ffe1497e511982feeea0f091ddb6'}
            inventory = result['bundle']['inventory']
            assert inventory == {**core, 'flit-core': {'version': '3.12.0', 'source_digest': digest(source)}}
            assert result['desired_diff'] == result['runtime_diff'] == {
                'flit-core': {'before': None, 'after': inventory['flit-core']}}
            assert service.publish_ready(claim, result)
            status = service.status('2', candidate)
            assert status['approval_supported']
            published = service.approve('2', candidate, status['result_digest'], 'approve')
            bundle = root / 'bundles' / result['bundle']['digest']
            verify_retained(bundle, result['bundle']['digest'])
            with engine.connect() as c:
                revision = c.scalar(select(tables['revision'].c.snapshot).where(
                    tables['revision'].c.id == published['revision_id']))
                assert revision['git_sources'] == result['git_sources']
                assert revision['desired_inventory'] == inventory
            # A second, unrelated cold preparation re-fetches the recorded SHA, not the tag.
            again = dict(claim.inputs, baseline=revision, contributions=revision['contributions'],
                requirement={'declaration': 'packaging>=25', 'normalized_name': 'packaging'})
            again.pop('git_source')
            again.pop('replace_git_contributions')
            repeated = builder.build(again)
            assert repeated['git_sources'] == result['git_sources']
            assert repeated['bundle']['inventory'] == inventory
            assert repeated['desired_diff'] == {}
            assert repeated['runtime_diff'] == result['runtime_diff']
            targeted = dict(again, change_operation='update', requirement={'declaration': 'flit-core',
                'normalized_name': 'flit-core'})
            fixed = builder.build(targeted)
            assert fixed['git_sources'] == result['git_sources']
            assert fixed['bundle']['inventory'] == inventory and fixed['desired_diff'] == {}
            removal = dict(targeted, change_operation='remove',
                remove_contribution_id=revision['contributions'][0]['id'])
            removed = builder.build(removal)
            assert removed['git_sources'] == {} and removed['bundle']['inventory'] == core
            assert removed['desired_diff'] == {'flit-core': {'before': inventory['flit-core'], 'after': None}}
            commit_requirement, commit_source, _ = parse_git_requirement(DECLARATION.replace('@3.12.0', '@' + source['resolved_sha']))
            committed = builder.build(dict(claim.inputs, requirement=commit_requirement.__dict__, git_source=commit_source))
            assert committed['git_sources']['flit-core']['ref_kind'] == 'commit'
            assert committed['git_sources']['flit-core']['resolved_sha'] == source['resolved_sha']
            mismatch_requirement, mismatch_source, _ = parse_git_requirement(DECLARATION.replace('flit_core @', 'Wrong @'))
            try:
                builder.build(dict(claim.inputs, requirement=mismatch_requirement.__dict__, git_source=mismatch_source))
            except PreparationError as error:
                assert str(error) == 'built Git metadata identity mismatch'
            else:
                raise AssertionError('Git metadata name mismatch accepted')
            env = {'PATH': os.environ['PATH'], 'HOME': str(root), 'UV_PROJECT_ENVIRONMENT': str(root / 'runtime'),
                   'UV_CACHE_DIR': str(root / 'empty-cache'), 'UV_PYTHON_DOWNLOADS': 'never'}
            applied = subprocess.run([uv, 'sync', '--frozen', '--no-build', '--offline', '--no-config',
                '--python', sys.executable], cwd=bundle, env=env, text=True, capture_output=True)
            assert applied.returncode == 0, frozen_apply_failure(applied, 'cold apply failed')
            check = subprocess.check_output([str(root / 'runtime/bin/python'), '-I', '-c',
                'import flit_core, packaging; print(flit_core.__version__, packaging.__version__)'], text=True).strip()
            assert check == '3.12.0 25.0'
            print(json.dumps({'uv': '0.12.21', 'source': source, 'bundle': result['bundle']['digest'],
                'prepare_approve': published, 'exact_core': True, 'complete_diffs': True,
                'cold_git_subdirectory_build': True, 'cold_unrelated_sha_preservation': True,
                'fixed_tag_update': True, 'immutable_commit_build': True, 'metadata_mismatch_rejected': True,
                'git_removal': True,
                'cold_post_approval_frozen_apply': True, 'runtime_versions': check,
                'scope': 'disposable-venv-not-live-maintenance'}, sort_keys=True))
            print('PASS: public HTTPS Git subdirectory -> prepare/approve -> cold pinned frozen apply')
    finally:
        engine.dispose()
        with admin.begin() as c:
            c.execute(text('DROP SCHEMA ' + schema + ' CASCADE'))
        admin.dispose()


if __name__ == '__main__':
    main()
