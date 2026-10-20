"""Local, live accepted-work capabilities; no runtime imports or heartbeat expiry.

Random bearer names identify flock-held scopes, not durable permission. A nested
request pins the scope before doing work. Dead issuers grant no permission, but
guardians can inherit the descriptors across wrapper death. This is not a sandbox
against code deliberately forging authority as the trusted runtime account.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
import re
import secrets

from .admission import Admission

_current: ContextVar[str] = ContextVar('accepted_caller', default='')
HEADER = 'X-DA-Accepted-Caller'
ENV = 'DA_ACCEPTED_CALLER'


def current() -> str:
    return _current.get() or os.environ.get(ENV, '')


@contextmanager
def caller_context(name: str):
    token = _current.set(name)
    try:
        yield
    finally:
        _current.reset(token)


def subprocess_env() -> dict[str, str]:
    return dict(os.environ, **{ENV: current()})


@dataclass
class Capability:
    name: str
    fds: tuple[int, ...]

    @property
    def traffic_fd(self) -> int:
        return self.fds[-1]


BUSY_ROLES = ('web', 'websockets', 'nltk', 'conversion')


def role_busy(state_dir: Path, role: str) -> bool:
    if role not in BUSY_ROLES:
        raise ValueError('unknown busy role')
    try:
        with (state_dir / 'role-activity' / (role + '.lock')).open('r') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
    except OSError:
        pass  # Missing/uninspectable activity is not proof of retained work.
    return False


class Capabilities:
    def __init__(self, state_dir: Path, *, role: str | None = None):
        if role is not None and role not in BUSY_ROLES:
            raise ValueError('unknown receiving role')
        self.role = role
        self.admission = Admission(state_dir)
        self.directory = state_dir / 'callers'
        self.accepted = state_dir / 'accepted.lock'
        self._stopping = False

    def prepare(self):
        self.admission.state_dir.mkdir(parents=True, exist_ok=True)
        self.directory.mkdir(exist_ok=True, mode=0o700)
        if os.geteuid() == 0:
            os.chown(self.directory, 33, 33)
        self.accepted.touch(exist_ok=True)
        self.accepted.chmod(0o644)
        activity = self.admission.state_dir / 'role-activity'
        activity.mkdir(exist_ok=True, mode=0o755)
        for role in BUSY_ROLES:
            path = activity / (role + '.lock')
            path.touch(exist_ok=True)
            path.chmod(0o644)
        # Lifecycle preparation may remove only scopes proved expired under the
        # same mutex used to pin them. Never unlink a surviving scope's inode.
        with self.admission._mutex():
            for path in self.directory.iterdir():
                try:
                    with path.open('r') as handle:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        path.unlink()
                except OSError:
                    pass

    @contextmanager
    def accept(self, name: str = '', *, recovery_http: bool = False, launch: bool = False):
        with ExitStack() as stack:
            capability = None
            try:
                # Serialize fresh admission with closure. A nested caller is
                # allowed only while its actual authority descriptor is live.
                with self.admission._mutex():
                    if self._stopping:
                        raise ValueError('this service is retiring')
                    if launch and self.admission.state()['mode'] != 'preparing':
                        raise ValueError('replacement roles require qualification behind admission')
                    if name:
                        if not isinstance(name, str) or not re.fullmatch('[0-9a-f]{64}', name):
                            raise ValueError('invalid caller capability')
                        scope = stack.enter_context((self.directory / name).open('r'))
                        try:
                            fcntl.flock(scope, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            fcntl.flock(scope, fcntl.LOCK_SH)
                        else:
                            raise ValueError('expired caller capability')
                    else:
                        state = self.admission.state()
                        preparing = launch and state['mode'] == 'preparing'
                        if not preparing and (state['mode'] != 'open' or (state.get('consumers') is not True and not recovery_http)):
                            raise ValueError('consumer admission is closed')
                        scope = None
                        if preparing or state.get('consumers') is True:
                            name = secrets.token_hex(32)
                            scope = stack.enter_context((self.directory / name).open('x+'))
                            os.chmod(self.directory / name, 0o600)
                            if os.geteuid() == 0:
                                os.chown(self.directory / name, 33, 33)
                            fcntl.flock(scope, fcntl.LOCK_SH)
                    fds: tuple[int, ...] = ()
                    if scope is not None:
                        accepted = stack.enter_context(self.accepted.open('r'))
                        fcntl.flock(accepted, fcntl.LOCK_SH)
                        fds = (scope.fileno(), accepted.fileno())
                    if self.role is not None:
                        activity = stack.enter_context((self.admission.state_dir / 'role-activity' / (self.role + '.lock')).open('r'))
                        fcntl.flock(activity, fcntl.LOCK_SH)
                        fds = (*fds, activity.fileno())
                    traffic = stack.enter_context(self.admission.traffic.open('r'))
                    fcntl.flock(traffic, fcntl.LOCK_SH)
                    capability = Capability(name, (*fds, traffic.fileno()))
            except (OSError, ValueError):
                pass  # Missing authority or an expired/forged caller fails closed.
            yield capability
        # Do not unlink: an inherited/nested description can still be live. The
        # scope is now expired if nobody holds it; lifecycle preparation prunes it.

    def begin_idle_stop(self) -> bool:
        """Fence this listener before warm exit, atomically with fresh admission.

        A controller reopen cannot admit work into an already retiring service.
        A replacement listener will use a new Capabilities instance after the
        old guardian releases singleton authority.
        """
        with self.admission._mutex():
            if self._stopping:
                return True
            state = self.admission.state()
            if state['mode'] == 'preparing' or (state['mode'] == 'open' and state.get('consumers') is True):
                return False
            if not self.idle():
                return False
            self._stopping = True
            return True

    def idle(self) -> bool:
        try:
            with self.accepted.open('r') as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
        except OSError:
            return False
