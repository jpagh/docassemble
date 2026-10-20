"""Ticket 15 disposable fixture proof: exact retained applies, no solve, no old setup."""
import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
from unittest.mock import patch
import zipfile

from sqlalchemy import MetaData, Table, select, text, update

import core_upgrade_image_proof as core
from image_bridge_proof import ROOT, STORE, DESCRIPTOR, database, run
from docassemble.base.managed_preparation import PreparationError
from docassemble_executor.alembic_probe import read_database_revision
from docassemble_executor.config import database_settings, load_config
from docassemble_executor.installation import Installation, InstallationPoller
from docassemble_executor.inventory import inspect_environment
from docassemble_executor.journal import Journal
from docassemble_executor.paths import ExecutorPaths
from docassemble_executor.registry_builder import RegistryBuilder
from docassemble_executor.rollback_schema import retained_schema_policy
from docassemble_executor.selected_baseline import load_baselines


def release_with_negative_policy(paths, version):
    wheels = core.fixture_wheels(paths, version)
    wheel = wheels['docassemble-webapp']
    with zipfile.ZipFile(wheel) as archive:
        files = {name: archive.read(name) for name in archive.namelist() if not name.endswith('.dist-info/RECORD')}
    metadata = next(name for name in files if name.endswith('.dist-info/METADATA'))
    files[metadata] = files[metadata].replace(b'\n\n', b'\nDocassemble-Incompatible-Schema-Revision: fixture_incompatible_revision\n\n', 1)
    record_name = metadata.rsplit('/', 1)[0] + '/RECORD'
    record = io.StringIO()
    writer = csv.writer(record)
    for name, data in files.items():
        writer.writerow([name, 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('='), len(data)])
    writer.writerow([record_name, '', ''])
    files[record_name] = record.getvalue().encode()
    with zipfile.ZipFile(wheel, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    core.save('negative-schema-policy-source.json', {
        'fixture_only': True, 'wheel': wheel.name,
        'sha256': hashlib.sha256(wheel.read_bytes()).hexdigest(),
        'header': 'Docassemble-Incompatible-Schema-Revision: fixture_incompatible_revision'})
    return wheels


def main(action):
    paths = ExecutorPaths.from_env()
    if action == 'build':
        inputs = json.loads((ROOT / 'rollback-inputs.json').read_text())
        result = RegistryBuilder(STORE, image_baseline=DESCRIPTOR).build(inputs)
        (STORE / 'rollback-result.json').write_text(json.dumps(result))
        return
    if action in ('bootstrap', 'success'):
        original = core.fixture_wheels
        if action == 'success':
            # Only this synthetic fixture release authors the negative policy.
            def fixture(paths, version):
                with patch.object(core, 'fixture_wheels', original):
                    return release_with_negative_policy(paths, version)
            with patch.object(core, 'fixture_wheels', fixture):
                return core.main(action)
        return core.main(action)
    engine, prefix = database()
    installation = Installation(engine, lambda *_: False, prefix)
    poller = InstallationPoller(installation, paths, STORE, DESCRIPTOR)
    publisher = core.publisher_at(paths, engine, prefix)

    def state():
        with engine.connect() as connection:
            return dict(connection.execute(select(installation.tables['environment_state'])).mappings().one())

    def prepare(revision, key, assignments=None):
        candidate = publisher.prepare('1', None, key, operation='rollback', rollback_revision=revision,
                                      assign_owners=assignments)
        claim = publisher.claim(candidate, lease_seconds=300)
        core.save('rollback-inputs.json', claim.inputs)
        before_uv = Path('/var/log/docassemble-uv-invocations.log').read_text()
        run(['runuser', '-u', 'da-environment', '--', sys.executable, __file__, 'build'])
        result = json.loads((STORE / 'rollback-result.json').read_text())
        added = Path('/var/log/docassemble-uv-invocations.log').read_text()[len(before_uv):]
        assert '\tsync ' in added and '--frozen' in added and '--offline' in added and '--no-build' in added
        assert not any('\t' + op + ' ' in added for op in ('lock', 'build'))
        assert publisher.publish_ready(claim, result)
        return candidate, publisher.status('1', candidate), added

    if action == 'rollback':
        bootstrap = json.loads((ROOT / 'bootstrap.json').read_text())['publication']['revision_id']
        # Intervening user-package intent changes use the normal fresh solve and
        # publication pipeline around the selected 1.10.14 exact core.
        candidate = publisher.prepare('1', 'cowsay==6.1', 'intervening')
        claim = publisher.claim(candidate, lease_seconds=300)
        core.save('upgrade-inputs.json', claim.inputs)
        run(['runuser', '-u', 'da-environment', '--', sys.executable,
             '/repo/uv-experiment/preparation/core_upgrade_image_proof.py', 'build', 'seed'])
        assert publisher.publish_ready(claim, json.loads((STORE / 'upgrade-result.json').read_text()))
        publisher.approve('1', candidate, publisher.status('1', candidate)['result_digest'], 'intervening')
        assert poller.run_once() == 'reported'
        assert Journal.load(paths.journal_file).attempt_result['outcome'] == 'applied-normal'
        assert inspect_environment(paths.runtime_venv)['cowsay'] == '6.1'
        # Explicit disposable account/grant provisioning, then deletion; the
        # historical account/grants are never used by rollback publication.
        with publisher.authority_write() as connection:
            users = Table(prefix + 'user', MetaData(), autoload_with=connection)
            original = dict(connection.execute(select(users).where(users.c.id == 1)).mappings().one())
            original.update(id=9001, email='deleted-owner@example.invalid', social_id='local$rollback-deleted')
            connection.execute(users.insert().values(**original))
            identities = publisher.tables['package_identity']
            identity = connection.execute(select(identities).where(identities.c.normalized_name == 'sampleproject')).mappings().one()
            connection.execute(text('UPDATE ' + prefix + 'package_auth SET user_id=9001 WHERE package_id=:package'),
                               {'package': identity['legacy_package_id']})
            connection.execute(update(identities).where(identities.c.id == identity['id']).values(owner_id='9001'))
            connection.execute(text('DELETE FROM ' + prefix + 'package_auth WHERE user_id=9001'))
            connection.execute(users.delete().where(users.c.id == 9001))
            connection.execute(update(identities).where(identities.c.id == identity['id']).values(owner_id=None))
        before = state()
        # This disposable DB has unchanged compatible tables but an advanced
        # marker absent from the older retained wheel. It must be unproven, not
        # rejected merely for being newer, and old setup must never stamp it.
        with engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num='fixture_future_compatible'"))
        try:
            publisher.prepare('1', None, 'ownerless-denied', operation='rollback', rollback_revision=bootstrap)
        except PreparationError as error:
            assert 'explicit assignment' in str(error)
        else:
            raise AssertionError('ownerless rollback lacked explicit assignment')
        candidate, preview, preparation_uv = prepare(bootstrap, 'rollback-image', ['sampleproject'])
        assert preview['preview']['rollback']['requirements']['after'] == ['sampleproject==4.0.0']
        assert 'cowsay==6.1' in preview['preview']['rollback']['requirements']['before']
        try:
            publisher.approve('1', candidate, preview['result_digest'], 'no-ack')
        except PreparationError as error:
            assert 'acknowledgement' in str(error)
        else:
            raise AssertionError('rollback missing explicit database acknowledgement')
        core.save('rollback-preview.json', preview)
        schema_before = read_database_revision(database_settings(load_config(paths.config_file)))
        config_before = paths.config_file.read_bytes()
        response = publisher.approve('1', candidate, preview['result_digest'], 'rollback-image', acknowledge_database_unchanged=True)
        uv_before = Path('/var/log/docassemble-uv-invocations.log').read_text()
        with patch('docassemble_executor.published._create_tables', side_effect=AssertionError('older setup ran')):
            assert poller.run_once() == 'reported'
        journal = Journal.load(paths.journal_file)
        assert journal.attempt_result['outcome'] == 'applied-normal', journal.to_dict()
        assert inspect_environment(paths.runtime_venv) == {name: value['version'] for name, value in preview['preview']['bundle']['inventory'].items()}
        assert 'cowsay' not in inspect_environment(paths.runtime_venv)
        assert read_database_revision(database_settings(load_config(paths.config_file))) == schema_before
        assert paths.config_file.read_bytes() == config_before
        assert state()['snapshot']['schema_mode'] == 'unchanged'
        assert json.loads((paths.state_dir / 'rollback-schema-check.json').read_text())['check']['status'] == 'unproven'
        assert state()['snapshot']['core'] != before['snapshot']['core']
        assert load_baselines(paths)['selected_baseline'] is None
        with engine.connect() as connection:
            assert connection.scalar(text('SELECT count(*) FROM ' + prefix + '"user" WHERE id=9001')) == 0
            assert connection.scalar(text('SELECT user_id FROM ' + prefix + 'package_auth WHERE package_id=:package'),
                                     {'package': identity['legacy_package_id']}) == 1
        added = Path('/var/log/docassemble-uv-invocations.log').read_text()[len(uv_before):]
        assert len([line for line in added.splitlines() if '\tsync ' in line]) == 1
        assert not any('\t' + op + ' ' in added for op in ('lock', 'build'))
        core.save('rollback-applied.json', {'publication': response, 'journal': journal.to_dict(),
            'state': state(), 'schema_before_and_after': schema_before, 'preparation_uv': preparation_uv, 'apply_uv': added})
        core.save('rollback-before-restart.json', {'baselines': load_baselines(paths), 'schema': journal.to_dict()['schema'],
            'inventory': inspect_environment(paths.runtime_venv), 'uv_length': len(Path('/var/log/docassemble-uv-invocations.log').read_text()),
            'config_sha256': hashlib.sha256(config_before).hexdigest()})
        print('PASS post-upgrade exact image-core rollback: changed user intent, deleted owner assignment, DB/config unchanged, no solve/setup', flush=True)
        return
    if action == 'restart-rollback':
        before = json.loads((ROOT / 'rollback-before-restart.json').read_text())
        assert load_baselines(paths) == before['baselines']
        assert Journal.load(paths.journal_file).to_dict()['schema'] == before['schema']
        assert inspect_environment(paths.runtime_venv) == before['inventory']
        assert hashlib.sha256(paths.config_file.read_bytes()).hexdigest() == before['config_sha256']
        applied = json.loads((ROOT / 'rollback-applied.json').read_text())
        assert read_database_revision(database_settings(load_config(paths.config_file))) == applied['schema_before_and_after']
        assert json.loads(paths.status_file.read_text())['normal_ready']
        added = Path('/var/log/docassemble-uv-invocations.log').read_text()[before['uv_length']:]
        assert not any('\t' + op + ' ' in added for op in ('lock', 'build', 'sync'))
        core.save('restart-rollback.json', {'status': json.loads(paths.status_file.read_text()), 'uv_added': added})
        print('PASS same-image rollback restart retains unchanged schema mode; no setup/solve/sync', flush=True)
        return
    if action == 'incompatible':
        historical = json.loads((ROOT / 'success-publication.json').read_text())['response']['revision_id']
        candidate, preview, _ = prepare(historical, 'rollback-known-incompatible')
        directory = STORE / preview['preview']['bundle']['digest']
        assert retained_schema_policy(directory)['known_incompatible_revisions'] == ['fixture_incompatible_revision']
        # Verify missing retained material and a mismatching target fail closed
        # without creating or applying a replacement solve.
        inputs = json.loads((ROOT / 'rollback-inputs.json').read_text())
        bundle = inputs['rollback']['snapshot']['publication']['result']['bundle']
        lock = STORE / bundle['digest'] / 'uv.lock'
        saved_lock = lock.read_bytes()
        lock.unlink()
        try:
            run(['runuser', '-u', 'da-environment', '--', sys.executable, __file__, 'build'])
        except AssertionError:
            pass
        else:
            raise AssertionError('missing retained lock accepted')
        finally:
            lock.write_bytes(saved_lock)
            lock.chmod(0o600)
            shutil.chown(lock, user='da-environment', group='da-environment')
        inputs['rollback']['snapshot']['target'] = {'python': '3.13'}
        core.save('rollback-inputs.json', inputs)
        try:
            run(['runuser', '-u', 'da-environment', '--', sys.executable, __file__, 'build'])
        except AssertionError:
            pass
        else:
            raise AssertionError('incompatible target accepted')
        with engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num='fixture_incompatible_revision'"))
        response = publisher.approve('1', candidate, preview['result_digest'], 'incompatible', acknowledge_database_unchanged=True)
        uv_before = Path('/var/log/docassemble-uv-invocations.log').read_text()
        assert poller.run_once() == 'reported'
        terminal = Journal.load(paths.journal_file).attempt_result
        assert terminal['outcome'] == 'blocked' and not terminal['mutation_started'], terminal
        assert state()['desired_revision_id'] == response['revision_id']
        assert state()['snapshot']['selected_baseline']['release']['webapp'] == '1.10.14'
        assert publisher.publication_status('1', revision_id=response['revision_id'])['installation'] != 'verified-running'
        added = Path('/var/log/docassemble-uv-invocations.log').read_text()[len(uv_before):]
        assert not any('\t' + op + ' ' in added for op in ('sync', 'lock', 'build'))
        core.save('incompatible-blocked.json', {'terminal': terminal, 'state': state(), 'no_apply_uv': True, 'uv_added': added})
        print('PASS retained known-incompatible advanced DB blocked before mutation; desired/failure evidence retained', flush=True)
        return
    raise AssertionError(action)


if __name__ == '__main__':
    main(sys.argv[1])
