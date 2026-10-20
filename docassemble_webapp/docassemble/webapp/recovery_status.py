"""Dependency-light projection of executor recovery status for restricted admin.

Kept separate from ``recovery.py`` (which imports the full Flask/webapp stack) so
the truthful desired / last-verified / actual-recovery / failure / notification
display can be tested without loading the application runtime. It only formats an
already-authorized status document; it never reads or writes authoritative state.
"""

from __future__ import annotations

from typing import Any


def first(status: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = status.get(key)
        if value is not None:
            return value
    return None


def _publication(status: dict[str, Any]) -> dict[str, Any]:
    value = status.get("publication")
    return value if isinstance(value, dict) else {}


def desired_revision(status: dict[str, Any]) -> Any:
    return first(status, "desired_revision", "desired_revision_id") or _publication(status).get("desired_revision_id")


def last_normally_verified(status: dict[str, Any]) -> Any:
    return (first(status, "last_normally_verified_revision", "last_normally_verified_revision_id")
            or _publication(status).get("last_normally_verified_revision_id"))


def verified_running(status: dict[str, Any]) -> Any:
    return (first(status, "verified_running_revision", "verified_running_revision_id")
            or _publication(status).get("verified_running_revision_id"))


def recovery_summary(status: dict[str, Any]) -> str:
    publication, recovery = _publication(status), status.get("actual_recovery") or {}
    if not isinstance(recovery, dict):
        recovery = {}
    kind = first(recovery, "runtime_kind") or first(status, "runtime_kind", "runtime_state") or publication.get("runtime_kind")
    result = first(recovery, "result") or first(status, "last_recovery_result") or publication.get("last_recovery_result")
    recovery_id = first(recovery, "recovery_id") or first(status, "recovery_id") or publication.get("recovery_id")
    core = first(recovery, "core_baseline") or first(status, "core_baseline")
    if not (kind or result or recovery_id or core):
        return "none recorded"
    return ("kind=" + str(kind) + ", result=" + str(result) + ", recovery_id=" + str(recovery_id)
            + ", core_baseline=" + str(core))


def failure_summary(status: dict[str, Any]) -> str:
    stage = status.get("failure") or {}
    if isinstance(stage, dict) and (stage.get("stage") or stage.get("reason")):
        return "stage=" + str(stage.get("stage")) + ", reason=" + str(stage.get("reason"))
    phase = status.get("last_failure") or {}
    if isinstance(phase, dict) and (phase.get("phase") or phase.get("message")):
        return ("phase=" + str(phase.get("phase")) + ", message=" + str(phase.get("message"))
                + ", recorded=" + str(phase.get("at")))
    return "none recorded"


def notification_summary(status: dict[str, Any]) -> str:
    notification = status.get("error_notification") or {}
    if not isinstance(notification, dict) or not notification:
        return "none recorded"
    if notification.get("sent"):
        return "sent to " + str(notification.get("recipients"))
    return str(notification.get("guidance") or notification.get("reason") or "not sent")
