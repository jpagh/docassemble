"""Content-backed checkpoints via the existing trusted s4cmd/az backup CLIs.

List/read/write and readback are mandatory. No provider versioning, object-lock,
credential service, or live-service qualification is assumed. The caller holds
its installation drain/runtime locks throughout capture and publication.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

from .managed_backup import ManagedBackupError, ObjectCopy, _sha


class CloudCheckpoint:
    def __init__(self, inputs: dict):
        self.provider = 's3' if inputs.get('S3ENABLE') == 'true' else 'azure'
        self.container = inputs.get('S3BUCKET' if self.provider == 's3' else 'AZURECONTAINER', '')
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]*', self.container):
            raise ManagedBackupError('cloud checkpoint destination unavailable')
        # Same resolved environment and authentication as backup.sh, not ambient
        # application PATH/credentials. CLI output is never included in errors.
        self.env = dict(HOME=os.environ.get('HOME', '/root'), LANG='C.UTF-8')
        self.env.update(inputs)
        self.env.setdefault('PATH', '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin')
        if self.provider == 's3':
            options = []
            for key, option in (('S3ENDPOINTURL', '--endpoint-url'),
                                ('S3_SSE_ALGORITHM', '--API-ServerSideEncryption'),
                                ('S3_SSE_CUSTOMER_ALGORITHM', '--API-SSECustomerAlgorithm'),
                                ('S3_SSE_CUSTOMER_KEY', '--API-SSECustomerKey'),
                                ('S3_SSE_KMS_KEY_ID', '--API-SSEKMSKeyId')):
                if inputs.get(key) not in (None, '', 'null'):
                    options.append(option + '=' + inputs[key])
            # Construct the same endpoint/encryption flags from resolved values.
            # Unknown operator options may affect security or transfer semantics:
            # fail explicitly rather than silently dropping or replaying them.
            try:
                supplied = shlex.split(inputs.get('S4CMD_OPTS', ''))
            except ValueError:
                raise ManagedBackupError('cloud checkpoint CLI options unverified') from None
            if supplied and sorted(supplied) != sorted(options):
                raise ManagedBackupError('cloud checkpoint CLI options unverified')
            # s4cmd reads this with shlex.split; customer keys never enter argv.
            self.env['S4CMD_OPTS'] = shlex.join(options)

    def _run(self, arguments: list[str]) -> str:
        try:
            result = subprocess.run(arguments, env=self.env, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, text=True, timeout=600)
            if result.returncode:
                raise ManagedBackupError('cloud checkpoint CLI failed (output redacted)')
            return result.stdout
        except (OSError, subprocess.SubprocessError, UnicodeError):
            raise ManagedBackupError('cloud checkpoint CLI unavailable (output redacted)') from None

    def _s3(self, *arguments: str) -> list[str]:
        return ['s4cmd', *arguments]

    def _azure(self, verb: str) -> list[str]:
        return ['az', 'storage', 'blob', verb, '--only-show-errors', '--container-name', self.container]

    @staticmethod
    def _key(key: str) -> str:
        # s4cmd expands wildcards and its text listing cannot certify control
        # characters/trailing whitespace. Reject rather than omit such objects.
        if (not isinstance(key, str) or not key or key != key.strip() or any(ord(c) < 32 for c in key) or
                any(c in key for c in '*?[')):
            raise ManagedBackupError('cloud object name cannot be verified')
        return key

    def listing(self, prefix: str = '') -> dict:
        rows = {}
        try:
            if self.provider == 's3':
                uri = 's3://' + self.container + '/'
                output = self._run(self._s3('-r', 'ls', uri + prefix))
                for line in output.splitlines():
                    match = re.fullmatch(r'\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s+(\d+)\s+(s3://.*)', line)
                    if not match or not match[3].startswith(uri):
                        raise ValueError
                    key = self._key(match[3][len(uri):])
                    if key in rows or not key.startswith(prefix):
                        raise ValueError
                    rows[key] = {'size': int(match[2]), 'modified': match[1]}
            else:
                output = json.loads(self._run(self._azure('list') + ['--prefix', prefix, '--num-results', '*', '--output', 'json']))
                if not isinstance(output, list):
                    raise ValueError
                for row in output:
                    key = self._key(row['name'])
                    props = row['properties']
                    if key in rows or not key.startswith(prefix) or not props['etag'] or not isinstance(props['contentLength'], int):
                        raise ValueError
                    rows[key] = {'size': props['contentLength'], 'etag': props['etag']}
        except (ValueError, KeyError, TypeError):
            raise ManagedBackupError('cloud checkpoint listing cannot be verified') from None
        return rows

    def get(self, key: str, destination: Path) -> None:
        key = self._key(key)
        if destination.exists():
            raise ManagedBackupError('cloud retrieval destination exists')
        if self.provider == 's3':
            self._run(self._s3('get', 's3://' + self.container + '/' + key, str(destination)))
        else:
            self._run(self._azure('download') + ['--name', key, '--file', str(destination), '--no-progress', '--output', 'none'])
        if not destination.is_file() or destination.is_symlink():
            raise ManagedBackupError('cloud object retrieval incomplete')

    def put(self, key: str, source: Path) -> None:
        key = self._key(key)
        if self.provider == 's3':
            self._run(self._s3('put', str(source), 's3://' + self.container + '/' + key))
        else:
            self._run(self._azure('upload') + ['--name', key, '--file', str(source), '--overwrite', 'false', '--no-progress', '--output', 'none'])

    def restore(self, directory: Path, captured: dict) -> None:
        """Explicit gated operator workflow only; preserve backup/checkpoint keys."""
        if captured['provider'] != self.provider or captured['container'] != self.container:
            raise ManagedBackupError('cloud restore target incompatible')
        # Delete also the post-checkpoint objects; an overlay is not a rewind.
        for key in self.live_listing():
            if self.provider == 's3':
                self._run(self._s3('del', 's3://' + self.container + '/' + self._key(key)))
            else:
                self._run(self._azure('delete') + ['--name', self._key(key), '--output', 'none'])
        for key, row in captured['files'].items():
            self.put(key, directory / 'objects' / row['file'])
        # Providers assign new etags/timestamps on restore, so compare content
        # and exact live coverage, not historical version labels.
        if set(self.live_listing()) != set(captured['files']):
            raise ManagedBackupError('cloud restore coverage incomplete; remain gated')
        with tempfile.TemporaryDirectory() as scratch:
            for key, row in captured['files'].items():
                target = Path(scratch) / 'object'
                self.get(key, target)
                if _sha(target) != row['sha256']:
                    raise ManagedBackupError('cloud restored content incomplete; remain gated')
                target.unlink()

    def live_listing(self) -> dict:
        # Capture a superset of application object namespaces, including custom
        # SavedFile sections. Never recurse into historical backup/checkpoint data.
        return {key: row for key, row in self.listing().items() if not key.startswith(('backup/', 'checkpoints/'))}

    def capture(self, copied: ObjectCopy) -> dict:
        listing = self.live_listing()
        files = {}
        with tempfile.TemporaryDirectory() as scratch:
            for key, evidence in listing.items():
                file = Path(scratch) / 'object'
                self.get(key, file)
                if file.stat().st_size != evidence['size']:
                    raise ManagedBackupError('cloud object size changed during checkpoint')
                relative = 'cloud/' + hashlib.sha256(key.encode()).hexdigest()
                copied.file(file, relative)
                copied.files[relative]['source'] = self.provider + '://' + self.container + '/' + key
                copied.directories.pop(str(file.parent))
                files[key] = dict(evidence, file=relative, sha256=_sha(file))
                file.unlink()
        return {'provider': self.provider, 'container': self.container,
                'excluded_prefixes': ['backup/', 'checkpoints/'], 'listing': listing, 'files': files}

    def verify_live(self, captured: dict) -> None:
        if self.live_listing() != captured['listing']:
            raise ManagedBackupError('cloud object coverage changed during checkpoint')
        # S3 listings only have minute-resolution time and size. Independently
        # read every object again, also detecting same-size in-place edits.
        with tempfile.TemporaryDirectory() as scratch:
            for key, row in captured['files'].items():
                file = Path(scratch) / 'object'
                self.get(key, file)
                if _sha(file) != row['sha256']:
                    raise ManagedBackupError('cloud object content changed during checkpoint')
                file.unlink()
        if self.live_listing() != captured['listing']:
            raise ManagedBackupError('cloud object coverage changed during checkpoint')

    def publish(self, directory: Path, prefix: str, table_prefix: str) -> dict:
        """No overwrite/delete: upload unique payload, read it all, marker last."""
        from .checkpoints import verify_checkpoint
        if not re.fullmatch(r'checkpoints/[0-9a-f-]{36}-[0-9a-f-]{36}/', prefix) or self.listing(prefix):
            raise ManagedBackupError('cloud checkpoint identity already exists or invalid')
        document = verify_checkpoint(directory, table_prefix, manifest_name='checkpoint.pending')
        self._verify_identity(document, prefix)
        files = {p.relative_to(directory).as_posix(): _sha(p) for p in directory.rglob('*') if p.is_file()}
        files.pop('checkpoint.pending')
        files.pop('checkpoint.json', None)  # Even a completed local copy publishes its marker last.
        for name in files:
            self.put(prefix + name, directory / name)
        if set(self.listing(prefix)) != {prefix + name for name in files}:
            raise ManagedBackupError('cloud checkpoint upload coverage incomplete')
        with tempfile.TemporaryDirectory() as scratch:
            restored = Path(scratch)
            for name, sha in files.items():
                target = restored / name
                target.parent.mkdir(parents=True, exist_ok=True)
                self.get(prefix + name, target)
                if _sha(target) != sha:
                    raise ManagedBackupError('cloud checkpoint upload integrity failed')
            # Verify managed DB binding and cloud/local file coverage using only
            # retrieved bytes, not the local staging directory or live bucket.
            (restored / 'checkpoint.pending').write_bytes((directory / 'checkpoint.pending').read_bytes())
            verify_checkpoint(restored, table_prefix, manifest_name='checkpoint.pending')
            self.put(prefix + 'checkpoint.json', directory / 'checkpoint.pending')
            self.get(prefix + 'checkpoint.json', restored / 'checkpoint.json')
            if (restored / 'checkpoint.json').read_bytes() != (directory / 'checkpoint.pending').read_bytes():
                raise ManagedBackupError('cloud checkpoint manifest upload incomplete')
        return {'provider': self.provider, 'container': self.container, 'prefix': prefix}

    def retrieve(self, prefix: str, destination: Path, table_prefix: str) -> dict:
        """Retrieve and verify captured bytes; does not restore DB/files or authority."""
        from .checkpoints import verify_checkpoint
        if not re.fullmatch(r'checkpoints/[0-9a-f-]{36}-[0-9a-f-]{36}/', prefix):
            raise ManagedBackupError('invalid cloud checkpoint identity')
        if any(path.is_symlink() for path in (destination, *destination.parents)):
            raise ManagedBackupError('cloud retrieval destination symlink refused')
        destination.mkdir(mode=0o700, parents=True, exist_ok=False)
        listing = self.listing(prefix)
        for key in listing:
            relative = key[len(prefix):]
            if not relative or any(part in ('', '.', '..') for part in relative.split('/')):
                raise ManagedBackupError('unsafe cloud checkpoint path')
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            self.get(key, target)
        document = verify_checkpoint(destination, table_prefix)
        self._verify_identity(document, prefix)
        return document

    def _verify_identity(self, document: dict, prefix: str) -> None:
        if (document.get('cloud', {}).get('provider') != self.provider or document['cloud']['container'] != self.container or
                prefix != 'checkpoints/' + document['operation']['attempt_id'] + '-' + document['id'] + '/'):
            raise ManagedBackupError('cloud checkpoint destination binding incomplete')


def verify_cloud_files(document: dict) -> None:
    """Bind every enumerated live key to exactly one retained content object."""
    cloud = document.get('cloud')
    names = {name for name in document['files'] if name.startswith('cloud/')}
    if cloud is None:
        if names:
            raise ManagedBackupError('cloud checkpoint manifest missing')
        return
    if cloud.get('provider') not in ('s3', 'azure') or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]*', cloud.get('container', '')):
        raise ManagedBackupError('cloud checkpoint destination binding incomplete')
    listing, files = cloud['listing'], cloud['files']
    expected = {'cloud/' + hashlib.sha256(key.encode()).hexdigest() for key in listing}
    if (set(files) != set(listing) or names != expected or cloud['excluded_prefixes'] != ['backup/', 'checkpoints/']):
        raise ManagedBackupError('cloud checkpoint manifest coverage incomplete')
    for key, evidence in listing.items():
        row = files[key]
        name = 'cloud/' + hashlib.sha256(key.encode()).hexdigest()
        if (row['file'] != name or row['size'] != evidence['size'] or
                row['sha256'] != document['files'][name]['sha256']):
            raise ManagedBackupError('cloud checkpoint manifest binding incomplete')
