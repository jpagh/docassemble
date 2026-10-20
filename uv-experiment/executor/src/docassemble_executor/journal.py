"""Durable crash journal for managed mutation and recovery.

The journal is deliberately a tiny self-contained JSON document: it must be
readable and resolvable by the executor before the application runtime or the
database management tables are usable.  It is written atomically before any
external effect and it never contains credentials.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FORMAT = 1

PHASE_IDLE = "idle"
PHASE_INSTALLATION_PREPARING = "installation_preparing"
PHASE_MUTATION_STARTED = "mutation_started"
PHASE_VERIFIED = "verified"
PHASE_OVERRIDE = "override"
PHASE_RECOVERY_ATTEMPT_STARTED = "recovery_attempt_started"
PHASE_RECOVERY_SUCCEEDED = "recovery_succeeded"
PHASE_RECOVERY_FAILED = "recovery_failed"

PHASES = (
    PHASE_IDLE,
    PHASE_INSTALLATION_PREPARING,
    PHASE_MUTATION_STARTED,
    PHASE_VERIFIED,
    PHASE_OVERRIDE,
    PHASE_RECOVERY_ATTEMPT_STARTED,
    PHASE_RECOVERY_SUCCEEDED,
    PHASE_RECOVERY_FAILED,
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Operation:
    id: str
    kind: str
    revision: str
    previous_revision: str | None = None
    started_at: str = field(default_factory=utcnow)
    binding: dict[str, Any] | None = None


@dataclass
class Recovery:
    attempt: int
    reason: str
    started_at: str = field(default_factory=utcnow)
    finished_at: str | None = None
    result: str | None = None
    detail: str | None = None


@dataclass
class Failure:
    phase: str
    message: str
    at: str = field(default_factory=utcnow)
    revision: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SchemaWork:
    """Durable start/success evidence for one attempt's schema/setup work.

    Written before the first schema-mutating effect and marked succeeded only
    after that work durably completes, independently of DB reporting. A start
    without a durable success is an uncertain schema outcome that fences a
    silent replay on startup or ordinary Retry.
    """
    attempt_id: str
    revision: str
    identity: str
    started_at: str = field(default_factory=utcnow)
    completed_at: str | None = None
    result: str | None = None


@dataclass
class Journal:
    phase: str = PHASE_IDLE
    operation: Operation | None = None
    verified_revision: str | None = None
    verified_at: str | None = None
    recovery: Recovery | None = None
    failure: Failure | None = None
    error_notification: dict[str, Any] | None = None
    format: int = FORMAT
    attempt_result: dict[str, Any] | None = None
    schema: SchemaWork | None = None
    clearance: dict[str, Any] | None = None
    schema_release: dict[str, Any] | None = None

    # ------------------------------------------------------------------ I/O
    @classmethod
    def load(cls, path: Path) -> "Journal":
        if not path.is_file():
            return cls()
        import json

        raw = json.loads(path.read_text(encoding="utf-8"))
        journal = cls(phase=raw.get("phase", PHASE_IDLE), format=raw.get("format", FORMAT))
        if raw.get("operation"):
            journal.operation = Operation(**raw["operation"])
        journal.attempt_result = raw.get('attempt_result')
        journal.verified_revision = raw.get("verified_revision")
        journal.verified_at = raw.get("verified_at")
        if raw.get("schema"):
            journal.schema = SchemaWork(**raw["schema"])
        journal.clearance = raw.get("clearance")
        journal.schema_release = raw.get("schema_release")
        if raw.get("recovery"):
            journal.recovery = Recovery(**raw["recovery"])
        if raw.get("failure"):
            journal.failure = Failure(**raw["failure"])
        journal.error_notification = raw.get("error_notification")
        return journal

    def save(self, path: Path) -> None:
        import json

        if self.phase not in PHASES:
            raise ValueError("unknown journal phase: " + self.phase)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format": self.format,
            "attempt_result": self.attempt_result,
            "phase": self.phase,
            "operation": asdict(self.operation) if self.operation else None,
            "verified_revision": self.verified_revision,
            "verified_at": self.verified_at,
            "recovery": asdict(self.recovery) if self.recovery else None,
            "failure": asdict(self.failure) if self.failure else None,
            "error_notification": self.error_notification,
            "schema": asdict(self.schema) if self.schema else None,
            "clearance": self.clearance,
            "schema_release": self.schema_release,
        }
        fd, tmp_name = tempfile.mkstemp(prefix=".lifecycle-", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    # ------------------------------------------------------------- mutation
    def begin_mutation(self, operation: Operation, *, schema_unchanged: bool = False) -> None:
        """Record the point after which the runtime venv may be partial.

        A prior uncertain schema outcome and any matching clearance are retained:
        the database schema state is shared across attempts and must not be
        silently forgotten when the runtime is re-synced. Successful evidence is
        also retained when the new attempt must leave the schema unchanged.
        """
        self.phase = PHASE_MUTATION_STARTED
        self.operation = operation
        self.attempt_result = None
        self.recovery = None
        self.failure = None
        self.error_notification = None
        if not schema_unchanged and not self.schema_uncertain:
            self.schema = None
            self.clearance = None

    def record_verified(self, revision: str) -> None:
        self.phase = PHASE_VERIFIED
        self.verified_revision = revision
        self.verified_at = utcnow()
        self.failure = None

    def record_override(self) -> None:
        """Usable development code is not verification of the recorded lock."""
        self.phase = PHASE_OVERRIDE
        self.failure = None

    # ------------------------------------------------------------- recovery
    @property
    def recovery_attempted(self) -> bool:
        return self.recovery is not None and self.recovery.attempt >= 1

    def begin_recovery(self, reason: str) -> None:
        """Mark that an automatic core restoration is about to run.

        The marker is written before restoration so that a crash during
        restoration cannot turn into an automatic retry loop.  The failed
        installation evidence is preserved.
        """
        attempt = (self.recovery.attempt if self.recovery else 0) + 1
        interrupted_installation = self.phase == PHASE_MUTATION_STARTED
        self.phase = PHASE_RECOVERY_ATTEMPT_STARTED
        self.recovery = Recovery(attempt=attempt, reason=reason)
        if self.failure is None:
            revision = self.operation.revision if self.operation else self.verified_revision
            self.failure = Failure(
                phase=PHASE_MUTATION_STARTED if interrupted_installation else 'runtime-probe',
                message="installation was interrupted before its result was verified" if interrupted_installation else reason,
                revision=revision,
            )

    def finish_recovery(self, success: bool, detail: str | None = None) -> None:
        if self.recovery is None:
            raise ValueError("finish_recovery called without begin_recovery")
        self.phase = PHASE_RECOVERY_SUCCEEDED if success else PHASE_RECOVERY_FAILED
        self.recovery.finished_at = utcnow()
        self.recovery.result = "succeeded" if success else "failed"
        self.recovery.detail = detail
        if not success:
            self.failure = Failure(
                phase=PHASE_RECOVERY_FAILED,
                message=detail or "core restoration failed",
                revision=self.operation.revision if self.operation else self.verified_revision,
            )

    def record_failure(self, phase: str, message: str, revision: str | None = None) -> None:
        self.failure = Failure(phase=phase, message=message, revision=revision)

    # --------------------------------------------------------------- schema
    @property
    def schema_uncertain(self) -> bool:
        """Schema work started without a durable successful completion."""
        return self.schema is not None and self.schema.result != 'succeeded'

    @property
    def schema_succeeded(self) -> bool:
        """Durable schema/setup success evidence, independent of DB reporting."""
        return self.schema is not None and self.schema.result == 'succeeded'

    def begin_schema(self, operation: Operation, identity: str) -> None:
        """Durably mark schema/setup start for one attempt before its effects.

        Rerunning uncertain work requires a matching operator clearance; an
        ordinary attempt cannot overwrite a previous uncertain outcome silently.
        """
        if self.schema_uncertain and not self.schema_cleared:
            raise ValueError('uncertain schema requires operator clearance')
        self.schema = SchemaWork(attempt_id=operation.id, revision=operation.revision, identity=identity)
        self.clearance = None

    def record_schema_success(self) -> None:
        """Mark schema/setup success independently of DB reporting."""
        if self.schema is None:
            raise ValueError('record_schema_success called without begin_schema')
        self.schema.result = 'succeeded'
        self.schema.completed_at = utcnow()

    @property
    def schema_cleared(self) -> bool:
        """True when a recorded clearance is bound to the current uncertain job."""
        clearance, schema = self.clearance, self.schema
        return bool(schema is not None and isinstance(clearance, dict) and
                    clearance.get('attempt_id') == schema.attempt_id and
                    clearance.get('schema_identity') == schema.identity and
                    isinstance(clearance.get('observed_schema'), str) and clearance['observed_schema'])

    def record_clearance(self, clearance: dict[str, Any]) -> None:
        """Persist validated attempt/schema-bound administrator/operator clearance."""
        if self.schema is None:
            raise ValueError('no uncertain schema to clear')
        if (clearance.get('attempt_id') != self.schema.attempt_id or
                clearance.get('schema_identity') != self.schema.identity or
                not isinstance(clearance.get('observed_schema'), str) or not clearance['observed_schema']):
            raise ValueError('clearance does not match the uncertain schema attempt')
        self.clearance = clearance

    # ------------------------------------------------------------ queries
    def mutation_possibly_partial(self) -> bool:
        """True when the last mutation never reached a durable verified result."""
        return self.phase == PHASE_MUTATION_STARTED

    def recovery_interrupted(self) -> bool:
        """True when a previous automatic restoration did not finish."""
        return self.phase == PHASE_RECOVERY_ATTEMPT_STARTED

    def in_recovery_state(self) -> bool:
        return self.phase in (PHASE_RECOVERY_ATTEMPT_STARTED, PHASE_RECOVERY_SUCCEEDED, PHASE_RECOVERY_FAILED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_result": self.attempt_result,
            "phase": self.phase,
            "operation": asdict(self.operation) if self.operation else None,
            "verified_revision": self.verified_revision,
            "verified_at": self.verified_at,
            "recovery": asdict(self.recovery) if self.recovery else None,
            "failure": asdict(self.failure) if self.failure else None,
            "error_notification": self.error_notification,
            "schema": asdict(self.schema) if self.schema else None,
            "clearance": self.clearance,
            "schema_release": self.schema_release,
        }
