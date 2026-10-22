"""Source-frozen target migration, schema direction, and new-image recovery."""
from docassemble.base.managed_python import python_requires, python_target
import copy
from types import SimpleNamespace

import pytest

from docassemble.base.managed_migration import schema_direction, release_version
from docassemble.base.managed_preparation import PreparationError
from docassemble_executor.registry_builder import RegistryBuilder
from docassemble_executor.journal import Journal


def core(version):
    return {'docassemble-' + name: {'version': version, 'source_digest': 'a' * 64} for name in ('base', 'webapp', 'demo')}


def baseline(version='2.0'):
    return {'core': core(version), 'image_baseline': {'image_target': 'old'}, 'publication': {'inputs': {'parent_id': 'parent'}}}


@pytest.mark.parametrize('target,expected', [('1.0', 'unchanged'), ('2.0', 'unchanged'), ('3.0', 'upgrade')])
def test_direction_uses_selected_release_not_temporary_recovery(target, expected):
    snapshot = dict(baseline(), runtime={'kind': 'core-only', 'inventory': core('0.1')})
    mode, high = schema_direction(snapshot, {'core': core(target)})
    assert mode == expected
    assert high['release'] == '2.0' and high['source'] == 'selected-baseline-fallback'


@pytest.mark.parametrize('target,expected', [('1.0', 'unchanged'), ('3.0', 'unchanged'), ('4.0', 'unchanged'), ('5.0', 'upgrade')])
def test_durable_schema_high_water_governs_later_forward_replacements(target, expected):
    snapshot = baseline('1.0')
    high = {'release': '4.0', 'source': 'successful-setup', 'attempt_id': 'old-setup', 'revision_id': 'old-revision',
            'schema_identity': 'a' * 64, 'database_revision': 'advanced'}
    snapshot['schema_release'] = high
    assert schema_direction(snapshot, {'core': core(target)}) == (expected, high)


def test_selected_failure_above_schema_high_water_does_not_skip_applicable_setup():
    snapshot = baseline('5.0')
    snapshot['schema_release'] = {'release': '2.0', 'source': 'successful-setup', 'attempt_id': 'old',
        'revision_id': 'old', 'schema_identity': 'a' * 64, 'database_revision': 'old'}
    assert schema_direction(snapshot, {'core': core('3.0')})[0] == 'upgrade'


@pytest.mark.parametrize('value', [{}, {'docassemble-webapp': {'version': 'unknown'}},
    dict(core('1.0'), **{'docassemble-demo': {'version': '2.0'}})])
def test_unknown_or_incoherent_release_evidence_is_not_direction_authority(value):
    with pytest.raises(PreparationError, match='unreadable coherent release'):
        release_version(value)


def test_schema_success_evidence_and_uncertainty_survive_journal_roundtrip(tmp_path):
    journal = Journal(schema_release={'release': '3.0', 'database_revision': 'advanced'})
    journal.save(tmp_path / 'journal.json')
    assert Journal.load(tmp_path / 'journal.json').schema_release == journal.schema_release
    assert journal.to_dict()['schema_release'] == journal.schema_release


@pytest.mark.parametrize('version', ['1.0', '3.0'])
def test_migration_builder_solves_all_declared_requirements_against_new_exact_core_and_seeds_preferences(tmp_path, monkeypatch, version):
    import docassemble_executor.registry_builder as module
    inputs = {'change_operation': 'migration', 'ownership_effects': {}, 'baseline': {
        'core': core(version), 'target': python_target(), 'tool': {'uv': '0.12.21'},
        'config': {'index_url': 'https://pypi.org/simple'}, 'image_baseline': {'image_target': 'new'},
        'desired_inventory': core('2.0'), 'runtime': {'kind': 'unknown', 'inventory': {}}},
        'contributions': [{'id': 'one', 'declaration': 'Example[extra]>=1,<3; os_name == "posix"'},
                          {'id': 'two', 'declaration': 'Example!=1.5'}]}
    monkeypatch.setattr(module.os, 'geteuid', lambda: 999)
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=b'uv 0.12.21'))
    monkeypatch.setattr('docassemble_executor.image_baseline.load_image_registry_wheels', lambda *a: {})
    monkeypatch.setattr('docassemble_executor.image_baseline.load_image_wheels', lambda *_: (inputs['baseline'], {}))
    builder = RegistryBuilder(tmp_path, image_baseline=tmp_path / 'image')
    captured = []
    monkeypatch.setattr(builder, '_solve', lambda inputs, **kwargs: captured.append(kwargs) or {})
    builder.build(inputs)
    assert captured[0]['core'] == core(version)
    assert captured[0]['seed_parent'] == 'required'
    assert captured[0]['requirements'][:2] == [row['declaration'] for row in inputs['contributions']]
    assert captured[0]['requirements'][2:] == [name + '==' + version for name in core(version)]
    assert 'upgrade' not in captured[0]


def test_migration_preserves_git_sha_subdirectory_and_never_refreshes_branch(tmp_path, monkeypatch):
    from docassemble_executor.git_sources import resolve_sources
    from docassemble.base.managed_git import parse_git_requirement
    _, source, _ = parse_git_requirement('Example @ git+https://example.org/repo.git@main#subdirectory=package')
    source.update(ref_kind='branch', resolved_sha='a' * 40)
    inputs = {'change_operation': 'migration', 'contributions': [{'id': 'git', 'declaration':
        'Example @ git+https://example.org/repo.git@main#subdirectory=package'}],
        'baseline': {'core': {}, 'git_sources': {'example': source}}}
    monkeypatch.setattr('docassemble_executor.git_sources.resolve_source', lambda *a, **k: pytest.fail('migration must not resolve branch'))
    assert resolve_sources(inputs, tmp_path) == {'example': source}


def test_migration_direct_url_reuses_immutable_hash_without_download(tmp_path, monkeypatch):
    from docassemble_executor.direct_sources import resolve_direct_sources
    from docassemble.base.managed_direct import parse_direct_requirement
    _, source, _ = parse_direct_requirement('Example @ https://example.org/example.zip')
    source.update(digest='b' * 64, filename='example.zip', normalized_name='example', version='1.0')
    inputs = {'change_operation': 'migration', 'contributions': [{'id': 'direct', 'declaration':
        'Example @ https://example.org/example.zip'}], 'baseline': {'core': {}, 'direct_sources': {'example': source}}}
    # Missing immutable bytes fail rather than retrieving mutable URL bytes.
    with pytest.raises((PreparationError, OSError)):
        resolve_direct_sources(copy.deepcopy(inputs), tmp_path,
            download=lambda *_: pytest.fail('migration must not refresh archive'))


def test_executor_replacement_archives_old_receipt_selects_new_image_recovery_and_preserves_uncertain_schema(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from dataclasses import replace
    from docassemble_executor import image_migration as module
    from docassemble_executor.paths import ExecutorPaths
    from docassemble_executor.journal import Operation, SchemaWork
    from docassemble_executor.state import _atomic_write_json
    paths = replace(ExecutorPaths.from_env(), state_dir=tmp_path / 'state', journal_file=tmp_path / 'state/lifecycle.json')
    paths.state_dir.mkdir()
    journal = Journal(phase='verified', operation=Operation(id='old', kind='published-apply', revision='old'),
        attempt_result={'outcome': 'applied-normal'}, schema=SchemaWork('uncertain', 'old', 'a' * 64))
    journal.save(paths.journal_file)
    _atomic_write_json(paths.state_dir / 'published-baselines.json', {'selected_baseline': {'old': 'incompatible'},
        'recovery_baseline': {'core_bundle': {'digest': 'old-lock'}}})
    target = {'core': core('3.0'), 'image_baseline': {'image_target': 'new-image'}}
    state = {'desired_revision_id': 'old', 'snapshot': baseline()}
    record = {'state': 'failed', 'schema_mode': 'upgrade'}
    class Engine:
        @contextmanager
        def begin(self):
            yield None
        def dispose(self):
            pass
    engine = Engine()
    class Service:
        def __init__(self, *args, **kwargs):
            self.engine = engine
        def _environment(self, connection):
            return state
        def observe(self, target, **kwargs):
            return record
        def prepare_observed(self):
            pass
        def advance(self):
            state['snapshot']['migration'] = record
    monkeypatch.setenv('DA_QUALIFIED_IMAGE_BASELINE', '/image/descriptor')
    monkeypatch.setenv('DA_DURABLE_WORK_STORE', str(tmp_path / 'retained'))
    monkeypatch.setattr(module, 'ImageMigration', Service)
    monkeypatch.setattr(module, 'create_engine', lambda *a, **k: engine)
    monkeypatch.setattr(module, 'load_config', lambda *_: {})
    monkeypatch.setattr('docassemble_executor.durable_work.configuration', lambda *_: ('db', ''))
    monkeypatch.setattr('docassemble_executor.image_baseline.load_image_registry_wheels', lambda *a: {})
    monkeypatch.setattr('docassemble_executor.image_baseline.load_image_wheels', lambda *a: (target, {}))
    monkeypatch.setattr('docassemble_executor.image_baseline.verify_image_recovery_core', lambda *_: None)
    module.observe_migration(SimpleNamespace(paths=paths))
    after = Journal.load(paths.journal_file)
    assert after.operation is None and after.attempt_result is None and after.phase == 'idle'
    assert after.schema == journal.schema and after.schema_uncertain
    assert (paths.state_dir / 'attempt-history/replacement-new-image.json').exists()
    from docassemble_executor.selected_baseline import load_baselines
    selected = load_baselines(paths)
    assert selected['selected_baseline'] is None
    assert selected['recovery_baseline'] == {'image_baseline': target['image_baseline'], 'core': target['core']}
    # A restart does not clear failed new-target recovery evidence or retry it.
    after.begin_recovery('new image preparation failed')
    after.finish_recovery(False, 'incompatible database')
    after.save(paths.journal_file)
    module.observe_migration(SimpleNamespace(paths=paths))
    assert Journal.load(paths.journal_file).phase == 'recovery_failed'


@pytest.mark.parametrize('schema_mode', ['unchanged', 'upgrade', None])
def test_installation_runtime_mutation_preserves_schema_evidence_only_for_unchanged_schema(tmp_path, monkeypatch, schema_mode):
    from dataclasses import replace
    from docassemble_executor import installation as module
    from docassemble_executor.installation import InstallationClaim, InstallationPoller
    from docassemble_executor.journal import Operation
    from docassemble_executor.paths import ExecutorPaths
    paths = replace(ExecutorPaths.from_env(), state_dir=tmp_path / 'state', journal_file=tmp_path / 'state/lifecycle.json',
        run_dir=tmp_path / 'run', status_file=tmp_path / 'status.json', log_dir=tmp_path / 'log', cache_dir=tmp_path / 'cache',
        repair_request_file=tmp_path / 'requests/repair.json')
    journal = Journal(phase='verified', schema_release={'release': '3.0', 'database_revision': 'advanced'})
    journal.begin_schema(Operation('setup', 'published-apply', 'previous'), 'setup-identity')
    journal.record_schema_success()
    journal.save(paths.journal_file)
    before = journal.to_dict()
    claim = InstallationClaim('replacement', 'replacement-revision', 1, 'host', 2,
        {'image_baseline': {'image_target': 'replacement'}, 'schema_mode': schema_mode, 'target': python_target(),
         'publication': {'result': {'bundle': {'digest': 'a' * 64}}}})
    reports = []
    def report(receipt):
        reports.append(receipt)
        return 'reported'
    poller = InstallationPoller(SimpleNamespace(claim_installation=lambda _: claim, report_terminal=report), paths, tmp_path, tmp_path)
    monkeypatch.setattr(poller, '_gates', lambda _: tmp_path)
    monkeypatch.setattr(poller, '_recover_core', lambda _: None)
    monkeypatch.setattr(module, 'load_context', lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(module, 'enter_maintenance', lambda _: True)
    monkeypatch.setattr('docassemble_executor.published.close_published', lambda _: None)
    observed = []
    def sync(*args, **kwargs):
        observed.append(Journal.load(paths.journal_file))
        return SimpleNamespace(ok=False)
    monkeypatch.setattr(module, 'sync_frozen', sync)
    assert poller.run_once() == 'reported'
    assert len(observed) == 1
    during = observed[0]
    assert during.operation.id == claim.attempt_id and during.mutation_possibly_partial()
    assert during.to_dict()['schema'] == (before['schema'] if schema_mode == 'unchanged' else None)
    assert during.schema_release == before['schema_release']
    after = Journal.load(paths.journal_file)
    assert after.attempt_result == reports[0] and after.attempt_result['mutation_started']
    assert after.to_dict()['schema'] == (before['schema'] if schema_mode == 'unchanged' else None)
    assert after.schema_release == before['schema_release']


@pytest.mark.parametrize('database_usable', [True, False])
def test_replacement_recovery_uses_only_new_image_core_and_never_setup(tmp_path, monkeypatch, database_usable):
    from dataclasses import replace
    from docassemble_executor import controller
    from docassemble_executor.paths import ExecutorPaths
    from docassemble_executor.state import AppliedState, _atomic_write_json
    paths = replace(ExecutorPaths.from_env(), state_dir=tmp_path / 'state', bundles_dir=tmp_path / 'image-bundles')
    paths.state_dir.mkdir()
    _atomic_write_json(paths.state_dir / 'published-baselines.json', {'migration': True, 'schema_mode': 'unchanged',
        'image_baseline': {'image_target': 'replacement'}, 'selected_baseline': None,
        'recovery_baseline': {'image_baseline': {'image_target': 'replacement'}, 'core': core('1.0')}})
    context = controller.Context(paths=paths, journal=Journal(), desired=None, applied=AppliedState(core_baseline='old-incompatible'),
        manifest={'identity': {'core_baseline': 'new-image', 'core_versions': {'docassemble.webapp': '1.0'}},
                  'inventories': {'core-new-image': {'distributions': {}, 'files': {}}}}, config={}, db=None)
    monkeypatch.setattr(context.applied, 'save', lambda *_: None)
    for name in ('_persist', 'stop_web', '_remove_ready', '_record_error_notification', '_write_status'):
        monkeypatch.setattr(controller, name, lambda *a, **k: None)
    monkeypatch.setattr(controller, '_create_tables', lambda *_: pytest.fail('recovery must never run setup'))
    def sync(directory, *a, **kwargs):
        assert directory == paths.bundles_dir / 'core-new-image'
        assert kwargs['reinstall']
        return SimpleNamespace(ok=True)
    monkeypatch.setattr(controller, 'sync_frozen', sync)
    monkeypatch.setattr(controller, 'verify_bundle', lambda *a: (True, 'verified'))
    monkeypatch.setattr(controller, 'verify_environment', lambda *a: SimpleNamespace(ok=True))
    monkeypatch.setattr(controller, 'probe_runtime', lambda *a: SimpleNamespace(ok=True, checks=[]))
    monkeypatch.setattr('docassemble_executor.rollback_schema.check_rollback_schema', lambda *a:
                        SimpleNamespace(usable=database_usable, detail='observed actual database'))
    assert controller._restore_core(context, 'replacement failure', serve=False) == ('recovery' if database_usable else 'maintenance')
    assert context.journal.recovery.attempt == 1
    assert context.journal.recovery.result == ('succeeded' if database_usable else 'failed')


def test_forward_image_migration_records_new_image_baseline_not_old_web_selected_id(tmp_path, monkeypatch):
    from dataclasses import replace
    from docassemble_executor import published
    from docassemble_executor.paths import ExecutorPaths
    marked = []
    context = SimpleNamespace(manifest={'identity': {'core_baseline': 'new-image-core'}}, core_baseline='old-web-core-digest',
        applied=SimpleNamespace(mark=lambda *args: marked.append(args), save=lambda *_: None), journal=Journal())
    monkeypatch.setattr(published, 'load_context', lambda *a, **k: context)
    monkeypatch.setattr('docassemble_executor.selected_baseline.promote_recovery', lambda *a: None)
    paths = replace(ExecutorPaths.from_env(), journal_file=tmp_path / 'lifecycle.json')
    claim = SimpleNamespace(revision_id='replacement', attempt_id='new-attempt', snapshot={'migration': {'state': 'published'},
                            'schema_mode': 'upgrade'})
    published.record_published(paths, claim, {'identity': {}})
    assert marked == [('replacement', 'new-image-core')]


def test_seeded_migration_only_unlocks_fixed_image_core_metadata(tmp_path, monkeypatch):
    import json
    from docassemble.base.managed_preparation import digest
    from docassemble_executor.bundles import sha256_file
    store = tmp_path / 'retained'
    parent = store / 'staging'
    parent.mkdir(parents=True)
    (parent / 'solve.lock').write_text('# parent solve preferences, not the apply lock\nrequires-python = ' + json.dumps(python_requires()))
    (parent / 'solve-project.toml').write_text('[project]\nrequires-python = ' + json.dumps(python_requires()))
    (parent / 'tool.json').write_text(json.dumps({'uv': '0.12.21', 'target': python_target()}))
    manifest = {p.name: sha256_file(p) for p in parent.iterdir()}
    reference = digest(manifest)
    (parent / 'manifest.json').write_text(json.dumps(manifest))
    parent.rename(store / reference)
    inputs = {'change_operation': 'migration', 'contributions': [], 'ownership_effects': {},
        'baseline': {'publication': {'result': {'bundle': {'digest': reference}}}}}
    builder = RegistryBuilder(store)
    class BeforeSolve(Exception):
        pass
    def run(arguments, project, *args):
        assert arguments == ['lock', '--no-config', '--upgrade-package', 'corefixture']
        assert (project / 'uv.lock').read_text().startswith('# parent solve preferences')
        raise BeforeSolve()
    monkeypatch.setattr(builder, '_run', run)
    with pytest.raises(BeforeSolve):
        builder._solve(inputs, operation='migration', core={'corefixture': {'version': '1.0'}}, requirements=['corefixture==1.0'],
            image_wheels={}, local_artifacts={}, direct_sources={}, authorizations={}, seed_parent='required')


def test_migration_resumption_prefers_successful_setup_evidence():
    # Forward setup advances the DB past the immutable pre-migration evidence;
    # a verified resumption must validate against the successful setup evidence
    # bound to this revision/attempt, not the pre-change snapshot.
    from docassemble_executor.installation import migration_schema_evidence
    from docassemble_executor.journal import Journal
    snapshot = {'schema_release': {'release': '2.0', 'database_revision': 'A'}}
    journal = Journal(schema_release={'release': '2.0', 'database_revision': 'B',
                                      'revision_id': 'rev', 'attempt_id': 'att'})
    assert migration_schema_evidence(snapshot, journal, revision_id='rev', attempt_id='att') == journal.schema_release
    assert migration_schema_evidence(snapshot, journal, revision_id='other', attempt_id='att') == snapshot['schema_release']
    assert migration_schema_evidence(snapshot, journal, revision_id='rev', attempt_id='other') == snapshot['schema_release']
    assert migration_schema_evidence(snapshot, Journal(), revision_id='rev', attempt_id='att') == snapshot['schema_release']
