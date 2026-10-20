"""Real replacement images share durable DB/state, never the runtime venv/cache."""
import json
import copy
import subprocess
from pathlib import Path
import shutil
import sys

from sqlalchemy import select

from docassemble.base.managed_migration import SYSTEM_ACTOR, POLICY
from docassemble_executor.alembic_probe import read_database_revision
from docassemble_executor.config import database_settings, load_config
from docassemble_executor.inventory import inspect_environment
from docassemble_executor.journal import Journal
from docassemble_executor.paths import ExecutorPaths
from docassemble_executor.selected_baseline import load_baselines
from image_bridge_proof import database, STORE, DESCRIPTOR

ROOT = Path('/var/lib/docassemble/migration-proof')


def main(action, label):
    ROOT.mkdir(exist_ok=True)
    if action == 'conflict-build':
        from docassemble_executor.registry_builder import RegistryBuilder
        from docassemble.base.managed_preparation import PreparationError
        inputs = json.loads((ROOT / 'conflict-inputs.json').read_text())
        try:
            RegistryBuilder(STORE, image_baseline=DESCRIPTOR).build(inputs)
        except PreparationError as error:
            assert str(error) == 'registry preparation command failed', str(error)
            print('PASS real pinned-uv refuses preserved conflicting declarations; no runtime/intent mutation', flush=True)
            return
        raise AssertionError('conflicting declared requirements were silently relaxed')
    paths = ExecutorPaths.from_env()
    engine, prefix = database()
    from docassemble_executor.installation import Installation
    service = Installation(engine, lambda *_: False, prefix)
    with engine.begin() as connection:
        state = dict(connection.execute(select(service.tables['environment_state'])).mappings().one())
    journal = Journal.load(paths.journal_file)
    schema = read_database_revision(database_settings(load_config(paths.config_file)))
    uv = Path('/var/log/docassemble-uv-invocations.log').read_text()
    snapshot = state['snapshot']
    status = json.loads(paths.status_file.read_text())
    document = {'state': state, 'journal': journal.to_dict(), 'baselines': load_baselines(paths),
        'inventory': inspect_environment(paths.runtime_venv), 'database_revision': schema,
        'uv_length': len(uv), 'status': status}
    if action == 'conflict':
        inputs = copy.deepcopy(snapshot['publication']['inputs'])
        inputs['contributions'].append({'id': 'conflict', 'declaration': 'sampleproject<4', 'artifact_ref': None})
        (ROOT / 'conflict-inputs.json').write_text(json.dumps(inputs))
        result = subprocess.run(['runuser', '-u', 'da-environment', '--', sys.executable, __file__, 'conflict-build', label],
                                capture_output=True, text=True, timeout=900)
        assert result.returncode == 0, result.stdout + result.stderr
        with engine.begin() as connection:
            after = dict(connection.execute(select(service.tables['environment_state'])).mappings().one())
        assert after == state
        print(result.stdout, end='', flush=True)
        (ROOT / ('conflict-' + label + '.json')).write_text(json.dumps({'desired_unchanged': True,
            'revision_id': state['desired_revision_id'], 'conflict_preserved': 'sampleproject==4.0.0 and sampleproject<4'}))
        return
    if action == 'capture':
        (ROOT / (label + '.json')).write_text(json.dumps(document, sort_keys=True, indent=2))
        return
    before = json.loads((ROOT / (label + '.json')).read_text())
    if action == 'restart':
        added = uv[before['uv_length']:]
        assert not any('\t' + op + ' ' in added for op in ('sync', 'lock', 'build')), added
        assert state['desired_revision_id'] == before['state']['desired_revision_id']
        assert journal.to_dict()['schema'] == before['journal']['schema']
        assert journal.schema_release == before['journal']['schema_release']
        assert status['normal_ready'], status
    else:
        old = before['state']['snapshot']
        assert snapshot['publication']['system_actor'] == SYSTEM_ACTOR
        assert snapshot['publication']['enabling_policy'] == POLICY
        assert 'approver_id' not in snapshot['publication']
        assert state['desired_revision_id'] != before['state']['desired_revision_id']
        assert state['publication_generation'] == before['state']['publication_generation'] + 1
        assert snapshot['contributions'] == old['contributions']
        assert snapshot['image_baseline'] == json.loads(DESCRIPTOR.read_text())['baseline']['image_baseline']
        assert snapshot['core'] == json.loads(DESCRIPTOR.read_text())['baseline']['core']
        assert snapshot['recovery_baseline'] == {'image_baseline': snapshot['image_baseline'], 'core': snapshot['core']}
        assert journal.attempt_result['outcome'] == 'applied-normal', journal.to_dict()
        assert status['normal_ready'] and state['active_attempt_id'] is None, status
        assert document['inventory']['sampleproject'] == before['inventory']['sampleproject'] == '4.0.0'
        assert document['database_revision'] == before['database_revision']
        if action == 'older':
            assert snapshot['schema_mode'] == 'unchanged'
            assert not (paths.log_dir / 'create-tables.log').exists(), 'older setup ran'
            assert journal.to_dict()['schema'] == before['journal']['schema']
            assert journal.schema_release == before['journal']['schema_release']
        else:
            assert snapshot['schema_mode'] == 'upgrade'
            assert (paths.log_dir / 'create-tables.log').exists(), 'forward setup absent'
            assert journal.schema_succeeded
            # The release is the coherent core of the provisioned replacement image, not
            # a literal: the documented pair is built with --build-arg RELEASE, and
            # published.record_schema_success derives the journal release the same way.
            from docassemble.base.managed_migration import release_version
            assert journal.schema_release['release'] == release_version(
                json.loads(DESCRIPTOR.read_text())['baseline']['core'])
        bundle = snapshot['publication']['result']['bundle']
        directory = ROOT / (action + '-bundle')
        directory.mkdir(exist_ok=True)
        for filename in ('manifest.json', 'inventory.json', 'tool.json', 'solve.lock', 'solve-project.toml', 'uv.lock', 'pyproject.toml'):
            shutil.copyfile(STORE / bundle['digest'] / filename, directory / filename)
        assert bundle['artifacts']
        assert snapshot['publication']['result']['desired_diff']
    (ROOT / (action + '-' + label + '.json')).write_text(json.dumps(document, sort_keys=True, indent=2))
    print('PASS ' + action + ': ' + label + ', pinned uv, immutable system publication, schema policy, all-role health', flush=True)


if __name__ == '__main__':
    main(*sys.argv[1:])
