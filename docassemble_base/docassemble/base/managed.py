"""Fail-closed legacy-operation policy, usable before application imports.

This is a trusted-runtime lifecycle fence, not a sandbox for arbitrary operator
Python/OS commands. No flag, caller token or queued task grants legacy mutation.
Only installations without managed evidence and with the flag off remain legacy.
"""
import os
from pathlib import Path


PACKAGE_OPERATIONS_UNSUPPORTED = (
    'Managed package changes are not implemented; no intent was changed. '
    'Applying changes requires maintenance and restart (restart=false is unsupported).'
)


class ManagedOperationError(RuntimeError):
    """The legacy operation cannot run against a managed installation."""


def managed_installation():
    if os.environ.get('DA_UV_MANAGED') == 'true':
        return True
    directory = Path(os.environ.get('DA_MANAGED_STATE_DIR', '/var/lib/docassemble/managed'))
    for name in ('desired.json', 'applied.json', 'lifecycle.json', 'initialized.json', 'admission.json'):
        try:
            (directory / name).lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise ManagedOperationError('cannot inspect managed evidence; legacy operation refused') from error
        return True  # Corrupt records and dangling symlinks still prohibit legacy writes.
    return False


def reject_legacy_operation(operation):
    if managed_installation():
        raise ManagedOperationError(operation + ' is unsupported in managed mode; use the independent executor')
