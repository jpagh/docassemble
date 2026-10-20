"""Infrastructure bootstrap that must work without runtime imports.

PostgreSQL and Redis are started directly through OS tooling.  Role/database
creation uses ``psql`` as the postgres OS user; none of this imports
docassemble or reads configuration through the runtime.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from .config import DatabaseSettings
from .health import Check


def _run(command: list[str], input_text: str | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def pg_is_ready() -> bool:
    completed = _run(["pg_isready", "-q"], timeout=10)
    return completed.returncode == 0


def postgres_cluster() -> str | None:
    clusters = sorted(Path("/etc/postgresql").glob("*/main"))
    if not clusters:
        return None
    return clusters[0].parent.name


def start_postgres(wait_seconds: int = 60) -> Check:
    if pg_is_ready():
        return Check("postgres", True, "PostgreSQL is already accepting connections")
    version = postgres_cluster()
    if version is None:
        return Check("postgres", False, "no PostgreSQL cluster directory found under /etc/postgresql")
    completed = _run(["pg_ctlcluster", version, "main", "start"])
    if completed.returncode != 0 and not pg_is_ready():
        detail = (completed.stderr or completed.stdout or "").strip()
        return Check("postgres", False, "pg_ctlcluster failed: " + detail)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if pg_is_ready():
            return Check("postgres", True, "PostgreSQL " + version + " is accepting connections")
        time.sleep(1)
    return Check("postgres", False, "PostgreSQL did not become ready within " + str(wait_seconds) + " seconds")


def redis_is_ready() -> bool:
    completed = _run(["redis-cli", "ping"], timeout=10)
    return completed.returncode == 0 and "PONG" in completed.stdout


def start_redis(wait_seconds: int = 30) -> Check:
    if redis_is_ready():
        return Check("redis", True, "Redis is already accepting connections")
    completed = _run(["redis-server", "/etc/redis/redis.conf", "--daemonize", "yes"])
    if completed.returncode != 0 and not redis_is_ready():
        detail = (completed.stderr or completed.stdout or "").strip()
        return Check("redis", False, "redis-server failed: " + detail)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if redis_is_ready():
            return Check("redis", True, "Redis is accepting connections")
        time.sleep(1)
    return Check("redis", False, "Redis did not become ready within " + str(wait_seconds) + " seconds")


def stop_postgres() -> Check:
    version = postgres_cluster()
    if version is None:
        return Check('postgres-stop', False, 'local cluster is unavailable')
    status = _run(['/usr/bin/pg_ctlcluster', version, 'main', 'status'])
    if status.returncode == 3:
        return Check('postgres-stop', True, 'PostgreSQL process is already stopped')
    result = _run(['/usr/bin/pg_ctlcluster', '--mode', 'smart', version, 'main', 'stop'], timeout=120)
    stopped = _run(['/usr/bin/pg_ctlcluster', version, 'main', 'status']).returncode == 3
    return Check('postgres-stop', result.returncode == 0 and stopped, 'OS smart stop; no forced termination')


def stop_redis() -> Check:
    def exited():
        result = _run(['/usr/bin/ps', '-C', 'redis-server', '-o', 'stat='])
        return result.returncode in (0, 1) and all(state.startswith('Z') for state in result.stdout.split())
    if exited() and not redis_is_ready():
        return Check('redis-stop', True, 'Redis process is already stopped')
    result = _run(['/usr/bin/redis-cli', 'shutdown', 'save'], timeout=120)
    deadline = time.monotonic() + 10
    while not exited() and time.monotonic() < deadline:
        time.sleep(0.05)
    return Check('redis-stop', result.returncode == 0 and exited(), 'OS shutdown with durable SAVE')


def _quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def ensure_role_and_database(settings: DatabaseSettings) -> Check:
    """Create the fixture role/database if absent; existing objects are kept."""
    if not settings.user:
        return Check("database-bootstrap", False, "no database user configured")
    sql_parts = []
    check_role = _run(
        ["su", "postgres", "-s", "/bin/sh", "-c", "psql -Atq"],
        input_text="SELECT 1 FROM pg_roles WHERE rolname=" + _quote_literal(settings.user) + ";\n",
    )
    if check_role.returncode != 0:
        return Check("database-bootstrap", False, "could not inspect PostgreSQL roles: " + check_role.stderr.strip())
    if check_role.stdout.strip() != "1":
        sql_parts.append(
            "CREATE ROLE " + _quote_ident(settings.user) + " LOGIN PASSWORD " + _quote_literal(settings.password or "") + ";\n"
        )
    else:
        sql_parts.append(
            "ALTER ROLE " + _quote_ident(settings.user) + " PASSWORD " + _quote_literal(settings.password or "") + ";\n"
        )
    check_db = _run(
        ["su", "postgres", "-s", "/bin/sh", "-c", "psql -Atq"],
        input_text="SELECT 1 FROM pg_database WHERE datname=" + _quote_literal(settings.name) + ";\n",
    )
    if check_db.returncode != 0:
        return Check("database-bootstrap", False, "could not inspect PostgreSQL databases: " + check_db.stderr.strip())
    if check_db.stdout.strip() != "1":
        sql_parts.append(
            "CREATE DATABASE " + _quote_ident(settings.name) + " OWNER " + _quote_ident(settings.user) + ";\n"
        )
    completed = _run(
        ["su", "postgres", "-s", "/bin/sh", "-c", "psql -Atq -v ON_ERROR_STOP=1"],
        input_text="".join(sql_parts),
    )
    if completed.returncode != 0:
        return Check(
            "database-bootstrap",
            False,
            "database bootstrap failed: " + (completed.stderr or completed.stdout).strip(),
        )
    return Check("database-bootstrap", True, "role and database are available")
