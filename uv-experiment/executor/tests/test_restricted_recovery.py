"""Restricted recovery truthfulness, ingress allowlist and error-notification durability."""
from pathlib import Path

from docassemble.base.managed_qualification import bridge_http_path
from docassemble_executor.journal import Journal, Operation
from docassemble_executor.state import AppliedState, DesiredState
from docassemble_executor.status import MODE_MAINTENANCE, build_status


def test_status_separates_desired_last_verified_recovery_and_failure():
    journal = Journal()
    journal.begin_mutation(Operation('attempt', 'published-apply', 'rev-desired', binding={}))
    journal.begin_recovery('published application failed')
    journal.finish_recovery(True, 'core restored')
    journal.attempt_result = {'identity': {}, 'outcome': 'failed-stopped', 'mutation_started': True,
        'child_safe': True, 'normal_verified': False,
        'failure': {'stage': 'sync', 'reason': 'sync-failed'},
        'recovery': {'id': 'attempt:core:1', 'attempt': 1, 'result': 'succeeded',
                     'core_baseline': {'image_target': 'image', 'bundle': 'core-r1', 'bundle_digest': 'b' * 64},
                     'inventory': {'core': '1.0'}}}
    status = build_status(mode=MODE_MAINTENANCE, journal=journal, desired=DesiredState(revision='rev-desired'),
                          applied=AppliedState(revision='rev-previous', core_baseline='core-r1'), checks=[], schema=None,
                          recovery_available=True)
    assert status['desired_revision'] == 'rev-desired'
    assert status['applied_revision'] == 'rev-previous'
    # A recovered core-only state never presents history as the running revision.
    assert status['verified_running_revision'] is None
    assert status['last_normally_verified_revision'] is None
    assert status['actual_recovery'] == {'runtime_kind': 'core-only', 'recovery_id': 'attempt:core:1',
                                         'result': 'succeeded', 'core_baseline': 'core-r1'}
    assert status['failure'] == {'stage': 'sync', 'reason': 'sync-failed'}
    assert status['recovery_available'] is True


def test_journal_error_notification_is_durable_and_cleared_by_mutation(tmp_path):
    journal = Journal()
    journal.begin_recovery('published application failed')
    journal.error_notification = {'sent': False, 'reason': 'no-error-notification-email',
                                  'guidance': 'No "error notification email" is configured.'}
    journal.finish_recovery(True)
    path = tmp_path / 'lifecycle.json'
    journal.save(path)
    assert Journal.load(path).error_notification == journal.error_notification
    reloaded = Journal.load(path)
    reloaded.begin_mutation(Operation('next', 'published-apply', 'rev', binding={}))
    assert reloaded.error_notification is None


def test_recovery_publication_fallback_imports_uuid():
    # Regression guard for the P0: the repair/retry POST falls back to
    # uuid.uuid4() when no idempotency key is submitted, so uuid must be imported.
    source = (Path(__file__).resolve().parents[3] /
              'docassemble_webapp/docassemble/webapp/recovery.py').read_text()
    assert 'import uuid' in source
    assert 'uuid.uuid4()' in source


def test_recovery_page_renders_flashes_and_repair_approve_control():
    # The repair flow must expose a reachable approve control and visible feedback
    # on the recovery page itself (packages_bp is not registered in recovery mode).
    source = (Path(__file__).resolve().parents[3] /
              'docassemble_webapp/docassemble/webapp/recovery.py').read_text()
    assert 'get_flashed_messages' in source
    assert 'recovery_repair_candidate' in source
    assert 'name="action" value="approve"' in source
    # The approve branch must re-read the current readiness digest server-side;
    # a queued candidate has no digest yet, so a stored one can never be valid.
    approve = source.split('elif action == "approve":', 1)[1].split('else:', 1)[0]
    assert 'service.status(actor, candidate_id)' in approve
    assert 'current.get("result_digest")' in approve
    assert 'result_digest' not in source.split('name="action" value="approve"', 1)[1].split('</form>', 1)[0]


def test_bridge_ingress_admits_required_application_but_restricts_recovery_and_writers():
    # I2: normal ingress must admit config/logs/interviews; recovery still must
    # not admit workloads, and uncoordinated account/grant writers stay closed.
    for path in ('/recovery', '/recovery/status.json', '/recovery/publication', '/recovery/repair',
                 '/package_preview', '/health_check', '/user/sign-in'):
        assert bridge_http_path(path), path
    for path in ('/config', '/logs', '/interview', '/i', '/interviews', '/', '/list'):
        assert bridge_http_path(path), path
    for path in ('/admin/config', '/recovery/other', '/api/package_nonexistent',
                 '/user/register', '/user/invite', '/userlist', '/privileges', '/manage_api',
                 '/api/user', '/api/privileges'):
        assert not bridge_http_path(path), path
        assert not bridge_http_path(path, recovery=True), path
    for path in ('/config', '/logs', '/logfile/web.log', '/recovery', '/recovery/publication', '/user/sign-in'):
        assert bridge_http_path(path, recovery=True), path
    for path in ('/', '/interview', '/i', '/interviews', '/list', '/api/package', '/package_preview'):
        assert not bridge_http_path(path, recovery=True), path
