"""Finite, fixed managed runtime phases; no runtime imports before admission.

The legacy combined schedules are deliberately not executed: certbot/control
hooks are unqualified. Backups keep their independent trusted-caller interface.
"""
from __future__ import annotations

import os
from pathlib import Path

from .consumers import run_consumer
from .paths import ExecutorPaths

KINDS = ('cron', 'hourly', 'daily', 'weekly', 'monthly', 'mail')
CRON_TYPES = ('cron_hourly', 'cron_daily', 'cron_weekly', 'cron_monthly')


def run_helper(paths: ExecutorPaths, kind: str, *, cron_type: str = 'cron_daily') -> int:
    if kind not in KINDS or cron_type not in CRON_TYPES:
        raise ValueError('unsupported managed helper')
    previous = dict(os.environ)
    try:
        os.environ.update(HOME='/var/www', DA_CONFIG_FILE=str(paths.config_file),
                          PYTHONDONTWRITEBYTECODE='1')
        if kind == 'mail':
            os.environ.pop('IN_CRON', None)
        else:
            os.environ['IN_CRON'] = 'true'
        os.environ['PATH'] = str(paths.runtime_venv / 'bin') + ':' + os.environ.get('PATH', '')
        # No --wait: a closed runtime tick is skipped, never queued for reopening.
        return run_consumer(paths, [str(paths.runtime_venv / 'bin/python'),
                                   str(Path(__file__).with_name('helper_runtime.py')),
                                   '--kind', kind, '--cron-type', cron_type],
                            log_file=paths.log_dir / 'managed-mail.log' if kind == 'mail' else None)
    finally:
        # Do not leak runtime PATH/HOME/scheduler context into independent phases.
        os.environ.clear()
        os.environ.update(previous)


def run_schedule(paths: ExecutorPaths, kind: str) -> int:
    if kind not in ('hourly', 'daily', 'weekly', 'monthly'):
        raise ValueError('unsupported managed schedule')
    result = run_helper(paths, kind)
    # Backup stays independent even when this runtime tick was refused. Neither
    # phase waits for admission, and missed interview ticks are never replayed.
    if kind in ('hourly', 'daily'):
        from .scheduled_backup import run_backup
        print('managed schedule: backup is separate; certbot/control hooks remain refused (04b3)', flush=True)
        backup = run_backup(paths, Path('/etc/docassemble/managed-backup.json'),
                            phase='hourly' if kind == 'hourly' else 'full')
        return result or backup
    return result
