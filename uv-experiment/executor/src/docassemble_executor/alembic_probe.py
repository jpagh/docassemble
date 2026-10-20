"""Database schema compatibility checking without importing docassemble.

The recovery core must be able to use the database, but recovery must never run
older setup, stamp a schema, or roll back.  This module compares the database's
recorded Alembic revision against the revisions shipped with the core that is
about to serve restricted administration.  Unknown or missing revisions stop
recovery for operator intervention.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from .config import DatabaseSettings

STATUS_COMPATIBLE = "compatible"
STATUS_INCOMPATIBLE = "incompatible"
STATUS_UNREADABLE = "unreadable"
STATUS_UNPROVEN = "unproven"


@dataclass(frozen=True)
class SchemaCheck:
    status: str
    detail: str
    database_revision: str | None = None
    known_revisions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def usable(self) -> bool:
        return self.status in (STATUS_COMPATIBLE, STATUS_UNPROVEN)

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "detail": self.detail,
            "database_revision": self.database_revision,
            "known_revision_count": len(self.known_revisions),
        }


def find_versions_dir(venv: Path) -> Path | None:
    """Locate the Alembic versions directory inside an installed core."""
    candidates = sorted(venv.glob("lib/python3.*/site-packages/docassemble/webapp/alembic/versions"))
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def _literal_or_none(node: ast.AST) -> object:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def collect_revisions(versions_dir: Path) -> dict[str, tuple[str, ...]]:
    """Return ``revision -> down_revision(s)`` for every migration file.

    Parsing is done with :mod:`ast` so no migration or docassemble code is
    imported.  Files whose revision cannot be determined are ignored, which
    makes the compatibility set conservative.
    """
    revisions: dict[str, tuple[str, ...]] = {}
    for path in sorted(versions_dir.glob("*.py")):
        if path.name.startswith("__"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        revision: str | None = None
        downs: tuple[str, ...] = ()
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                if target.id == "revision":
                    value = _literal_or_none(node.value)
                    if isinstance(value, str) and value:
                        revision = value
                elif target.id == "down_revision":
                    value = _literal_or_none(node.value)
                    if isinstance(value, str):
                        downs = (value,)
                    elif isinstance(value, (tuple, list)):
                        downs = tuple(item for item in value if isinstance(item, str))
        if revision is not None:
            revisions[revision] = downs
    return revisions


def decide_compatibility(database_revision: str | None, known_revisions: dict[str, tuple[str, ...]]) -> tuple[str, str]:
    """Pure decision used by the checker and by unit tests."""
    if database_revision is None:
        return STATUS_INCOMPATIBLE, "database has no recorded Alembic revision; operator intervention required"
    if database_revision in known_revisions:
        return STATUS_COMPATIBLE, "database revision is known to the recovery core"
    return STATUS_INCOMPATIBLE, (
        "database revision " + database_revision + " is not known to the recovery core; operator intervention required"
    )


def read_database_revision(settings: DatabaseSettings) -> str | None:
    """Read ``alembic_version.version_num`` or return ``None`` when absent."""
    import psycopg2  # imported lazily so pure unit tests need no database

    with psycopg2.connect(**settings.connect_kwargs) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('alembic_version')")
            if cursor.fetchone()[0] is None:
                return None
            cursor.execute("SELECT version_num FROM alembic_version LIMIT 2")
            rows = cursor.fetchall()
            if not rows:
                return None
            if len(rows) != 1 or not isinstance(rows[0][0], str) or not rows[0][0].strip():
                raise ValueError('database requires exactly one nonempty Alembic head')
            return rows[0][0]


def check_schema(venv: Path, settings: DatabaseSettings, compatible_revisions: tuple[str, ...] = ()) -> SchemaCheck:
    versions_dir = find_versions_dir(venv)
    if versions_dir is None:
        return SchemaCheck(
            status=STATUS_UNREADABLE,
            detail="recovery core does not contain an Alembic versions directory",
        )
    known = collect_revisions(versions_dir)
    if not known:
        return SchemaCheck(
            status=STATUS_UNREADABLE,
            detail="recovery core contains no readable Alembic revisions",
        )
    try:
        database_revision = read_database_revision(settings)
    except Exception as error:  # pragma: no cover - exercised in the container
        return SchemaCheck(
            status=STATUS_UNREADABLE,
            detail="database is not usable by the recovery core: " + error.__class__.__name__ + ": " + str(error),
            known_revisions=tuple(sorted(known)),
        )
    status, detail = decide_compatibility(database_revision, known)
    if database_revision in compatible_revisions:
        status = STATUS_COMPATIBLE
        detail = 'database revision is explicitly qualified by the retained fixture compatibility inventory'
    return SchemaCheck(
        status=status,
        detail=detail,
        database_revision=database_revision,
        known_revisions=tuple(sorted(known)),
    )
