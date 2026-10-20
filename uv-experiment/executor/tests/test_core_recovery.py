"""Stopped published core restoration: one marker, child exclusion and durable replay."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from docassemble_executor.authority import RuntimeLock, MutationLock
from docassemble_executor.admission import Admission
from docassemble_executor.installation import InstallationClaim, InstallationPoller
from docassemble_executor.journal import Journal, Operation
from docassemble_executor.paths import ExecutorPaths
from docassemble_executor.preparation import PreparationError


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    paths = replace(ExecutorPaths.from_env(), state_dir=tmp_path, journal_file=tmp_path / 'lifecycle.json',
        run_dir=tmp_path / 'run', log_dir=tmp_path / 'log', cache_dir=tmp_path / 'cache',
        status_file=tmp_path / 'status.json', repair_request_file=tmp_path / 'requests/repair.json')
    paths.ensure_directories()
    claim = InstallationClaim('attempt', 'revision', 1, 'host', 2,
        {'image_baseline': {'image_target': 'image'}, 'target': {},
         'publication': {'result': {'bundle': {'digest': 'a' * 64}}}})
    journal = Journal()
    journal.begin_mutation(Operation('attempt', 'published-apply', 'revision', binding=claim.identity))
    journal.save(paths.journal_file)
    manifest = {'identity': {'core_baseline': 'r1'}, 'bundles': {'core-r1': 'core-digest'},
                'inventories': {'core-r1': {'distributions': {'core': '1'}}}}
    def context(*a, **k):
        return SimpleNamespace(paths=paths, journal=Journal.load(paths.journal_file), manifest=manifest,
            core_baseline='r1', bundle_name=lambda kind, revision: kind + '-' + revision)
    monkeypatch.setattr('docassemble_executor.installation.load_context', context)
    monkeypatch.setattr('docassemble_executor.startup.target_id', lambda _: 'image')
    calls = []
    def restore(context, reason, *, serve):
        # Ticket 13: a successful published-path core recovery serves restricted
        # administration (`serve=True`) instead of stopping at core-only.
        assert serve is True
        calls.append(reason)
        context.journal.begin_recovery(reason)
        context.journal.save(paths.journal_file)
        context.journal.finish_recovery(True)
        context.journal.save(paths.journal_file)
    monkeypatch.setattr('docassemble_executor.controller._restore_core', restore)
    installation = SimpleNamespace(report_terminal=lambda _: 'reported')
    return InstallationPoller(installation, paths, tmp_path, tmp_path), claim, calls


def test_published_core_restoration_marker_prevents_a_second_automatic_attempt(recovery):
    poller, claim, calls = recovery
    with MutationLock(poller.paths.state_dir):
        result = poller._recover_core(claim)
        assert result['id'] == 'attempt:core:1' and result['result'] == 'succeeded'
        with pytest.raises(PreparationError, match='already attempted'):
            poller._recover_core(claim)
    assert len(calls) == 1
    assert Journal.load(poller.paths.journal_file).verified_revision is None


def test_child_lease_prevents_recovery_marker_and_any_restore(recovery):
    poller, claim, calls = recovery
    with MutationLock(poller.paths.state_dir), RuntimeLock(poller.paths.state_dir, shared=True):
        with pytest.raises(BlockingIOError):
            poller._recover_core(claim)
    assert calls == []
    assert not Journal.load(poller.paths.journal_file).recovery_attempted


@pytest.mark.parametrize('phase', ['recovery_attempt_started', 'recovery_failed'])
def test_interrupted_or_failed_core_recovery_is_not_automatically_repeated(recovery, phase):
    poller, claim, calls = recovery
    journal = Journal.load(poller.paths.journal_file)
    journal.begin_recovery('previous')
    if phase == 'recovery_failed':
        journal.finish_recovery(False)
    journal.save(poller.paths.journal_file)
    with pytest.raises(PreparationError, match='already attempted'):
        poller._recover_core(claim)
    assert calls == []


def test_recovery_terminal_replay_excludes_children_and_does_not_restore(recovery):
    poller, claim, calls = recovery
    result = poller._recover_core(claim)
    journal = Journal.load(poller.paths.journal_file)
    journal.attempt_result = {'identity': claim.identity, 'outcome': 'failed-stopped', 'recovery': result}
    journal.save(poller.paths.journal_file)
    with RuntimeLock(poller.paths.state_dir):
        with pytest.raises(BlockingIOError):
            poller.run_once()
    # A serving (shared) lease is compatible with the replay's shared lease, so a
    # served successful recovery replays while restricted administration is live.
    with RuntimeLock(poller.paths.state_dir, shared=True):
        assert poller.run_once() == 'reported'
    assert poller.run_once() == 'reported'
    assert len(calls) == 1
    journal.phase = 'mutation_started'
    journal.save(poller.paths.journal_file)
    with pytest.raises(PreparationError, match='recovery journal phase mismatch'):
        poller.run_once()


def test_failed_frozen_apply_records_recovery_before_attempt_terminal(recovery, monkeypatch):
    poller, claim, calls = recovery
    Journal().save(poller.paths.journal_file)
    poller.installation.claim_installation = lambda _: claim
    monkeypatch.setattr(poller, '_gates', lambda _: poller.store)
    monkeypatch.setattr('docassemble_executor.installation.enter_maintenance', lambda _: True)
    monkeypatch.setattr('docassemble_executor.installation.sync_frozen', lambda *a, **k: SimpleNamespace(ok=False))
    monkeypatch.setattr('docassemble_executor.published.close_published', lambda _: None)
    assert poller.run_once() == 'reported'
    receipt = Journal.load(poller.paths.journal_file).attempt_result
    assert receipt['outcome'] == 'failed-stopped' and receipt['normal_verified'] is False
    assert receipt['failure'] == {'stage': 'sync', 'reason': 'sync-failed'}
    assert receipt['child_safe'] is True and receipt['recovery']['result'] == 'succeeded'
    assert len(calls) == 1
    assert poller.run_once() == 'reported'
    assert len(calls) == 1


def _failed_apply(poller, monkeypatch, claim):
    Journal().save(poller.paths.journal_file)
    poller.installation.claim_installation = lambda _: claim
    monkeypatch.setattr(poller, '_gates', lambda _: poller.store)
    monkeypatch.setattr('docassemble_executor.installation.enter_maintenance', lambda _: True)
    monkeypatch.setattr('docassemble_executor.installation.sync_frozen', lambda *a, **k: SimpleNamespace(ok=False))
    monkeypatch.setattr('docassemble_executor.published.close_published', lambda _: None)


def test_served_core_recovery_keeps_restricted_admission_open(recovery, monkeypatch):
    """A successful published-path recovery serves restricted administration."""
    poller, claim, _ = recovery
    _failed_apply(poller, monkeypatch, claim)
    Admission(poller.paths.state_dir).open()  # What the serving restore leaves behind.
    assert poller.run_once() == 'reported'
    assert Admission(poller.paths.state_dir).state()['mode'] == 'open'


def test_surviving_installer_child_blocks_replacement_executor_mutation(recovery, monkeypatch):
    """Parent death with a surviving child keeps the attempt fenced; no auto recovery."""
    poller, claim, calls = recovery
    _failed_apply(poller, monkeypatch, claim)
    with RuntimeLock(poller.paths.state_dir, shared=True):
        with pytest.raises(BlockingIOError):
            poller.run_once()
    assert calls == []
    assert not Journal.load(poller.paths.journal_file).recovery_attempted
