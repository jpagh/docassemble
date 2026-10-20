"""Independent backup phase using the existing trusted resolved-environment seam.

Never obtain shell exports by importing the mutable runtime as root. This proof
requires an operator-provisioned, root-owned JSON mapping of backup.sh's inputs.
It is not a config-reader replacement or new checkpoint product.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys

from .paths import ExecutorPaths
from .authority import guarded_popen
from .infrastructure import backup_admission


def run_backup(paths: ExecutorPaths, environment_file: Path, *, phase: str = 'full') -> int:
    try:
        if phase not in ('full', 'hourly'):
            raise ValueError('unknown independent backup phase')
        with environment_file.open() as handle:
            metadata = os.fstat(handle.fileno())
            if metadata.st_uid != 0 or metadata.st_mode & 0o022 or not stat.S_ISREG(metadata.st_mode):
                raise ValueError('backup environment must be a root-owned regular file without untrusted writes')
            inputs = json.load(handle)
        if not isinstance(inputs, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in inputs.items()):
            raise ValueError('backup environment must map names to strings')
        environment = dict(HOME=os.environ.get('HOME', '/root'),
                           LANG=os.environ.get('LANG', 'C.UTF-8'))
        if 'TZ' in os.environ:
            environment['TZ'] = os.environ['TZ']
        environment.update(inputs)
        # Preserve explicitly provisioned trusted tools, never ambient runtime
        # PATH. Operators must provision immutable OS/backup-tool paths here.
        environment.setdefault('PATH', '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin')
        environment.update(DA_ROOT=str(paths.da_root), DA_CONFIG_FILE=str(paths.config_file))
        # Image/operator-provisioned managed paths, not application imports. A
        # trusted backup mapping may override store/DB settings explicitly.
        if os.environ.get('DA_UV_MANAGED') == 'true':
            environment['DA_UV_MANAGED'] = 'true'
            environment.update(DA_MANAGED_STATE_DIR=str(paths.state_dir),
                               DA_BUNDLE_DIR=str(paths.bundles_dir))
            for key in ('DA_DURABLE_WORK_STORE', 'DA_QUALIFIED_IMAGE_BASELINE',
                        'DA_MANAGED_ARTIFACT_ROOT', 'DA_EXECUTOR_ARTIFACT_ROOT',
                        'DA_MANAGED_SECRET_ROOT', 'DA_INDEX_CREDENTIAL_ROOT',
                        'DA_PREPARATION_DATABASE_URL', 'DA_EXECUTOR_ENTRYPOINT'):
                if key in os.environ:
                    environment.setdefault(key, os.environ[key])
        # Independent of *runtime* admission, but shutdown must await every
        # backup descendant and atomically fence new infrastructure users.
        script = 'backup.sh' if phase == 'full' else 'backup-hourly.sh'
        with backup_admission(paths).lease(consumer=True) as fd:
            if fd is None:
                return 75
            paths.log_dir.mkdir(parents=True, exist_ok=True)
            with (paths.log_dir / 'managed-backup.log').open('ab') as output:
                process = guarded_popen(['/bin/bash', str(paths.da_root / 'webapp' / script)], fd,
                                        env=environment, graceful=True, stdout=output, stderr=subprocess.STDOUT)
                return process.wait()
    except (OSError, ValueError) as error:
        print('managed backup refused: ' + str(error), file=sys.stderr)
        return 75
