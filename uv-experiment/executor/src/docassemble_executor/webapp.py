"""Start, stop and wait for the application web server.

The web server is always launched from the single runtime venv.  Restricted
recovery is the same core code with ``DARECOVERYMODE=true``; it is not a second
application environment.
"""

from __future__ import annotations

import os
import pwd
import signal
import stat
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .authority import RuntimeLock, guarded_popen
from .paths import ExecutorPaths


@dataclass
class WebServer:
    pid: int
    process: subprocess.Popen
    log_path: Path
    mode: str


def web_log_path(paths: ExecutorPaths, mode: str) -> Path:
    return paths.log_dir / ("web-" + mode + ".log")


def managed_ingress_environment(paths: ExecutorPaths, parent: dict[str, str]) -> dict[str, str]:
    """Forward web ingress only; worker stores remain private parent authority."""
    names = ('DA_MANAGED_ARTIFACT_ROOT', 'DA_MANAGED_ARTIFACT_UID',
             'DA_EXECUTOR_ARTIFACT_ROOT', 'DA_MANAGED_SECRET_ROOT')
    if not any(parent.get(name) for name in names):
        if parent.get('DA_QUALIFIED_IMAGE_BASELINE'):
            raise RuntimeError('managed web ingress configuration required')
        return {}
    if not all(parent.get(name) for name in names):
        raise RuntimeError('managed web ingress configuration incomplete')
    try:
        uid = int(parent['DA_MANAGED_ARTIFACT_UID'])
        account = pwd.getpwnam('www-data')
        if uid != account.pw_uid or uid == 0:
            raise ValueError()
        artifact = Path(parent['DA_MANAGED_ARTIFACT_ROOT'])
        web_secret = Path(parent.get('DA_MANAGED_WEB_SECRET_ROOT') or str(artifact) + '-secrets')
        worker_artifact = Path(parent['DA_EXECUTOR_ARTIFACT_ROOT'])
        worker_secret = Path(parent['DA_MANAGED_SECRET_ROOT'])
        roots = [artifact, web_secret, worker_artifact, worker_secret]
        configured = list(roots)
        retained = [paths.state_dir, paths.run_dir, paths.bundles_dir, paths.runtime_venv,
                    paths.log_dir, paths.config_file]
        for name in ('DA_DURABLE_WORK_STORE', 'DA_INDEX_CREDENTIAL_ROOT'):
            if parent.get(name):
                configured.append(Path(parent[name]))
                retained.append(Path(parent[name]))
        if parent.get('DA_QUALIFIED_IMAGE_BASELINE'):
            configured.append(Path(parent['DA_QUALIFIED_IMAGE_BASELINE']).parent)
            retained.append(Path(parent['DA_QUALIFIED_IMAGE_BASELINE']).parent)
        for root in [*roots, *retained]:
            if not root.is_absolute():
                raise ValueError()
        # Operator-configured roots must not hide behind symlinks. Fixed platform
        # paths are exempt from the ancestor check (e.g. /var/run -> /run on
        # Debian) and are compared resolved below instead.
        for root in configured:
            if any(part.is_symlink() for part in (root, *root.parents)):
                raise ValueError()
        resolved = [root.resolve() for root in roots]
        for index, root in enumerate(resolved):
            for other in [*resolved[index + 1:], *(path.resolve() for path in retained)]:
                if root == other or root in other.parents or other in root.parents:
                    raise ValueError()
        from .cache import validate_retained_paths
        validate_retained_paths(paths, [*roots, *retained])
        if not parent.get('DA_MANAGED_WEB_SECRET_ROOT') and os.geteuid() == 0:
            try:
                web_secret.mkdir(mode=0o700)
            except FileExistsError:
                pass  # Existing metadata is authority; never repair it.
            else:
                os.chown(web_secret, uid, account.pw_gid)
        for root in (artifact, web_secret):
            metadata = root.lstat()
            if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != uid or
                    stat.S_IMODE(metadata.st_mode) != 0o700):
                raise ValueError()
    except (OSError, ValueError, KeyError, RuntimeError):
        raise RuntimeError('unsafe managed web ingress layout') from None
    return {'DA_MANAGED_ARTIFACT_ROOT': parent['DA_MANAGED_ARTIFACT_ROOT'],
            'DA_MANAGED_ARTIFACT_UID': parent['DA_MANAGED_ARTIFACT_UID'],
            'DA_MANAGED_SECRET_ROOT': str(web_secret),
            'DA_MANAGED_WEB_SECRET_ROOT': str(web_secret)}


def start_web(paths: ExecutorPaths, recovery: bool, env_extra: dict[str, str] | None = None) -> WebServer:
    mode = "recovery" if recovery else "normal"
    gunicorn = paths.runtime_venv / "bin" / "gunicorn"
    log_path = web_log_path(paths, mode)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": str(paths.runtime_venv / "bin") + ":/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": "/var/www",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "CONTAINERROLE": ":all:",
        "DA_CONFIG_FILE": str(paths.config_file),
        "DARECOVERYMODE": "true" if recovery else "false",
        "DA_UV_MANAGED": os.environ.get('DA_UV_MANAGED', 'false'),
        "DA_RECOVERY_RUN_DIR": str(paths.status_file.parent),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
        "DA_MANAGED_STATE_DIR": str(paths.state_dir),
    }
    from docassemble.base.managed_qualification import ENVIRONMENT
    if os.environ.get(ENVIRONMENT):
        env[ENVIRONMENT] = os.environ[ENVIRONMENT]
    if env_extra:
        env.update(env_extra)
    # These roots are parent identity configuration, not env_extra overrides.
    ingress = managed_ingress_environment(paths, dict(os.environ))
    for name in ('DA_MANAGED_ARTIFACT_ROOT', 'DA_MANAGED_ARTIFACT_UID',
                 'DA_MANAGED_SECRET_ROOT', 'DA_MANAGED_WEB_SECRET_ROOT', 'DA_EXECUTOR_ARTIFACT_ROOT'):
        env.pop(name, None)
    env.update(ingress)
    # Options are passed through GUNICORN_CMD_ARGS, not argv: docassemble's
    # configuration loader treats file paths in sys.argv as a config file, and
    # an existing access-log path would be mistaken for one.
    gunicorn_args = [
        "--bind",
        "127.0.0.1:" + str(paths.backend_port),
        "--workers",
        "1",
        "--threads",
        "8",
        "--timeout",
        "300",
        "--graceful-timeout",
        "3600",
        "--access-logfile",
        str(log_path),
        "--error-logfile",
        str(log_path),
    ]
    if os.geteuid() == 0:
        gunicorn_args += ["--user", "www-data", "--group", "www-data"]
    env["GUNICORN_CMD_ARGS"] = " ".join(gunicorn_args)
    command = [str(gunicorn), "docassemble_executor.web_runtime:application"]
    with RuntimeLock(paths.state_dir, shared=True) as runtime_fd, open(log_path, 'ab') as log_handle:
        process = guarded_popen(command, runtime_fd, graceful=True,
                                cwd=str(paths.da_root / 'webapp'), env=env,
                                stdout=log_handle, stderr=subprocess.STDOUT)
    (paths.run_dir / ("web-" + mode + ".pid")).write_text(str(process.pid), encoding="utf-8")
    return WebServer(pid=process.pid, process=process, log_path=log_path, mode=mode)


def stop_web(paths: ExecutorPaths, mode: str | None = None, timeout: int = 30) -> None:
    # All controlled stops are graceful. A live descendant keeps its lease;
    # callers cannot interpret stop timeout as exclusive runtime authority.
    drain_web(paths, timeout=timeout, mode=mode)


def drain_web(paths: ExecutorPaths, timeout: float = 30, mode: str | None = None) -> bool:
    """Gracefully stop the drained backend; never force-kill to claim success."""
    deadline = time.monotonic() + timeout
    for current in ([mode] if mode else ['normal', 'recovery']):
        pid_file = paths.run_dir / ('web-' + current + '.pid')
        if not pid_file.is_file():
            continue
        pid = int(pid_file.read_text().strip())
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pid_file.unlink(missing_ok=True)
            continue
        while True:
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
            try:
                os.kill(pid, 0)
                proc_stat = Path('/proc') / str(pid) / 'stat'
                if proc_stat.exists() and proc_stat.read_text().rsplit(')', 1)[-1].split()[0] == 'Z':
                    pid_file.unlink(missing_ok=True)
                    break
            except ProcessLookupError:
                pid_file.unlink(missing_ok=True)
                break
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
    return True


def http_get(port: int, path: str, timeout: float = 5.0) -> tuple[int, bytes]:
    url = "http://127.0.0.1:" + str(port) + path
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as error:
        return 0, str(error).encode("utf-8")


def wait_for_http(port: int, path: str, timeout: int = 120, expected: tuple[int, ...] = (200,)) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, _ = http_get(port, path)
        if status in expected:
            return True
        time.sleep(1)
    return False
