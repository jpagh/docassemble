from docassemble_executor.journal import (
    Journal,
    Operation,
    PHASE_IDLE,
    PHASE_MUTATION_STARTED,
    PHASE_RECOVERY_FAILED,
    PHASE_RECOVERY_ATTEMPT_STARTED,
    PHASE_RECOVERY_SUCCEEDED,
    PHASE_VERIFIED,
)


def operation(revision: str = "runtime-r2") -> Operation:
    return Operation(id="op-1", kind="runtime-apply", revision=revision, previous_revision="runtime-r1")


def test_missing_journal_is_idle(tmp_path):
    journal = Journal.load(tmp_path / "lifecycle.json")
    assert journal.phase == PHASE_IDLE
    assert not journal.mutation_possibly_partial()
    assert not journal.recovery_attempted


def test_round_trip(tmp_path):
    path = tmp_path / "lifecycle.json"
    journal = Journal()
    journal.begin_mutation(operation())
    journal.save(path)
    loaded = Journal.load(path)
    assert loaded.phase == PHASE_MUTATION_STARTED
    assert loaded.operation is not None
    assert loaded.operation.revision == "runtime-r2"
    assert loaded.operation.previous_revision == "runtime-r1"
    assert loaded.mutation_possibly_partial()


def test_verified_clears_partial_mutation(tmp_path):
    journal = Journal()
    journal.begin_mutation(operation())
    journal.record_verified("runtime-r2")
    assert journal.phase == PHASE_VERIFIED
    assert journal.verified_revision == "runtime-r2"
    assert not journal.mutation_possibly_partial()
    assert journal.failure is None


def test_begin_recovery_records_failure_evidence_and_one_attempt():
    journal = Journal()
    journal.begin_mutation(operation())
    journal.begin_recovery("interrupted-mutation")
    assert journal.phase == PHASE_RECOVERY_ATTEMPT_STARTED
    assert journal.recovery is not None
    assert journal.recovery.attempt == 1
    assert journal.failure is not None
    assert journal.failure.revision == "runtime-r2"
    assert journal.recovery_interrupted()
    assert journal.recovery_attempted


def test_finish_recovery_failure_records_evidence():
    journal = Journal()
    journal.begin_mutation(operation())
    journal.begin_recovery("interrupted-mutation")
    journal.finish_recovery(False, "locked artifacts unavailable")
    assert journal.phase == PHASE_RECOVERY_FAILED
    assert journal.recovery.result == "failed"
    assert journal.failure is not None
    assert "unavailable" in journal.failure.message
    assert journal.recovery_attempted


def test_finish_recovery_success():
    journal = Journal()
    journal.begin_recovery("runtime-unhealthy")
    journal.finish_recovery(True, "core-r1 validated")
    assert journal.phase == PHASE_RECOVERY_SUCCEEDED
    assert journal.recovery.result == "succeeded"
    assert journal.in_recovery_state()


def test_new_mutation_resets_recovery_marker():
    journal = Journal()
    journal.begin_recovery("runtime-unhealthy")
    journal.finish_recovery(True)
    journal.begin_mutation(Operation(id="op-2", kind="runtime-apply", revision="runtime-r3"))
    assert journal.phase == PHASE_MUTATION_STARTED
    assert journal.recovery is None
    assert not journal.recovery_attempted
