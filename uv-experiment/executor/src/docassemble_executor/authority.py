"""Host mutation exclusion and privileged child-lifetime supervision.

Never explicitly unlock an inherited flock: closing the last description is
what releases authority. The guardian owns a duplicate and, on Linux, reaps
orphaned descendants even when they create a new session or close all fds.
This is crash exclusion, not a sandbox against malicious privileged operators.
"""
from __future__ import annotations

import ctypes
import fcntl
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from contextvars import ContextVar

_current_authority: ContextVar[int | None] = ContextVar('mutation_authority', default=None)


def mutation_fd() -> int:
    fd = _current_authority.get()
    if fd is None:
        raise RuntimeError('frozen sync requires exclusive mutation authority')
    return fd


class MutationLockBusy(BlockingIOError):
    """The nonblocking mutation flock found another owner.

    Subclassing BlockingIOError keeps existing busy-lock no-op callers unchanged
    while allowing callers to distinguish contention from a failure to open or
    configure the lock file.
    """


class MutationLock:
    def __init__(self, state_dir: Path, wait_seconds: float = 0):
        self.path = state_dir / 'mutation.lock'
        self.wait_seconds = wait_seconds
        self.handle = None

    @property
    def fd(self) -> int:
        if self.handle is None:
            raise RuntimeError('mutation authority is not held')
        return self.handle.fileno()

    def __enter__(self):
        self.handle = self.path.open('a')
        try:
            deadline = time.monotonic() + self.wait_seconds
            while True:
                try:
                    fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as error:
                    if time.monotonic() >= deadline:
                        raise MutationLockBusy(*error.args) from error
                    time.sleep(0.02)
        except BaseException:
            self.handle.close()
            self.handle = None
            raise
        self.token = _current_authority.set(self.fd)
        return self

    def __exit__(self, *exc_info):
        if self.handle is not None:
            _current_authority.reset(self.token)
            self.handle.close()
            self.handle = None


class RuntimeLock:
    def __init__(self, state_dir: Path, shared: bool = False):
        self.path = state_dir / 'runtime.lock'
        self.shared = shared

    def __enter__(self):
        self.handle = self.path.open('r' if self.shared and self.path.exists() else 'a')
        try:
            fcntl.flock(self.handle, (fcntl.LOCK_SH if self.shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BaseException:
            self.handle.close()
            raise
        return self.handle.fileno()

    def __exit__(self, *args):
        self.handle.close()


def guarded_popen(command: list[str], lock_fd: int, extra_fds: tuple[int, ...] = (),
                  graceful: bool = False, inherit_lock: bool = False, **kwargs) -> subprocess.Popen:
    """The guardian survives its controller and retains authority until reaped."""
    return subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), str(lock_fd),
         'inherit' if inherit_lock else 'graceful' if graceful else 'cancel', *command],
        pass_fds=(lock_fd, *extra_fds), start_new_session=True, **kwargs,
    )


def run_runtime(command: list[str], *, env: dict[str, str], timeout: float) -> subprocess.CompletedProcess:
    """Run a controlled runtime helper under child-lifetime read authority."""
    state_dir = Path(os.environ.get('DA_MANAGED_STATE_DIR', '/var/lib/docassemble/managed'))
    with RuntimeLock(state_dir, shared=True) as runtime_fd:
        process = guarded_popen(command, runtime_fd, extra_fds=(mutation_fd(),),
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                pass  # The guardian still owns authority until all children exit.
            raise
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _guard(fd: int, command: list[str], graceful: bool, inherit_lock: bool = False) -> int:
    if sys.platform == 'linux':
        # PR_SET_CHILD_SUBREAPER: detached grandchildren become our children,
        # not PID 1's, so parent exit cannot release mutation authority early.
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), 'cannot establish child-lifetime authority')
    os.fstat(fd)  # Fail before launching anything if authority was not inherited.
    child = None
    cancelled = False

    def cancel(*args):
        nonlocal cancelled
        cancelled = True
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGTERM if graceful else signal.SIGKILL)
            except ProcessLookupError:
                pass

    def reload_child(*args):
        if child is not None:
            try:
                os.kill(child.pid, signal.SIGHUP)
            except ProcessLookupError:
                pass

    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    signal.signal(signal.SIGHUP, reload_child)
    child = subprocess.Popen(command, start_new_session=True, pass_fds=(fd,) if inherit_lock else ())
    if cancelled:
        cancel()
    code = child.wait()
    while True:
        try:
            os.waitpid(-1, 0)
        except InterruptedError:
            continue
        except ChildProcessError:
            break
    return code if code >= 0 else 128 - code


if __name__ == '__main__':
    sys.exit(_guard(int(sys.argv[1]), sys.argv[3:], sys.argv[2] == 'graceful', sys.argv[2] == 'inherit'))
