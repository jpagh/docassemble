#!/usr/bin/env python3
"""Disposable-container proof for ticket 01: independent browser recovery.

Run with the image already built (see run-proof.sh).  Scenarios assert the
contract from ``.scratch/uv-environment-implementation/issues/01-*.md`` and
write captured evidence under the evidence directory.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from html import unescape
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ContainerHttp, Docker, Evidence, Failure, Http, check  # noqa: E402

CONTAINER = "da-uv-proof"
OFFLINE_CONTAINER = "da-uv-proof-offline"
MISSING_CONTAINER = "da-uv-proof-missing"
PORT = 18080
OFFLINE_SNAPSHOT = "da-uv-proof-snapshot:local"
SENTINEL_DIR = "/tmp/da-sentinels"
SENTINEL_FILES = (
    "sentinel-user-import",
    "sentinel-hook-module",
    "sentinel-hook-executed",
    "sentinel-preloaded-module",
    "sentinel-interview",
    "sentinel-favicon",
)
STATE_DIR = "/var/lib/docassemble/managed"
JOURNAL = STATE_DIR + "/lifecycle.json"
DESIRED = STATE_DIR + "/desired.json"
APPLIED = STATE_DIR + "/applied.json"
CREATE_TABLES_LOG = "/usr/share/docassemble/log/create-tables.log"
VENV = "/usr/share/docassemble/local3.12"


@dataclass
class Context:
    docker: Docker
    evidence: Evidence
    image: str
    results: list[dict] = field(default_factory=list)

    def record(self, scenario: str, ok: bool, detail: str) -> None:
        self.results.append({"scenario": scenario, "ok": ok, "detail": detail})
        self.evidence.note(("PASS " if ok else "FAIL ") + scenario + ": " + detail)

    def journal(self, container: str = CONTAINER) -> dict:
        raw = self.docker.read_file(container, JOURNAL)
        return json.loads(raw) if raw.strip() else {}

    def applied(self, container: str = CONTAINER) -> dict:
        raw = self.docker.read_file(container, APPLIED)
        return json.loads(raw) if raw.strip() else {}

    def desired(self, container: str = CONTAINER) -> dict:
        raw = self.docker.read_file(container, DESIRED)
        return json.loads(raw) if raw.strip() else {}

    def sentinels(self, container: str) -> dict[str, bool]:
        script = "for f in " + " ".join(SENTINEL_FILES) + "; do test -f " + SENTINEL_DIR + "/$f || exit 1; done"
        present = self.docker.exec_shell(container, script, check_result=False).returncode == 0
        return {name: present for name in SENTINEL_FILES} if present else {
            name: self.docker.exec(container, ["test", "-f", SENTINEL_DIR + "/" + name], check_result=False).returncode == 0
            for name in SENTINEL_FILES
        }

    def write_desired(self, container: str, payload: dict) -> None:
        encoded = json.dumps(payload)
        self.docker.exec_shell(
            container,
            "mkdir -p " + STATE_DIR + " && printf '%s\\n' " + json.dumps(encoded) + " > " + DESIRED,
        )

    def hashlib_file(self, container: str, path: str) -> str:
        completed = self.docker.exec(container, ["md5sum", path], check_result=False)
        return completed.stdout.split()[0] if completed.returncode == 0 and completed.stdout.split() else ""


def wait_for_recovery(docker: Docker, http: Http, timeout: int = 900) -> dict:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            status, _, body = http.get("/health_status", timeout=10)
            if status == 200:
                payload = json.loads(body)
                last = body
                if payload.get("recovery") is True and payload.get("recovery_available") is True:
                    return payload
        except Failure:
            pass
        time.sleep(3)
    raise Failure("recovery administration did not become available; last status: " + last[:400])


def wait_for_maintenance(http: Http, timeout: int = 900) -> dict:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            status, _, body = http.get("/health_status", timeout=10)
            if status == 200:
                payload = json.loads(body)
                last = body
                # Independent ingress also exposes in-progress recovery. Wait
                # for terminal evidence, not the preserved original failure.
                if payload.get("mode") == "maintenance" and payload.get('journal_phase') == 'recovery_failed':
                    return payload
        except Failure:
            pass
        time.sleep(3)
    raise Failure("maintenance status did not become available; last status: " + last[:400])


def admin_session(http: Http) -> None:
    status, location = http.login("admin@example.com", "FixtureAdmin101!")
    check(status in (302, 303), "admin login returned " + str(status))
    check("sign-in" not in location, "admin login did not establish a session: " + location)


def read_alembic_revision(docker: Docker, container: str = CONTAINER) -> str:
    completed = docker.exec(
        container,
        ["psql", "-Atq", "docassemble", "-c", "SELECT version_num FROM alembic_version"],
        user="postgres",
        check_result=False,
    )
    return completed.stdout.strip()


def scenario_normal_boot(context: Context, port: int) -> Http:
    docker = context.docker
    evidence = context.evidence
    docker.start(CONTAINER, context.image, port=port)
    http = Http("http://127.0.0.1:" + str(port))
    http.wait_for("/health_check?ready=1", timeout=1200)
    # Positive control: a real normal-mode core Celery worker, not a process
    # absence assertion against a fixture which never ran workers at all.
    docker.run(['exec', '-d', CONTAINER, 'bash', '-c',
                'exec runuser -u www-data -- ' + VENV + '/bin/celery -A docassemble.webapp.worker '
                '-b redis://localhost:6379/1 worker --pool=solo --concurrency=1 --loglevel=INFO '
                '> /usr/share/docassemble/log/fixture-worker.log 2>&1'])
    check(docker.wait_for_exec(CONTAINER, "grep -q ' ready.' /usr/share/docassemble/log/fixture-worker.log", timeout=120),
          'normal-mode worker did not become ready: ' + docker.read_file(CONTAINER, '/usr/share/docassemble/log/fixture-worker.log')[-500:])
    check(docker.exec(CONTAINER, ['pgrep', '-af', 'celery'], check_result=False).returncode == 0,
          'normal-mode worker positive control is missing')
    evidence.write('scenario-1/worker-log.txt', docker.read_file(CONTAINER, '/usr/share/docassemble/log/fixture-worker.log'))
    ready_status, _, _ = http.get("/health_check?ready=1")
    check(ready_status == 200, "normal readiness is false after boot")
    status, _, body = http.get("/health_status")
    payload = json.loads(body)
    present = context.sentinels(CONTAINER)
    check(all(present.values()), "normal boot did not execute all fixture sentinels: " + json.dumps(present))
    evidence.write_json("scenario-1/sentinels.json", present)
    # Fresh normal setup creates ORM tables then stamps the Alembic head; it
    # does not execute additive migration bodies. Materialize the proof-only
    # table explicitly before snapshotting this genuinely advanced database.
    docker.exec(CONTAINER, ['psql', '-v', 'ON_ERROR_STOP=1', 'docassemble', '-c',
                            'CREATE TABLE fixture_advanced_evidence (id integer PRIMARY KEY)'], user='postgres')
    admin_session(http)
    config_status, _, _ = http.get("/config")
    check(config_status == 200, "/config returned " + str(config_status))
    logs_status, _, _ = http.get("/logs")
    check(logs_status == 200, "/logs returned " + str(logs_status))
    recovery_status, _, _ = http.get("/recovery")
    check(recovery_status == 404, "recovery blueprint is unexpectedly registered in normal mode")
    evidence.write_json("scenario-1/health_status.json", payload)
    evidence.write("scenario-1/journal.json", docker.read_file(CONTAINER, JOURNAL))
    evidence.write("scenario-1/applied.json", docker.read_file(CONTAINER, APPLIED))
    evidence.write("scenario-1/manifest.json", docker.read_file(CONTAINER, "/usr/share/docassemble/managed/bundles/manifest.json"))
    evidence.write("scenario-1/create-tables-log.md5", context.hashlib_file(CONTAINER, CREATE_TABLES_LOG))
    context.record("normal-boot-sentinels", True, "all six sentinels executed; admin config/logs available")
    docker.commit(CONTAINER, OFFLINE_SNAPSHOT)
    return http


def scenario_broken_runtime(context: Context, http: Http) -> None:
    docker = context.docker
    evidence = context.evidence
    website_files = VENV + "/lib/python3.12/site-packages"
    docker.exec_shell(
        CONTAINER,
        "rm -rf " + website_files + "/docassemble/webapp " + website_files + "/docassemble_webapp-*.dist-info "
        + "&& rm -rf " + SENTINEL_DIR,
    )
    probe = docker.exec(
        CONTAINER,
        [VENV + "/bin/python", "-c", "import docassemble.webapp.server"],
        check_result=False,
        timeout=120,
    )
    check(probe.returncode != 0, "removing the core webapp did not break the runtime probe")
    evidence.write("scenario-2/broken-probe.txt", probe.stderr)
    docker.restart(CONTAINER)
    wait_for_recovery(docker, http)
    ready, _, _ = http.get("/health_check?ready=1")
    check(ready == 400, "normal readiness must remain false in recovery; got " + str(ready))
    present = context.sentinels(CONTAINER)
    check(not any(present.values()), "recovery executed user sentinels: " + json.dumps(present))
    evidence.write_json("scenario-2/sentinels.json", present)
    admin_session(http)
    recovery_page, _, body = http.get("/recovery")
    check(recovery_page == 200, "/recovery returned " + str(recovery_page))
    check("Restricted recovery administration" in body, "recovery page content missing")
    status_page, _, status_body = http.get("/recovery/status.json")
    status_payload = json.loads(status_body)
    check(status_page == 200, "/recovery/status.json returned " + str(status_page))
    check(status_payload.get("mode") == "recovery", "status mode is not recovery")
    check(status_payload.get("recovery_available") is True, "status does not advertise recovery availability")
    check(status_payload.get("journal_phase") == "recovery_succeeded", "journal phase is " + str(status_payload.get("journal_phase")))
    check((status_payload.get("database") or {}).get("status") == "compatible", "database schema was not accepted")
    check(status_payload.get("desired_revision") == status_payload.get("applied_revision"), "unexpected drift after plain recovery")
    config_status, _, config_html = http.get("/config")
    check(config_status == 200, "/config in recovery returned " + str(config_status))
    scripts = re.findall(r'<script[^>]+src="([^"]+)"', config_html)
    check(any('/rjs/' in src for src in scripts), 'configuration editor initialization script is absent')
    editor_script = unescape(next(src for src in scripts if '/rjs/' in src))
    for src in scripts:
        if src.startswith('/'):
            script_status, _, script_body = http.get(unescape(src))
            check(script_status == 200, 'configuration editor script failed: ' + src + ' (' + str(script_status) + ')')
            if '/rjs/' in src:
                check('daContent' in script_body, 'configuration editor data was not delivered')
    original_config = docker.read_file(CONTAINER, '/usr/share/docassemble/config/config.yml')
    edited_config = original_config + '\n# recovery configuration-edit proof\n'
    saved_status, saved_headers, saved_body = http.post('/config', {
        'csrf_token': Http.csrf(config_html), 'config_content': edited_config, 'submit': 'Save',
    })
    evidence.write_json('scenario-2/config-save-response.json', {
        'status': saved_status, 'headers': saved_headers, 'body': saved_body,
    })
    check(saved_status == 302 and '/recovery' in saved_headers.get('Location', ''),
          'configuration save did not return to recovery: ' + str(saved_status) + ' ' + saved_body[:1000])
    check(docker.read_file(CONTAINER, '/usr/share/docassemble/config/config.yml') == edited_config, 'configuration editing did not save the file')
    check(not docker.exec(CONTAINER, ['redis-cli', 'get', 'da:skip_create_tables']).stdout.strip(), 'configuration save invoked legacy restart signaling')
    logs_status, _, _ = http.get("/logs")
    check(logs_status == 200, "/logs in recovery returned " + str(logs_status))
    for path in ("/interview?i=docassemble.sentinel_fixture:data/questions/sentinel.yml", "/playground", "/updatepackage", "/api/user", "/list", "/interviews", "/goto", "/packagestatic/docassemble.sentinel_fixture/sentinel.txt", "/restart"):
        blocked_status, _, _ = http.get(path)
        check(blocked_status == 404, path + " returned " + str(blocked_status) + " in recovery")
    celery = docker.exec(CONTAINER, ["pgrep", "-af", "celery"], check_result=False)
    check(celery.returncode != 0, "celery workers are running in recovery")
    unauthenticated = Http("http://127.0.0.1:" + str(PORT))
    unauth_status, unauth_headers, _ = unauthenticated.get("/recovery")
    check(unauth_status == 302, "unauthenticated /recovery returned " + str(unauth_status))
    check("/user/sign-in" in unauth_headers.get("Location", ""), "unauthenticated /recovery did not redirect to login")
    unauth_json, _, _ = unauthenticated.get("/recovery/status.json")
    check(unauth_json == 302, "unauthenticated status endpoint returned " + str(unauth_json))
    unauth_js, _, _ = unauthenticated.get(editor_script)
    check(unauth_js == 302, 'unauthenticated configuration editor data was not rejected')
    non_admin = Http("http://127.0.0.1:" + str(PORT))
    register_status, register_location = non_admin.register("nonadmin-fixture@example.com", "NonAdmin101!")
    check(register_status in (302, 303), "non-admin registration failed: " + str(register_status))
    admin_session_check = non_admin.login("nonadmin-fixture@example.com", "NonAdmin101!")
    check(admin_session_check[0] in (302, 303), "non-admin login failed")
    for path in ("/recovery", "/recovery/status.json", "/config", "/logs", editor_script):
        forbidden, _, _ = non_admin.get(path)
        check(forbidden == 403, "non-admin " + path + " returned " + str(forbidden))
    _, _, login_html = unauthenticated.get("/user/sign-in")
    token = Http.csrf(login_html)
    unauth_repair, _, _ = unauthenticated.post("/recovery/repair", {"csrf_token": token})
    check(unauth_repair == 302, "unauthenticated repair was not rejected")
    forbidden, _, _ = non_admin.post("/recovery/repair", {"csrf_token": non_admin.csrf_token})
    check(forbidden == 403, "non-admin repair was not rejected by authorization")
    attempt_before = status_payload.get("recovery_attempt")
    docker.restart(CONTAINER)
    restarted = wait_for_recovery(docker, http)
    check(restarted.get("recovery_attempt") == attempt_before, "restarting verified recovery started a new attempt")
    evidence.write_json("scenario-2/recovery-status.json", status_payload)
    evidence.write("scenario-2/journal.json", docker.read_file(CONTAINER, JOURNAL))
    context.record(
        "broken-runtime-recovery",
        True,
        "core restored, restricted admin served, sentinels/interviews/workers excluded, auth enforced",
    )


def scenario_interrupted_install(context: Context, http: Http) -> dict:
    docker = context.docker
    evidence = context.evidence
    context.write_desired(CONTAINER, {"revision": "r2", "core_baseline": "r1", "packages": ["docassemble-sentinel-fixture", "slowfixture"]})
    apply_result = docker.exec(
        CONTAINER,
        ["da-executor", "apply-runtime", "--revision", "r2", "--fault-after-seconds", "20"],
        env={"SLOWFIXTURE_BUILD_DELAY": "180"},
        check_result=False,
        timeout=600,
    )
    check(apply_result.returncode == 2, "fault-injected apply returned " + str(apply_result.returncode))
    journal = context.journal()
    check(journal.get("phase") == "mutation_started", "journal phase after interruption is " + str(journal.get("phase")))
    check((journal.get("failure") or {}).get("revision") == "r2", "failure evidence does not name r2")
    check(context.desired().get("revision") == "r2", "desired intent was changed by the interrupted installation")
    evidence.write("scenario-3/lifecycle-after-fault.json", json.dumps(journal, indent=2))
    evidence.write("scenario-3/apply-output.txt", apply_result.stdout + apply_result.stderr)
    docker.restart(CONTAINER)
    wait_for_recovery(docker, http)
    admin_session(http)
    _, _, status_body = http.get("/recovery/status.json")
    status_payload = json.loads(status_body)
    check(status_payload.get("desired_revision") == "r2", "desired revision was not preserved")
    check(status_payload.get("applied_revision") == "r1", "applied revision changed during recovery")
    check((status_payload.get("last_failure") or {}).get("revision") == "r2", "failure evidence was not preserved")
    check(status_payload.get("recovery_attempt") == 1, "automatic recovery attempt count is not one")
    started_at = (context.journal().get("recovery") or {}).get("started_at")
    docker.restart(CONTAINER)
    restarted = wait_for_recovery(docker, http)
    check(restarted.get("recovery_attempt") == 1, "interrupted installation triggered a second automatic recovery")
    check((context.journal().get("recovery") or {}).get("started_at") == started_at, "recovery was restarted without an explicit request")
    evidence.write_json("scenario-3/recovered-status.json", status_payload)
    evidence.write("scenario-3/journal.json", docker.read_file(CONTAINER, JOURNAL))
    evidence.write_json("scenario-3/desired.json", context.desired())
    context.record("interrupted-install-recovery", True, "one automatic core restore preserved desired r2 and failure evidence")
    return status_payload


def scenario_repair_action(context: Context, http: Http) -> None:
    docker = context.docker
    evidence = context.evidence
    admin_session(http)
    _, _, page = http.get("/recovery")
    token = Http.csrf(page)
    # Keep distribution metadata intact: plain sync could incorrectly decide
    # this damaged package is already installed and do no repair at all.
    damaged_file = VENV + '/lib/python3.12/site-packages/docassemble/webapp/startup.py'
    original_hash = context.hashlib_file(CONTAINER, damaged_file)
    check(bool(original_hash), 'repair fault target is missing')
    docker.exec(CONTAINER, ['rm', damaged_file])
    repair_status, _, _ = http.post("/recovery/repair", {"csrf_token": token})
    check(repair_status in (200, 302), "repair request returned " + str(repair_status))
    deadline = time.monotonic() + 900
    payload: dict = {}
    while time.monotonic() < deadline:
        try:
            status, _, body = http.get("/recovery/status.json", timeout=10)
            if status == 200:
                payload = json.loads(body)
                if payload.get("recovery_attempt") == 2 and payload.get("journal_phase") == "recovery_succeeded":
                    break
        except Failure:
            pass
        time.sleep(3)
    check(payload.get("recovery_attempt") == 2, "explicit repair did not record a new recovery attempt")
    check(context.hashlib_file(CONTAINER, damaged_file) == original_hash, 'repair did not restore the damaged package file')
    check(payload.get("journal_phase") == "recovery_succeeded", "repair did not finish successfully")
    check(payload.get("desired_revision") == "r2", "repair changed desired intent")
    check(payload.get("applied_revision") == "r1", "repair changed applied intent")
    check(not any(context.sentinels(CONTAINER).values()), "repair executed user code")
    journal = context.journal()
    check((journal.get("recovery") or {}).get("reason") == "administrator-repair", "repair reason was not recorded")
    evidence.write_json("scenario-4/repair-status.json", payload)
    evidence.write("scenario-4/journal.json", json.dumps(journal, indent=2))
    context.record("fixture-repair-action", True, "administrator repair performed a second explicit core restore without intent change")


def scenario_restart_and_schema_skip(context: Context, http: Http, create_tables_hash: str) -> None:
    docker = context.docker
    evidence = context.evidence
    revision = read_alembic_revision(docker)
    docker.restart(CONTAINER)
    payload = wait_for_recovery(docker, http)
    revision_after = read_alembic_revision(docker)
    check(revision == revision_after == 'fixture_advanced_revision', "restarting restricted recovery changed the advanced database schema revision")
    advanced_table = docker.exec(CONTAINER, ['psql', '-Atq', 'docassemble', '-c', "SELECT to_regclass('fixture_advanced_evidence')"], user='postgres').stdout.strip()
    check(advanced_table == 'fixture_advanced_evidence', 'advanced schema table disappeared')
    admin_session(http)
    for path in ('/recovery', '/config', '/logs'):
        status, _, _ = http.get(path)
        check(status == 200, 'older recovery core could not serve ' + path + ' against the advanced schema')
    check(bool(revision), "no Alembic revision was recorded after normal setup")
    check(context.hashlib_file(CONTAINER, CREATE_TABLES_LOG) == create_tables_hash, "recovery reran older schema setup")
    check(payload.get("journal_phase") == "recovery_succeeded", "restarted recovery is not verified")
    evidence.write("scenario-5/alembic-version.txt", revision + "\n")
    evidence.write("scenario-5/create-tables-log.md5", create_tables_hash + "\n")
    context.record("advanced-database-restart", True, "compatible advanced schema accepted without schema work or stamping")


def scenario_incompatible_database(context: Context, http: Http) -> None:
    docker = context.docker
    evidence = context.evidence
    updated = docker.exec(
        CONTAINER,
        ["psql", "-Atq", "docassemble", "-c", "UPDATE alembic_version SET version_num='fixture_incompatible_revision';"],
        user="postgres",
        check_result=False,
    )
    check(updated.returncode == 0, "could not make the database schema incompatible: " + updated.stderr)
    recover = docker.exec(CONTAINER, ["da-executor", "recover", "--reason", "fixture-incompatible-db"], check_result=False, timeout=900)
    check(recover.returncode == 1, "incompatible-database recovery did not fail explicitly: " + str(recover.returncode))
    payload = wait_for_maintenance(http)
    check(payload.get("recovery_available") is False, "maintenance status advertises recovery availability")
    database = payload.get("database") or {}
    check(database.get("status") == "incompatible", "incompatible database was not rejected: " + json.dumps(database))
    revision = read_alembic_revision(docker)
    check(revision == "fixture_incompatible_revision", "schema was stamped or rolled back")
    attempt = (context.journal().get("recovery") or {}).get("attempt")
    docker.restart(CONTAINER)
    restart_payload = wait_for_maintenance(http)
    check(restart_payload.get("recovery_available") is False, "restart advertised recovery after an incompatible database")
    check(((context.journal().get("recovery") or {}).get("attempt")) == attempt, "incompatible database triggered an automatic retry")
    evidence.write_json("scenario-6/maintenance-status.json", payload)
    evidence.write("scenario-6/alembic-version.txt", revision + "\n")
    evidence.write("scenario-6/journal.json", docker.read_file(CONTAINER, JOURNAL))
    context.record("incompatible-database-stop", True, "incompatible schema stopped recovery for operator intervention without stamping")


def scenario_offline_cache_cleared(context: Context) -> None:
    docker = context.docker
    evidence = context.evidence
    website_files = VENV + "/lib/python3.12/site-packages"
    docker.start(
        OFFLINE_CONTAINER, OFFLINE_SNAPSHOT, network='none',
        cold_setup="rm -rf " + website_files + "/docassemble/webapp " + website_files + "/docassemble_webapp-*.dist-info "
        + "&& rm -rf /var/cache/docassemble/uv " + SENTINEL_DIR,
    )
    container_http = ContainerHttp(docker, OFFLINE_CONTAINER)
    deadline = time.monotonic() + 900
    payload: dict = {}
    while time.monotonic() < deadline:
        if container_http.status("/health_status") == 200:
            try:
                payload = json.loads(container_http.body("/health_status"))
            except ValueError:
                payload = {}
            if payload.get("mode") == "maintenance" and payload.get('journal_phase') == 'recovery_failed':
                break
        time.sleep(3)
    check(payload.get("mode") == "maintenance", "offline boot did not reach maintenance mode")
    check(payload.get("recovery_available") is False, "offline boot advertised authenticated recovery")
    infra = {check['name']: check['ok'] for check in payload.get('checks', [])}
    check(infra.get('postgres') is True and infra.get('redis') is True and infra.get('database-bootstrap') is True,
          'independent offline infrastructure bootstrap failed: ' + json.dumps(infra))
    check("restoration failed" in (payload.get("detail") or "") or "restoration failed" in json.dumps(payload), "offline failure was not reported")
    processes = docker.exec(OFFLINE_CONTAINER, ["pgrep", "-af", "gunicorn|celery"], check_result=False).stdout
    check("gunicorn" not in processes and "celery" not in processes, "workload processes started offline: " + processes)
    ready = docker.exec(OFFLINE_CONTAINER, ["test", "-f", "/var/run/docassemble/ready"], check_result=False)
    check(ready.returncode != 0, "normal readiness was advertised offline")
    check(container_http.status("/health_check?ready=1") == 400, "offline maintenance claimed readiness")
    attempt = (context.journal(OFFLINE_CONTAINER).get("recovery") or {}).get("attempt")
    docker.restart(OFFLINE_CONTAINER)
    time.sleep(20)
    check(((context.journal(OFFLINE_CONTAINER).get("recovery") or {}).get("attempt")) == attempt, "offline failure retried automatically")
    evidence.write_json("scenario-7/offline-status.json", payload)
    evidence.write("scenario-7/journal.json", docker.read_file(OFFLINE_CONTAINER, JOURNAL))
    evidence.write("scenario-7/executor-log.txt", docker.logs(OFFLINE_CONTAINER, tail=300))
    context.record("offline-cache-cleared", True, "executor and maintenance started without runtime imports; restoration failed explicitly")


def scenario_missing_material(context: Context, port: int) -> None:
    docker = context.docker
    evidence = context.evidence
    website_files = VENV + "/lib/python3.12/site-packages"
    docker.start(
        MISSING_CONTAINER, OFFLINE_SNAPSHOT, port=port,
        cold_setup="rm -rf " + website_files + "/docassemble/webapp " + website_files + "/docassemble_webapp-*.dist-info "
        + "&& rm -rf /usr/share/docassemble/managed/bundles/core-r1",
    )
    http = Http("http://127.0.0.1:" + str(port))
    payload = wait_for_maintenance(http)
    failure = json.dumps(payload)
    check("recovery material unavailable" in failure, "missing recovery material was not reported explicitly")
    check(payload.get("recovery_available") is False, "missing material advertised browser recovery")
    processes = docker.exec(MISSING_CONTAINER, ["pgrep", "-af", "gunicorn|celery"], check_result=False).stdout
    check("gunicorn" not in processes and "celery" not in processes, "workloads started with missing recovery material")
    attempt = (context.journal(MISSING_CONTAINER).get("recovery") or {}).get("attempt")
    docker.restart(MISSING_CONTAINER)
    time.sleep(15)
    check(((context.journal(MISSING_CONTAINER).get("recovery") or {}).get("attempt")) == attempt, "missing material retried automatically")
    evidence.write_json("scenario-8/maintenance-status.json", payload)
    evidence.write("scenario-8/journal.json", docker.read_file(MISSING_CONTAINER, JOURNAL))
    context.record("missing-recovery-material", True, "missing retained material failed explicitly without a retry loop")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the independent recovery proof scenarios.")
    parser.add_argument("--image", default="da-uv-proof:local")
    parser.add_argument("--evidence", default=None)
    parser.add_argument('--keep-containers', action='store_true', help='keep disposable fixtures for debugging')
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    evidence_root = Path(args.evidence) if args.evidence else Path(__file__).resolve().parent.parent / "evidence" / ("run-" + stamp)
    evidence = Evidence(evidence_root)
    evidence.write("provenance.txt", "image=" + args.image + "\nstarted=" + stamp + "\n")
    context = Context(docker=Docker(evidence), evidence=evidence, image=args.image)
    image_info = json.loads(context.docker.run(['image', 'inspect', args.image]).stdout)[0]
    evidence.write_json('image-identity.json', {
        'id': image_info['Id'], 'repo_digests': image_info.get('RepoDigests', []),
        'architecture': image_info['Architecture'], 'os': image_info['Os'], 'created': image_info['Created'],
    })
    create_tables_hash = ""
    try:
        http = scenario_normal_boot(context, PORT)
        create_tables_hash = context.hashlib_file(CONTAINER, CREATE_TABLES_LOG)
        scenario_broken_runtime(context, http)
        scenario_interrupted_install(context, http)
        scenario_repair_action(context, http)
        scenario_restart_and_schema_skip(context, http, create_tables_hash)
        scenario_incompatible_database(context, http)
        scenario_offline_cache_cleared(context)
        scenario_missing_material(context, PORT + 1)
    except (Failure, ValueError, OSError, subprocess.SubprocessError) as error:
        context.record("harness", False, type(error).__name__ + ': ' + str(error))
    summary = {
        "image": args.image,
        "image_id": image_info['Id'],
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "results": context.results,
    }
    evidence.write_json("summary.json", summary)
    evidence.write("summary.md", "\n".join([
        "# Independent recovery proof: " + stamp,
        "",
        "Image: `" + args.image + "`",
        "",
        *["- " + ("PASS" if item["ok"] else "FAIL") + " **" + item["scenario"] + "**: " + item["detail"] for item in context.results],
        "",
    ]))
    print(json.dumps(summary, indent=2))
    for container in (CONTAINER, OFFLINE_CONTAINER, MISSING_CONTAINER):
        evidence.write("logs/" + container + ".txt", context.docker.logs(container, tail=500))
        if not args.keep_containers:
            context.docker.rm(container)
    if not args.keep_containers:
        context.docker.run(['image', 'rm', OFFLINE_SNAPSHOT], check_result=False)
    if any(not item["ok"] for item in context.results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
