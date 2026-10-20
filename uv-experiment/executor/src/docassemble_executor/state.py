"""Desired and applied state used by the proof fixture.

``desired.json`` is owned by the publication side (in the full design) and is
never rewritten by recovery.  ``applied.json`` records the revision the
executor actually verified.  Both are plain JSON so a broken runtime is not
needed to read them.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .journal import utcnow


def _atomic_write_json(path: Path, payload: dict[str, Any], *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".state-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fchmod(handle.fileno(), mode)
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


@dataclass
class DesiredState:
    revision: str | None = None
    core_baseline: str | None = None
    packages: tuple[str, ...] = ()

    @classmethod
    def load(cls, path: Path) -> "DesiredState":
        if not path.is_file():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            revision=raw.get("revision"),
            core_baseline=raw.get("core_baseline"),
            packages=tuple(raw.get("packages", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "core_baseline": self.core_baseline,
            "packages": list(self.packages),
        }


@dataclass
class AppliedState:
    revision: str | None = None
    core_baseline: str | None = None
    at: str | None = None

    @classmethod
    def load(cls, path: Path) -> "AppliedState":
        if not path.is_file():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            revision=raw.get("revision"),
            core_baseline=raw.get("core_baseline"),
            at=raw.get("at"),
        )

    def save(self, path: Path) -> None:
        _atomic_write_json(
            path,
            {"revision": self.revision, "core_baseline": self.core_baseline, "at": self.at},
        )

    def mark(self, revision: str, core_baseline: str | None) -> None:
        self.revision = revision
        self.core_baseline = core_baseline
        self.at = utcnow()
