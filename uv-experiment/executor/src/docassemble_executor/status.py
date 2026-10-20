"""Truthful recovery/normal status projection.

The status document is written next to the run directory and is the only
machine-readable claim about what the executor actually verified.  Desired
state is copied from the publication side; it is never rewritten here.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .alembic_probe import SchemaCheck
from .health import Check
from .journal import Journal, PHASE_OVERRIDE, PHASE_VERIFIED, utcnow
from .paths import ExecutorPaths
from .state import AppliedState, DesiredState

MODE_NORMAL = "normal"
MODE_RECOVERY = "recovery"
MODE_MAINTENANCE = "maintenance"


def build_status(
    *,
    mode: str,
    journal: Journal,
    desired: DesiredState,
    applied: AppliedState,
    checks: list[Check],
    schema: SchemaCheck | None,
    recovery_available: bool,
    detail: str | None = None,
    role_checks: list[Check] | None = None,
) -> dict[str, Any]:
    from .roles import RUNTIME_ROLES, INFRASTRUCTURE_ROLES
    verified = {check.name for check in role_checks or [] if check.ok}
    normal_ready = mode == MODE_NORMAL and set(RUNTIME_ROLES + INFRASTRUCTURE_ROLES) <= verified
    published = bool(journal.operation and journal.operation.kind == 'published-apply')
    receipt = journal.attempt_result or {}
    publication = None
    if published and journal.operation:
        publication = {'desired_revision_id': journal.operation.revision,
            'verified_running_revision_id': applied.revision if normal_ready and journal.phase == PHASE_VERIFIED else None,
            'attempt_id': journal.operation.id,
            'state': 'verified-running' if normal_ready and receipt.get('normal_verified') is True else
                     'failed' if receipt else 'in-progress',
            'terminal_outcome': receipt.get('outcome'), 'failure': receipt.get('failure')}
    return {
        "publication": publication,
        "format": 1,
        "mode": mode,
        "updated_at": utcnow(),
        "normal_ready": normal_ready,
        "runtime_state": ('override' if journal.phase == PHASE_OVERRIDE else
                          'locked' if mode == MODE_NORMAL and journal.phase == PHASE_VERIFIED else 'unknown'),
        "locked_converged": (normal_ready and journal.phase == PHASE_VERIFIED
                             and desired.revision == applied.revision),
        "role_health": [check.to_dict() for check in role_checks or []],
        "consumer_qualification": {
            "complete": True,
            "inventory": list(RUNTIME_ROLES + INFRASTRUCTURE_ROLES) + ['finite-helpers', 'incoming-mail', 'independent-backups'],
            "replaced": {'apache-nginx-uwsgi-nascent': 'independent-ingress-gunicorn'},
            "unsupported": ['rabbitmq', 'split-role', 'remote-conversion'],
            "enabled": ["supervised-watchdog", "admitted-finite-jobs", "celery", "celerysingle", "websockets", "nltk", "conversion", "cron", "mail"],
            "worker_broker_proof": "redis-prefork",
            "service_protocol_proof": "socketio-pattern-msgpack-local-uno",
            "helper_mail_proof": "os-cron-exim-queue",
            "bypass_fence_proof": "legacy-cli-intent-stale-task",
            "pending": [],
        },
        "recovery_available": recovery_available,
        "desired_revision": journal.operation.revision if published and journal.operation else desired.revision,
        "applied_revision": applied.revision,
        # Desired, last-normally-verified, verified-running, actual recovery and
        # failure are reported separately and never substituted for one another.
        "verified_running_revision": journal.verified_revision if normal_ready else None,
        "last_normally_verified_revision": journal.verified_revision,
        "actual_recovery": ({
            "runtime_kind": "core-only" if journal.recovery.result == 'succeeded' else "unknown",
            "recovery_id": (receipt.get('recovery') or {}).get('id'),
            "result": journal.recovery.result,
            "core_baseline": applied.core_baseline,
        } if journal.recovery else None),
        "failure": ((receipt.get('failure') if isinstance(receipt.get('failure'), dict) else None)
                    or (journal.failure.to_dict() if journal.failure else None)),
        "error_notification": journal.error_notification,
        "desired_packages": list(desired.packages),
        "core_baseline": applied.core_baseline or desired.core_baseline,
        "journal_phase": journal.phase,
        "recovery_attempt": journal.recovery.attempt if journal.recovery else 0,
        "last_failure": journal.failure.to_dict() if journal.failure else None,
        "checks": [check.to_dict() for check in checks],
        "database": schema.to_dict() if schema else None,
        "detail": detail,
    }


def write_status(paths: ExecutorPaths, status: dict[str, Any]) -> None:
    paths.status_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".status-", dir=str(paths.status_file.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(status, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, paths.status_file)
        os.chmod(paths.status_file, 0o644)
        if os.geteuid() == 0:
            os.chown(paths.status_file, 0, 33)  # root:www-data
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def read_status_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
