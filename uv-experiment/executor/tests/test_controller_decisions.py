from docassemble_executor.controller import (
    ACTION_MAINTENANCE,
    ACTION_NORMAL,
    ACTION_RECOVER,
    ACTION_RESTART_RECOVERY,
    decide_boot_action,
)
from docassemble_executor.journal import (
    Journal,
    Operation,
    PHASE_RECOVERY_ATTEMPT_STARTED,
    PHASE_RECOVERY_FAILED,
    PHASE_RECOVERY_SUCCEEDED,
    PHASE_VERIFIED,
)


def test_healthy_runtime_boots_normally():
    decision = decide_boot_action(Journal(phase=PHASE_VERIFIED), runtime_ok=True)
    assert decision.action == ACTION_NORMAL


def test_unhealthy_runtime_recovers_once():
    decision = decide_boot_action(Journal(), runtime_ok=False)
    assert decision.action == ACTION_RECOVER


def test_interrupted_installation_recovers():
    journal = Journal()
    journal.begin_mutation(Operation(id="op", kind="runtime-apply", revision="runtime-r2"))
    decision = decide_boot_action(journal, runtime_ok=True)
    assert decision.action == ACTION_RECOVER


def test_interrupted_installation_after_attempt_does_not_loop():
    journal = Journal()
    journal.begin_mutation(Operation(id="op", kind="runtime-apply", revision="runtime-r2"))
    journal.begin_recovery("interrupted-mutation")
    journal.finish_recovery(False, "artifacts unavailable")
    decision = decide_boot_action(journal, runtime_ok=True)
    assert decision.action == ACTION_MAINTENANCE
    assert "explicit intervention" in decision.reason


def test_interrupted_recovery_does_not_retry():
    journal = Journal(phase=PHASE_RECOVERY_ATTEMPT_STARTED)
    decision = decide_boot_action(journal, runtime_ok=False)
    assert decision.action == ACTION_MAINTENANCE


def test_failed_recovery_stays_in_maintenance():
    decision = decide_boot_action(Journal(phase=PHASE_RECOVERY_FAILED), runtime_ok=True)
    assert decision.action == ACTION_MAINTENANCE


def test_successful_recovery_restarts_restricted_administration():
    decision = decide_boot_action(Journal(phase=PHASE_RECOVERY_SUCCEEDED), runtime_ok=True)
    assert decision.action == ACTION_RESTART_RECOVERY


def test_operator_cannot_force_normal_service_after_interrupted_mutation():
    journal = Journal()
    journal.begin_mutation(Operation(id="op", kind="runtime-apply", revision="runtime-r2"))
    assert decide_boot_action(journal, runtime_ok=True, force="normal").action == ACTION_RECOVER
    assert decide_boot_action(journal, runtime_ok=True, force="recovery").action == ACTION_RECOVER
