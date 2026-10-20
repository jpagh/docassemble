"""Local pre-change checkpoint option, capture failure and lifecycle ordering."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from docassemble_executor.checkpoints import backup_inputs
from docassemble_executor.installation import InstallationClaim, InstallationPoller
from docassemble_executor.journal import Journal
from docassemble_executor.paths import ExecutorPaths


@pytest.mark.parametrize('inputs', [
    {'DABACKUPDAYS': '0'}, {'BACKUPFILESTORAGE': 'false'}, {'S3ENABLE': 'true'},
    {'AZUREENABLE': 'true'}, {'DAREADONLYFILESYSTEM': 'true'}, {'DBBACKUP': 'false'},
])
def test_incomplete_backup_or_missing_cloud_destination_fails(tmp_path, inputs):
    import json
    file = tmp_path / 'backup.json'
    file.write_text(json.dumps(inputs))
    file.chmod(0o600)
    with pytest.raises(RuntimeError):
        backup_inputs(file)


def test_trusted_mapping_never_follows_symlink(tmp_path):
    (tmp_path / 'input').write_text('{}')
    (tmp_path / 'link').symlink_to(tmp_path / 'input')
    with pytest.raises(OSError):
        backup_inputs(tmp_path / 'link')


@pytest.mark.parametrize('requested,failed', [(False, False), (True, False), (True, True)])
def test_capture_is_after_drain_and_before_any_mutation(tmp_path, monkeypatch, requested, failed):
    paths = replace(ExecutorPaths.from_env(), state_dir=tmp_path / 'state', run_dir=tmp_path / 'run',
                    log_dir=tmp_path / 'log', journal_file=tmp_path / 'state/lifecycle.json',
                    cache_dir=tmp_path / 'cache', status_file=tmp_path / 'run/status.json',
                    repair_request_file=tmp_path / 'run/requests/repair.json')
    paths.ensure_directories()
    Journal().save(paths.journal_file)
    claim = InstallationClaim('attempt', 'revision', 1, 'host', 2,
        {'target': {}, 'publication': {'inputs': {'pre_change_checkpoint': requested},
                                     'result': {'bundle': {'digest': 'a' * 64}}}})
    events = []
    installation = SimpleNamespace(claim_installation=lambda _: claim, report_terminal=lambda receipt: receipt)
    poller = InstallationPoller(installation, paths, tmp_path, tmp_path)
    monkeypatch.setattr(poller, '_gates', lambda *a, **k: tmp_path)
    monkeypatch.setattr('docassemble_executor.installation.load_context', lambda *a, **k: None)
    monkeypatch.setattr('docassemble_executor.installation.enter_maintenance', lambda _: events.append('drain') or True)
    def capture(*args):
        assert Journal.load(paths.journal_file).phase == 'installation_preparing'
        from docassemble_executor.authority import RuntimeLock
        with pytest.raises(BlockingIOError):
            with RuntimeLock(paths.state_dir, shared=True):
                pytest.fail('concurrent runtime writer admitted during capture')
        events.append('capture')
        if failed:
            raise OSError('interrupted copy')
        return {'state': 'verified', 'complete': True}
    monkeypatch.setattr('docassemble_executor.checkpoints.capture_for_installation', capture)
    def sync(*a, **k):
        assert Journal.load(paths.journal_file).phase == 'mutation_started'
        events.append('sync')
        return SimpleNamespace(ok=False)
    monkeypatch.setattr('docassemble_executor.installation.sync_frozen', sync)
    receipt = poller.run_once()
    assert events == (['drain', 'capture'] if failed else ['drain', 'capture', 'sync'] if requested else ['drain', 'sync'])
    if failed:
        assert receipt['mutation_started'] is False
        assert receipt['failure'] == {'stage': 'checkpoint', 'reason': 'capture-failed'}
        assert receipt['checkpoint']['complete'] is False
        assert not Journal.load(paths.journal_file).recovery_attempted
        assert poller.run_once() == receipt  # no repeated capture or sync on replay


@pytest.mark.parametrize('runtime_ok,schema_ok,expected', [(True, True, True), (False, True, False), (True, False, False)])
def test_prechange_health_evidence_never_calls_broken_runtime_known_good(monkeypatch, runtime_ok, schema_ok, expected):
    from docassemble_executor.checkpoints import observe_pre_change
    monkeypatch.setattr('docassemble_executor.checkpoints.load_config', lambda _: {})
    def probe(venv, config, versions, recovery):
        assert versions == {'docassemble.base': '1', 'docassemble.webapp': '1', 'docassemble.demo': '1'}
        assert recovery is True
        return SimpleNamespace(ok=runtime_ok)
    monkeypatch.setattr('docassemble_executor.health.probe_runtime', probe)
    monkeypatch.setattr('docassemble_executor.alembic_probe.check_schema',
                        lambda *a: SimpleNamespace(status='compatible' if schema_ok else 'incompatible'))
    claim = SimpleNamespace(pre_change={'runtime': {'kind': 'normal'}, 'core': {
        name: {'version': '1'} for name in ('docassemble-base', 'docassemble-webapp', 'docassemble-demo', 'dependency')}})
    paths = SimpleNamespace(runtime_venv='venv', config_file='config')
    observe_pre_change(paths, claim)
    assert claim.pre_change['runtime_health_verified'] is expected


def test_cold_restore_does_not_replay_original_upgrade_checkpoint_option():
    claim = InstallationClaim('attempt', 'revision', 1, 'host', 2,
        {'publication': {'inputs': {'pre_change_checkpoint': True}}})
    assert claim.checkpoint_requested
    assert not replace(claim, cold_restore=True).checkpoint_requested


@pytest.mark.parametrize('config', [
    {'s3': {'bucket': 'live-files'}}, {'azure': {'container': 'live-files', 'account name': 'fixture'}},
    {'backup days': 0}, {'backup file storage': False}, {'db': {'backup': False}},
])
def test_stale_backup_mapping_cannot_qualify_cloud_or_disabled_data(config):
    from docassemble_executor.checkpoints import require_complete_backup
    with pytest.raises(RuntimeError):
        require_complete_backup({}, config)
