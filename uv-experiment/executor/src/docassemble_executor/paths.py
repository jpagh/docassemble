"""Filesystem and identity paths for the lifecycle executor.

Everything is derived from environment variables with conservative defaults so
that the same code can run inside the disposable fixture container and in unit
tests.
"""

from __future__ import annotations

import os
import pwd
from dataclasses import dataclass
from pathlib import Path


def _env_path(name: str, default: str) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else Path(default)


@dataclass(frozen=True)
class ExecutorPaths:
    da_root: Path
    runtime_venv: Path
    bundles_dir: Path
    state_dir: Path
    run_dir: Path
    cache_dir: Path
    log_dir: Path
    config_file: Path
    uv_binary: str
    web_port: int
    intent_file: Path
    journal_file: Path
    applied_file: Path
    status_file: Path
    repair_request_file: Path
    manifest_file: Path

    @classmethod
    def from_env(cls) -> "ExecutorPaths":
        da_root = _env_path("DA_ROOT", "/usr/share/docassemble")
        runtime_venv = _env_path("DA_RUNTIME_VENV", str(da_root / "local3.12"))
        bundles_dir = _env_path("DA_BUNDLE_DIR", str(da_root / "managed" / "bundles"))
        state_dir = _env_path("DA_MANAGED_STATE_DIR", "/var/lib/docassemble/managed")
        run_dir = _env_path("DA_RUN_DIR", "/var/run/docassemble")
        recovery_run_dir = run_dir / "recovery"
        return cls(
            da_root=da_root,
            runtime_venv=runtime_venv,
            bundles_dir=bundles_dir,
            state_dir=state_dir,
            run_dir=run_dir,
            cache_dir=_env_path("UV_CACHE_DIR", "/var/cache/docassemble/uv"),
            log_dir=_env_path("DA_LOG_DIR", str(da_root / "log")),
            config_file=_env_path("DA_CONFIG_FILE", str(da_root / "config" / "config.yml")),
            uv_binary=os.environ.get("UV_BINARY", "uv"),
            web_port=int(os.environ.get("DA_WEB_PORT", "80")),
            intent_file=state_dir / "desired.json",
            journal_file=state_dir / "lifecycle.json",
            applied_file=state_dir / "applied.json",
            status_file=recovery_run_dir / "status.json",
            repair_request_file=recovery_run_dir / 'requests' / "repair-request.json",
            manifest_file=bundles_dir / "manifest.json",
        )

    def ensure_directories(self) -> None:
        for directory in (
            self.state_dir,
            self.run_dir,
            self.status_file.parent,
            self.repair_request_file.parent,
            self.cache_dir,
            self.log_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        if os.geteuid() == 0:
            # The web process (www-data) must be able to publish repair requests
            # and read status, but must not be able to rewrite executor state.
            for directory in (self.repair_request_file.parent,):
                os.chown(directory, 0, 33)  # root:www-data
                os.chmod(directory, 0o775)
            for directory in (self.state_dir, self.run_dir, self.status_file.parent):
                os.chown(directory, 0, 0)
                os.chmod(directory, 0o755)
            user = os.environ.get('DA_ENVIRONMENT_USER')
            if user:
                account = pwd.getpwnam(user)
                # Preparation is non-root but shares exclusion with the root
                # installer/cache operator. The root-owned parent prevents lock
                # replacement; only this inert flock file is group-writable.
                lock = self.state_dir / 'mutation.lock'
                lock.touch(exist_ok=True)
                os.chown(lock, 0, account.pw_gid)
                os.chmod(lock, 0o660)
                os.chown(self.cache_dir, account.pw_uid, account.pw_gid)
                os.chmod(self.cache_dir, 0o755)

    @property
    def backend_port(self) -> int:
        return int(os.environ.get('DA_BACKEND_PORT', str(self.web_port + 1)))

    def core_bundle(self, baseline: str) -> Path:
        return self.bundles_dir / ("core-" + baseline)

    def runtime_bundle(self, revision: str) -> Path:
        return self.bundles_dir / ("runtime-" + revision)
