"""Frozen uv synchronization into the single application runtime venv."""

from __future__ import annotations

import os
import shutil
import pwd
import tempfile
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .authority import RuntimeLock, guarded_popen, mutation_fd


@dataclass
class SyncResult:
    ok: bool
    command: list[str]
    returncode: int | None
    duration_seconds: float
    interrupted: bool = False
    output_tail: str = ""
    log_path: str | None = None

    def describe(self) -> str:
        if self.interrupted:
            return "frozen sync was interrupted after " + str(round(self.duration_seconds, 1)) + " seconds"
        if self.ok:
            return "frozen sync completed in " + str(round(self.duration_seconds, 1)) + " seconds"
        return ("frozen sync failed with exit code " + str(self.returncode) + ": " + (self.output_tail or "no output")
                + "; verify locked source availability, network, committed authentication and retained mandatory artifacts. "
                "Restore access and explicitly retry the same revision, or prepare/approve a repair; "
                "cache removal cannot replace unavailable source material and never authorizes a new solve.")


def build_sync_env(venv: Path, cache_dir: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    env.pop("VIRTUAL_ENV_PROMPT", None)
    env["UV_PROJECT_ENVIRONMENT"] = str(venv)
    env["UV_CACHE_DIR"] = str(cache_dir)
    env["UV_PYTHON_DOWNLOADS"] = "never"
    env["UV_NO_PROGRESS"] = "1"
    env["CONTAINERROLE"] = ":all:"
    env.setdefault("HOME", "/var/www")
    if extra:
        env.update(extra)
    return env


def build_sync_command(bundle_dir: Path, uv_binary: str) -> list[str]:
    return [uv_binary, "sync", "--frozen", "--project", str(bundle_dir)]


def sync_frozen(
    bundle_dir: Path,
    venv: Path,
    uv_binary: str,
    cache_dir: Path,
    log_dir: Path | None = None,
    fault_after_seconds: float | None = None,
    env_extra: dict[str, str] | None = None,
    reinstall: bool = False,
) -> SyncResult:
    """Run ``uv sync --frozen`` for ``bundle_dir`` into ``venv``.

    With ``fault_after_seconds`` set the sync is started and then killed with
    SIGKILL as a process group.  This is the deliberate-interruption fixture:
    the venv may be partially mutated and the journal is left at
    ``mutation_started``.
    """
    user = os.environ.get('DA_ENVIRONMENT_USER') if os.geteuid() == 0 else None
    if os.geteuid() == 0 and not user:
        raise ValueError('root sync requires a dedicated DA_ENVIRONMENT_USER')
    if user:
        # Build hooks may write to their source tree. Never let them modify the
        # retained image bundle, executor, pinned interpreter or uv executable.
        account = pwd.getpwnam(user)
        if account.pw_uid == 0:
            raise ValueError('third-party sync/build processes must never run as root')
        with tempfile.TemporaryDirectory(prefix='frozen-', dir=account.pw_dir) as directory:
            staging = Path(directory)
            shutil.copytree(bundle_dir, staging, dirs_exist_ok=True)
            for path in [staging, *staging.rglob('*')]:
                if not path.is_symlink():
                    os.chown(path, account.pw_uid, account.pw_gid)
            return _sync_frozen(staging, venv, uv_binary, cache_dir, log_dir,
                                fault_after_seconds, env_extra, reinstall, user,
                                allow_builds=(bundle_dir.name == 'runtime-fault' and fault_after_seconds is not None))
    return _sync_frozen(bundle_dir, venv, uv_binary, cache_dir, log_dir,
                        fault_after_seconds, env_extra, reinstall,
                        allow_builds=(bundle_dir.name == 'runtime-fault' and fault_after_seconds is not None))


def _sync_frozen(
    bundle_dir: Path, venv: Path, uv_binary: str, cache_dir: Path,
    log_dir: Path | None, fault_after_seconds: float | None,
    env_extra: dict[str, str] | None, reinstall: bool, user: str | None = None,
    allow_builds: bool = False,
) -> SyncResult:
    command = build_sync_command(bundle_dir, uv_binary)
    if not allow_builds:
        command.append('--no-build')
    if reinstall:
        command.append("--reinstall")
    env = build_sync_env(venv, cache_dir, env_extra)
    if user:
        env['HOME'] = pwd.getpwnam(user).pw_dir
        command = ['runuser', '-u', user, '--'] + command
    log_path: Path | None = None
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / ("uv-sync-" + time.strftime("%Y%m%d-%H%M%S") + ".log")
    started = time.monotonic()
    state_dir = Path(os.environ.get('DA_MANAGED_STATE_DIR', '/var/lib/docassemble/managed'))
    with RuntimeLock(state_dir) as runtime_fd:
        with open(log_path, 'w+b') if log_path else tempfile.TemporaryFile() as handle:
            try:
                process = guarded_popen(command, mutation_fd(), extra_fds=(runtime_fd,),
                                        cwd=str(bundle_dir), env=env, stdout=handle,
                                        stderr=subprocess.STDOUT)
            except OSError as error:
                return SyncResult(False, command, None, time.monotonic() - started, output_tail=str(error))
            interrupted = False
            try:
                process.wait(timeout=fault_after_seconds if fault_after_seconds is not None else 3600)
            except subprocess.TimeoutExpired:
                interrupted = True
                process.terminate()  # Guardian cancels the payload, then reaps all descendants.
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass  # Guardian retains both locks; no takeover while descendants survive.
            handle.seek(0)
            output = handle.read().decode(errors='replace')
    return SyncResult(
        ok=not interrupted and process.returncode == 0,
        command=command,
        returncode=process.returncode,
        duration_seconds=time.monotonic() - started,
        interrupted=interrupted,
        output_tail='\n'.join(output.strip().splitlines()[-10:]),
        log_path=str(log_path) if log_path else None,
    )
