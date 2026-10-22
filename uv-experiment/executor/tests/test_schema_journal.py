"""Durable schema start/success journaling and attempt/schema-bound clearance.

Ticket 14b: schema/setup start and success are journaled separately, an uncertain
outcome fences a silent replay on startup or ordinary Retry, and only a matching
administrator/operator clearance permits a new attempt to rerun schema work.
"""
from docassemble.base.managed_python import python_target
import json
from dataclasses import replace

import pytest

from docassemble_executor.installation import (InstallationFailure, InstallationPoller,
                                               load_schema_clearance, schema_clearance_file)
from docassemble_executor.journal import Journal, Operation
from docassemble_executor.paths import ExecutorPaths


def paths_at(tmp_path):
    return replace(ExecutorPaths.from_env(), state_dir=tmp_path, journal_file=tmp_path / 'lifecycle.json',
                   run_dir=tmp_path / 'run', status_file=tmp_path / 'status.json',
                   repair_request_file=tmp_path / 'requests/repair.json', cache_dir=tmp_path / 'cache',
                   log_dir=tmp_path / 'log')


def poller_at(tmp_path):
    return InstallationPoller(None, paths_at(tmp_path), tmp_path, tmp_path / 'baseline')


def operation(attempt_id):
    return Operation(id=attempt_id, kind='published-apply', revision='rev-' + attempt_id, binding={})


def uncertain_journal(tmp_path, *, attempt_id='attempt-1', schema_identity='schema-1'):
    paths = paths_at(tmp_path)
    journal = Journal()
    journal.begin_mutation(operation(attempt_id))
    journal.begin_schema(operation(attempt_id), schema_identity)
    journal.save(paths.journal_file)
    return paths, journal


def test_schema_start_and_success_are_separate_and_survive_reload(tmp_path):
    paths, journal = uncertain_journal(tmp_path)
    loaded = Journal.load(paths.journal_file)
    assert loaded.schema_uncertain and not loaded.schema_succeeded
    assert loaded.schema.result is None and loaded.schema.completed_at is None
    loaded.record_schema_success()
    loaded.save(paths.journal_file)
    reloaded = Journal.load(paths.journal_file)
    assert reloaded.schema_succeeded and not reloaded.schema_uncertain
    assert reloaded.schema.attempt_id == 'attempt-1' and reloaded.schema.identity == 'schema-1'
    assert reloaded.schema.completed_at


def test_uncertain_schema_cannot_be_overwritten_by_a_new_attempt(tmp_path):
    _, journal = uncertain_journal(tmp_path)
    with pytest.raises(ValueError, match='operator clearance'):
        journal.begin_schema(operation('attempt-2'), 'schema-2')
    assert journal.schema.attempt_id == 'attempt-1'


def test_missing_clearance_blocks_rerun_with_allowlisted_gate_reason(tmp_path):
    paths, journal = uncertain_journal(tmp_path)
    with pytest.raises(InstallationFailure) as error:
        poller_at(tmp_path)._require_schema_clearance(journal)
    assert error.value.reason == 'schema-clearance-required'
    assert not Journal.load(paths.journal_file).clearance


@pytest.mark.parametrize('document', [
    'not json',
    json.dumps({'format': 2, 'attempt_id': 'attempt-1', 'schema_identity': 'schema-1', 'observed_schema': 'r2'}),
    json.dumps({'format': 1, 'attempt_id': 'attempt-1', 'schema_identity': 'schema-1'}),
    json.dumps({'format': 1, 'attempt_id': '', 'schema_identity': 'schema-1', 'observed_schema': 'r2'}),
    json.dumps({'format': 1, 'attempt_id': 'attempt-1', 'schema_identity': 'schema-1', 'observed_schema': ''}),
])
def test_clearance_file_fails_closed_on_malformed_input(tmp_path, document):
    paths, _ = uncertain_journal(tmp_path)
    schema_clearance_file(paths).write_text(document)
    schema_clearance_file(paths).chmod(0o600)
    assert load_schema_clearance(paths) is None


def test_symlinked_clearance_is_refused(tmp_path):
    paths, _ = uncertain_journal(tmp_path)
    target = tmp_path / 'elsewhere.json'
    target.write_text(json.dumps({'format': 1, 'attempt_id': 'attempt-1',
                                  'schema_identity': 'schema-1', 'observed_schema': 'r2'}))
    schema_clearance_file(paths).symlink_to(target)
    assert load_schema_clearance(paths) is None
    with pytest.raises(InstallationFailure, match='clearance'):
        poller_at(tmp_path)._require_schema_clearance(Journal.load(paths.journal_file))
    assert not Journal.load(paths.journal_file).clearance


def test_foreign_clearance_does_not_clear_and_matching_clearance_is_persisted(tmp_path, monkeypatch):
    monkeypatch.setattr('docassemble_executor.installation.load_config', lambda _: {'db': {'name': 'clearance-db'}})
    def read(settings):
        assert settings.name == 'clearance-db'
        return 'r2'
    monkeypatch.setattr('docassemble_executor.installation.read_database_revision', read)
    paths, journal = uncertain_journal(tmp_path)
    poller = poller_at(tmp_path)
    schema_clearance_file(paths).write_text(json.dumps(
        {'format': 1, 'attempt_id': 'other', 'schema_identity': 'schema-1', 'observed_schema': 'r2'}))
    schema_clearance_file(paths).chmod(0o600)
    with pytest.raises(InstallationFailure, match='clearance'):
        poller._require_schema_clearance(journal)
    assert not journal.schema_cleared
    schema_clearance_file(paths).write_text(json.dumps(
        {'format': 1, 'attempt_id': 'attempt-1', 'schema_identity': 'schema-1',
         'observed_schema': 'r2', 'cleared_at': '2026-01-01T00:00:00+00:00'}))
    poller._require_schema_clearance(journal)
    assert journal.schema_cleared
    persisted = Journal.load(paths.journal_file).clearance
    assert persisted['observed_schema'] == 'r2'
    assert persisted['source_digest'] and len(persisted['source_digest']) == 64
    # A stale/foreign clearance file can never authorize a different attempt.
    other = uncertain_journal(tmp_path / 'other', attempt_id='attempt-9', schema_identity='schema-1')[1]
    with pytest.raises(ValueError, match='does not match'):
        other.record_clearance(persisted)


@pytest.mark.parametrize('observed', ['changed', None, 'unreadable'])
@pytest.mark.parametrize('persisted', [False, True])
def test_clearance_requires_current_observed_database_revision(tmp_path, monkeypatch, observed, persisted):
    paths, journal = uncertain_journal(tmp_path)
    clearance = {'format': 1, 'attempt_id': 'attempt-1', 'schema_identity': 'schema-1', 'observed_schema': 'r2'}
    schema_clearance_file(paths).write_text(json.dumps(clearance))
    schema_clearance_file(paths).chmod(0o600)
    if persisted:
        journal.record_clearance(clearance)
        journal.save(paths.journal_file)
    monkeypatch.setattr('docassemble_executor.installation.load_config', lambda _: {})
    def read(_):
        if observed == 'unreadable':
            raise RuntimeError('restricted database error')
        return observed
    monkeypatch.setattr('docassemble_executor.installation.read_database_revision', read)
    with pytest.raises(InstallationFailure, match='^schema-clearance-required$'):
        poller_at(tmp_path)._require_schema_clearance(Journal.load(paths.journal_file))
    assert Journal.load(paths.journal_file).schema_uncertain


def test_replacement_clearance_supersedes_stale_persisted_clearance(tmp_path, monkeypatch):
    # A persisted clearance for an older revision must not shadow a replacement
    # operator clearance for the current observed database revision.
    paths, journal = uncertain_journal(tmp_path)
    journal.record_clearance({'format': 1, 'attempt_id': 'attempt-1', 'schema_identity': 'schema-1',
                              'observed_schema': 'r2'})
    journal.save(paths.journal_file)
    monkeypatch.setattr('docassemble_executor.installation.load_config', lambda _: {})
    monkeypatch.setattr('docassemble_executor.installation.read_database_revision', lambda _: 'r3')
    with pytest.raises(InstallationFailure, match='^schema-clearance-required$'):
        poller_at(tmp_path)._require_schema_clearance(Journal.load(paths.journal_file))
    schema_clearance_file(paths).write_text(json.dumps(
        {'format': 1, 'attempt_id': 'attempt-1', 'schema_identity': 'schema-1', 'observed_schema': 'r3'}))
    schema_clearance_file(paths).chmod(0o600)
    poller_at(tmp_path)._require_schema_clearance(Journal.load(paths.journal_file))
    assert Journal.load(paths.journal_file).clearance['observed_schema'] == 'r3'


def test_cleared_uncertain_schema_permits_a_new_attempt(tmp_path):
    paths, journal = uncertain_journal(tmp_path)
    journal.record_clearance({'attempt_id': 'attempt-1', 'schema_identity': 'schema-1', 'observed_schema': 'r2'})
    assert journal.schema_cleared
    journal.begin_schema(operation('attempt-2'), 'schema-2')
    assert journal.schema.attempt_id == 'attempt-2' and journal.clearance is None


def test_successful_schema_is_cleared_by_a_later_mutation(tmp_path):
    paths, journal = uncertain_journal(tmp_path)
    journal.record_schema_success()
    journal.begin_mutation(operation('attempt-2'))
    assert journal.schema is None and journal.clearance is None


@pytest.mark.parametrize('succeeded', [False, True])
@pytest.mark.parametrize('schema_unchanged', [False, True])
def test_begin_mutation_preserves_unchanged_schema_and_always_preserves_uncertainty(tmp_path, succeeded, schema_unchanged):
    paths, journal = uncertain_journal(tmp_path)
    journal.record_clearance({'attempt_id': 'attempt-1', 'schema_identity': 'schema-1', 'observed_schema': 'r2'})
    if succeeded:
        journal.record_schema_success()
    journal.schema_release = {'release': '3.0', 'database_revision': 'r2'}
    before = journal.to_dict()
    journal.begin_mutation(operation('attempt-2'), schema_unchanged=schema_unchanged)
    journal.save(paths.journal_file)
    after = Journal.load(paths.journal_file)
    assert after.operation.id == 'attempt-2' and after.mutation_possibly_partial()
    assert after.schema_release == before['schema_release']
    if schema_unchanged or not succeeded:
        assert after.to_dict()['schema'] == before['schema']
        assert after.clearance == before['clearance']
    else:
        assert after.schema is None and after.clearance is None


def test_legacy_journal_without_schema_loads_and_stays_uncertainty_free(tmp_path):
    paths = paths_at(tmp_path)
    legacy = {'format': 1, 'phase': 'verified', 'attempt_result': None, 'operation': None,
              'verified_revision': 'rev', 'verified_at': None, 'recovery': None, 'failure': None,
              'error_notification': None}
    paths.journal_file.write_text(json.dumps(legacy))
    loaded = Journal.load(paths.journal_file)
    assert loaded.schema is None and not loaded.schema_uncertain and not loaded.schema_cleared


def _prepare_paths(tmp_path):
    return replace(ExecutorPaths.from_env(), state_dir=tmp_path, journal_file=tmp_path / 'lifecycle.json',
                   intent_file=tmp_path / 'desired.json', runtime_venv=tmp_path / 'venv', run_dir=tmp_path / 'run',
                   status_file=tmp_path / 'status.json', log_dir=tmp_path / 'log', cache_dir=tmp_path / 'cache',
                   repair_request_file=tmp_path / 'requests/repair.json')


def _publish_context():
    from types import SimpleNamespace
    return SimpleNamespace(core_baseline='r1', manifest={'compatible_schema_revisions': ()},
                           db=SimpleNamespace(), expected_core_versions={}, desired=None)


def _publish_claim():
    from types import SimpleNamespace
    return SimpleNamespace(attempt_id='attempt-1', revision_id='rev-1', identity={'attempt_id': 'attempt-1'},
                           snapshot={'publication': {'result': {'bundle': {'inventory': {}}}},
                                     'target': python_target()})


def test_prepare_published_journals_start_then_success(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import docassemble_executor.published as published
    from docassemble_executor.health import Check
    from docassemble_executor.roles import RUNTIME_ROLES, INFRASTRUCTURE_ROLES

    paths = _prepare_paths(tmp_path)
    monkeypatch.setattr(published, 'load_context', lambda *a, **k: _publish_context())
    stages = []

    def create_tables(_):
        assert Journal.load(paths.journal_file).schema_uncertain
        stages.append('schema')
        return Check('create-tables', True)

    def probe(*a, **k):
        assert Journal.load(paths.journal_file).schema_succeeded
        stages.append('probe')
        return Check('probe', True)

    monkeypatch.setattr(published, '_create_tables', create_tables)
    monkeypatch.setattr(published, 'check_schema', lambda *a, **k: SimpleNamespace(usable=True, database_revision='fixture_revision'))
    monkeypatch.setattr(published, 'probe_runtime', probe)
    monkeypatch.setattr(published, '_start_web', lambda context, recovery: True)
    monkeypatch.setattr(published, 'prepare_roles',
                        lambda *a, **k: [Check(role, True) for role in RUNTIME_ROLES + INFRASTRUCTURE_ROLES])
    readiness = published.prepare_published(paths, _publish_claim())
    assert stages == ['schema', 'probe']
    journal = Journal.load(paths.journal_file)
    assert journal.schema_succeeded and journal.schema.attempt_id == 'attempt-1'
    assert journal.schema.identity and journal.schema.completed_at
    assert readiness == {'schema_verified': True, 'roles': sorted(RUNTIME_ROLES + INFRASTRUCTURE_ROLES)}


def test_prepare_published_schema_failure_leaves_uncertain_no_success(tmp_path, monkeypatch):
    import docassemble_executor.published as published
    from docassemble_executor.health import Check

    paths = _prepare_paths(tmp_path)
    monkeypatch.setattr(published, 'load_context', lambda *a, **k: _publish_context())
    monkeypatch.setattr(published, '_create_tables', lambda _: Check('create-tables', False, 'boom'))
    monkeypatch.setattr(published, 'check_schema', lambda *a, **k: pytest.fail('schema reused after failed setup'))
    with pytest.raises(published.NormalQualificationError, match='schema'):
        published.prepare_published(paths, _publish_claim())
    journal = Journal.load(paths.journal_file)
    assert journal.schema_uncertain and not journal.schema_succeeded
