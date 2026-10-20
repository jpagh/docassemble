"""Independent infrastructure lifecycle and whole-descendant backup exclusion."""
from __future__ import annotations

import os
import time

from .admission import Admission
from .authority import MutationLock
from .paths import ExecutorPaths
from .state import _atomic_write_json
from .startup import managed_requested


def backup_admission(paths: ExecutorPaths) -> Admission:
    """First standalone backup may initialize, never reopen existing closure."""
    gate = Admission(paths.state_dir / 'infrastructure')
    with gate._mutex():
        if not gate.path.exists() and not gate.path.is_symlink():
            gate.traffic.touch(exist_ok=True)
            _atomic_write_json(gate.path, {'mode': 'open', 'consumers': True, 'detail': 'independent infrastructure'}, mode=0o644)
    return gate


def shutdown(paths: ExecutorPaths, timeout: float = 30) -> int:
    from .controller import (_mark_ready, _start_web, _write_status, enter_maintenance,
                             load_context, log, status)
    from .roles import prepare_roles
    from .services import stop_postgres, stop_redis
    from .status import MODE_MAINTENANCE, MODE_NORMAL, MODE_RECOVERY
    from .supervision import stop_supervisor

    if not managed_requested(os.environ.get('DA_UV_MANAGED') == 'true', paths.state_dir):
        raise ValueError('managed shutdown requires managed mode')
    with MutationLock(paths.state_dir, wait_seconds=5):
        context = load_context(paths, start_infrastructure=False)
        previous = status(paths)
        backups = backup_admission(paths)
        backups.close('container shutdown requested', mode='draining')
        deadline = time.monotonic() + timeout
        if not enter_maintenance(context, timeout):
            backups.open(consumers=True)
            return 3
        if not backups.drain(max(0, deadline - time.monotonic())):
            backups.open(consumers=True)
            if previous.get('mode') == MODE_NORMAL and _start_web(context, recovery=False):
                checks = prepare_roles(paths)
                if all(check.ok for check in checks):
                    _write_status(context, MODE_NORMAL, role_checks=checks, detail='shutdown backup drain aborted; no infrastructure stopped')
                    Admission(paths.state_dir).open(consumers=True)
                    _mark_ready(paths)
                    return 3
            elif previous.get('mode') == MODE_RECOVERY and _start_web(context, recovery=True):
                _write_status(context, MODE_RECOVERY, recovery_available=True, detail='shutdown backup drain aborted')
                Admission(paths.state_dir).open()
                return 3
            _write_status(context, MODE_MAINTENANCE, detail='shutdown aborted; no infrastructure stopped; runtime remains unready')
            return 3
        # No accepted runtime or backup descendant survives. Fixed OS stops do
        # not activate/read the mutable application or run legacy cloud hooks.
        supervisor = stop_supervisor(paths)
        if not supervisor.ok:
            _write_status(context, MODE_MAINTENANCE, probe_checks=[supervisor], detail=supervisor.detail)
            return 1
        checks = [supervisor, stop_postgres(), stop_redis()]
        backups.close('independent infrastructure stopped')
        _write_status(context, MODE_MAINTENANCE, probe_checks=checks, detail='independent infrastructure stopped')
        log('independent infrastructure shutdown completed')
        return 0 if all(check.ok for check in checks) else 1
