"""Restricted recovery administration.

In managed recovery mode the core web application serves a deliberately small
surface: normal administrator authentication, configuration editing, log
viewing, accurate recovery/failure status, and a fixture-scoped repair action.
User package imports, preloaded modules/interviews, the startup hook,
Playground, package management, API, and every interview route are excluded.

This module must not import user packages, and it must remain safe to import
from ``setup``/``app_initialize`` before the application is fully built.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, abort, flash, get_flashed_messages, jsonify, redirect, request, session, url_for
from flask_login import current_user
from flask_wtf.csrf import generate_csrf
from docassemble_flask_user import login_required, roles_required
from markupsafe import escape
from werkzeug.exceptions import HTTPException

from docassemble.webapp import recovery_status

def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


RECOVERY_MODE = _env_flag("DARECOVERYMODE")
RECOVERY_RUN_DIR = Path(os.environ.get("DA_RECOVERY_RUN_DIR", "/var/run/docassemble/recovery"))
STATUS_FILE = RECOVERY_RUN_DIR / "status.json"
REPAIR_REQUEST_FILE = RECOVERY_RUN_DIR / 'requests' / "repair-request.json"

recovery_bp = Blueprint("recovery", __name__)


def read_status() -> dict[str, Any]:
    """Read the executor's status document; never raise while rendering."""
    if not STATUS_FILE.is_file():
        return {}
    try:
        payload = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _published_work(status):
    # Only an explicit publication:null proves legacy work. A qualified image
    # and legacy revision fields do not prove publication; missing/unreadable
    # or pre-publication projections cannot prove its absence. Do not use an
    # age rule: valid status can remain unchanged for hours. E independently
    # refuses published journals if this projection disagrees with them.
    return 'publication' not in status or status['publication'] is not None


def _format(value: Any) -> str:
    return str(escape("" if value is None else str(value)))


def _first(status: dict[str, Any], *keys: str) -> Any:
    return recovery_status.first(status, *keys)


def _page_html(status: dict[str, Any], candidate_status: dict[str, Any] | None = None) -> str:
    database = status.get("database") or {}
    checks = status.get("checks") or []
    check_rows = "".join(
        "<li><code>" + _format(check.get("name")) + "</code>: "
        + ("ok" if check.get("ok") else "failed")
        + " &mdash; " + _format(check.get("detail"))
        + "</li>"
        for check in checks
        if isinstance(check, dict)
    )
    if not status:
        status_block = "<p>No executor status has been recorded yet.</p>"
    else:
        desired = recovery_status.desired_revision(status)
        last_verified = recovery_status.last_normally_verified(status)
        running = recovery_status.verified_running(status)
        status_block = f"""
    <table>
      <tr><th>Mode</th><td><code>{_format(status.get('mode'))}</code></td></tr>
      <tr><th>Normal readiness</th><td><code>{_format(status.get('normal_ready'))}</code></td></tr>
      <tr><th>Restricted recovery available</th><td><code>{_format(status.get('recovery_available'))}</code></td></tr>
      <tr><th>Desired revision</th><td><code>{_format(desired)}</code></td></tr>
      <tr><th>Last normally verified revision</th><td><code>{_format(last_verified)}</code></td></tr>
      <tr><th>Verified running revision</th><td><code>{_format(running)}</code></td></tr>
      <tr><th>Actual recovery</th><td><code>{_format(recovery_status.recovery_summary(status))}</code></td></tr>
      <tr><th>Failure</th><td><code>{_format(recovery_status.failure_summary(status))}</code></td></tr>
      <tr><th>Error notification</th><td>{_format(recovery_status.notification_summary(status))}</td></tr>
      <tr><th>Applied revision</th><td><code>{_format(status.get('applied_revision'))}</code></td></tr>
      <tr><th>Core baseline</th><td><code>{_format(status.get('core_baseline'))}</code></td></tr>
      <tr><th>Journal phase</th><td><code>{_format(status.get('journal_phase'))}</code></td></tr>
      <tr><th>Recovery attempts</th><td><code>{_format(status.get('recovery_attempt'))}</code></td></tr>
      <tr><th>Last updated</th><td><code>{_format(status.get('updated_at'))}</code></td></tr>
    </table>
    <h3>Database compatibility</h3>
    <p>Status: <code>{_format(database.get('status'))}</code><br>
    Detail: {_format(database.get('detail'))}</p>
    <h3>Checks</h3>
    <ul>{check_rows}</ul>"""
    csrf_token = generate_csrf()
    messages = get_flashed_messages(with_categories=True)
    message_html = "".join(
        '<p class="' + ('error' if category == 'error' else 'info') + '">' + _format(message) + '</p>'
        for category, message in messages)
    pending = session.get('recovery_repair_candidate') or {}
    preview_html = ""
    approve_form = ""
    if pending:
        preview_html = "<h3>Repair preview</h3>"
        if candidate_status is None:
            preview_html += "<p>Repair preview unavailable or unauthorized. Approval is disabled.</p>"
        else:
            preview = candidate_status.get('preview') or {}
            intent = candidate_status.get('intent_change') or {}
            sections = {
                'Readiness': {key: candidate_status.get(key) for key in
                              ('state', 'stale', 'approval_supported', 'ready_at', 'expires_at')},
                'Requirement and source-policy diff': intent.get('repair_diff'),
                'Warnings': candidate_status.get('warnings', []) + preview.get('warnings', []),
                'Desired-vs-actual-recovery comparison': {
                    'desired_revision_id': candidate_status.get('desired_revision_id'),
                    'desired_diff': preview.get('desired_diff'),
                    'actual_recovery': candidate_status.get('runtime'),
                    'runtime_diff': preview.get('runtime_diff'),
                },
                'Readiness digest': candidate_status.get('result_digest'),
            }
            preview_html += "".join(
                '<h4>' + title + '</h4><pre>' + _format(json.dumps(value, indent=2, default=str)) + '</pre>'
                for title, value in sections.items())
            preview_html += "<p>Refresh this page to update readiness. Unverified recovery has no exact runtime diff.</p>"
            if (candidate_status.get('state') == 'ready' and candidate_status.get('stale') is False
                    and candidate_status.get('approval_supported') is True
                    and candidate_status.get('result_digest') and preview and intent.get('repair_diff')):
                approve_form = f"""
<form method="post" action="{url_for('recovery.publication')}">
  <input type="hidden" name="csrf_token" value="{csrf_token}">
  <input type="hidden" name="action" value="approve">
  <input type="hidden" name="candidate_id" value="{_format(candidate_status.get('candidate_id'))}">
  <button type="submit">Approve the prepared repair</button>
</form>"""
    core_control = ''
    if not _published_work(status):
        core_control = f"""
<h2>Core environment</h2>
<p>The core action asks the independent lifecycle executor to restore and
re-validate the exact retained core environment. It does not change committed
package intent or run legacy package installation.</p>
<form method="post" action="{url_for('recovery.repair')}">
  <input type="hidden" name="csrf_token" value="{csrf_token}">
  <input type="hidden" name="action" value="core-restore">
  <button type="submit">Restore and validate core environment</button>
</form>"""
    return f"""<!DOCTYPE html>
<html><head><title>Restricted recovery</title>
<style>
body {{ font-family: sans-serif; margin: 2rem; max-width: 70rem; }}
table {{ border-collapse: collapse; }} th, td {{ text-align: left; padding: 0.2rem 1rem 0.2rem 0; }}
code {{ background: #eee; padding: 0.1rem 0.3rem; }}
.warning {{ border: 2px solid #a00; padding: 1rem; background: #fff5f5; }}
</style></head><body>
<h1>Restricted recovery administration</h1>
<div class="warning">
<p><strong>Normal service is not ready.</strong> User packages, hooks,
preloaded modules and interviews, Playground, package management, the API, and
background workers are deliberately disabled in this mode.</p>
<p>Signed in as {_format(getattr(current_user, 'email', ''))}.</p>
</div>
<h2>Recovery status</h2>
{status_block}
<h2>Repair and retry</h2>
{message_html}
<h3>Legacy adoption</h3>
<pre>{_format(json.dumps(status.get('adoption') or (status.get('publication') or {}).get('adoption'), indent=2))}</pre>
<p>For failed unpublished adoption, repair the original Package/configuration records or restore required sources, then request fresh preparation. This is not an exact published-revision retry.</p>
<form method="post" action="{url_for('recovery.publication')}">
  <input type="hidden" name="csrf_token" value="{csrf_token}">
  <input type="hidden" name="action" value="adoption-retry">
  <button type="submit">Request fresh legacy adoption preparation</button>
</form>
<p>Retry creates a new attempt of the exact current desired revision without
re-solving. Repair re-solves the committed desired intent against the actual
recovery state and publishes a new revision. Neither action rewrites desired
intent; both preserve the failure evidence above.</p>
<form method="post" action="{url_for('recovery.publication')}">
  <input type="hidden" name="csrf_token" value="{csrf_token}">
  <input type="hidden" name="action" value="repair">
  <button type="submit">Prepare a repair preview</button>
</form>
<form method="post" action="{url_for('recovery.publication')}">
  <input type="hidden" name="csrf_token" value="{csrf_token}">
  <input type="hidden" name="action" value="retry">
  <label>Desired revision <input type="text" name="revision_id" value="{_format(_first(status, 'desired_revision', 'desired_revision_id'))}"></label>
  <button type="submit">Retry the exact desired revision</button>
</form>
{preview_html}
{approve_form}
{core_control}
<h2>Available controls</h2>
<ul>
  <li><a href="{url_for('admin.config_page')}">Configuration editing</a></li>
  <li><a href="{url_for('logs.logs')}">Logs</a></li>
</ul>
</body></html>"""


@recovery_bp.route("/recovery", methods=["GET"])
@login_required
@roles_required(["admin"])
def page() -> Response:
    candidate_status = None
    pending = session.get('recovery_repair_candidate')
    if pending:
        try:
            from docassemble.webapp.packages.managed import internal_publication_service
            candidate_status = internal_publication_service().status(str(current_user.id), pending['candidate_id'])
        except Exception:
            # Status may fail on revoked SQL authority or unavailable services.
            # Never render exception text, stored previews, or an approval control.
            candidate_status = None
    response = Response(_page_html(read_status(), candidate_status), 200, mimetype="text/html")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0"
    return response


@recovery_bp.route("/recovery/status.json", methods=["GET"])
@login_required
@roles_required(["admin"])
def status_json():
    return jsonify(read_status())


@recovery_bp.route("/recovery/repair", methods=["POST"])
@login_required
@roles_required(["admin"])
def repair():
    action = request.form.get('action')
    if action not in (None, 'core-restore'):
        abort(400, 'Unsupported recovery repair action.')
    if _published_work(read_status()):
        # Never enqueue restoration against an existing published attempt.
        if action == 'core-restore':
            abort(409, 'Published or unknown work requires managed retry or repair.')
        return _request_publication('repair')
    # Preserve csrf-only legacy requests when the projection proves no
    # publication. The page submits the explicit core-restore action.
    payload = {
        "requested_by": getattr(current_user, "email", None),
        "requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    REPAIR_REQUEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".repair-", dir=str(REPAIR_REQUEST_FILE.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, REPAIR_REQUEST_FILE)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    flash("A core environment repair was requested. Refresh this page for the result.", "info")
    return redirect(url_for("recovery.page"))


@recovery_bp.route("/recovery/publication", methods=["POST"])
@login_required
@roles_required(["admin"])
def publication():
    return _request_publication(request.form.get('action'))


def _request_publication(action: str | None):
    """Restricted adapter over the accepted prepare/approve/retry publication service.

    Retry reuses the exact published revision (no solve); repair re-solves the
    committed desired intent against the recovery state. Both keep current
    administrator/current-SQL authorization and redaction. There is no general
    API surface here and no automatic retry.
    """
    from docassemble.base.managed_preparation import PreparationError
    from docassemble.webapp.packages.managed import internal_publication_service
    actor = str(current_user.id)
    key = request.form.get("idempotency_key") or str(uuid.uuid4())
    try:
        service = internal_publication_service()
        if action == "migration-retry":
            from docassemble.base.managed_migration import ImageMigration
            ImageMigration(service.engine, service.prefix).request_retry(actor, authorize=service._authorize)
            flash("Fresh unpublished replacement preparation requested; committed intent is retained.", "info")
        elif action == "adoption-retry":
            from docassemble.base.managed_adoption import LegacyAdoption
            LegacyAdoption(service.engine, service.prefix).request_retry(actor, authorize=service._authorize)
            flash("Fresh unpublished adoption preparation requested; original evidence is retained.", "info")
        elif action == "repair":
            candidate = service.prepare(actor, None, key, operation="repair")
            status = service.status(actor, candidate)
            session['recovery_repair_candidate'] = {'candidate_id': candidate}
            flash("Repair preview prepared for the committed desired intent (state " + str(status.get("state")) + "). Review and approve it below once it is ready.", "info")
        elif action == "retry":
            result = service.retry(actor, request.form.get("revision_id") or "", key)
            flash("Retry accepted for revision " + str(result.get("revision_id")) + "; a new attempt is pending.", "info")
        elif action == "approve":
            # Re-read the current readiness digest: a queued candidate has none yet.
            candidate_id = request.form.get("candidate_id") or ""
            current = service.status(actor, candidate_id)
            if current.get("state") != "ready":
                raise PreparationError("candidate not ready for approval")
            result = service.approve(actor, candidate_id, current.get("result_digest"), key)
            session.pop('recovery_repair_candidate', None)
            flash("Approval published revision " + str(result.get("revision_id")) + "; installation is pending.", "info")
        else:
            flash("Unsupported recovery publication action.", "error")
    except PreparationError as error:
        flash(str(error), "error")
    except Exception:
        flash("Managed recovery request rejected.", "error")
    return redirect(url_for("recovery.page"))


def init_app(app):
    """Gate every request, including routes retained for core authentication.

    Core login (including MFA/SSO), static assets, configuration and logs remain
    available. Other core views can start tasks or resolve user file references
    and must not accidentally become a recovery workload surface.
    """
    @app.errorhandler(HTTPException)
    def recovery_http_error(error):
        # Ordinary error rendering is interview-aware; never enter it here.
        return error.get_response()

    @app.errorhandler(Exception)
    def recovery_server_error(error):
        app.logger.exception('Restricted recovery request failed')
        return Response('Recovery administration failed; see the executor and web logs.', 500, mimetype='text/plain')

    public_endpoints = {
        'static', 'main.health_check', 'main.health_status',
        'main.css_bundle', 'main.js_bundle', 'main.js_admin_bundle',
        'main.js_bundle_wrap', 'main.js_bundle_no_query',
        'main.post_sign_in',
    }
    admin_endpoints = {
        'admin.config_page', 'logs.logs', 'logs.logfile', 'main.rjs',
        'recovery.page', 'recovery.status_json', 'recovery.repair', 'recovery.publication',
        'packages.package_preview',
    }

    @app.before_request
    def restrict_requests():
        if request.endpoint is None:
            return None
        endpoint = request.endpoint
        if endpoint in admin_endpoints:
            if endpoint == 'main.rjs' and not current_user.is_authenticated:
                return redirect(url_for('user.login', next=request.url))
            if current_user.is_authenticated and not current_user.has_role('admin'):
                abort(403)
            # Existing decorators enforce login; do not replace authentication.
            return None
        if endpoint in public_endpoints or endpoint.startswith(('user.', 'auth.', 'phonelogin.')):
            return None
        if endpoint == 'main.package_static' and request.view_args.get('package') in ('docassemble.base', 'docassemble.webapp'):
            return None
        if endpoint.startswith('users.mfa_'):
            return None
        abort(404)
