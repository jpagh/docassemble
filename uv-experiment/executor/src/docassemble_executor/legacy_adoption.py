"""Independent legacy ingress and explicit preparation-worker storage bridge."""
from __future__ import annotations

import hashlib
import json
import os
import re
from email.parser import BytesParser
from pathlib import Path
import pwd
import subprocess
import sys
import stat
import uuid
import tempfile

from sqlalchemy import create_engine, inspect, select, text

from docassemble.base.managed_adoption import LegacyAdoption, adoption_status
from docassemble.base.managed_artifacts import open_regular, retain_artifact
from docassemble.base.managed_preparation import PreparationError
from docassemble.base.managed_records import preparation_tables

from .config import load_config
from .git_sources import resolve_source


def worker_root(variable):
    """No defaults, symlink normalization or inferred owner trust for executor stores."""
    value = os.environ.get(variable)
    if not value or not Path(value).is_absolute():
        raise PreparationError('explicit restricted executor store required')
    root = Path(value)
    if '..' in root.parts or any(path.is_symlink() for path in (root, *root.parents)):
        raise PreparationError('unsafe executor store')
    account = pwd.getpwnam(os.environ.get('DA_ENVIRONMENT_USER', 'da-environment'))
    info = root.stat()
    if (not root.is_dir() or info.st_uid != account.pw_uid or info.st_mode & 0o777 != 0o700):
        raise PreparationError('executor store must be restricted and preparation-worker owned')
    return root.resolve(strict=True)


def writer(arguments, payload, *, environ=None):
    account = pwd.getpwnam(os.environ.get('DA_ENVIRONMENT_USER', 'da-environment'))
    command = [sys.executable, '-m', __name__, *arguments]
    if os.geteuid() == 0:
        command = ['runuser', '-u', account.pw_name, '--', *command]
    elif os.getuid() != account.pw_uid:
        raise PreparationError('executor writer identity mismatch')
    result = subprocess.run(command, input=payload, capture_output=True, timeout=120, env=environ)
    if result.returncode:
        raise PreparationError('dedicated executor material writer failed')


def retain_upload(root, reference, path):
    if root != worker_root('DA_EXECUTOR_ARTIFACT_ROOT'):
        raise PreparationError('executor store mismatch')
    with open_regular(path) as handle:
        writer(['retain', reference], handle.read())
    return root / reference


def stage_secret(candidate_id, root, reference, payload, *, environ=None):
    if root != worker_root('DA_MANAGED_SECRET_ROOT'):
        raise PreparationError('executor secret store mismatch')
    writer(['secret', candidate_id, reference], payload, environ=environ)


def historical_git_sha(runtime, name, source):
    """Read pip's authoritative installed metadata without importing that runtime.

    Only matching repository/subdirectory/requested-ref evidence can preserve a
    commit. Installed versions alone never supply intent or a source identity.
    """
    from packaging.utils import canonicalize_name
    from docassemble.base.managed_git import parse_git_requirement
    from docassemble.base.managed_adoption import AdoptionBlocked
    commits = set()
    for path in runtime.glob('lib/python*/site-packages/*.dist-info/direct_url.json'):
        try:
            metadata = BytesParser().parsebytes((path.parent / 'METADATA').read_bytes())
            if canonicalize_name(metadata['Name']) != name:
                continue
            document = json.loads(path.read_text())
            vcs = document.get('vcs_info', {})
            if vcs.get('vcs') != 'git' or vcs.get('requested_revision') != source['declared_ref']:
                continue
            url = document['url']
            _, identity, _ = parse_git_requirement(name + ' @ ' + (url if url.startswith('git+') else 'git+' + url))
            if (identity['repository'] != source['repository'] or
                    document.get('subdirectory') != source['subdirectory']):
                continue
            commit = vcs.get('commit_id', '')
            if re.fullmatch('[0-9a-fA-F]{40}', commit):
                commits.add(commit.lower())
        except (OSError, ValueError, KeyError, TypeError, PreparationError):
            continue
    if len(commits) > 1:
        raise AdoptionBlocked('ambiguous installed Git commits; repair required')
    return next(iter(commits)) if commits else None


def resolve_git(name, source, payload):
    from docassemble.base.managed_git import scoped_authorization
    from .paths import ExecutorPaths
    commit = historical_git_sha(ExecutorPaths.from_env().runtime_venv, name, source)
    if commit is not None:
        # Preserve the verifiable historical identity: do not require the live
        # declared ref to still exist. The builder verifies the commit is
        # fetchable when it fetches the recorded SHA.
        kind = 'commit' if re.fullmatch('[0-9a-fA-F]{40}', source['declared_ref'] or '') else 'historical'
        return dict(source, ref_kind=kind, resolved_sha=commit)
    with tempfile.TemporaryDirectory(prefix='legacy-git-') as temporary:
        return resolve_source(source, Path(temporary),
                              authorization=scoped_authorization(source['repository'], payload))


def ensure_records(engine, prefix):
    """Additive management storage only, never application setup/schema stamping.

    Existing application tables and Alembic evidence are prerequisites. Partial
    management storage/corrupt managed records are repair, not fresh adoption.
    """
    tables = preparation_tables(prefix)
    with engine.begin() as connection:
        connection.execute(text('SELECT pg_advisory_xact_lock(160016)'))
        existing = set(inspect(connection).get_table_names())
        if not {prefix + name for name in ('package', 'package_auth', 'install', 'uploads', 'user')} <= existing:
            raise PreparationError('usable legacy database required for adoption')
        found = {relation.name for relation in tables.values()} & existing
        if found and found != {relation.name for relation in tables.values()}:
            raise PreparationError('incomplete managed schema requires operator repair')
        if not found:
            next(iter(tables.values())).metadata.create_all(connection)


def observe_adoption(context):
    """Before boot reconciliation, capture original inputs; leave consumers gated."""
    from .durable_work import configuration
    url, prefix = configuration(context.paths)
    engine = create_engine(url, connect_args={'connect_timeout': 5})
    try:
        ensure_records(engine, prefix)
        service = LegacyAdoption(engine, prefix)
        with engine.connect() as connection:
            state = connection.execute(select(service.tables['environment_state'])).mappings().first()
            if state and state['desired_revision_id']:
                return None
        descriptor = os.environ['DA_QUALIFIED_IMAGE_BASELINE']
        from .image_baseline import load_image_wheels
        baseline, _ = load_image_wheels(Path(descriptor), context.paths)
        configuration_inputs = load_config(context.paths.config_file)
        # Avoid copying unrelated configuration/application secrets into adoption history.
        keys = {'python packages', 'pip index url', 'pip extra index urls', 'pip trusted host',
                'private indexes', 'private bindings', 'private tls exceptions', 'config from', 'uploads', 's3', 'azure', 'allow updates'}
        configuration_inputs = {key: value for key, value in configuration_inputs.items() if key in keys}
        service.observe(baseline, configuration_inputs)
        artifact = worker_root('DA_EXECUTOR_ARTIFACT_ROOT') if os.environ.get('DA_EXECUTOR_ARTIFACT_ROOT') else None
        secret = worker_root('DA_MANAGED_SECRET_ROOT') if os.environ.get('DA_MANAGED_SECRET_ROOT') else None
        service.prepare_observed(resolve_git=resolve_git, artifact_root=artifact, secret_root=secret,
            retain_upload=retain_upload, stage_secret=lambda *args: stage_secret(*args, environ=dict(os.environ,
                DA_PREPARATION_DATABASE_URL=engine.url.render_as_string(hide_password=False), DA_ADOPTION_TABLE_PREFIX=prefix)))
        service.advance()
        with engine.connect() as connection:
            snapshot = connection.scalar(select(service.tables['environment_state'].c.snapshot))
        return adoption_status(snapshot)
    finally:
        engine.dispose()


def serve_adoption_failure(context):
    """One core recovery, then only non-mutating requalification on restart.

    A usable DB/core is required; failure stays maintenance. Preparation failure
    is not permission to repeat recovery or application/schema work every boot.
    """
    from .authority import MutationLock
    from .admission import Admission
    from .journal import Journal
    from .controller import _restore_core, _start_web, _write_status, _web_alive
    from .alembic_probe import check_schema
    from .health import probe_runtime
    from .status import MODE_RECOVERY
    with MutationLock(context.paths.state_dir):
        context.journal = Journal.load(context.paths.journal_file)
        if context.journal.recovery is None:
            _restore_core(context, 'system preparation failed; original committed evidence retained')
        elif context.journal.in_recovery_state() and context.journal.phase == 'recovery_succeeded':
            from .selected_baseline import load_baselines
            retained = load_baselines(context.paths)
            if retained.get('migration'):
                from .rollback_schema import check_rollback_schema
                schema = check_rollback_schema(context.paths.runtime_venv, context.db,
                    context.paths.core_bundle(context.identity.get('core_baseline', 'r1')))
            else:
                schema = check_schema(context.paths.runtime_venv, context.db,
                    tuple(context.manifest.get('compatible_schema_revisions', ())))
            if (schema.usable and probe_runtime(context.paths.runtime_venv, context.paths.config_file,
                    context.expected_core_versions).ok and
                    (_web_alive(context.paths, 'recovery') or _start_web(context, recovery=True))):
                _write_status(context, MODE_RECOVERY, schema=schema, recovery_available=True)
                Admission(context.paths.state_dir).open()


def main():
    # Only the root ingress launches this dedicated non-root writer. Artifact
    # exposure occurs while its parent holds the DB environment lock through
    # material registration, so cleanup cannot race an unregistered object.
    action = sys.argv[1]
    payload = sys.stdin.buffer.read()
    if action == 'retain':
        import io
        root = worker_root('DA_EXECUTOR_ARTIFACT_ROOT')
        retain_artifact(root, sys.argv[2], io.BytesIO(payload))
    elif action == 'secret':
        root = worker_root('DA_MANAGED_SECRET_ROOT')
        # Parent registered the queued reference before launching this writer.
        engine = create_engine(os.environ['DA_PREPARATION_DATABASE_URL'])
        try:
            service = LegacyAdoption(engine, os.environ.get('DA_ADOPTION_TABLE_PREFIX', ''))
            service._stage_queued_secret(sys.argv[2], root, sys.argv[3], payload)
        finally:
            engine.dispose()
    else:
        raise PreparationError('unsupported executor writer operation')


if __name__ == '__main__':
    main()


def _private_root(root: Path, uid: int) -> Path:
    if (not root.is_absolute() or '..' in root.parts or
            any(path.is_symlink() for path in (root, *root.parents))):
        raise PreparationError('unsafe registered ingress root')
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
        raise PreparationError('registered ingress root identity mismatch')
    return root


def ingest_registered_material(preparation, claim) -> None:
    """Copy only claim-selected SQL rows, preserving opaque references and store identity."""
    if os.geteuid() != 0:
        raise PreparationError('registered ingress requires privileged dispatch')
    try:
        uid = int(os.environ['DA_MANAGED_ARTIFACT_UID'])
        if uid != pwd.getpwnam('www-data').pw_uid or uid == 0:
            raise ValueError()
        artifact = _private_root(Path(os.environ['DA_MANAGED_ARTIFACT_ROOT']), uid)
        secret = _private_root(Path(os.environ.get('DA_MANAGED_WEB_SECRET_ROOT') or str(artifact) + '-secrets'), uid)
        worker_artifact = worker_root('DA_EXECUTOR_ARTIFACT_ROOT')
        worker_secret = worker_root('DA_MANAGED_SECRET_ROOT')
        roots = [artifact, secret, worker_artifact, worker_secret]
        if any(a == b or a in b.parents or b in a.parents for i, a in enumerate(roots) for b in roots[i + 1:]):
            raise ValueError()
        account = pwd.getpwnam(os.environ.get('DA_ENVIRONMENT_USER', 'da-environment'))
        if account.pw_uid == 0:
            raise ValueError()
    except (KeyError, ValueError, OSError):
        raise PreparationError('registered ingress configuration unavailable') from None

    def copy(path: str, kind: str) -> str:
        source_root, destination_root = ((artifact, worker_artifact) if kind == 'artifact' else
                                          (secret, worker_secret))
        source = Path(path)
        name = source.name
        valid = bool(re.fullmatch('[0-9a-f]{64}', name)) if kind == 'artifact' else str(uuid.UUID(name)) == name
        if not valid or source != source_root / name or path != str(source):
            raise PreparationError('material outside registered ingress')
        root_fd = os.open(source_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
        finally:
            os.close(root_fd)
        temporary = None
        try:
            with os.fdopen(fd, 'rb') as handle:
                info = os.fstat(handle.fileno())
                limit = 128 * 1024 * 1024 if kind == 'artifact' else 65536
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1 or
                        stat.S_IMODE(info.st_mode) != 0o600 or not 0 < info.st_size <= limit):
                    raise PreparationError('registered ingress file identity mismatch')
                output_fd, temporary = tempfile.mkstemp(prefix='.ingest-', dir=destination_root)
                sha = hashlib.sha256()
                with os.fdopen(output_fd, 'wb') as output:
                    size = 0
                    for block in iter(lambda: handle.read(1024 * 1024), b''):
                        size += len(block)
                        if size > limit:
                            raise PreparationError('registered ingress material exceeds limit')
                        sha.update(block)
                        output.write(block)
                    output.flush()
                    os.fsync(output.fileno())
                    os.fchown(output.fileno(), account.pw_uid, account.pw_gid)
                expected = sha.hexdigest()
                if kind == 'artifact' and expected != name:
                    raise PreparationError('registered ingress digest mismatch')
                if kind == 'secret':
                    from docassemble.base.managed_git import scoped_authorization, source_declarations
                    repositories = {source['repository'] for package, source in
                                    source_declarations(claim.inputs).items()
                                    if claim.inputs.get('git_credentials', {}).get(package) == name}
                    if len(repositories) != 1:
                        raise PreparationError('registered credential scope unavailable')
                    scoped_authorization(repositories.pop(), Path(temporary).read_bytes())
                destination = destination_root / name
                if destination.exists() or destination.is_symlink():
                    existing_fd = os.open(destination, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    with os.fdopen(existing_fd, 'rb') as existing:
                        metadata = os.fstat(existing.fileno())
                        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != account.pw_uid or
                                metadata.st_nlink != 1 or stat.S_IMODE(metadata.st_mode) != 0o600 or
                                metadata.st_size != size or hashlib.file_digest(existing, 'sha256').hexdigest() != expected):
                            raise PreparationError('registered destination material mismatch')
                else:
                    os.replace(temporary, destination)
                directory_fd = os.open(destination_root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                return str(destination)
        except (OSError, ValueError):
            raise PreparationError('registered ingress copy refused') from None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    preparation.ingest_registered_material(claim, artifact, secret, worker_artifact, worker_secret, copy)


def drop_to_preparation_worker() -> None:
    """Irreversible identity drop before hooks, solve, network or uv can run."""
    if os.geteuid() != 0:
        raise PreparationError('preparation privilege drop requires root dispatch')
    account = pwd.getpwnam(os.environ.get('DA_ENVIRONMENT_USER', 'da-environment'))
    if account.pw_uid == 0:
        raise PreparationError('preparation worker must be non-root')
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    # PR_SET_NO_NEW_PRIVS forbids setuid binaries/file capabilities regaining root.
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        raise PreparationError('preparation privilege drop unavailable')
    os.setgroups([])
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
    if os.getresuid() != (account.pw_uid,) * 3 or os.getresgid() != (account.pw_gid,) * 3:
        raise PreparationError('preparation privilege drop incomplete')
    # setuid from root clears effective/permitted capabilities; verify rather than assume.
    status = Path('/proc/self/status').read_text()
    for line in status.splitlines():
        if line.startswith(('CapInh:', 'CapPrm:', 'CapEff:', 'CapAmb:')) and int(line.split()[1], 16):
            raise PreparationError('preparation worker retains capabilities')
    os.environ['HOME'] = account.pw_dir
