"""Runtime environment probes.

Probing uses the runtime interpreter only through a short-lived subprocess with
a timeout, so a broken or partially-synced environment cannot break the
executor process itself.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .authority import run_runtime


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class ProbeResult:
    ok: bool
    checks: list[Check] = field(default_factory=list)
    core_versions: dict[str, str] = field(default_factory=dict)
    python_version: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "checks": [check.to_dict() for check in self.checks],
            "core_versions": self.core_versions,
            "python_version": self.python_version,
        }


CORE_DISTRIBUTIONS = ("docassemble.base", "docassemble.demo", "docassemble.webapp")


def runtime_python(venv: Path) -> Path:
    return venv / "bin" / "python"


def runtime_files_present(venv: Path) -> bool:
    """Cheap file-level check used before the database is initialized.

    A brand-new database cannot support the full core import, so the first
    managed boot verifies the installed files instead and validates the import
    after normal-mode schema initialization.
    """
    if not runtime_python(venv).is_file():
        return False
    site = sorted(venv.glob("lib/python3.*/site-packages"))
    if not site:
        return False
    packages = site[0]
    return (packages / "docassemble" / "webapp" / "__init__.py").is_file() and (packages / "docassemble" / "base").is_dir()


def _subprocess_env(venv: Path, config_file: Path | None, recovery: bool) -> dict[str, str]:
    env = {
        "PATH": str(venv / "bin") + ":/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": "/var/www",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "CONTAINERROLE": ":all:",
        "PYTHONDONTWRITEBYTECODE": "1",
        "DARECOVERYMODE": "true" if recovery else "false",
    }
    if config_file is not None:
        env["DA_CONFIG_FILE"] = str(config_file)
    else:
        env["DA_CONFIG_FILE"] = "/usr/share/docassemble/config/config.yml"
    return env


def _run_probe(python: Path, code: str, env: dict[str, str], timeout: int = 300) -> subprocess.CompletedProcess:
    command = [str(python), '-c', code]
    if os.geteuid() == 0:
        command = ['runuser', '-u', 'www-data', '--'] + command
    return run_runtime(command, env=env, timeout=timeout)


def probe_runtime(
    venv: Path,
    config_file: Path | None = None,
    expected_versions: dict[str, str] | None = None,
    recovery: bool = True,
) -> ProbeResult:
    """Import the core web application and verify the installed core identity.

    The probe deliberately runs with ``DARECOVERYMODE`` set (by default true):
    it must validate the core without importing or executing user packages.  A
    normal web server is started separately for normal service.
    """
    result = ProbeResult(ok=False)
    python = runtime_python(venv)
    if not python.is_file():
        result.checks.append(Check("runtime-interpreter", False, "runtime interpreter is missing: " + str(python)))
        return result
    result.checks.append(Check("runtime-interpreter", True, str(python)))
    env = _subprocess_env(venv, config_file, recovery)
    try:
        completed = _run_probe(python, "import docassemble.webapp.server; print('import-ok')", env)
    except subprocess.TimeoutExpired:
        result.checks.append(Check("core-import", False, "importing the core web application timed out"))
        return result
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        tail = " | ".join(detail[-3:]) if detail else "no output"
        result.checks.append(Check("core-import", False, tail))
        return result
    result.checks.append(Check("core-import", True, "core web application imports"))

    probe_code = (
        "import importlib.metadata as m, sys, json\n"
        "dists = ['docassemble.base','docassemble.demo','docassemble.webapp']\n"
        "versions = {}\n"
        "for name in dists:\n"
        "    try:\n"
        "        versions[name] = m.version(name)\n"
        "    except m.PackageNotFoundError:\n"
        "        versions[name] = None\n"
        "print(json.dumps({'python': sys.version.split()[0], 'versions': versions}))"
    )
    try:
        completed = _run_probe(python, probe_code, env, timeout=120)
    except subprocess.TimeoutExpired:
        result.checks.append(Check("core-identity", False, "reading installed core versions timed out"))
        return result
    if completed.returncode != 0:
        result.checks.append(Check("core-identity", False, "could not read installed core versions"))
        return result
    import json

    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        versions = payload['versions']
        if not all(isinstance(versions.get(name), str) for name in CORE_DISTRIBUTIONS):
            raise ValueError('core distribution metadata is missing')
    except (ValueError, IndexError, KeyError, TypeError, AttributeError) as error:
        result.checks.append(Check('core-identity', False, 'could not read core identity: ' + str(error)))
        return result
    result.python_version = payload.get("python")
    result.core_versions = versions
    result.checks.append(Check("core-identity", True, "installed core: " + ", ".join(sorted(result.core_versions.values()))))
    if expected_versions:
        mismatches = [
            name + ": expected " + expected + ", found " + result.core_versions.get(name, "missing")
            for name, expected in sorted(expected_versions.items())
            if result.core_versions.get(name) != expected
        ]
        if mismatches:
            result.checks.append(Check("core-version-match", False, "; ".join(mismatches)))
            return result
        result.checks.append(Check("core-version-match", True, "installed core versions match the retained bundle"))
    result.ok = True
    return result
