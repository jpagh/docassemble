"""Normal qualification for an explicit published image bridge; no recovery/boot."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .admission import Admission
from .alembic_probe import check_schema, SchemaCheck
from .controller import (_create_tables, _start_web, _write_status, _remove_ready,
                         _mark_ready, load_context)
from .health import probe_runtime
from .inventory import verify_environment
from .journal import Journal, Operation
from .paths import ExecutorPaths
from .roles import prepare_roles, check_roles, RUNTIME_ROLES, INFRASTRUCTURE_ROLES
from .state import DesiredState, _atomic_write_json
from .status import MODE_NORMAL, MODE_MAINTENANCE, read_status_file


class NormalQualificationError(RuntimeError):
    pass


def schema_identity(claim, context) -> str:
    """Bind one attempt's schema/setup work to its exact revision and target."""
    material = {'attempt_id': claim.attempt_id, 'revision': claim.revision_id,
                'target': claim.snapshot.get('target'),
                'selected_baseline': claim.snapshot.get('selected_baseline'),
                'compatible_schema_revisions': sorted(context.manifest.get('compatible_schema_revisions', ()))}
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def prepare_published(paths: ExecutorPaths, claim) -> dict:
    """Schema, isolated core probe, web and ALL roles behind closed admission."""
    context = load_context(paths, start_infrastructure=False)
    selected = claim.snapshot.get('selected_baseline')
    baseline_id = (selected['core_bundle']['digest'] if selected else
                   context.manifest.get('identity', {}).get('core_baseline', 'r1')
                   if claim.snapshot.get('schema_mode') == 'unchanged' or claim.snapshot.get('migration') else context.core_baseline)
    from .selected_baseline import release_versions
    expected = release_versions(selected) if selected else context.expected_core_versions
    compatible = () if selected else tuple(context.manifest.get('compatible_schema_revisions', ()))
    context.desired = DesiredState(revision=claim.revision_id, core_baseline=baseline_id,
                                  packages=tuple(sorted(claim.snapshot['publication']['result']['bundle']['inventory'])))
    _atomic_write_json(paths.intent_file, context.desired.to_dict())
    if claim.snapshot.get('schema_mode') == 'unchanged':
        # Rollback and subsequent same-image intent changes never run old setup.
        from .rollback_schema import check_rollback_schema
        directory = Path(os.environ['DA_DURABLE_WORK_STORE']) / claim.snapshot['publication']['result']['bundle']['digest']
        schema = check_rollback_schema(paths.runtime_venv, context.db, directory)
        _atomic_write_json(paths.state_dir / 'rollback-schema-check.json',
                           {'revision_id': claim.revision_id, 'check': schema.to_dict()})
        if not schema.usable:
            raise NormalQualificationError('schema')
    else:
        # Durable schema start precedes any schema-mutating effect; success is a
        # separate marker written before DB reporting becomes reachable.
        journal = Journal.load(paths.journal_file)
        journal.begin_schema(Operation(id=claim.attempt_id, kind='published-apply', revision=claim.revision_id,
                                       binding=claim.identity), schema_identity(claim, context))
        journal.save(paths.journal_file)
        restored_schema = getattr(claim, 'restore_schema', None)
        if restored_schema is not None and (restored_schema.get('revision') != claim.revision_id or
                restored_schema.get('result') != 'succeeded' or not restored_schema.get('completed_at')):
            raise NormalQualificationError('schema')
        # Successful setup belongs to the restored database, not a venv. A new
        # incarnation rechecks compatibility but never reruns successful setup.
        checkpoint_schema = getattr(claim, 'checkpoint_database_revision', None)
        if restored_schema is None and checkpoint_schema is None and not _create_tables(context).ok:
            raise NormalQualificationError('schema')
        schema = check_schema(paths.runtime_venv, context.db, compatible)
        if (not schema.usable or not schema.database_revision or
                checkpoint_schema is not None and schema.database_revision != checkpoint_schema):
            raise NormalQualificationError('schema')
        journal = Journal.load(paths.journal_file)
        journal.record_schema_success()
        from docassemble.base.managed_migration import release_version
        from packaging.version import Version
        if 'docassemble-webapp' in claim.snapshot.get('core', {}):
            evidence = {'release': release_version(claim.snapshot['core']), 'source': 'successful-setup',
                'revision_id': claim.revision_id, 'attempt_id': claim.attempt_id,
                'schema_identity': schema_identity(claim, context), 'database_revision': schema.database_revision}
            prior = journal.schema_release
            if prior is None or Version(evidence['release']) >= Version(prior['release']):
                journal.schema_release = evidence
        journal.save(paths.journal_file)
    for stage, operation in (
            ('probe', lambda: probe_runtime(paths.runtime_venv, paths.config_file, expected).ok),
            ('web', lambda: _start_web(context, recovery=False))):
        if not operation():
            raise NormalQualificationError(stage)
    roles = prepare_roles(paths)
    if not all(check.ok for check in roles) or {check.name for check in roles} != set(RUNTIME_ROLES + INFRASTRUCTURE_ROLES):
        raise NormalQualificationError('roles')
    return {'schema_verified': True, 'roles': sorted(check.name for check in roles)}


def record_published(paths: ExecutorPaths, claim, receipt: dict) -> None:
    context = load_context(paths, start_infrastructure=False)
    from .selected_baseline import promote_recovery
    # Called only after selected schema, probe, web and all-role health succeed.
    promote_recovery(paths, claim.snapshot)
    selected = claim.snapshot.get('selected_baseline')
    baseline_id = (selected['core_bundle']['digest'] if selected else
                   context.manifest.get('identity', {}).get('core_baseline', 'r1')
                   if claim.snapshot.get('schema_mode') == 'unchanged' or claim.snapshot.get('migration') else context.core_baseline)
    context.applied.mark(claim.revision_id, baseline_id)
    context.applied.save(paths.applied_file)
    context.journal.record_verified(claim.revision_id)
    if context.journal.schema_release and context.journal.schema_release['attempt_id'] == claim.attempt_id:
        receipt['schema_release'] = context.journal.schema_release
    context.journal.attempt_result = receipt
    context.journal.save(paths.journal_file)


def recheck_published(paths: ExecutorPaths, receipt: dict, material: dict) -> bool:
    """Replay verifies current material/incarnations; never repeats sync or schema."""
    context = load_context(paths, start_infrastructure=False)
    journal = Journal.load(paths.journal_file)
    revision = receipt['identity']['revision_id']
    if (journal.phase != 'verified' or journal.verified_revision != revision or
            context.applied.revision != revision or
            Admission(paths.state_dir).state()['mode'] not in ('preparing', 'open') or
            not verify_environment(paths.runtime_venv, receipt['inventory'], material).ok):
        return False
    from .capabilities import BUSY_ROLES, role_busy
    retained = read_status_file(paths.status_file)
    busy = {role for role in BUSY_ROLES if role_busy(paths.state_dir, role)}
    mode = Admission(paths.state_dir).state()['mode']
    if not Admission(paths.state_dir).prepare(expected_mode=mode):
        return False
    checks = check_roles(paths, retained=retained, retained_busy=busy)
    return (len(checks) == len(RUNTIME_ROLES + INFRASTRUCTURE_ROLES) and all(check.ok for check in checks) and
            {check.name for check in checks} == set(RUNTIME_ROLES + INFRASTRUCTURE_ROLES))


def open_published(paths: ExecutorPaths) -> None:
    context = load_context(paths, start_infrastructure=False)
    checks = prepare_roles(paths, retained=read_status_file(paths.status_file))
    if not all(check.ok for check in checks):
        raise NormalQualificationError('roles')
    schema = None
    from .selected_baseline import load_baselines
    schema_path = paths.state_dir / 'rollback-schema-check.json'
    if load_baselines(paths).get('schema_mode') == 'unchanged' and schema_path.exists():
        recorded = json.loads(schema_path.read_text())
        if recorded['revision_id'] == context.applied.revision:
            observed = recorded['check']
            schema = SchemaCheck(observed['status'], observed['detail'], observed['database_revision'])
    _write_status(context, MODE_NORMAL, role_checks=checks, schema=schema)
    _mark_ready(paths)
    Admission(paths.state_dir).open(consumers=True)


def close_published(paths: ExecutorPaths) -> None:
    Admission(paths.state_dir).close('published qualification failed; recovery/retry fenced')
    _remove_ready(paths)
    context = load_context(paths, start_infrastructure=False)
    _write_status(context, MODE_MAINTENANCE, detail='published qualification failed; recovery/retry fenced')
