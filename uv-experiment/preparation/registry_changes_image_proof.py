"""Ticket 06 real module -> pinned uv -> normal all-role apply, fixture only."""
import importlib.util
import json
import os
import shutil
import sys
from unittest.mock import patch

from sqlalchemy import select, text

from docassemble_executor.installation import Installation, InstallationPoller
from docassemble_executor.inventory import inspect_environment
from docassemble_executor.journal import Journal
from docassemble_executor.paths import ExecutorPaths
from docassemble_executor.registry_builder import RegistryBuilder, verify_retained
from docassemble.base.managed_preparation import parse_registry_requirement
from image_bridge_proof import ROOT, STORE, DESCRIPTOR, database, preparation_command, run


def save(name, value):
    (ROOT / name).write_text(json.dumps(value, sort_keys=True, indent=2, default=str))


def seed_build():
    # A historical fixture solve selects old versions while retaining the original
    # broad declarations. Only fixture setup uses these version preferences.
    class HistoricalFixtureBuilder(RegistryBuilder):
        def _run(self, arguments, directory, cache, venv):
            if arguments == ['lock', '--no-config']:
                arguments = arguments + ['--upgrade-package', 'sampleproject==3.0.0',
                                         '--upgrade-package', 'boltons==23.0.0']
            return super()._run(arguments, directory, cache, venv)
    inputs = json.loads((ROOT / 'seed-inputs.json').read_text())
    result = HistoricalFixtureBuilder(STORE, image_baseline=DESCRIPTOR).build(inputs)
    (STORE / 'seed-result.json').write_text(json.dumps(result))


def dependency_movement_build():
    """Small public fixture qualifies necessary movement without changing image core."""
    class HistoricalDependencyBuilder(RegistryBuilder):
        def _run(self, arguments, directory, cache, venv):
            if arguments == ['lock', '--no-config']:
                arguments = arguments + ['--upgrade-package', 'httpx==0.24.1',
                                         '--upgrade-package', 'boltons==23.0.0']
            return super()._run(arguments, directory, cache, venv)
    store = STORE / 'dependency-movement'
    inputs = {'baseline': {'target': {'python': '3.12'}, 'tool': {'uv': '0.12.21'},
              'config': {'index_url': 'https://pypi.org/simple'}, 'core': {}, 'desired_inventory': {},
              'runtime': {'kind': 'normal', 'inventory': {}}},
              'contributions': [{'id': 'unrelated', 'declaration': 'boltons>=20'},
                                {'id': 'core-seed', 'declaration': 'six==1.17.0'}],
              'requirement': parse_registry_requirement('httpx>=0.24').__dict__,
              'ownership_effects': {'owner_id': '1', 'existing': True, 'normalized_name': 'httpx'}}
    seed = HistoricalDependencyBuilder(store).build(inputs)
    before = seed['bundle']['inventory']
    inputs['baseline'].update(core={'six': before['six']}, desired_inventory=before,
        runtime={'kind': 'normal', 'inventory': before}, publication={'result': seed})
    inputs['contributions'].append({'id': 'selected', 'declaration': 'httpx>=0.24'})
    inputs.update(change_operation='update', requirement=parse_registry_requirement('HTTPX').__dict__)
    result = RegistryBuilder(store).build(inputs)
    after = result['bundle']['inventory']
    assert before['httpx']['version'] == '0.24.1' and after['httpx']['version'] != '0.24.1'
    assert before['httpcore'] != after['httpcore'], 'selected update must permit required dependency movement'
    assert after['boltons'] == before['boltons'] and after['six'] == before['six']
    assert result['desired_diff'] == result['runtime_diff']
    assert set(result['desired_diff']) == {n for n in set(before) | set(after) if before.get(n) != after.get(n)}
    evidence = STORE / 'dependency-movement-evidence'
    evidence.mkdir()
    for label, prepared in (('before', seed), ('after', result)):
        (evidence / (label + '.json')).write_text(json.dumps(prepared, sort_keys=True, indent=2))
        retained = store / prepared['bundle']['digest']
        for filename in ('manifest.json', 'solve.lock', 'solve-project.toml', 'uv.lock', 'pyproject.toml'):
            shutil.copyfile(retained / filename, evidence / (label + '-' + filename))


def main():
    paths = ExecutorPaths.from_env()
    engine, prefix = database()
    # Load only the image-installed shared publisher/adapter, not Flask or the
    # runtime's dependencies. The executor wheel normally needs preparation only.
    sys.dont_write_bytecode = True
    import docassemble.base
    docassemble.base.__path__.append(str(paths.runtime_venv / 'lib/python3.12/site-packages/docassemble/base'))
    location = paths.runtime_venv / 'lib/python3.12/site-packages/docassemble/webapp/packages/managed.py'
    spec = importlib.util.spec_from_file_location('registry_image_adapter', location)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    baseline = json.loads(DESCRIPTOR.read_text())['baseline']
    publisher = adapter.QualifiedPublication(engine, adapter.PreviewAuthorizer(prefix,
        lambda: {'allowed': {}, 'allow_updates': True, 'developer_can_install': True}),
        adapter.PackageProjection(prefix), prefix, baseline)
    tables = publisher.tables
    # Multiple contributions precede any configuration UI/import authority.
    with publisher.authority_write() as c:
        for name, origin, declaration in [('boltons', 'config', 'boltons>=20'),
                                          ('peppercorn', 'interactive', 'peppercorn>=0.6')]:
            package = c.scalar(text(f'INSERT INTO {prefix}package (name,type,active,core,dependency) VALUES (:name,\'pip\',true,false,false) RETURNING id'), {'name': name})
            c.execute(text(f'INSERT INTO {prefix}package_auth (package_id,user_id,authtype) VALUES (:package,1,\'owner\')'), {'package': package})
            c.execute(tables['package_identity'].insert().values(id=name, normalized_name=name,
                display_name=name, owner_id='1', legacy_package_id=package))
            c.execute(tables['requirement_contribution'].insert().values(id=name, identity_id=name,
                origin=origin, declaration=declaration, active=True))
    os.environ['DA_PREPARATION_DATABASE_URL'] = engine.url.render_as_string(hide_password=False)
    command = preparation_command()
    installation = Installation(engine, lambda *_: False, prefix)
    poller = InstallationPoller(installation, paths, STORE, DESCRIPTOR)

    def state():
        with engine.connect() as c:
            return {name: [dict(row) for row in c.execute(select(tables[name])).mappings()]
                    for name in ('environment_state', 'requirement_contribution', 'package_identity', 'revision')}

    def grants():
        with engine.connect() as c:
            return [dict(row) for row in c.execute(text(f'SELECT * FROM {prefix}package_auth ORDER BY id')).mappings()]

    def prepare(name, key, operation='add', contribution_id=None, historical=False):
        identity = publisher.prepare('1', name, key, operation=operation, contribution_id=contribution_id)
        if historical:
            claim = publisher.claim(identity, lease_seconds=300)
            save('seed-inputs.json', claim.inputs)
            run(['runuser', '-u', 'da-environment', '--', sys.executable, __file__, 'seed-build'])
            assert publisher.publish_ready(claim, json.loads((STORE / 'seed-result.json').read_text()))
        else:
            assert run(command).strip() == 'ready'
        preview = publisher.status('1', identity)
        assert preview['approval_supported'], preview
        save(key + '-preview.json', preview)
        digest = preview['preview']['bundle']['digest']
        directory = STORE / digest
        verify_retained(directory, digest)
        evidence = ROOT / (key + '-bundle')
        evidence.mkdir()
        for filename in ('manifest.json', 'solve.lock', 'solve-project.toml', 'uv.lock', 'pyproject.toml', 'inventory.json'):
            shutil.copyfile(directory / filename, evidence / filename)
        return identity, preview

    def approve(candidate, key, apply=True):
        identity, preview = candidate
        response = publisher.approve('1', identity, preview['result_digest'], key)
        save(key + '-publication.json', response)
        if apply:
            assert poller.run_once() == 'reported'
            journal = Journal.load(paths.journal_file)
            assert journal.attempt_result['outcome'] == 'applied-normal', journal.to_dict()
            assert inspect_environment(paths.runtime_venv) == {n: v['version'] for n, v in preview['preview']['bundle']['inventory'].items()}
            save(key + '-terminal.json', journal.to_dict())
        return response

    first = prepare('sampleproject>=2', 'historical-fixture', historical=True)
    initial = first[1]['preview']['bundle']['inventory']
    assert initial['sampleproject']['version'] == '3.0.0' and initial['boltons']['version'] == '23.0.0'
    approve(first, 'first')
    print('PASS explicit historical fixture: broad original declarations, old selected pins, exact image core, normal all-role frozen apply', flush=True)
    before = state()
    current_grants = grants()
    updated = prepare('SampleProject', 'targeted-update', 'update')
    selected = updated[1]['preview']['bundle']['inventory']
    assert selected['sampleproject']['version'] == '4.0.0'
    assert selected['boltons'] == initial['boltons']
    assert set(updated[1]['preview']['desired_diff']) == {'sampleproject'}
    assert all(selected[name] == pin for name, pin in baseline['core'].items())
    approve(updated, 'update')
    assert state()['requirement_contribution'] == before['requirement_contribution']
    assert state()['package_identity'] == before['package_identity'] and grants() == current_grants
    print('PASS targeted Update advances only selected pin, prefers unrelated boltons 23 despite newer releases, unchanged original requirements/grants/core; normal frozen apply', flush=True)

    # Actual uv conflict, not a synthetic worker attestation.
    before_conflict = state()
    inventory = inspect_environment(paths.runtime_venv)
    failed_candidate = publisher.prepare('1', 'sampleproject<2', 'conflict')
    assert run(command).strip() == 'failed'
    assert publisher.status('1', failed_candidate)['state'] == 'failed'
    assert state() == before_conflict and inspect_environment(paths.runtime_venv) == inventory
    print('PASS conflicting requirements fail before approval without desired/runtime/ownership mutation', flush=True)

    removed = prepare('PepperCorn', 'remove-explicit', 'remove', 'peppercorn')
    assert removed[1]['preview']['bundle']['inventory'] == selected
    assert removed[1]['preview']['desired_diff'] == {}
    approve(removed, 'remove-explicit')
    assert inspect_environment(paths.runtime_venv)['peppercorn'] == selected['peppercorn']['version']
    assert grants() == current_grants
    assert not next(row for row in state()['requirement_contribution'] if row['id'] == 'peppercorn')['active']
    print('PASS Removal deactivates only selected interactive contribution; still-required peppercorn retained transitively; config boltons and grants retained; normal frozen apply', flush=True)

    contribution = next(row['id'] for row in state()['requirement_contribution'] if row['origin'] == 'interactive' and row['active'])
    pruned = prepare('sampleproject', 'remove-last', 'remove', contribution)
    assert set(pruned[1]['preview']['desired_diff']) == {'sampleproject', 'peppercorn'}
    approve(pruned, 'remove-last')
    inventory = inspect_environment(paths.runtime_venv)
    assert 'sampleproject' not in inventory and 'peppercorn' not in inventory and inventory['boltons'] == '23.0.0'
    assert grants() == current_grants
    print('PASS Removal of last requiring contribution prunes only unneeded packages through frozen apply, never legacy uninstall', flush=True)

    # A real non-root uv failure after publishing an Update preserves new intent.
    failed = prepare('boltons', 'failed-update', 'update')
    published = approve(failed, 'failed-update', apply=False)
    intent_before_failure = state()
    from docassemble_executor.installation import sync_frozen
    failed_sync_results = []
    def denied_sync(*args, **kwargs):
        # Deny actual package writes, not only uv's advisory lock file (some
        # repeated-sync paths can proceed without reopening that file).
        packages = paths.runtime_venv / 'lib/python3.12/site-packages'
        original = packages.stat()
        os.chown(packages, 0, 0)
        packages.chmod(0o555)
        try:
            result = sync_frozen(*args, **kwargs)
            assert not result.ok and 'Permission denied' in result.output_tail, result
            failed_sync_results.append(result)
            return result
        finally:
            os.chown(packages, original.st_uid, original.st_gid)
            packages.chmod(original.st_mode & 0o777)
    with patch('docassemble_executor.installation.sync_frozen', denied_sync):
        assert poller.run_once() == 'reported'
    assert len(failed_sync_results) == 1, 'failure injection must execute the actual uv invocation'
    journal = Journal.load(paths.journal_file)
    assert journal.attempt_result['outcome'] == 'failed-stopped'
    status = publisher.publication_status('1', attempt_id=published['attempt_id'])
    assert status['installation'] == 'failed' and status['desired_revision_id'] == published['revision_id']
    after = state()
    for name in ('requirement_contribution', 'package_identity', 'revision'):
        assert after[name] == intent_before_failure[name]
    assert grants() == current_grants
    save('failed-update-terminal.json', journal.to_dict())
    save('failed-update-status.json', status)
    save('final-db.json', after)
    shutil.copyfile('/var/log/docassemble-uv-invocations.log', ROOT / 'registry-uv-invocations.log')
    print('PASS failed actual frozen Update apply preserves published desired/requirements/current grants; reports failed with separate stopped core recovery, never repair loop', flush=True)
    run(['runuser', '-u', 'da-environment', '--', sys.executable, __file__, 'dependency-movement'])
    shutil.copytree(STORE / 'dependency-movement-evidence', ROOT / 'dependency-movement')
    print('PASS pinned uv targeted Update permits necessary httpcore movement, retains unrelated boltons and exact six core, and previews every resulting change', flush=True)


if __name__ == '__main__':
    if sys.argv[-1] == 'seed-build':
        seed_build()
    elif sys.argv[-1] == 'dependency-movement':
        dependency_movement_build()
    else:
        main()
