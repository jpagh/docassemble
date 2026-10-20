"""Restricted-admin status projection reads desired/last-verified/recovery/failure separately."""
import importlib.util
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    'recovery_status', SOURCE / 'docassemble_webapp/docassemble/webapp/recovery_status.py')
recovery_status = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(recovery_status)


def test_durable_status_projects_separate_states():
    status = {
        'desired_revision': 'rev-desired',
        'verified_running_revision_id': None,
        'last_normally_verified_revision_id': 'rev-historical',
        'runtime_kind': 'core-only',
        'recovery_id': 'attempt:core:1',
        'last_recovery_result': 'succeeded',
        'core_baseline': 'core-r1',
        'failure': {'stage': 'sync', 'reason': 'sync-failed'},
        'error_notification': {'sent': False, 'reason': 'no-error-notification-email',
                               'guidance': 'No "error notification email" is configured; use the logs.'},
    }
    assert recovery_status.desired_revision(status) == 'rev-desired'
    assert recovery_status.last_normally_verified(status) == 'rev-historical'
    assert recovery_status.verified_running(status) is None
    summary = recovery_status.recovery_summary(status)
    assert 'kind=core-only' in summary and 'result=succeeded' in summary and 'recovery_id=attempt:core:1' in summary
    assert recovery_status.failure_summary(status) == 'stage=sync, reason=sync-failed'
    assert 'error notification email' in recovery_status.notification_summary(status)


def test_fixture_status_uses_actual_recovery_block():
    status = {
        'desired_revision': 'r2', 'applied_revision': 'r1',
        'last_normally_verified_revision': 'r1', 'verified_running_revision': None,
        'actual_recovery': {'runtime_kind': 'core-only', 'result': 'succeeded',
                            'recovery_id': 'a:core:1', 'core_baseline': 'core-r1'},
        'last_failure': {'phase': 'recovery_succeeded', 'message': 'core restored', 'at': 'now'},
    }
    assert recovery_status.desired_revision(status) == 'r2'
    assert recovery_status.last_normally_verified(status) == 'r1'
    assert recovery_status.verified_running(status) is None
    assert 'core_baseline=core-r1' in recovery_status.recovery_summary(status)
    assert recovery_status.failure_summary(status) == ('phase=recovery_succeeded, message=core restored, recorded=now')


def test_publication_subdocument_is_fallback_not_override():
    status = {'publication': {'desired_revision_id': 'rev-x', 'last_normally_verified_revision_id': 'rev-y',
                              'verified_running_revision_id': 'rev-x'}}
    assert recovery_status.desired_revision(status) == 'rev-x'
    assert recovery_status.last_normally_verified(status) == 'rev-y'
    assert recovery_status.verified_running(status) == 'rev-x'
