"""Selected core publication gates, health qualification and recovery promotion."""
from docassemble.base.managed_python import python_target, python_requires
import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from docassemble_executor.bundles import sha256_file
from docassemble_executor.installation import InstallationClaim, InstallationFailure, InstallationPoller
from docassemble_executor.journal import Journal
from docassemble_executor.paths import ExecutorPaths
from docassemble_executor.preparation import digest
from docassemble_executor.selected_baseline import load_baselines, retain_selection, selected_wheels


def retained_bundle(store, inventory):
    staging = store / 'staging'
    staging.mkdir(parents=True)
    for name, value in {'pyproject.toml': '[project]\nrequires-python = ' + json.dumps(python_requires()),
                        'uv.lock': 'requires-python = ' + json.dumps(python_requires()),
                        'inventory.json': json.dumps(inventory), 'installed-material.json': '{}',
                        'tool.json': json.dumps({'uv': '0.12.21', 'target': python_target()})}.items():
        (staging / name).write_text(value)
    (staging / 'wheels').mkdir()
    for name in inventory:
        (staging / 'wheels' / (name.replace('-', '_') + '-2.0-py3-none-any.whl')).write_bytes(b'retained wheel')
    manifest = {path.relative_to(staging).as_posix(): sha256_file(path)
                for path in staging.rglob('*') if path.is_file()}
    bundle_digest = digest(manifest)
    (staging / 'manifest.json').write_text(json.dumps(manifest))
    directory = store / bundle_digest
    staging.rename(directory)
    return {'digest': bundle_digest, 'project_digest': sha256_file(directory / 'pyproject.toml'),
            'lock_digest': sha256_file(directory / 'uv.lock'), 'inventory': inventory, 'artifacts': []}


@pytest.fixture
def upgrade(tmp_path, monkeypatch):
    paths = replace(ExecutorPaths.from_env(), state_dir=tmp_path / 'state',
                    config_file=tmp_path / 'config.yml', journal_file=tmp_path / 'state/journal.json',
                    intent_file=tmp_path / 'state/desired.json', applied_file=tmp_path / 'state/applied.json',
                    run_dir=tmp_path / 'run', log_dir=tmp_path / 'log', cache_dir=tmp_path / 'cache',
                    status_file=tmp_path / 'run/status.json', repair_request_file=tmp_path / 'run/requests/repair',
                    manifest_file=tmp_path / 'manifest.json')
    paths.config_file.write_text('{}')
    inventory = {'docassemble-' + name: {'version': '2.0', 'source_digest': str(i) * 64}
                 for i, name in enumerate(('base', 'webapp', 'demo'), 1)}
    bundle = retained_bundle(tmp_path / 'store', inventory)
    release = {'webapp': '2.0', 'base': '2.0', 'demo': '2.0', 'resolved': '2.0',
               'selection': {'mode': 'exact', 'version': '2.0'}, 'core_bundle': bundle['digest']}
    image = {'core': {'old-core': {'version': '1.0', 'source_digest': 'a' * 64}},
             'tool': {'uv': '0.12.21'}, 'target': python_target(),
             'image_baseline': {'image_target': 'image'}, 'config': {'index_url': 'https://pypi.org/simple'}}
    selected = {'core_bundle': bundle, 'release': release}
    snapshot = dict(image, core=inventory, selected_baseline=selected,
                    recovery_baseline={'image_baseline': image['image_baseline'], 'core': image['core']},
                    publication={'inputs': {'change_operation': 'upgrade', 'baseline': image, 'ownership_effects': {}},
                                 'result': {'bundle': bundle, **selected}})
    claim = InstallationClaim('attempt', 'revision', 1, 'host', 2, snapshot)
    baseline = tmp_path / 'baseline.json'
    baseline.write_text(json.dumps({'baseline': image}))
    monkeypatch.setenv('DA_UV_MANAGED', 'true')
    monkeypatch.setattr('docassemble.base.managed_qualification.configured_baseline', lambda: image)
    monkeypatch.setattr('docassemble_executor.image_baseline.load_image_wheels', lambda *a: (image, {}))
    monkeypatch.setattr('docassemble_executor.image_baseline.verify_image_recovery_core', lambda *_: None)
    monkeypatch.setattr('docassemble_executor.installation.subprocess.run',
                        lambda *a, **k: SimpleNamespace(stdout=b'uv 0.12.21'))
    return InstallationPoller(None, paths, tmp_path / 'store', baseline), claim, image


def test_upgrade_gates_accept_selected_core_and_reject_unrelated_core_change(upgrade):
    poller, claim, image = upgrade
    assert poller._gates(claim).name == claim.snapshot['publication']['result']['bundle']['digest']
    local = load_baselines(poller.paths)
    assert local['selected_baseline'] == claim.snapshot['selected_baseline']
    assert local['recovery_baseline']['core'] == image['core']
    claim.snapshot['core'] = {'unrelated': {'version': '9', 'source_digest': 'f' * 64}}
    with pytest.raises(InstallationFailure, match='target-mismatch'):
        poller._gates(claim)


def test_ordinary_operations_protect_selected_core_after_upgrade_and_restart(upgrade):
    poller, claim, image = upgrade
    poller._gates(claim)
    selected = claim.snapshot['selected_baseline']
    ordinary = copy.deepcopy(claim.snapshot)
    ordinary['publication']['inputs'] = {'change_operation': 'add', 'baseline': copy.deepcopy(ordinary),
                                       'ownership_effects': {}, 'requirement': {'normalized_name': 'example'}}
    ordinary['publication']['result'] = {'bundle': selected['core_bundle']}
    claim = replace(claim, snapshot=ordinary)
    restarted = InstallationPoller(None, poller.paths, poller.store, poller.baseline)
    assert restarted._gates(claim, mutation=False).name == selected['core_bundle']['digest']
    assert set(selected_wheels(poller.store, selected)) == set(ordinary['core'])
    ordinary['core']['unrelated'] = {'version': '9', 'source_digest': 'f' * 64}
    with pytest.raises(InstallationFailure, match='target-mismatch'):
        restarted._gates(claim)
    downgraded = dict(image, publication={'inputs': {'change_operation': 'add', 'baseline': image,
                          'ownership_effects': {}, 'requirement': {'normalized_name': 'example'}},
                          'result': ordinary['publication']['result']})
    with pytest.raises(InstallationFailure, match='target-mismatch'):
        restarted._gates(replace(claim, snapshot=downgraded))


def test_selected_release_qualification_uses_selected_versions_and_schema_metadata(upgrade, monkeypatch):
    import docassemble_executor.published as published
    from docassemble_executor.controller import load_context
    from docassemble_executor.health import Check
    from docassemble_executor.roles import RUNTIME_ROLES, INFRASTRUCTURE_ROLES

    poller, claim, image = upgrade
    poller._gates(claim)
    context = load_context(poller.paths, start_infrastructure=False)
    context.manifest = {'identity': {'core_versions': {'old-core': '1.0'}},
                        'compatible_schema_revisions': ['image-only']}
    monkeypatch.setattr(published, 'load_context', lambda *a, **k: context)
    monkeypatch.setattr(published, '_create_tables', lambda _: Check('schema', True))
    def schema(venv, db, compatible):
        assert compatible == ()  # The old image cannot qualify selected-release schema.
        return SimpleNamespace(usable=True, database_revision='fixture_revision')
    monkeypatch.setattr(published, 'check_schema', schema)
    def probe(venv, config, expected):
        assert expected == {'docassemble.' + name: '2.0' for name in ('base', 'webapp', 'demo')}
        return Check('probe', True)
    monkeypatch.setattr(published, 'probe_runtime', probe)
    monkeypatch.setattr(published, '_start_web', lambda *a, **k: True)
    monkeypatch.setattr(published, 'prepare_roles',
                        lambda *a, **k: [Check(role, True) for role in RUNTIME_ROLES + INFRASTRUCTURE_ROLES])
    readiness = published.prepare_published(poller.paths, claim)
    assert readiness['schema_verified']
    assert load_baselines(poller.paths)['recovery_baseline']['core'] == image['core']
    # Health success is the only caller of record_published; promotion is durable
    # before DB reporting, so reporting loss/restart cannot repeat schema work.
    context.journal = Journal.load(poller.paths.journal_file)
    published.record_published(poller.paths, claim, {'identity': claim.identity})
    assert load_baselines(poller.paths)['recovery_baseline'] == claim.snapshot['selected_baseline']
    assert context.applied.core_baseline == claim.snapshot['selected_baseline']['core_bundle']['digest']
    assert Journal.load(poller.paths.journal_file).schema_succeeded
    assert load_context(poller.paths, start_infrastructure=False).expected_core_versions == {
        'docassemble.' + name: '2.0' for name in ('base', 'webapp', 'demo')}


def test_failed_selected_release_health_keeps_previous_recovery(upgrade, monkeypatch):
    import docassemble_executor.published as published
    from docassemble_executor.controller import load_context
    from docassemble_executor.health import Check

    poller, claim, image = upgrade
    poller._gates(claim)
    monkeypatch.setattr(published, '_create_tables', lambda _: Check('schema', True))
    monkeypatch.setattr(published, 'check_schema', lambda *a: SimpleNamespace(usable=True, database_revision='fixture_revision'))
    monkeypatch.setattr(published, 'probe_runtime', lambda *a: Check('probe', False))
    with pytest.raises(published.NormalQualificationError, match='probe'):
        published.prepare_published(poller.paths, claim)
    assert load_baselines(poller.paths)['recovery_baseline']['core'] == image['core']
    assert load_context(poller.paths, start_infrastructure=False).applied.revision is None


def test_selected_recovery_uses_retained_bundle_not_image(upgrade, monkeypatch):
    import docassemble_executor.controller as controller
    from docassemble_executor.selected_baseline import promote_recovery
    from docassemble_executor.health import Check

    poller, claim, _ = upgrade
    retain_selection(poller.paths, claim.snapshot, poller.store)
    promote_recovery(poller.paths, claim.snapshot)
    context = controller.load_context(poller.paths, start_infrastructure=False)
    monkeypatch.setattr(controller, '_persist', lambda _: None)
    monkeypatch.setattr(controller, 'stop_web', lambda _: None)
    monkeypatch.setattr(controller, '_remove_ready', lambda _: None)
    monkeypatch.setattr(controller, '_write_status', lambda *a, **k: None)
    monkeypatch.setattr(controller, '_record_error_notification', lambda *a, **k: None)
    def sync(directory, *a, **k):
        assert directory == poller.store / claim.snapshot['selected_baseline']['core_bundle']['digest']
        return SimpleNamespace(ok=True)
    monkeypatch.setattr(controller, 'sync_frozen', sync)
    monkeypatch.setattr(controller, 'verify_environment', lambda *a: Check('inventory', True))
    monkeypatch.setattr(controller, 'probe_runtime', lambda venv, config, expected:
                        SimpleNamespace(ok=expected == {'docassemble.' + name: '2.0'
                                                       for name in ('base', 'webapp', 'demo')}, checks=[]))
    def schema(venv, db, compatible):
        assert compatible == ()
        return SimpleNamespace(usable=True, database_revision='fixture_revision')
    monkeypatch.setattr(controller, 'check_schema', schema)
    assert controller._restore_core(context, 'failed apply', serve=False) == 'recovery'


def test_first_upgrade_verifies_distinct_image_recovery_bytes_before_retaining_selection(upgrade, monkeypatch):
    poller, claim, _ = upgrade
    assert not load_baselines(poller.paths)
    checked = []
    def corrupt(paths):
        checked.append(paths)
        raise ValueError('image core-only bytes are corrupt')
    monkeypatch.setattr('docassemble_executor.image_baseline.verify_image_recovery_core', corrupt)
    with pytest.raises(InstallationFailure, match='bundle-invalid'):
        poller._gates(claim)
    assert checked == [poller.paths]
    assert not load_baselines(poller.paths)
    assert not poller.paths.journal_file.exists()


@pytest.mark.parametrize('rehash', [False, True])
def test_selected_old_target_refuses_even_with_rehashed_digest_before_recovery(upgrade, monkeypatch, rehash):
    from docassemble_executor.selected_baseline import selected_directory
    from docassemble_executor.preparation import PreparationError
    poller, claim, _ = upgrade
    selected = copy.deepcopy(claim.snapshot['selected_baseline'])
    bundle = selected['core_bundle']
    directory = poller.store / bundle['digest']
    (directory / 'pyproject.toml').write_text('[project]\nrequires-python="==3.12.*"')
    (directory / 'uv.lock').write_text('requires-python="==3.12.*"')
    if rehash:
        manifest = {p.relative_to(directory).as_posix(): sha256_file(p)
                    for p in directory.rglob('*') if p.is_file() and p.name != 'manifest.json'}
        reference = digest(manifest)
        (directory / 'manifest.json').write_text(json.dumps(manifest))
        directory.rename(poller.store / reference)
        bundle.update(digest=reference, project_digest=manifest['pyproject.toml'], lock_digest=manifest['uv.lock'])
    with pytest.raises(PreparationError, match='selected core material changed' if rehash else 'retained bytes mismatch'):
        selected_directory(poller.store, selected)
    monkeypatch.setattr('docassemble_executor.controller.sync_frozen', lambda *a, **k: pytest.fail('foreign selection cannot sync'))
    from docassemble_executor import controller
    from docassemble_executor.selected_baseline import promote_recovery
    claim = replace(claim, snapshot=dict(claim.snapshot, selected_baseline=selected))
    retain_selection(poller.paths, claim.snapshot, poller.store)
    promote_recovery(poller.paths, claim.snapshot)
    context = controller.load_context(poller.paths, start_infrastructure=False)
    monkeypatch.setattr(controller, '_persist', lambda *_: None)
    monkeypatch.setattr(controller, 'stop_web', lambda *_: None)
    monkeypatch.setattr(controller, '_remove_ready', lambda *_: None)
    monkeypatch.setattr(controller, '_write_status', lambda *a, **k: None)
    monkeypatch.setattr(controller, '_recovery_failed', lambda context, detail: detail)
    assert controller._restore_core(context, 'failed apply', serve=False) == 'recovery material unavailable'
