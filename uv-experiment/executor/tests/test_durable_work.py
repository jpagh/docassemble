"""Continuous image-bridge daemon configuration and subsequent-attempt isolation."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from docassemble_executor.cli import build_parser
from docassemble_executor.installation import InstallationPoller
from docassemble_executor.journal import Journal, Operation
from docassemble_executor.paths import ExecutorPaths


def test_durable_work_cli_selects_independent_preparation_and_installation():
    for kind in ('preparation', 'installation'):
        args = build_parser().parse_args(['run-durable-work', '--kind', kind])
        assert args.kind == kind


def test_subsequent_pending_rollover_does_not_reopen_or_health_replay_old_success(tmp_path, monkeypatch):
    paths = replace(ExecutorPaths.from_env(), state_dir=tmp_path, journal_file=tmp_path / 'lifecycle.json',
                    run_dir=tmp_path / 'run', log_dir=tmp_path / 'log', cache_dir=tmp_path / 'cache',
                    status_file=tmp_path / 'status.json', repair_request_file=tmp_path / 'requests/repair.json')
    identity = {'attempt_id': 'old', 'revision_id': 'previous'}
    journal = Journal(phase='verified', verified_revision='previous',
        operation=Operation('old', 'published-apply', 'previous', binding=identity),
        attempt_result={'identity': identity, 'outcome': 'applied-normal'})
    journal.save(paths.journal_file)
    installation = SimpleNamespace(next_after_terminal=lambda receipt: True, claim_installation=lambda host: None)
    poller = InstallationPoller(installation, paths, tmp_path, tmp_path)
    monkeypatch.setattr(poller, '_report_normal', lambda *a: (_ for _ in ()).throw(AssertionError('old health replay')))
    assert poller.run_once() == 'idle'
    assert (tmp_path / 'attempt-history/old.json').exists()
    assert Journal.load(paths.journal_file).attempt_result is None

    # A known DB terminal is not permission to archive a locally uncertain phase.
    journal.phase = 'mutation_started'
    journal.save(paths.journal_file)
    with pytest.raises(Exception, match='terminal journal phase mismatch'):
        poller.run_once()
    assert Journal.load(paths.journal_file).attempt_result == journal.attempt_result


def test_fresh_fixture_initialization_does_not_start_pollers_without_boot_reconciliation(tmp_path, monkeypatch):
    from docassemble_executor import controller
    from docassemble_executor.health import Check
    paths = replace(ExecutorPaths.from_env(), state_dir=tmp_path, run_dir=tmp_path, status_file=tmp_path / "status.json")
    server = SimpleNamespace(shutdown=lambda: None, server_close=lambda: None)
    context = SimpleNamespace(paths=paths, infra_checks=[], journal=Journal())
    initialized = []
    calls = []
    monkeypatch.setenv("DA_UV_MANAGED", "true")
    monkeypatch.setenv("DA_DURABLE_WORK_STORE", str(tmp_path))
    monkeypatch.setattr(controller, "start_ingress", lambda *a: server)
    monkeypatch.setattr(controller, "_remove_ready", lambda *a: None)
    monkeypatch.setattr(controller, "load_context", lambda *a: context)
    monkeypatch.setattr(controller, "start_supervisor", lambda *a: Check("supervisor", True))
    monkeypatch.setattr(controller, "validate_startup", lambda *a: None)
    monkeypatch.setattr(controller, "prepare_boot", lambda *a: "retain")
    monkeypatch.setattr(controller, "_write_status", lambda *a, **k: None)
    monkeypatch.setattr(controller, "run_normal", lambda *a: initialized.append(True) or "normal")
    monkeypatch.setattr(controller, "Executor", lambda *a, **k: SimpleNamespace(run=lambda: 0))
    monkeypatch.setattr("docassemble_executor.boot_observation.published_boot_required", lambda *a: calls.append("detect") or bool(initialized))
    monkeypatch.setattr("docassemble_executor.supervision.supervisor_command", lambda *a: pytest.fail("polling before reconciliation"))
    assert controller.boot(paths) == 0
    assert initialized == [True] and calls == ["detect"]


def test_healthy_retention_ignores_update_disable_but_never_skips_bundle_gates(tmp_path, monkeypatch):
    from docassemble_executor.installation import InstallationFailure
    monkeypatch.setenv("DA_UV_MANAGED", "true")
    monkeypatch.setenv("DAALLOWUPDATES", "false")
    monkeypatch.setattr("docassemble_executor.installation.load_config", lambda *a: {"allow updates": False})
    poller = InstallationPoller(SimpleNamespace(), ExecutorPaths.from_env(), tmp_path, tmp_path / "absent.json")
    with pytest.raises(InstallationFailure) as error:
        poller._gates(SimpleNamespace())
    assert error.value.reason == "updates-disabled"
    with pytest.raises(InstallationFailure) as error:
        poller._gates(SimpleNamespace(), mutation=False)
    assert error.value.reason == "baseline-invalid"


def test_continuous_same_incarnations_retain_admission_but_never_skip_changed_or_invalid_health(tmp_path, monkeypatch):
    import json
    from docassemble_executor.admission import Admission
    from docassemble_executor.status import write_status
    paths = replace(ExecutorPaths.from_env(), state_dir=tmp_path, run_dir=tmp_path / "run",
        journal_file=tmp_path / "lifecycle.json", status_file=tmp_path / "status.json",
        log_dir=tmp_path / "log", cache_dir=tmp_path / "cache", repair_request_file=tmp_path / "requests/repair.json")
    identity = {"attempt_id": "a", "revision_id": "r", "bundle_digest": "bundle"}
    receipt = {"identity": identity, "outcome": "applied-normal", "inventory": {"core": "1"}}
    Journal(phase="verified", verified_revision="r", operation=Operation("a", "published-apply", "r", binding=identity), attempt_result=receipt).save(paths.journal_file)
    (tmp_path / "publication-reported.json").write_text(json.dumps(identity))
    directory = tmp_path / "bundle"
    directory.mkdir()
    (directory / "installed-material.json").write_text("{}")
    processes = {"web": [["1", "start"]]}
    write_status(paths, {"normal_ready": True, "role_processes": processes})
    Admission(tmp_path).open(consumers=True)
    installation = SimpleNamespace(next_after_terminal=lambda *a: False, normal_replay_allowed=lambda *a, **k: k["reported_only"])
    poller = InstallationPoller(installation, paths, tmp_path, tmp_path)
    monkeypatch.setattr("docassemble_executor.roles.process_identities", lambda *a: processes)
    monkeypatch.setattr("docassemble_executor.installation.verify_retained", lambda *a: None)
    monkeypatch.setattr("docassemble_executor.installation.verify_environment", lambda *a: SimpleNamespace(ok=True))
    monkeypatch.setattr(poller, "_report_normal", lambda *a: pytest.fail("periodic admission closure"))
    assert poller.run_once(retain_live_health=True) == "healthy"
    assert Admission(tmp_path).state()["mode"] == "open"
    monkeypatch.setattr("docassemble_executor.roles.process_identities", lambda *a: {"web": [["2", "new"]]})
    assert not poller._reported_healthy(receipt)
    monkeypatch.setattr("docassemble_executor.roles.process_identities", lambda *a: processes)
    monkeypatch.setattr("docassemble_executor.installation.verify_environment", lambda *a: SimpleNamespace(ok=False))
    assert not poller._reported_healthy(receipt)
    (tmp_path / "publication-reported.json").unlink()
    assert not poller._reported_healthy(receipt)
