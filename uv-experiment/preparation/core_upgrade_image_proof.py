"""Ticket 14b disposable fixture releases: real uv/apply/schema/roles, no adoption.

PyPI's newest release equals this tree's 1.10.13. Explicit fixture catalogs
therefore expose repacked image wheels as coherent 1.10.14/1.10.15 releases.
Only fixture wheel inputs are injected; production selection, solving, retention,
publication, qualification, recovery and observed-DB clearance stay real.
"""
from docassemble.base.managed_python import python_site_packages
import base64
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
from unittest.mock import patch
import zipfile

from packaging.utils import canonicalize_name, parse_wheel_filename
from sqlalchemy import select

from docassemble.base.managed_preparation import PreparationError
from docassemble_executor.admission import Admission
from docassemble_executor.alembic_probe import read_database_revision
from docassemble_executor.config import database_settings, load_config
from docassemble_executor.installation import Installation, InstallationFailure, InstallationPoller, schema_clearance_file
from docassemble_executor.inventory import inspect_environment
from docassemble_executor.journal import Journal
from docassemble_executor.paths import ExecutorPaths
from docassemble_executor.registry_builder import RegistryBuilder, verify_retained
from docassemble_executor.release_catalog import PublicIndexCatalog
from docassemble_executor.selected_baseline import load_baselines
from image_bridge_proof import ROOT, STORE, DESCRIPTOR, database, run

TRIO = {'docassemble-base', 'docassemble-webapp', 'docassemble-demo'}


def save(name, value):
    (ROOT / name).write_text(json.dumps(value, sort_keys=True, indent=2, default=str))


def fixture_wheels(paths, version):
    """Change real METADATA/RECORD and versions, preserving current managed code.

    Runtime fixture wheels include the advanced Alembic revision already in this
    disposable database. This is selected-release metadata, not a schema bypass.
    """
    destination = ROOT / ('fixture-release-' + version)
    destination.mkdir(exist_ok=True)
    wheels = {}
    for wheel in (DESCRIPTOR.parent / 'wheels').glob('*.whl'):
        name, old_version, *_ = parse_wheel_filename(wheel.name)
        name = canonicalize_name(name)
        if name not in TRIO:
            wheels[name] = wheel
            continue
        source = paths.bundles_dir / 'runtime-r1' / 'wheels' / wheel.name
        output = destination / wheel.name.replace(str(old_version), version)
        with zipfile.ZipFile(source) as archive:
            files = {}
            for member in archive.namelist():
                if member.endswith('/') or member.endswith('.dist-info/RECORD'):
                    continue
                data = archive.read(member)
                target = member.replace('-' + str(old_version) + '.dist-info/', '-' + version + '.dist-info/')
                if member.endswith('.dist-info/METADATA'):
                    data = data.replace(('Version: ' + str(old_version) + '\n').encode(), ('Version: ' + version + '\n').encode())
                    for project in ('base', 'demo'):
                        data = data.replace(('docassemble.' + project + '==' + str(old_version)).encode(),
                                            ('docassemble.' + project + '==' + version).encode())
                    if name == 'docassemble-webapp':
                        # This disposable topology serves through Gunicorn. The
                        # image project pins it outside upstream Requires-Dist;
                        # the synthetic release must declare its own tool need.
                        data = data.replace(b'\n\n', b'\nRequires-Dist: gunicorn==23.0.0\n\n', 1)
                files[target] = data
        record_name = next(name.rsplit('/', 1)[0] + '/RECORD' for name in files if name.endswith('.dist-info/METADATA'))
        record = io.StringIO()
        writer = csv.writer(record)
        for name, data in files.items():
            writer.writerow([name, 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('='), len(data)])
        writer.writerow([record_name, '', ''])
        files[record_name] = record.getvalue().encode()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        wheels[canonicalize_name(parse_wheel_filename(output.name)[0])] = output
    assert TRIO <= wheels.keys()
    save('fixture-release-' + version + '.json', {
        'fixture_only': True, 'not_a_published_registry_release': True,
        'reason': 'PyPI latest and image both 1.10.13; no newer real release available',
        'version': version, 'wheels': {name: {'filename': wheel.name,
            'sha256': hashlib.sha256(wheel.read_bytes()).hexdigest()} for name, wheel in wheels.items()}})
    return wheels


def build(version):
    inputs = json.loads((ROOT / 'upgrade-inputs.json').read_text())
    if version == 'seed':
        result = RegistryBuilder(STORE, image_baseline=DESCRIPTOR).build(inputs)
        (STORE / 'upgrade-result.json').write_text(json.dumps(result))
        return
    wheels = {canonicalize_name(parse_wheel_filename(p.name)[0]): p for p in
              (DESCRIPTOR.parent / 'wheels').glob('*.whl')}
    wheels.update({canonicalize_name(parse_wheel_filename(p.name)[0]): p for p in
                   (ROOT / ('fixture-release-' + version)).glob('*.whl')})
    webapp = wheels['docassemble-webapp']
    def fetch(url, headers):
        if url.endswith('/docassemble-webapp/'):
            return json.dumps({'files': [{'filename': webapp.name, 'url': 'https://fixture.invalid/' + webapp.name}]}).encode()
        assert url == 'https://fixture.invalid/' + webapp.name
        return webapp.read_bytes()
    catalog = PublicIndexCatalog('https://fixture.invalid', fetch=fetch)

    class FixtureReleaseBuilder(RegistryBuilder):
        def _solve(self, inputs, **kwargs):
            # Inject fixtures only into the fresh core solve. The full solve
            # uses its verified image_wheels without duplicate local bindings.
            assert kwargs['operation'] in ('upgrade-core', 'upgrade')
            if kwargs['operation'] == 'upgrade-core':
                kwargs['local_artifacts'] = {name: ({'filename': wheel.name,
                    'version': str(parse_wheel_filename(wheel.name)[1]),
                    'digest': hashlib.sha256(wheel.read_bytes()).hexdigest()}, wheel)
                    for name, wheel in wheels.items()}
            return super()._solve(inputs, **kwargs)
    result = FixtureReleaseBuilder(STORE, image_baseline=DESCRIPTOR, release_catalog=catalog).build(inputs)
    (STORE / 'upgrade-result.json').write_text(json.dumps(result))


def publisher_at(paths, engine, prefix):
    # Root is only the publication driver, never a writer of runtime bytecode.
    # Match the existing registry image proof's independent adapter loading.
    sys.dont_write_bytecode = True
    import docassemble.base
    docassemble.base.__path__.append(str(paths.runtime_venv / python_site_packages() / 'docassemble/base'))
    location = paths.runtime_venv / python_site_packages() / 'docassemble/webapp/packages/managed.py'
    spec = importlib.util.spec_from_file_location('core_upgrade_image_adapter', location)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    baseline = json.loads(DESCRIPTOR.read_text())['baseline']
    return adapter.QualifiedPublication(engine, adapter.PreviewAuthorizer(prefix,
        lambda: {'allowed': {}, 'allow_updates': True, 'developer_can_install': True}),
        adapter.PackageProjection(prefix), prefix, baseline)


def main(action):
    paths = ExecutorPaths.from_env()
    if action == 'build':
        return build(sys.argv[-1])
    engine, prefix = database()
    installation = Installation(engine, lambda *_: False, prefix)
    poller = InstallationPoller(installation, paths, STORE, DESCRIPTOR)

    def state():
        with engine.connect() as connection:
            return dict(connection.execute(select(installation.tables['environment_state'])).mappings().one())

    if action.startswith('restart-'):
        label = action.removeprefix('restart-')
        before = json.loads((ROOT / (label + '-before-restart.json')).read_text())
        journal = Journal.load(paths.journal_file)
        assert load_baselines(paths) == before['baselines']
        assert journal.to_dict()['schema'] == before['journal']['schema']
        assert journal.clearance == before['journal']['clearance']
        assert state()['snapshot']['selected_baseline'] == before['baselines']['selected_baseline']
        assert inspect_environment(paths.runtime_venv) == before['inventory']
        added = Path('/var/log/docassemble-uv-invocations.log').read_text()[before['uv_length']:]
        assert not any('\t' + op + ' ' in added for op in ('sync', 'lock', 'build'))
        status = json.loads(paths.status_file.read_text())
        assert status['normal_ready'] is (label == 'success'), status
        save(action + '.json', {'baselines': load_baselines(paths), 'journal': journal.to_dict(),
                               'status': status, 'uv_added': added})
        print('PASS ' + action + ': selected/recovery baselines and schema evidence survive; no solve/sync/setup replay', flush=True)
        return

    publisher = publisher_at(paths, engine, prefix)
    if action == 'bootstrap':
        candidate = publisher.prepare('1', 'sampleproject==4.0.0', 'bootstrap')
        claim = publisher.claim(candidate, lease_seconds=300)
        save('upgrade-inputs.json', claim.inputs)
        run(['runuser', '-u', 'da-environment', '--', sys.executable, __file__, 'build', 'seed'])
        result = json.loads((STORE / 'upgrade-result.json').read_text())
        assert publisher.publish_ready(claim, result)
        preview = publisher.status('1', candidate)
        response = publisher.approve('1', candidate, preview['result_digest'], 'bootstrap-approve')
        assert poller.run_once() == 'reported'
        journal = Journal.load(paths.journal_file)
        assert journal.attempt_result['outcome'] == 'applied-normal', journal.to_dict()
        save('bootstrap.json', {'publication': response, 'journal': journal.to_dict()})
        print('PASS explicit image baseline plus user intent: real uv publication and normal all-role apply', flush=True)
        return
    version = '1.10.14' if action == 'success' else '1.10.15'
    before = state()
    fixture_wheels(paths, version)
    candidate = publisher.prepare('1', None, 'upgrade-' + version, operation='upgrade',
                                  release_selection={'mode': 'exact', 'version': version})
    claim = publisher.claim(candidate, lease_seconds=300)
    save('upgrade-inputs.json', claim.inputs)
    run(['runuser', '-u', 'da-environment', '--', sys.executable, __file__, 'build', version])
    result = json.loads((STORE / 'upgrade-result.json').read_text())
    assert publisher.publish_ready(claim, result)
    preview = publisher.status('1', candidate)
    assert preview['approval_supported'] and not preview['stale'], preview
    save(action + '-preview.json', preview)
    for kind in ('bundle', 'core_bundle'):
        bundle = result[kind]
        directory = STORE / bundle['digest']
        verify_retained(directory, bundle['digest'])
        evidence = ROOT / (action + '-' + kind)
        evidence.mkdir(exist_ok=True)
        for filename in ('manifest.json', 'pyproject.toml', 'uv.lock', 'inventory.json', 'solve.lock', 'solve-project.toml'):
            shutil.copyfile(directory / filename, evidence / filename)
    response = publisher.approve('1', candidate, preview['result_digest'], 'approve-' + version)
    selected = {key: result[key] for key in ('core_bundle', 'release')}
    published = state()
    assert published['snapshot']['selected_baseline'] == selected
    assert published['snapshot']['core'] == selected['core_bundle']['inventory'] != before['snapshot']['core']
    prior = published['snapshot']['recovery_baseline']
    assert prior.get('core_bundle', {}).get('inventory', prior.get('core')) == before['snapshot']['core']
    save(action + '-publication.json', {'response': response, 'snapshot': published['snapshot']})
    uv_before = Path('/var/log/docassemble-uv-invocations.log').read_text()
    from docassemble_executor.published import _create_tables, record_published
    stages = []

    def schema(context):
        journal = Journal.load(paths.journal_file)
        assert journal.schema_uncertain and journal.schema.attempt_id == response['attempt_id']
        assert Admission(paths.state_dir).state()['mode'] != 'open'
        assert load_baselines(paths)['recovery_baseline'] == prior
        assert inspect_environment(paths.runtime_venv)['docassemble-webapp'] == version
        save(action + '-schema-start.json', journal.to_dict())
        check = _create_tables(context)
        assert check.ok, check
        stages.append('real-schema-setup-completed')
        if action == 'failure':
            raise RuntimeError('fixture interruption after real setup, before schema success journal')
        return check

    def record(paths, claim, receipt):
        journal = Journal.load(paths.journal_file)
        assert journal.schema_succeeded and receipt['readiness']['schema_verified']
        assert len(receipt['readiness']['roles']) == 12
        assert load_baselines(paths)['recovery_baseline'] == prior
        save('success-before-promotion.json', {'journal': journal.to_dict(), 'receipt': receipt,
                                              'baselines': load_baselines(paths)})
        stages.append('real-schema-and-all-role-health-before-promotion')
        return record_published(paths, claim, receipt)

    report = installation.report_terminal
    def lost_report(receipt):
        raise RuntimeError('fixture terminal reporting outage')
    installation.report_terminal = lost_report
    with patch('docassemble_executor.published._create_tables', schema), patch('docassemble_executor.published.record_published', record):
        try:
            poller.run_once()
        except RuntimeError as error:
            assert str(error) == 'fixture terminal reporting outage', error
        else:
            raise AssertionError('missing terminal reporting outage')
    terminal = Journal.load(paths.journal_file)
    save(action + '-terminal-before-report.json', terminal.to_dict())
    assert state()['desired_revision_id'] == response['revision_id']
    assert state()['active_attempt_id'] == response['attempt_id']
    installed = inspect_environment(paths.runtime_venv)
    if action == 'success':
        assert terminal.schema_succeeded and terminal.attempt_result['outcome'] == 'applied-normal'
        assert load_baselines(paths)['recovery_baseline'] == selected
        assert installed == {n: v['version'] for n, v in result['bundle']['inventory'].items()}
        assert len(stages) == 2
    else:
        assert terminal.schema_uncertain and terminal.recovery.attempt == 1
        assert terminal.recovery.result == 'succeeded', terminal.to_dict()
        assert terminal.attempt_result['outcome'] == 'failed-stopped'
        assert load_baselines(paths)['selected_baseline'] == selected
        assert load_baselines(paths)['recovery_baseline'] == prior
        assert installed == {n: v['version'] for n, v in prior['core_bundle']['inventory'].items()}
        assert installed['docassemble-webapp'] == '1.10.14'
        assert json.loads(paths.intent_file.read_text())['revision'] == response['revision_id']
    added = Path('/var/log/docassemble-uv-invocations.log').read_text()[len(uv_before):]
    syncs = [line for line in added.splitlines() if '\tsync ' in line]
    assert len(syncs) == (1 if action == 'success' else 2), syncs
    assert all('--frozen' in line and '--no-build' in line for line in syncs)
    if action == 'failure':
        assert '--reinstall' in syncs[1]
    (ROOT / (action + '-apply-uv.log')).write_text(added)
    installation.report_terminal = report
    assert poller.run_once() == 'reported'
    assert poller.run_once() == 'replayed'
    assert Path('/var/log/docassemble-uv-invocations.log').read_text()[len(uv_before):] == added
    status = publisher.publication_status('1', attempt_id=response['attempt_id'])
    save(action + '-reported-status.json', status)
    if action == 'failure':
        # 13.2: a completed, child-safe core recovery makes the retained revision exactly
        # retryable. Eligibility is evidence, not clearance: the environment is still
        # unmaterialized for the desired revision and the schema stays uncertain until the
        # operator clearance asserted below is recorded.
        assert status['retry_supported'] and status['desired_revision_id'] == response['revision_id']
        assert status['installation'] == 'failed' and status['terminal_outcome'] == 'failed-stopped'
        assert status['last_recovery_result'] == 'succeeded' and status['recovery_core']
        assert status['materialized_revision_id'] is None and status['verified_running_revision_id'] is None
        # Retry authority is still bounded: it is never granted without an explicit
        # idempotency key, which is validated before any durable state changes.
        try:
            publisher.retry('1', response['revision_id'], '')
        except PreparationError as error:
            assert 'idempotency key required' in str(error), error
        else:
            raise AssertionError('retry accepted without an idempotency key')
        journal = Journal.load(paths.journal_file)
        observed = read_database_revision(database_settings(load_config(paths.config_file)))
        assert observed
        clearance = {'format': 1, 'attempt_id': journal.schema.attempt_id,
                     'schema_identity': journal.schema.identity, 'observed_schema': observed}
        for document in (None, dict(clearance, observed_schema='wrong-schema'), dict(clearance, attempt_id='wrong-attempt')):
            if document is not None:
                schema_clearance_file(paths).write_text(json.dumps(document))
                schema_clearance_file(paths).chmod(0o600)
            try:
                poller._require_schema_clearance(Journal.load(paths.journal_file))
            except InstallationFailure as error:
                assert error.reason == 'schema-clearance-required'
            else:
                raise AssertionError('missing/stale/foreign operator clearance accepted')
        schema_clearance_file(paths).write_text(json.dumps(clearance))
        schema_clearance_file(paths).chmod(0o600)
        poller._require_schema_clearance(Journal.load(paths.journal_file))
        journal = Journal.load(paths.journal_file)
        assert journal.schema_cleared and journal.schema_uncertain
        save('operator-clearance.json', {'observed_database_revision': observed, 'journal': journal.to_dict(),
            'retry_eligible_before_clearance': True, 'missing_stale_foreign_clearance_denied': True})
        assert poller.run_once() == 'replayed'  # clearance is not an apply/retry request
        assert Path('/var/log/docassemble-uv-invocations.log').read_text()[len(uv_before):] == added
    else:
        # 13.2: only a coherently recovered failure is retry-eligible; a normally
        # verified apply is not a retry target.
        assert not status['retry_supported'] and status['desired_revision_id'] == response['revision_id']
    save(action + '-before-restart.json', {'baselines': load_baselines(paths),
        'journal': Journal.load(paths.journal_file).to_dict(), 'inventory': installed,
        'uv_length': len(Path('/var/log/docassemble-uv-invocations.log').read_text())})
    print('PASS ' + action + ': fixture coherent ' + version + '; real non-root uv/schema/roles; publication, promotion/recovery and lost-report replay checked', flush=True)


if __name__ == '__main__':
    main(sys.argv[1])
