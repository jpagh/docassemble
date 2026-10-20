#!/usr/bin/env python3
"""External provider CLI fake; filesystem content transport, never a live service.

Only the command forms used by cloud_checkpoints are implemented. Failure modes
are deliberate adversarial provider-boundary injections, not provider emulation.
"""
import json
import os
from pathlib import Path
import shutil
import sys


def main():
    args = sys.argv[1:]
    root = Path(os.environ['CLOUD_FAKE_ROOT'])
    root.mkdir(parents=True, exist_ok=True)
    mode = os.environ.get('CLOUD_FAKE_FAILURE', '')
    if mode == 'auth':
        print('authentication failed: ' + os.environ.get('AWS_SECRET_ACCESS_KEY', os.environ.get('AZURE_STORAGE_KEY', '')), file=sys.stderr)
        print('credential-bearing provider diagnostic', file=sys.stdout)
        return 41
    s3 = Path(sys.argv[0]).name == 's4cmd'
    if s3:
        delete_removed = '--delete-removed' in args
        args = [a for a in args if not a.startswith(('--endpoint-url=', '--API-')) and a not in ('-r', '-f', '--delete-removed')]
        verb = args[0]
        uri = args[-1] if verb in ('put', 'dsync') else args[1]
        bucket, _, key = uri.removeprefix('s3://').partition('/')
        base = root / bucket
    else:
        verb = args[2]
        def value(option):
            return args[args.index(option) + 1]
        base = root / value('--container-name')
        key = value('--prefix') if verb == 'list' else value('--name' if '--name' in args else '-n')
    base.mkdir(parents=True, exist_ok=True)
    if verb == 'dsync':
        source = Path(args[1])
        target = base / key
        if delete_removed and target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target, dirs_exist_ok=True)
        return 0
    if verb in ('delete', 'del'):
        (base / key).unlink(missing_ok=True)
        return 0
    if verb in ('ls', 'list'):
        rows = []
        for path in sorted(base.rglob('*')):
            name = path.relative_to(base).as_posix()
            if path.is_file() and name.startswith(key):
                if mode == 'partial-upload' and name.endswith('database'):
                    continue
                rows.append((name, path.stat().st_size))
        if s3:
            for name, size in rows:
                print('2026-10-16 12:00 %d s3://%s/%s' % (size, bucket, name))
        else:
            print(json.dumps([{'name': name, 'properties': {'contentLength': size, 'etag': 'fake-etag'}} for name, size in rows]))
        return 0
    if verb in ('get', 'download'):
        source = base / key
        if mode == 'missing-object' and key.startswith('files/'):
            return 44
        if not source.is_file():
            return 44
        target = Path(args[2] if s3 else value('--file'))
        if target.exists():
            return 45
        shutil.copyfile(source, target)
        if mode == 'changed-live' and key.startswith('files/'):
            source.write_bytes(b'X' * source.stat().st_size)
        return 0
    if verb in ('put', 'upload'):
        source = Path(args[1] if s3 else value('--file' if '--file' in args else '-f'))
        target = base / key
        if target.exists() and (s3 or '--overwrite' not in args or value('--overwrite') != 'true'):
            return 46
        target.parent.mkdir(parents=True, exist_ok=True)
        if mode == 'partial-upload' and key.endswith('database'):
            return 0
        shutil.copyfile(source, target)
        if mode == 'interrupted-upload':
            target.write_bytes(target.read_bytes()[:3])
            return 47
        if mode == 'corrupt-upload':
            target.write_bytes(b'corrupted')
        return 0
    raise AssertionError('unsupported fake CLI command')


if __name__ == '__main__':
    sys.exit(main())
