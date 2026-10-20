"""Lifecycle controller for the independent recovery proof.

The controller is deliberately small and explicit: it reads durable local
state, starts infrastructure through OS tooling, probes the runtime through
subprocesses, restores the retained core-only bundle with a frozen uv sync, and
then serves either normal service, restricted recovery, or an unauthenticated
static maintenance endpoint.  It is not wired into Supervisor or the full
publication flow yet; ticket 01 only asks for the runnable recovery proof.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from yaml import YAMLError

from .admission import Admission, start_ingress
from .authority import MutationLock, RuntimeLock, run_runtime
from .alembic_probe import SchemaCheck, check_schema
from .bundles import verify_bundle
from .config import DatabaseSettings, database_settings, load_config
from .health import Check, probe_runtime, runtime_python
from .inventory import verify_environment
from .journal import (
    Journal,
    Operation,
    PHASE_IDLE,
    PHASE_MUTATION_STARTED,
    PHASE_OVERRIDE,
    PHASE_RECOVERY_ATTEMPT_STARTED,
    PHASE_RECOVERY_FAILED,
    PHASE_RECOVERY_SUCCEEDED,
)
from .maintenance import start_maintenance_server
from .paths import ExecutorPaths
from .services import ensure_role_and_database, start_postgres, start_redis, pg_is_ready, redis_is_ready
from .supervision import start_supervisor
from .state import AppliedState, DesiredState
from .startup import (
    ManagedModeError,
    STARTUP_FAILURE_MESSAGES,
    UnwritablePathError,
    managed_requested,
    prepare_boot,
    record_initialization,
    validate_startup,
)
from .status import MODE_MAINTENANCE, MODE_NORMAL, MODE_RECOVERY, build_status, write_status
from .uvsync import SyncResult, sync_frozen
from .webapp import drain_web, start_web, stop_web, wait_for_http

ACTION_NORMAL = "normal"
ACTION_RECOVER = "recover"
ACTION_RESTART_RECOVERY = "restart-recovery"
ACTION_MAINTENANCE = "maintenance"


def log(message: str) -> None:
    print("da-executor[%s]: %s" % (time.strftime("%Y-%m-%dT%H:%M:%S"), message), flush=True)


@dataclass
class BootDecision:
    action: str
    reason: str


def decide_boot_action(journal: Journal, runtime_ok: bool, force: str | None = None) -> BootDecision:
    if force == "recovery":
        return BootDecision(ACTION_RECOVER, "recovery forced by operator")
    if journal.phase == PHASE_OVERRIDE:
        return BootDecision(ACTION_RECOVER, 'full restart leaves development override and restores restricted core')
    if journal.phase == PHASE_MUTATION_STARTED:
        if journal.recovery_attempted:
            return BootDecision(
                ACTION_MAINTENANCE,
                "interrupted installation and automatic recovery was already attempted",
            )
        return BootDecision(ACTION_RECOVER, "interrupted installation detected; restoring last known-good core")
    if journal.phase == PHASE_RECOVERY_ATTEMPT_STARTED:
        return BootDecision(
            ACTION_MAINTENANCE,
            "a previous automatic recovery was interrupted; explicit intervention is required",
        )
    if journal.phase == PHASE_RECOVERY_FAILED:
        return BootDecision(
            ACTION_MAINTENANCE,
            "a previous automatic recovery failed; explicit intervention is required",
        )
    if journal.phase == PHASE_RECOVERY_SUCCEEDED:
        if runtime_ok:
            return BootDecision(ACTION_RESTART_RECOVERY, "verified restricted recovery is restarted")
        return BootDecision(ACTION_MAINTENANCE, "recovery was recorded but the runtime no longer validates")
    if runtime_ok:
        return BootDecision(ACTION_NORMAL, "runtime validates against the retained core")
    if journal.recovery_attempted:
        return BootDecision(
            ACTION_MAINTENANCE,
            "runtime is unhealthy and automatic recovery was already attempted",
        )
    return BootDecision(ACTION_RECOVER, "runtime is unhealthy; restoring last known-good core")


@dataclass
class Context:
    paths: ExecutorPaths
    journal: Journal
    desired: DesiredState
    applied: AppliedState
    manifest: dict[str, Any]
    config: dict[str, Any]
    db: DatabaseSettings
    infra_checks: list[Check] = field(default_factory=list)

    @property
    def identity(self) -> dict[str, Any]:
        return self.manifest.get("identity", {})

    @property
    def expected_core_versions(self) -> dict[str, str]:
        from .selected_baseline import load_baselines, release_versions
        selected = load_baselines(self.paths).get('selected_baseline')
        return release_versions(selected) if selected else self.identity.get("core_versions", {})

    @property
    def core_baseline(self) -> str:
        return self.applied.core_baseline or self.identity.get("core_baseline", "r1")

    @property
    def default_runtime_revision(self) -> str:
        return self.identity.get("runtime_revision", "r1")

    def bundle_name(self, kind: str, revision: str) -> str:
        return kind + "-" + revision


def load_context(paths: ExecutorPaths, start_infrastructure: bool = True) -> Context:
    from .checkpoint_restore import require_restore_ready
    require_restore_ready(paths)
    paths.ensure_directories()
    manifest = {}
    if paths.manifest_file.is_file():
        import json

        manifest = json.loads(paths.manifest_file.read_text(encoding="utf-8"))
    config: dict[str, Any] = {}
    try:
        config = load_config(paths.config_file)
    except (FileNotFoundError, ValueError) as error:
        log(safe_boot_failure(error))
    db = database_settings(config) if config else DatabaseSettings()
    context = Context(
        paths=paths,
        journal=Journal.load(paths.journal_file),
        desired=DesiredState.load(paths.intent_file),
        applied=AppliedState.load(paths.applied_file),
        manifest=manifest,
        config=config,
        db=db,
    )
    if start_infrastructure:
        context.infra_checks.append(start_postgres())
        context.infra_checks.append(start_redis())
        if context.db.user and all(check.ok for check in context.infra_checks):
            context.infra_checks.append(ensure_role_and_database(context.db))
    return context


def _persist(context: Context) -> None:
    context.journal.save(context.paths.journal_file)


def _write_status(
    context: Context,
    mode: str,
    *,
    probe_checks: list[Check] | None = None,
    schema: SchemaCheck | None = None,
    recovery_available: bool = False,
    detail: str | None = None,
    role_checks: list[Check] | None = None,
) -> dict[str, Any]:
    checks = list(context.infra_checks)
    if probe_checks:
        checks.extend(probe_checks)
    status = build_status(
        mode=mode,
        journal=context.journal,
        desired=context.desired,
        applied=context.applied,
        checks=checks,
        schema=schema,
        recovery_available=recovery_available,
        detail=detail,
        role_checks=role_checks,
    )
    if mode == MODE_NORMAL:
        from .roles import RoleCheck
        status['role_processes'] = {check.name: check.processes for check in role_checks or []
                                    if isinstance(check, RoleCheck)}
    write_status(context.paths, status)
    return status


def _remove_ready(paths: ExecutorPaths) -> None:
    (paths.run_dir / "ready").unlink(missing_ok=True)


def _mark_ready(paths: ExecutorPaths) -> None:
    (paths.run_dir / "ready").touch()


def _web_alive(paths: ExecutorPaths, mode: str) -> bool:
    pid_file = paths.run_dir / ("web-" + mode + ".pid")
    if not pid_file.is_file():
        return False
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        try:
            if os.waitpid(pid, os.WNOHANG)[0] == pid:
                return False
        except ChildProcessError:
            pass
        os.kill(pid, 0)
    except (ValueError, ProcessLookupError, PermissionError):
        return False
    return True


def _create_tables(context: Context) -> Check:
    """Run normal-mode schema initialization from the selected runtime.

    This is never called in recovery: recovery must not run older setup or
    stamp a schema.
    """
    python = runtime_python(context.paths.runtime_venv)
    if not python.is_file():
        return Check("create-tables", False, "runtime interpreter is missing")
    env = {
        "PATH": str(context.paths.runtime_venv / "bin") + ":/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": "/var/www",
        "CONTAINERROLE": ":all:",
        "DA_CONFIG_FILE": str(context.paths.config_file),
        "DA_UV_MANAGED": os.environ.get('DA_UV_MANAGED', 'false'),
        "DA_ADMIN_EMAIL": os.environ.get("DA_ADMIN_EMAIL", "admin@example.com"),
        "DA_ADMIN_PASSWORD": os.environ.get("DA_ADMIN_PASSWORD", "password"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    # The legacy module CLI is refused even while serving normally. Schema
    # initialization has its own controlled, leased executor entrypoint.
    command = [str(python), '-c',
               'import docassemble.base.config; docassemble.base.config.load(); '
               'from docassemble.webapp.create_tables import main; main()']
    if os.geteuid() == 0:
        command = ["runuser", "-u", "www-data", "--"] + command
    completed = run_runtime(command, env=env, timeout=900)
    output = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
    log_path = context.paths.log_dir / "create-tables.log"
    log_path.write_text(output, encoding="utf-8")
    if completed.returncode != 0:
        return Check("create-tables", False, "\n".join(output.splitlines()[-5:]))
    return Check("create-tables", True, "normal-mode schema initialization completed")


def _start_web(context: Context, recovery: bool) -> bool:
    mode = "recovery" if recovery else "normal"
    if _web_alive(context.paths, mode) and wait_for_http(context.paths.backend_port, '/health_check', timeout=2):
        return True
    server = start_web(context.paths, recovery)
    log("started " + mode + " web server pid " + str(server.pid))
    if not wait_for_http(context.paths.backend_port, "/health_check", timeout=300):
        log(mode + " web server did not answer /health_check")
        stop_web(context.paths)
        return False
    if not wait_for_http(context.paths.backend_port, "/user/sign-in", timeout=60, expected=(200, 302)):
        log(mode + " web server did not serve the login page")
        stop_web(context.paths)
        return False
    return True


def _failure_detail(probe: Any) -> str:
    for check in probe.checks:
        if not check.ok:
            return check.name + ": " + check.detail
    return "runtime validation failed"


def _record_error_notification(context: Context, *, subject: str, body: str) -> dict[str, Any]:
    """Trigger the existing configured error email; record honest guidance otherwise."""
    from .error_notification import notify

    outcome = notify(context.paths, subject=subject, body=body)
    context.journal.error_notification = outcome
    _persist(context)
    log("error notification: " + ("sent" if outcome.get("sent") else str(outcome.get("reason"))))
    return outcome


def _recovery_failed(context: Context, detail: str, schema: SchemaCheck | None = None) -> str:
    Admission(context.paths.state_dir).close(detail)
    context.journal.finish_recovery(False, detail)
    _persist(context)
    _record_error_notification(
        context,
        subject="docassemble managed recovery failed",
        body="Managed core recovery failed and restricted administration is unavailable.\n\n" + detail,
    )
    _remove_ready(context.paths)
    stop_web(context.paths)
    _write_status(context, MODE_MAINTENANCE, schema=schema, recovery_available=False, detail=detail)
    log("recovery failed: " + detail)
    return MODE_MAINTENANCE


def run_recovery(context: Context, reason: str) -> str:
    """Restore the retained core-only bundle into the runtime venv once."""
    with MutationLock(context.paths.state_dir, wait_seconds=5):
        context = load_context(context.paths, start_infrastructure=False)
        if context.journal.operation and context.journal.operation.kind in ('published-apply', 'published-materialization'):
            log('legacy core restoration refused for published work; use managed retry or repair')
            return status(context.paths).get('mode', MODE_MAINTENANCE)
        if not enter_maintenance(context):
            return status(context.paths).get('mode', MODE_MAINTENANCE)
        if not pg_is_ready() or not redis_is_ready():
            _write_status(context, MODE_MAINTENANCE, detail='independent infrastructure is unavailable; recovery refused before mutation')
            return MODE_MAINTENANCE
        return _restore_core(context, reason)


def _restore_core(context: Context, reason: str, *, serve: bool = True) -> str:
    Admission(context.paths.state_dir).close(reason, mode='mutating')
    log("starting recovery: " + reason)
    context.journal.begin_recovery(reason)
    _persist(context)
    stop_web(context.paths)
    _remove_ready(context.paths)
    _write_status(context, MODE_MAINTENANCE, detail="core restoration in progress")
    from .selected_baseline import load_baselines, selected_directory, core_versions, release_versions
    retained = load_baselines(context.paths)
    recovery = retained.get('recovery_baseline', {})
    if recovery.get('core_bundle'):
        baseline = recovery['core_bundle']['digest']
        name = 'selected-core'
        try:
            bundle_dir = selected_directory(Path(retained['store']), recovery)
            expected = {'distributions': core_versions(recovery),
                        'files': json.loads((bundle_dir / 'installed-material.json').read_text())}
        except Exception:
            return _recovery_failed(context, 'recovery material unavailable')
        versions, compatible = release_versions(recovery), ()
    else:
        baseline = context.identity.get('core_baseline', 'r1')
        name = context.bundle_name("core", baseline)
        bundle_dir = context.paths.core_bundle(baseline)
        ok, detail = verify_bundle(bundle_dir, context.manifest, name)
        if not ok:
            return _recovery_failed(context, "recovery material unavailable: " + detail)
        expected = context.manifest['inventories'][name]
        versions = context.identity.get('core_versions', {})
        compatible = tuple(context.manifest.get('compatible_schema_revisions', ()))
    result: SyncResult = sync_frozen(
        bundle_dir,
        context.paths.runtime_venv,
        context.paths.uv_binary,
        context.paths.cache_dir,
        log_dir=context.paths.log_dir,
        reinstall=True,
        env_extra={'UV_OFFLINE': '1', 'UV_NO_CONFIG': '1'},
    )
    if not result.ok:
        return _recovery_failed(context, "core restoration failed: " + result.describe())
    installed = verify_environment(context.paths.runtime_venv, expected['distributions'], expected['files'])
    if not installed.ok:
        return _recovery_failed(context, 'restored installed target did not validate: ' + installed.detail)
    probe = probe_runtime(context.paths.runtime_venv, context.paths.config_file, versions)
    if not probe.ok:
        return _recovery_failed(context, "restored core did not validate: " + _failure_detail(probe))
    if retained.get('schema_mode') == 'unchanged' or (retained.get('migration') and not recovery.get('core_bundle')):
        from .rollback_schema import check_rollback_schema
        schema = check_rollback_schema(context.paths.runtime_venv, context.db, bundle_dir)
    else:
        schema = check_schema(context.paths.runtime_venv, context.db, compatible)
    if not schema.usable:
        return _recovery_failed(context, "database is not usable: " + schema.detail, schema=schema)
    if serve and not _start_web(context, recovery=True):
        return _recovery_failed(context, "restored core could not start restricted administration")
    context.journal.finish_recovery(True, "core " + name + " restored and validated")
    _persist(context)
    _record_error_notification(
        context,
        subject="docassemble managed environment in restricted recovery",
        body=("Managed installation failed and the retained core environment was restored. "
              "Normal workloads and interviews are suppressed. Sign in to restricted "
              "administration to retry the desired revision or approve a repair."),
    )
    context.applied.core_baseline = baseline
    context.applied.save(context.paths.applied_file)
    _write_status(context, MODE_RECOVERY if serve else MODE_MAINTENANCE,
                  probe_checks=probe.checks, schema=schema, recovery_available=serve)
    if serve:
        Admission(context.paths.state_dir).open()
        log("restricted recovery is available")
    return MODE_RECOVERY


def run_normal(context: Context) -> str:
    log("starting normal service")
    for check in context.infra_checks:
        if not check.ok:
            detail = check.name + ": " + check.detail
            context.journal.record_failure(PHASE_MUTATION_STARTED, detail)
            _persist(context)
            _write_status(context, MODE_MAINTENANCE, recovery_available=False, detail=detail)
            return MODE_MAINTENANCE
    applied = context.applied
    selected_revision = applied.revision or context.desired.revision or context.default_runtime_revision
    if context.journal.phase == PHASE_MUTATION_STARTED and context.journal.operation:
        selected_revision = context.journal.operation.revision
    if context.journal.schema_uncertain:
        detail = 'uncertain schema outcome requires administrator/operator clearance; no schema work rerun'
        context.journal.record_failure(PHASE_MUTATION_STARTED, detail)
        _persist(context)
        _write_status(context, MODE_MAINTENANCE, recovery_available=False, detail=detail)
        return MODE_MAINTENANCE
    if not applied.revision or context.journal.phase == PHASE_MUTATION_STARTED:
        check = _create_tables(context)
        if not check.ok:
            detail = "first normal boot could not initialize the database: " + check.detail
            context.journal.record_failure(PHASE_MUTATION_STARTED, detail)
            _persist(context)
            _write_status(context, MODE_MAINTENANCE, recovery_available=False, detail=detail)
            return MODE_MAINTENANCE
    probe = probe_runtime(context.paths.runtime_venv, context.paths.config_file, context.expected_core_versions)
    if not probe.ok:
        detail = "runtime did not validate: " + _failure_detail(probe)
        context.journal.record_failure("normal-boot", detail)
        _persist(context)
        _write_status(context, MODE_MAINTENANCE, probe_checks=probe.checks, recovery_available=False, detail=detail)
        return MODE_MAINTENANCE
    if not _start_web(context, recovery=False):
        detail = "normal web server could not start"
        context.journal.record_failure(PHASE_MUTATION_STARTED, detail)
        _persist(context)
        _write_status(context, MODE_MAINTENANCE, recovery_available=False, detail=detail)
        return MODE_MAINTENANCE
    from .roles import prepare_roles
    role_checks = prepare_roles(context.paths)
    if not all(check.ok for check in role_checks):
        detail = 'all-role readiness failed: ' + ', '.join(check.name for check in role_checks if not check.ok)
        Admission(context.paths.state_dir).close(detail)
        _write_status(context, MODE_MAINTENANCE, role_checks=role_checks, detail=detail)
        return MODE_MAINTENANCE
    applied.mark(selected_revision, context.core_baseline)
    applied.save(context.paths.applied_file)
    record_initialization(context)
    context.journal.record_verified(selected_revision)
    _persist(context)
    _mark_ready(context.paths)
    _write_status(context, MODE_NORMAL, probe_checks=probe.checks, role_checks=role_checks)
    Admission(context.paths.state_dir).open(consumers=True)
    log("normal service is ready")
    return MODE_NORMAL


def run_maintenance(context: Context, detail: str, schema: SchemaCheck | None = None) -> str:
    Admission(context.paths.state_dir).close(detail)
    stop_web(context.paths)
    _remove_ready(context.paths)
    _write_status(context, MODE_MAINTENANCE, schema=schema, recovery_available=False, detail=detail)
    log("maintenance mode: " + detail)
    return MODE_MAINTENANCE


# --------------------------------------------------------------------- apply


def enter_maintenance(context: Context, timeout: float | None = None) -> bool:
    """Close admission before drain; timeout is a pre-mutation abort."""
    admission = Admission(context.paths.state_dir)
    previous_status = status(context.paths)
    admission.close('maintenance requested', mode='draining')
    _remove_ready(context.paths)
    _write_status(context, MODE_MAINTENANCE, detail='draining accepted requests')
    budget = float(os.environ.get('DA_DRAIN_TIMEOUT', '30')) if timeout is None else timeout
    deadline = time.monotonic() + budget
    requests_drained = admission.drain(budget)
    if requests_drained and drain_web(context.paths, max(0, deadline - time.monotonic())):
        try:
            with RuntimeLock(context.paths.state_dir):
                pass  # Prove no otherwise-untracked web descendants remain.
        except BlockingIOError:
            pass  # Abort, rather than taking over a surviving child's lease.
        else:
            admission.close('exclusive runtime operation', mode='mutating')
            return True
    if previous_status.get('mode') not in (MODE_NORMAL, MODE_RECOVERY):
        admission.close('drain aborted; runtime status unknown')
        _write_status(context, MODE_MAINTENANCE, detail='drain aborted before mutation; runtime status unknown')
        return False
    if requests_drained and not _start_web(context, recovery=previous_status.get('mode') == MODE_RECOVERY):
        admission.close('drain aborted; unchanged runtime could not resume web')
        _write_status(context, MODE_MAINTENANCE, detail='drain aborted; no mutation; web resume failed')
        return False
    context.journal.record_failure('drain-aborted', 'drain timeout; unchanged runtime resumed')
    _persist(context)
    if previous_status.get('mode') == MODE_NORMAL:
        from .roles import prepare_roles
        checks = prepare_roles(context.paths, retained=previous_status)
        if not all(check.ok for check in checks):
            admission.close('drain aborted; unchanged roles could not become ready')
            _write_status(context, MODE_MAINTENANCE, role_checks=checks, detail='no mutation; unchanged role readiness failed')
            return False
        _write_status(context, MODE_NORMAL, role_checks=checks, detail='drain timeout; unchanged runtime resumed')
        _mark_ready(context.paths)
    else:
        write_status(context.paths, dict(previous_status, detail='drain timeout; unchanged runtime resumed',
                                         last_failure=context.journal.failure.to_dict() if context.journal.failure else None))
    admission.open(consumers=previous_status.get('mode') == MODE_NORMAL)
    log('drain timed out before mutation; unchanged service resumed')
    return False


def apply_runtime(paths: ExecutorPaths, revision: str, fault_after_seconds: float | None = None) -> int:
    """Apply a frozen runtime revision; used by the interrupted-install fixture."""
    if not managed_requested(os.environ.get('DA_UV_MANAGED') == 'true', paths.state_dir):
        raise ValueError('managed apply requires DA_UV_MANAGED=true')
    context = load_context(paths, start_infrastructure=False)
    context.infra_checks = [Check('postgres', pg_is_ready()), Check('redis', redis_is_ready())]
    if not all(check.ok for check in context.infra_checks):
        log("infrastructure is not ready; refusing to mutate")
        return 1
    name = 'runtime-fault' if fault_after_seconds is not None else context.bundle_name("runtime", revision)
    bundle_dir = paths.bundles_dir / name
    ok, detail = verify_bundle(bundle_dir, context.manifest, name)
    if not ok:
        log("bundle unavailable: " + detail)
        return 1
    with MutationLock(paths.state_dir, wait_seconds=5):
        context = load_context(paths, start_infrastructure=False)
        if not enter_maintenance(context):
            return 3
        context.journal.begin_mutation(
            Operation(
                id=uuid.uuid4().hex,
                kind="runtime-apply",
                revision=revision,
                previous_revision=context.applied.revision,
            )
        )
        _persist(context)
        stop_web(paths)
        _remove_ready(paths)
        _write_status(context, MODE_MAINTENANCE, detail='runtime installation in progress')
        log("mutation started for " + name)
        result = sync_frozen(
            bundle_dir,
            paths.runtime_venv,
            paths.uv_binary,
            paths.cache_dir,
            log_dir=paths.log_dir,
            fault_after_seconds=fault_after_seconds,
        )
        if result.interrupted:
            context.journal.record_failure(PHASE_MUTATION_STARTED, result.describe(), revision=revision)
            _persist(context)
            log(result.describe())
            return 2
        if not result.ok:
            context.journal.record_failure(PHASE_MUTATION_STARTED, result.describe(), revision=revision)
            _persist(context)
            log(result.describe())
            return 1
        expected = context.manifest['inventories'][name]
        installed = verify_environment(paths.runtime_venv, expected['distributions'], expected['files'])
        if not installed.ok:
            context.journal.record_failure(PHASE_MUTATION_STARTED, installed.detail, revision=revision)
            _persist(context)
            return 1
        probe = probe_runtime(paths.runtime_venv, paths.config_file, context.expected_core_versions)
        if not probe.ok:
            detail = "applied revision did not validate: " + _failure_detail(probe)
            context.journal.record_failure(PHASE_MUTATION_STARTED, detail, revision=revision)
            _persist(context)
            log(detail)
            return 1
        if not _start_web(context, recovery=False):
            detail = "normal web server could not start after apply"
            context.journal.record_failure(PHASE_MUTATION_STARTED, detail, revision=revision)
            _persist(context)
            return 1
        from .roles import prepare_roles
        role_checks = prepare_roles(paths)
        if not all(check.ok for check in role_checks):
            detail = 'all-role readiness failed: ' + ', '.join(check.name for check in role_checks if not check.ok)
            context.journal.record_failure(PHASE_MUTATION_STARTED, detail, revision=revision)
            _persist(context)
            Admission(paths.state_dir).close(detail)
            _write_status(context, MODE_MAINTENANCE, role_checks=role_checks, detail=detail)
            return 1
        context.applied.mark(revision, context.core_baseline)
        context.applied.save(paths.applied_file)
        record_initialization(context)
        context.journal.record_verified(revision)
        _persist(context)
        _mark_ready(paths)
        _write_status(context, MODE_NORMAL, role_checks=role_checks)
        Admission(paths.state_dir).open(consumers=True)
        log("applied " + name + " and normal service is ready")
        return 0


# ----------------------------------------------------------------- serving


class Executor:
    def __init__(self, context: Context, initial_mode: str, maintenance_only: bool = False):
        self.context = context
        self.mode = initial_mode
        self.maintenance_only = maintenance_only
        self.maintenance_server: ThreadingHTTPServer | None = None
        self.stop_requested = False

    def _status_provider(self) -> dict[str, Any]:
        from .status import read_status_file

        return read_status_file(self.context.paths.status_file)

    def _start_maintenance(self) -> None:
        if self.maintenance_server is not None:
            return
        self.maintenance_server = start_maintenance_server(self.context.paths.web_port, self._status_provider)
        log("static maintenance endpoint is serving on port " + str(self.context.paths.web_port))

    def _stop_maintenance(self) -> None:
        if self.maintenance_server is not None:
            self.maintenance_server.shutdown()
            self.maintenance_server.server_close()
            self.maintenance_server = None

    def _handle_repair(self) -> None:
        request_file = self.context.paths.repair_request_file
        if not request_file.is_file():
            return
        try:
            request_handle = request_file.with_suffix(".processing")
            os.replace(request_file, request_handle)
        except FileNotFoundError:
            return
        log("administrator repair requested; starting an explicit new recovery attempt")
        self.context = load_context(self.context.paths, start_infrastructure=False)
        self.mode = run_recovery(self.context, "administrator-repair")
        self.context = load_context(self.context.paths, start_infrastructure=False)
        request_handle.unlink(missing_ok=True)

    def _ensure_service(self) -> None:
        try:
            with MutationLock(self.context.paths.state_dir):
                self._ensure_service_locked()
        except BlockingIOError:
            return

    def _ensure_service_locked(self) -> None:
        if Admission(self.context.paths.state_dir).state()['mode'] in ('draining', 'mutating'):
            _remove_ready(self.context.paths)
            return
        journal = Journal.load(self.context.paths.journal_file)
        if os.environ.get('DA_DURABLE_WORK_STORE') and (self.context.paths.state_dir / 'published-boot.json').exists():
            return  # The supervised DB poller owns published health and readiness.
        if journal.operation and journal.operation.kind == 'published-apply':
            import json
            try:
                reported = json.loads((self.context.paths.state_dir / 'publication-reported.json').read_text())
            except (OSError, ValueError):
                reported = None
            if journal.phase != 'verified' or reported != journal.operation.binding:
                _remove_ready(self.context.paths)
                return  # DB reporting/replay alone owns this unpublished readiness handoff.
        if journal.phase in (PHASE_MUTATION_STARTED, PHASE_RECOVERY_ATTEMPT_STARTED):
            # Another process owns mutation/recovery.  Stop serving rather than
            # guessing at the environment state.
            stop_web(self.context.paths)
            self.mode = "quiesced"
            return
        if journal.phase == PHASE_RECOVERY_FAILED:
            # An explicit recovery attempt failed (possibly from another
            # process).  Do not retry automatically and do not serve normal
            # traffic; serve only the unauthenticated maintenance status.
            stop_web(self.context.paths)
            if self.mode != MODE_MAINTENANCE:
                self.context = load_context(self.context.paths, start_infrastructure=False)
                _remove_ready(self.context.paths)
                # Keep the failing process's detailed schema/failure status;
                # replacing it here would erase the evidence just published.
                self.mode = MODE_MAINTENANCE
                self.maintenance_only = True
            return
        if Admission(self.context.paths.state_dir).state()['mode'] == 'closed':
            _remove_ready(self.context.paths)
            return  # Preserve an interrupted/aborted handoff; only explicit control may reopen it.
        if journal.phase == PHASE_RECOVERY_SUCCEEDED:
            self.mode = MODE_RECOVERY
            if not _web_alive(self.context.paths, "recovery"):
                if not _start_web(self.context, recovery=True):
                    self.context = load_context(self.context.paths, start_infrastructure=False)
                    self.mode = _recovery_failed(self.context, "restricted recovery web server died")
                    self.maintenance_only = True
                    self._start_maintenance()
            return
        self.mode = MODE_NORMAL
        if not _web_alive(self.context.paths, "normal"):
            if not _start_web(self.context, recovery=False):
                context = load_context(self.context.paths, start_infrastructure=False)
                self.context = context
                context.journal.record_failure(PHASE_MUTATION_STARTED, "normal web server died")
                context.journal.save(context.paths.journal_file)
                run_maintenance(self.context, "normal web server died")
                self.mode = MODE_MAINTENANCE
                self._start_maintenance()
        if self.mode == MODE_NORMAL:
            from .roles import check_roles, process_identities
            projection = status(self.context.paths)
            current = process_identities(self.context.paths)
            changed = (current != projection.get('role_processes') or not all(current.values())
                       or Admission(self.context.paths.state_dir).state()['mode'] == 'preparing')
            if changed:
                self.context = load_context(self.context.paths, start_infrastructure=False)
                _remove_ready(self.context.paths)
                admission = Admission(self.context.paths.state_dir)
                observed_mode = admission.state()['mode']
                if observed_mode not in ('open', 'preparing') or not admission.prepare(expected_mode=observed_mode):
                    return
                checks = check_roles(self.context.paths)
                if not all(check.ok for check in checks):
                    _remove_ready(self.context.paths)
                    if admission.state()['mode'] != 'preparing':
                        return
                    _write_status(self.context, MODE_MAINTENANCE, role_checks=checks,
                                  detail='waiting behind admission for live role protocol health')
                    return  # Retry health, never sync/schema; release mutation authority between probes.
                if not Admission(self.context.paths.state_dir).open(consumers=True, expected_mode='preparing'):
                    _remove_ready(self.context.paths)
                    return  # A closure superseded requalification; never overwrite it.
                _write_status(self.context, MODE_NORMAL, role_checks=checks)
                _mark_ready(self.context.paths)

    def run(self) -> int:
        signal.signal(signal.SIGTERM, self._request_stop)
        signal.signal(signal.SIGINT, self._request_stop)
        if self.mode == MODE_MAINTENANCE:
            self._start_maintenance()
        while True:
            if self.stop_requested:
                from .infrastructure import shutdown
                try:
                    code = shutdown(self.context.paths, float(os.environ.get('DA_DRAIN_TIMEOUT', '30')))
                except (OSError, ValueError) as error:
                    log('shutdown refused without forcing accepted work: ' + str(error))
                    code = 3
                if code == 0:
                    self._stop_maintenance()
                    log('executor stopping after independent drained shutdown')
                    return 0
                self.stop_requested = False
            try:
                try:
                    while os.waitpid(-1, os.WNOHANG)[0]:
                        pass
                except ChildProcessError:
                    pass
                if self.maintenance_only:
                    self._start_maintenance()
                else:
                    self._handle_repair()
                    self._ensure_service()
            except Exception as error:  # pragma: no cover - defensive loop
                log("service loop error: " + error.__class__.__name__ + ": " + str(error))
            time.sleep(2)

    def _request_stop(self, *args) -> None:
        self.stop_requested = True


def safe_boot_failure(error: Exception) -> str:
    """Anonymous/log diagnostics never include parser source or exception values."""
    if isinstance(error, YAMLError):
        detail = 'managed boot refused: configuration-invalid'
        mark = getattr(error, 'problem_mark', None)
        if mark is not None and isinstance(mark.line, int) and isinstance(mark.column, int):
            detail += f' (line {mark.line + 1}, column {mark.column + 1})'
        return detail
    if isinstance(error, OSError):
        return 'managed boot refused: input-unavailable'
    if isinstance(error, subprocess.SubprocessError):
        return 'managed boot refused: subprocess-failed'
    reason = STARTUP_FAILURE_MESSAGES.get(type(error))
    if reason is not None:
        return 'managed boot refused: ' + reason
    return 'managed boot refused: validation-failed'


def boot(paths: ExecutorPaths, force: str | None = None) -> int:
    """Independent bootstrap remains observable before any runtime use."""
    bootstrap_status = {'mode': MODE_MAINTENANCE, 'normal_ready': False,
                        'recovery_available': False, 'detail': 'independent boot validation in progress'}
    from .status import read_status_file

    admission = Admission(paths.state_dir)
    maintenance = start_ingress(paths.web_port, paths.backend_port, admission,
                                lambda: read_status_file(paths.status_file) or bootstrap_status)
    context = None
    try:
        admission.close('independent boot validation')
        _remove_ready(paths)
        if not managed_requested(os.environ.get('DA_UV_MANAGED') == 'true', paths.state_dir):
            raise ManagedModeError(STARTUP_FAILURE_MESSAGES[ManagedModeError])
        for directory in (paths.state_dir, paths.run_dir):
            if directory.exists() and not directory.stat().st_mode & 0o222:
                raise UnwritablePathError(STARTUP_FAILURE_MESSAGES[UnwritablePathError])
        from .checkpoint_restore import require_restore_ready
        require_restore_ready(paths)
        context = load_context(paths)
        with MutationLock(paths.state_dir):
            require_restore_ready(paths)
            context.infra_checks.append(start_supervisor(paths))
            with RuntimeLock(paths.state_dir):
                pass  # Surviving backend/build descendants forbid even runtime probes.
            from .boot_observation import published_boot_required, reconcile_published_boot
            published_boot = published_boot_required(paths, context.journal)
            if published_boot:
                if os.environ.get('DA_DURABLE_WORK_STORE'):
                    from .legacy_adoption import observe_adoption
                    observe_adoption(context)
                    from .image_migration import observe_migration
                    observe_migration(context, locked=True)
                    context.journal = Journal.load(paths.journal_file)
                observed = reconcile_published_boot(context)
                mode = observed['mode']
            else:
                from .infrastructure import backup_admission
                backup_admission(paths).open(consumers=True)
                receipt = validate_startup(context)
                _write_status(context, MODE_MAINTENANCE, detail='frozen managed boot validation in progress')
                if force or context.journal.phase not in (PHASE_IDLE, 'verified'):
                    probe = probe_runtime(paths.runtime_venv, paths.config_file, context.expected_core_versions)
                    decision = decide_boot_action(context.journal, probe.ok, force)
                else:
                    action = prepare_boot(context, receipt)
                    decision = BootDecision(ACTION_RECOVER if action == 'recover' else ACTION_NORMAL,
                                            'managed startup: ' + action)
                log('boot decision: ' + decision.action + ' (' + decision.reason + ')')
                if decision.action == ACTION_RECOVER:
                    mode = _restore_core(context, decision.reason)
                elif decision.action == ACTION_RESTART_RECOVERY:
                    schema = check_schema(paths.runtime_venv, context.db, tuple(context.manifest.get('compatible_schema_revisions', ())))
                    if not schema.usable:
                        mode = _recovery_failed(context, 'database is not usable: ' + schema.detail, schema=schema)
                    elif _start_web(context, recovery=True):
                        _write_status(context, MODE_RECOVERY, schema=schema, recovery_available=True)
                        mode = MODE_RECOVERY
                        admission.open()
                    else:
                        mode = run_maintenance(context, 'verified recovery could not restart restricted administration')
                elif decision.action == ACTION_NORMAL:
                    mode = run_normal(context)
                else:
                    mode = run_maintenance(context, decision.reason)
        if os.environ.get('DA_DURABLE_WORK_STORE') and published_boot:
            if not os.environ.get('DA_QUALIFIED_IMAGE_BASELINE'):
                raise ValueError('durable polling requires a provisioned qualified image bridge')
            from .supervision import supervisor_command
            for kind in ('preparation', 'installation'):
                started = supervisor_command(paths, ['start', 'durable-' + kind])
                if started.returncode != 0:
                    raise ValueError('durable polling supervision unavailable')
    except (OSError, ValueError, KeyError, TypeError, AttributeError, YAMLError, subprocess.SubprocessError) as error:
        detail = safe_boot_failure(error)
        log(detail)
        bootstrap_status['detail'] = detail
        try:
            _remove_ready(paths)
        except OSError:
            pass
        # Corrupt/unwritable durable input is never replaced with empty state.
        # Keep independent failure availability even if status cannot be saved.
        try:
            maintenance.server_close()
        except OSError:
            pass
        maintenance.shutdown()
        failure_server = start_maintenance_server(paths.web_port, lambda: bootstrap_status)
        stop = False
        def request_stop(*args):
            nonlocal stop
            stop = True
        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        while not stop:
            time.sleep(1)
        failure_server.shutdown()
        failure_server.server_close()
        return 1
    executor = Executor(context, mode, maintenance_only=(mode == MODE_MAINTENANCE))
    executor.maintenance_server = maintenance
    try:
        return executor.run()
    finally:
        maintenance.shutdown()
        maintenance.server_close()


def status(paths: ExecutorPaths) -> dict[str, Any]:
    from .status import read_status_file

    return read_status_file(paths.status_file)
