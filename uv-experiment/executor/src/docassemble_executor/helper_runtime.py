"""Runtime side of fixed finite helpers, entered only with accepted authority.

No combined legacy schedule is sourced. There are no certbot/control/installer
hooks here. Runtime and all descendants run as www-data, including inbound mail.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


def cleanup_tmp() -> int:
    """Legacy hourly cache cleanup, now in the admitted www-data tree."""
    status = 0
    cutoff = time.time() - 3600
    for path in [*Path('/tmp').glob('SavedFile*'), *Path('/tmp').glob('datemp*')]:
        try:
            if path.is_symlink() or path.stat().st_mtime >= cutoff:
                continue
            if path.name.startswith('SavedFile') and path.is_dir():
                shutil.rmtree(path)
            elif path.name.startswith('datemp') and path.is_file():
                path.unlink()
        except FileNotFoundError:
            pass  # Concurrent cleanup/disappearance is harmless.
        except OSError as error:
            print('managed hourly cleanup: ' + str(error), file=sys.stderr)
            status = 1
    directory = Path('/tmp/files')
    if directory.is_dir():
        for root, dirs, files in os.walk(directory, topdown=False, followlinks=False):
            for name in files + [name for name in dirs if (Path(root) / name).is_symlink()]:
                path = Path(root) / name
                try:
                    if path.lstat().st_atime < cutoff - 3600:
                        path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as error:
                    print('managed hourly cleanup: ' + str(error), file=sys.stderr)
                    status = 1
            try:
                Path(root).rmdir()
            except OSError:
                pass  # Retain nonempty directories (as find -empty does).
    return status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--kind', required=True)
    parser.add_argument('--cron-type', default='cron_daily')
    args = parser.parse_args()
    config = os.environ['DA_CONFIG_FILE']
    if args.kind == 'mail':
        # The pipe's exit status must be the actual handler's, not rm's. A
        # disconnected SMTP client does not cancel a delivery accepted by Exim.
        with tempfile.NamedTemporaryFile() as message:
            while data := sys.stdin.buffer.read(65536):
                message.write(data)
            message.flush()
            result = subprocess.call([sys.executable, str(Path(__file__).with_name('mail_runtime.py')),
                                      message.name, config], cwd='/tmp')
            return 0 if result == 0 else 75  # Runtime/queue errors retain mail for retry, never masquerade as success.
    kind = args.cron_type if args.kind == 'cron' else 'cron_' + args.kind
    status = cleanup_tmp() if args.kind == 'hourly' else 0
    cron = subprocess.call([sys.executable, '-c',
                            'from docassemble.webapp.cron import main; main()',
                            config, '-type', kind], cwd='/tmp')
    status = status or cron
    if args.kind == 'hourly':
        cleanup = subprocess.call([sys.executable, '-c',
                                   'import docassemble.webapp.cleanup_sessions', config], cwd='/tmp')
        status = status or cleanup
    return status


if __name__ == '__main__':
    raise SystemExit(main())
