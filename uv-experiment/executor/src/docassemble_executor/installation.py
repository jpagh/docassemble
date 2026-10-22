"""DB installation authority: stopped materialization or explicit image-bridge apply.

Only the operator-configured all-role image bridge can qualify normal application.
Retry requires shared terminal eligibility. Qualified failed mutation restores core
once, attempt-bound. Uncertain retry/resumption remain fenced.
"""
from __future__ import annotations

from docassemble.base.managed_python import python_target

import hashlib
import json
import os
import sys
import re
import stat
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select, update

from .admission import Admission
from .authority import MutationLock, MutationLockBusy, RuntimeLock
from .bundles import sha256_file, verify_python_target
from .alembic_probe import read_database_revision
from .config import database_settings, load_config
from .controller import enter_maintenance, load_context
from .inventory import verify_environment
from .journal import Journal, Operation, PHASE_IDLE, PHASE_INSTALLATION_PREPARING, PHASE_RECOVERY_SUCCEEDED, PHASE_VERIFIED
from .paths import ExecutorPaths
from .preparation import Preparation, PreparationError, digest
from .registry_builder import UV_VERSION, verify_retained
from .state import _atomic_write_json
from .uvsync import sync_frozen


from docassemble.base.managed_preparation import INSTALLATION_FAILURE_REASONS as FAILURE_REASONS
from docassemble.base.managed_qualification import installation_retry_eligible


class InstallationBusy(BlockingIOError):
    """Another owner holds mutation authority; this turn did no work.

    Subclassing BlockingIOError keeps the historic contract that a contended poll
    reports a busy lock, so existing callers and tests that catch BlockingIOError
    remain correct, while the durable worker matches only this exact type and
    still fails closed on every other error.
    """


class InstallationFailure(PreparationError):
    """An allowlisted diagnostic, never raw tooling/configuration exception text."""
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def pre_sync_abort(receipt: dict[str, Any]) -> bool:
    return (receipt.get('outcome') == 'aborted-before-sync' and
            receipt.get('mutation_started') is False and receipt.get('child_safe') is False and
            receipt.get('failure') == {'stage': 'drain', 'reason': 'drain-aborted'})


def schema_clearance_file(paths: ExecutorPaths) -> Path:
    """Restricted root/operator-owned clearance; not web-writable."""
    return paths.state_dir / 'schema-clearance.json'


def load_schema_clearance(paths: ExecutorPaths) -> dict[str, Any] | None:
    """Validate an administrator/operator clearance bound to the uncertain job.

    Fail closed on missing/mismatched/foreign/symlinked/oversized input; never
    surface file contents or secrets in diagnostics.
    """
    path = schema_clearance_file(paths)
    try:
        # Operator-written restricted file: no symlinks, owned by the executor's
        # user, not group/other-writable, bounded read.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
                return None
            raw = os.read(fd, 8193)
        finally:
            os.close(fd)
    except OSError:
        return None
    if len(raw) > 8192:
        return None
    try:
        document = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(document, dict) or document.get('format') != 1:
        return None
    fields = {key: document.get(key) for key in ('attempt_id', 'schema_identity', 'observed_schema')}
    if not all(isinstance(value, str) and value and len(value) <= 255 for value in fields.values()):
        return None
    cleared_at = document.get('cleared_at')
    return {**fields, 'cleared_at': cleared_at if isinstance(cleared_at, str) and cleared_at else None,
            'source_digest': hashlib.sha256(raw).hexdigest()}


@dataclass(frozen=True)
class InstallationClaim:
    attempt_id: str
    revision_id: str
    fence: int
    host_id: str
    runtime_generation: int
    snapshot: dict[str, Any]
    restore_schema: dict[str, Any] | None = None
    cold_restore: bool = False
    pre_change: dict[str, Any] | None = None
    checkpoint_database_revision: str | None = None
    automatic: bool = False

    @property
    def checkpoint_requested(self) -> bool:
        return (not self.cold_restore and
                self.snapshot['publication'].get('inputs', {}).get('pre_change_checkpoint') is True)

    @property
    def identity(self) -> dict[str, Any]:
        return {'attempt_id': self.attempt_id, 'revision_id': self.revision_id,
                'fence': self.fence, 'host_id': self.host_id,
                'runtime_generation': self.runtime_generation,
                'bundle_digest': self.snapshot['publication']['result']['bundle']['digest'],
                'target': self.snapshot['target']}


class Installation(Preparation):
    def pending_first_adoption(self) -> bool:
        """Only new system work may follow unbound pre-publication core recovery."""
        with self.engine.begin() as connection:
            environment = self._environment(connection)
            attempts = self.tables['installation_attempt']
            row = connection.execute(select(attempts).where(
                attempts.c.id == environment['active_attempt_id'])).mappings().first()
            if not row or row['record'].get('phase') != 'pending' or row['record'].get('reason') not in ('automatic-adoption', 'automatic-migration'):
                return False
            revision, candidate = self._published_revision(connection, row['revision_id'])
            return bool((candidate['operation'] == 'migration' or (candidate['operation'] == 'adoption' and candidate['parent_id'] is None)) and
                environment['desired_revision_id'] == revision['id'])

    def interrupted_claim(self, journal: Journal) -> InstallationClaim:
        """Recover only the original running claim, never reclaim installation work."""
        operation = journal.operation
        if not operation or operation.kind != 'published-apply' or not operation.binding:
            raise PreparationError('interrupted published binding missing')
        with self.engine.begin() as connection:
            environment = self._environment(connection)
            row = connection.execute(select(self.tables['installation_attempt']).where(
                self.tables['installation_attempt'].c.id == operation.id)).mappings().one()
            record = row['record']
            restored = environment['snapshot'].get('cold_restore')
            if (environment['active_attempt_id'] != operation.id or row['revision_id'] != operation.revision or
                    environment['desired_revision_id'] != operation.revision or
                    (restored and restored.get('incarnation') != operation.binding.get('host_id')) or
                    record.get('phase') != 'running' or record.get('terminal') or
                    record.get('claim') != operation.binding):
                raise PreparationError('interrupted published claim mismatch')
            revision, _ = self._published_revision(connection, operation.revision)
            identity = operation.binding
            claim = InstallationClaim(operation.id, operation.revision, identity['fence'], identity['host_id'],
                                      identity['runtime_generation'], revision['snapshot'])
            if claim.identity != identity:
                raise PreparationError('interrupted published identity mismatch')
            return claim

    def claim_installation(self, host_id: str) -> InstallationClaim | None:
        with self.engine.begin() as connection:
            environment = self._environment(connection)
            attempts = self.tables['installation_attempt']
            row = connection.execute(select(attempts).where(
                attempts.c.id == environment['active_attempt_id']).with_for_update()).mappings().first()
            if not row:
                if environment['active_attempt_id']:
                    raise PreparationError('active attempt missing')
                return None
            record = row['record']
            restored = environment['snapshot'].get('cold_restore')
            if restored and host_id != restored['incarnation']:
                raise PreparationError('foreign restore incarnation')
            if record.get('phase') != 'pending' or record.get('reason') not in ('approval', 'retry', 'automatic-adoption', 'automatic-migration', 'cold-restore'):
                raise PreparationError('uncertain or unsupported installation remains fenced')
            if record.get('reason') == 'cold-restore' and (not restored or record.get('incarnation') != host_id):
                raise PreparationError('restore authority mismatch')
            if (record.get('reason') in ('automatic-adoption', 'automatic-migration', 'cold-restore') and
                    os.environ.get('DAUPDATEONSTART', 'true') not in ('true', 'initial')):
                return None  # Preserve the immutable pending request while convergence is disabled.
            if record.get('reason') == 'retry':
                previous = connection.execute(select(attempts).where(
                    attempts.c.revision_id == row['revision_id'], attempts.c.id != row['id']).order_by(
                    attempts.c.record['created_at'].as_string().desc(), attempts.c.id).limit(1)).mappings().first()
                if not previous or not installation_retry_eligible(previous['record'].get('terminal', {})):
                    raise PreparationError('retry requires an eligible durable terminal')
            revision, candidate = self._published_revision(connection, row['revision_id'])
            snapshot = revision['snapshot']
            if record.get('reason') == 'retry' and not snapshot.get('image_baseline'):
                raise PreparationError('retry requires a qualified image baseline')
            publication = snapshot['publication']
            if (environment['desired_revision_id'] != row['revision_id'] or
                    publication['inputs'] != candidate['inputs'] or
                    publication['result'] != candidate['result'] or
                    publication['result_digest'] != candidate['result_digest'] or
                    digest({'inputs': publication['inputs'], 'result': publication['result']}) != candidate['result_digest'] or
                    any(environment['snapshot'][key] != snapshot[key] for key in ('core', 'tool', 'target'))):
                raise PreparationError('published identity changed')
            self._validate_result(publication['inputs'], publication['result'])
            generation = environment['runtime_generation'] + 1
            prior_runtime = environment['snapshot'].get('runtime', {})
            revisions = self.tables['revision']
            applied_snapshot = connection.scalar(select(revisions.c.snapshot).where(
                revisions.c.id == prior_runtime.get('revision_id'))) if prior_runtime.get('kind') == 'normal' else None
            pre_change = {key: (applied_snapshot or environment['snapshot']).get(key) for key in
                          ('target', 'tool', 'core', 'image_baseline', 'selected_baseline', 'schema_release')}
            if applied_snapshot is None:
                pre_change['selected_baseline'] = None
                pre_change['core'] = None
            pre_change['runtime'] = prior_runtime
            claim = InstallationClaim(row['id'], row['revision_id'], record['fence'] + 1,
                                      host_id, generation, snapshot, record.get('restore_schema'), record.get('reason') == 'cold-restore',
                                      automatic=record.get('reason') in ('automatic-adoption', 'automatic-migration', 'cold-restore'),
                                      pre_change=pre_change, checkpoint_database_revision=record.get('checkpoint_database_revision'))
            connection.execute(update(attempts).where(attempts.c.id == row['id']).values(
                record=dict(record, phase='running', claim=claim.identity, fence=claim.fence,
                            pre_change=claim.pre_change)))
            current = dict(environment['snapshot'])
            runtime = current.get('runtime', {})
            if runtime.get('kind') == 'normal':
                current['last_normally_verified'] = runtime
            current['runtime'] = {'kind': 'unknown', 'inventory': {}, 'attempt_id': row['id']}
            connection.execute(update(self.tables['environment_state']).where(
                self.tables['environment_state'].c.id == 1).values(snapshot=current, runtime_generation=generation))
            return claim

    def record_checkpoint(self, claim: InstallationClaim, status: dict[str, Any]) -> None:
        with self.engine.begin() as connection:
            environment = self._environment(connection)
            attempts = self.tables['installation_attempt']
            row = connection.execute(select(attempts).where(
                attempts.c.id == claim.attempt_id).with_for_update()).mappings().one()
            if environment['active_attempt_id'] != claim.attempt_id or row['record'].get('claim') != claim.identity:
                raise PreparationError('checkpoint attempt fence mismatch')
            connection.execute(update(attempts).where(attempts.c.id == claim.attempt_id).values(
                record=dict(row['record'], checkpoint=status)))

    def report_terminal(self, receipt: dict[str, Any]) -> str:
        """Trusted journal replay under host lock; mutation results require child exclusion."""
        aborted = pre_sync_abort(receipt)
        normal = receipt.get('outcome') == 'applied-normal' and receipt.get('normal_verified') is True
        if normal:
            from .roles import RUNTIME_ROLES, INFRASTRUCTURE_ROLES
            if (receipt.get('child_safe') is not True or receipt.get('mutation_started') is not True or
                    receipt.get('readiness') != {'schema_verified': True, 'roles': sorted(RUNTIME_ROLES + INFRASTRUCTURE_ROLES)} or
                    not receipt.get('inventory') or receipt.get('failure') is not None):
                raise PreparationError('invalid normal evidence')
        if not normal and ((not aborted and (receipt.get('outcome') not in ('materialized-stopped', 'failed-stopped', 'blocked') or
                              receipt.get('child_safe') is not True)) or
                receipt.get('normal_verified') is not False):
            raise PreparationError('invalid terminal evidence')
        recovery = receipt.get('recovery')
        if recovery is not None and (receipt.get('outcome') != 'failed-stopped' or
                receipt.get('mutation_started') is not True or not isinstance(recovery, dict) or
                set(recovery) != {'id', 'attempt', 'result', 'core_baseline', 'inventory'} or
                recovery.get('id') != receipt['identity']['attempt_id'] + ':core:1' or
                recovery.get('attempt') != 1 or recovery.get('result') not in ('succeeded', 'failed') or
                not isinstance(recovery.get('core_baseline'), dict) or not recovery.get('inventory')):
            raise PreparationError('invalid recovery evidence')
        failure = receipt.get('failure')
        if failure is not None and (not isinstance(failure, dict) or set(failure) != {'stage', 'reason'} or
                not isinstance(failure.get('stage'), str) or not isinstance(failure.get('reason'), str) or
                failure['reason'] not in FAILURE_REASONS.get(failure['stage'], set())):
            raise PreparationError('invalid failure reason')
        identity = receipt['identity']
        with self.engine.begin() as connection:
            environment = self._environment(connection)
            restored = environment['snapshot'].get('cold_restore')
            if restored and identity.get('host_id') != restored['incarnation']:
                raise PreparationError('historical restore receipt has no local authority')
            attempts = self.tables['installation_attempt']
            row = connection.execute(select(attempts).where(
                attempts.c.id == identity['attempt_id']).with_for_update()).mappings().first()
            if not row or row['revision_id'] != identity['revision_id'] or row['record'].get('claim') != identity:
                raise PreparationError('attempt fence mismatch')
            record = row['record']
            if record.get('terminal'):
                if record['terminal'] != receipt:
                    raise PreparationError('terminal evidence mismatch')
                return 'replayed'
            if environment['active_attempt_id'] != row['id'] or record['phase'] != 'running':
                raise PreparationError('active attempt fence mismatch')
            if normal and environment['runtime_generation'] != identity['runtime_generation']:
                raise PreparationError('normal observation superseded')
            if recovery:
                revision, _ = self._published_revision(connection, row['revision_id'])
                image = revision['snapshot'].get('image_baseline')
                if (not image or recovery['core_baseline'].get('image_target') != image['image_target']):
                    raise PreparationError('recovery image identity mismatch')
            connection.execute(update(attempts).where(attempts.c.id == row['id']).values(
                record=dict(record, phase='succeeded' if normal else 'stopped' if receipt['outcome'] == 'materialized-stopped' else
                            'aborted' if aborted else 'blocked' if receipt['outcome'] == 'blocked' else 'failed',
                            terminal=receipt)))
            # A newer observation must never be overwritten by an old result.
            values: dict[str, Any] = {'active_attempt_id': None}
            if environment['runtime_generation'] == identity['runtime_generation']:
                current = dict(environment['snapshot'])
                current['runtime'] = {'kind': 'unknown', 'inventory': {},
                                      'attempt_id': row['id'], 'materialized_revision_id':
                                      row['revision_id'] if receipt['outcome'] == 'materialized-stopped' else None}
                if recovery:
                    current['last_recovery'] = recovery
                    if recovery['result'] == 'succeeded':
                        current['runtime'] = {'kind': 'core-only', 'inventory': recovery['inventory'],
                            'core_baseline': recovery['core_baseline'], 'recovery_id': recovery['id'],
                            'attempt_id': row['id']}
                if current.get('cold_restore', {}).get('attempt_id') == row['id']:
                    current['cold_restore'] = dict(current['cold_restore'], state='verified' if normal else 'failed')
                if normal:
                    revision, _ = self._published_revision(connection, row['revision_id'])
                    if not revision['snapshot'].get('image_baseline'):
                        raise PreparationError('normal report requires a qualified image baseline')
                    inventory = revision['snapshot']['publication']['result']['bundle']['inventory']
                    if receipt['inventory'] != {name: value['version'] for name, value in inventory.items()}:
                        raise PreparationError('normal inventory identity mismatch')
                    current['runtime'] = {'kind': 'normal', 'inventory': inventory,
                                          'revision_id': row['revision_id'], 'attempt_id': row['id']}
                    current['last_normally_verified'] = current['runtime']
                    if receipt.get('schema_release'):
                        from docassemble.base.managed_migration import validate_schema_release
                        from packaging.version import Version
                        evidence = receipt['schema_release']
                        validate_schema_release(evidence)
                        if (evidence['attempt_id'] != row['id'] or evidence['revision_id'] != row['revision_id']):
                            raise PreparationError('schema release identity mismatch')
                        previous = current.get('schema_release')
                        if previous is None or Version(evidence['release']) > Version(previous['release']):
                            current['schema_release'] = evidence
                    if revision['snapshot'].get('selected_baseline') or revision['snapshot'].get('schema_mode') == 'unchanged':
                        current['recovery_baseline'] = revision['snapshot'].get('selected_baseline') or {
                            'image_baseline': revision['snapshot']['image_baseline'], 'core': revision['snapshot']['core']}
                values.update(snapshot=current, runtime_generation=environment['runtime_generation'] + 1)
            connection.execute(update(self.tables['environment_state']).where(
                self.tables['environment_state'].c.id == 1).values(**values))
            return 'reported'


    def invalidate_normal(self, identity: dict[str, Any]) -> None:
        with self.engine.begin() as connection:
            environment = self._environment(connection)
            snapshot = dict(environment['snapshot'])
            runtime = snapshot.get('runtime', {})
            if runtime.get('kind') == 'normal' and runtime.get('attempt_id') == identity['attempt_id']:
                snapshot['runtime'] = {'kind': 'unknown', 'inventory': {}, 'attempt_id': identity['attempt_id']}
                connection.execute(update(self.tables['environment_state']).values(
                    snapshot=snapshot, runtime_generation=environment['runtime_generation'] + 1))

    def normal_replay_allowed(self, receipt: dict[str, Any], *, reported_only: bool = False) -> bool:
        """A historical success cannot reopen admission over another attempt/observation."""
        identity = receipt['identity']
        with self.engine.begin() as connection:
            environment = self._environment(connection)
            if environment['desired_revision_id'] != identity['revision_id']:
                return False
            if environment['active_attempt_id']:
                return (not reported_only and environment['active_attempt_id'] == identity['attempt_id'] and
                        environment['runtime_generation'] == identity['runtime_generation'])
            runtime = environment['snapshot'].get('runtime', {})
            return (runtime.get('kind') == 'normal' and runtime.get('attempt_id') == identity['attempt_id'] and
                    runtime.get('revision_id') == identity['revision_id'])

    def next_after_terminal(self, receipt: dict[str, Any]) -> bool:
        """Rollover requires the exact durable prior terminal and new pending authority."""
        if receipt.get('outcome') != 'applied-normal' and not installation_retry_eligible(receipt):
            return False
        with self.engine.begin() as connection:
            environment = self._environment(connection)
            attempts = self.tables['installation_attempt']
            previous = connection.execute(select(attempts).where(
                attempts.c.id == receipt['identity']['attempt_id'])).mappings().one()
            if previous['record'].get('terminal') != receipt:
                return False
            pending = connection.execute(select(attempts).where(
                attempts.c.id == environment['active_attempt_id'])).mappings().first()
            if not pending or pending['record'].get('phase') != 'pending':
                return False
            if receipt['outcome'] == 'applied-normal':
                return True
            if pending['revision_id'] == previous['revision_id'] and pending['record'].get('reason') == 'retry':
                return True
            if receipt.get('recovery') and pending['record'].get('reason') == 'approval':
                revision, candidate = self._published_revision(connection, pending['revision_id'])
                return (candidate['operation'] == 'repair' and
                        environment['desired_revision_id'] == revision['id'])
            return False


def migration_schema_evidence(snapshot: dict, journal: Journal, *, revision_id: str, attempt_id: str) -> dict:
    """Prefer successful setup evidence bound to this revision/attempt over the
    immutable pre-migration snapshot evidence (forward setup may have advanced
    the database revision)."""
    successful = journal.schema_release
    if (isinstance(successful, dict) and successful.get('revision_id') == revision_id and
            successful.get('attempt_id') == attempt_id and successful.get('database_revision')):
        return successful
    return snapshot['schema_release']


class InstallationPoller:
    def __init__(self, installation: Installation, paths: ExecutorPaths, store: Path,
                 baseline: Path):
        self.installation, self.paths, self.store, self.baseline = installation, paths, store, baseline

    def _require_schema_clearance(self, journal: Journal) -> None:
        """Refuse to rerun schema/setup over an uncertain prior outcome without
        a matching administrator/operator clearance; ordinary retry/startup
        cannot satisfy this gate. Clears only when bound attempt+schema match."""
        if not journal.schema_uncertain:
            return
        # Always consider a freshly written operator clearance first: a stale
        # persisted clearance for an older observed revision must not shadow a
        # replacement clearance for the current database revision.
        operator = load_schema_clearance(self.paths)
        persisted = journal.clearance if journal.schema_cleared else None
        try:
            observed = read_database_revision(database_settings(load_config(self.paths.config_file)))
        except Exception:
            observed = None
        chosen = None
        for candidate in (operator, persisted):
            if candidate is not None and observed is not None and candidate.get('observed_schema') == observed:
                chosen = candidate
                break
        if chosen is None:
            raise InstallationFailure('schema-clearance-required')
        try:
            journal.record_clearance(chosen)
        except Exception:
            raise InstallationFailure('schema-clearance-required') from None
        journal.save(self.paths.journal_file)

    def _gates(self, claim: InstallationClaim, *, mutation: bool = True, retain_baseline: bool = True) -> Path:
        if os.environ.get('DA_UV_MANAGED') != 'true':
            raise InstallationFailure('managed-disabled')
        config = load_config(self.paths.config_file)
        if mutation and (config.get('allow updates', True) is not True or os.environ.get('DAALLOWUPDATES', 'true') != 'true'):
            raise InstallationFailure('updates-disabled')
        if (mutation and os.environ.get('DAUPDATEONSTART', 'true') not in ('true', 'initial') and
                (claim.automatic or claim.cold_restore)):
            raise InstallationFailure('updates-disabled')
        try:
            trusted = json.loads(self.baseline.read_text())
            trusted = trusted.get('baseline', trusted)
            for key in ('target', 'tool', 'core'):
                trusted[key]
        except (OSError, ValueError, KeyError, TypeError):
            raise InstallationFailure('baseline-invalid') from None
        if claim.snapshot.get('image_baseline'):
            from docassemble.base.managed_qualification import configured_baseline, require_qualified_inputs
            from .image_baseline import load_image_wheels
            configured = configured_baseline()
            trusted_image, _ = load_image_wheels(self.baseline, self.paths)
            if configured != trusted_image:
                raise InstallationFailure('target-mismatch')
            require_qualified_inputs(claim.snapshot['publication']['inputs'], configured)
        publication = claim.snapshot['publication']
        selected = claim.snapshot.get('selected_baseline')
        from .selected_baseline import load_baselines, selected_directory
        local = load_baselines(self.paths)
        if selected:
            if not claim.snapshot.get('image_baseline'):
                raise InstallationFailure('target-mismatch')
            if local and local.get('image_baseline') != claim.snapshot['image_baseline']:
                raise InstallationFailure('target-mismatch')
            if publication['inputs'].get('change_operation') in ('upgrade', 'rollback'):
                expected_selection = (publication['inputs']['rollback']['snapshot'].get('selected_baseline')
                    if publication['inputs'].get('change_operation') == 'rollback' else
                    {key: publication['result'][key] for key in ('core_bundle', 'release')})
                if selected != expected_selection:
                    raise InstallationFailure('target-mismatch')
                prior = publication['inputs']['baseline']
                if prior.get('selected_baseline') != local.get('selected_baseline') and selected != local.get('selected_baseline'):
                    raise InstallationFailure('target-mismatch')
            elif selected != local.get('selected_baseline'):
                raise InstallationFailure('target-mismatch')
            if claim.snapshot['core'] != selected['core_bundle']['inventory']:
                raise InstallationFailure('target-mismatch')
            try:
                selected_directory(self.store, selected)
                recovery = (local.get('recovery_baseline') if claim.cold_restore else None) or claim.snapshot['recovery_baseline']
                if recovery.get('core_bundle'):
                    selected_directory(self.store, recovery)
                elif recovery != {'image_baseline': trusted.get('image_baseline'), 'core': trusted['core']}:
                    raise ValueError()
                else:
                    from .image_baseline import verify_image_recovery_core
                    verify_image_recovery_core(self.paths)
            except Exception:
                raise InstallationFailure('bundle-invalid') from None
        elif claim.snapshot['core'] != trusted['core']:
            raise InstallationFailure('target-mismatch')
        elif local.get('selected_baseline'):
            prior = publication['inputs']['baseline']
            if (publication['inputs'].get('change_operation') != 'rollback' or
                    prior.get('selected_baseline') != local.get('selected_baseline')):
                raise InstallationFailure('target-mismatch')
        if any(claim.snapshot[key] != trusted[key] for key in ('target', 'tool')):
            raise InstallationFailure('target-mismatch')
        if trusted['target'] != python_target() or trusted['tool'] != {'uv': UV_VERSION}:
            raise InstallationFailure('unsupported-target')
        if subprocess.run([self.paths.uv_binary, '--version'], capture_output=True, check=True).stdout.decode().split()[:2] != ['uv', UV_VERSION]:
            raise InstallationFailure('tool-mismatch')
        bundle = claim.snapshot['publication']['result']['bundle']
        if not re.fullmatch('[0-9a-f]{64}', bundle['digest']):
            raise InstallationFailure('bundle-invalid')
        directory = self.store / bundle['digest']
        try:
            verify_retained(directory, bundle['digest'])
        except Exception:
            raise InstallationFailure('bundle-invalid') from None
        if (not verify_python_target(directory / 'pyproject.toml', directory / 'uv.lock') or
                sha256_file(directory / 'pyproject.toml') != bundle['project_digest'] or
                sha256_file(directory / 'uv.lock') != bundle['lock_digest'] or
                json.loads((directory / 'inventory.json').read_text()) != bundle['inventory'] or
                json.loads((directory / 'tool.json').read_text()) != {'uv': UV_VERSION, 'target': trusted['target']}):
            raise InstallationFailure('bundle-invalid')
        if claim.snapshot.get('schema_mode') == 'unchanged' or publication['inputs'].get('change_operation') == 'migration':
            recovery = ((local.get('recovery_baseline') if claim.cold_restore else None) or
                        claim.snapshot.get('recovery_baseline', {}))
            try:
                if recovery.get('core_bundle'):
                    selected_directory(self.store, recovery)
                elif recovery != {'image_baseline': trusted.get('image_baseline'), 'core': trusted['core']}:
                    raise ValueError()
                else:
                    from .image_baseline import verify_image_recovery_core
                    verify_image_recovery_core(self.paths)
            except Exception:
                raise InstallationFailure('bundle-invalid') from None
            from .rollback_schema import retained_schema_policy
            from .alembic_probe import read_database_revision
            try:
                policy = retained_schema_policy(directory)
            except Exception:
                raise InstallationFailure('bundle-invalid') from None
            try:
                observed = read_database_revision(database_settings(config))
                if not observed:
                    raise ValueError()
            except Exception:
                raise InstallationFailure('schema-unreadable') from None
            if observed in policy['known_incompatible_revisions']:
                raise InstallationFailure('schema-incompatible')
            if publication['inputs'].get('change_operation') == 'migration':
                from docassemble.base.managed_migration import validate_schema_release
                # On verified resumption, prefer the successful setup evidence bound
                # to this revision/attempt: forward setup may have advanced the
                # database past the immutable pre-migration evidence.
                evidence = migration_schema_evidence(claim.snapshot, Journal.load(self.paths.journal_file),
                                                     revision_id=claim.revision_id, attempt_id=claim.attempt_id)
                try:
                    validate_schema_release(evidence)
                except PreparationError:
                    raise InstallationFailure('schema-unreadable') from None
                if evidence.get('database_revision') and evidence['database_revision'] != observed:
                    raise InstallationFailure('schema-incompatible')
        if mutation and retain_baseline and (selected or claim.snapshot.get('schema_mode') == 'unchanged' or claim.snapshot.get('migration')):
            # A restored successful selection already has a newer verified
            # recovery baseline than the immutable publication's pre-apply one.
            # Do not reset that runtime evidence to historical/image defaults.
            if not (claim.cold_restore and local.get('selected_baseline') == selected):
                from .selected_baseline import retain_selection
                retain_selection(self.paths, claim.snapshot, self.store)
        return directory

    def run_once(self, *, retain_live_health: bool = False) -> str:
        paths = self.paths
        paths.ensure_directories()
        lock = MutationLock(paths.state_dir)
        try:
            lock.__enter__()
        except MutationLockBusy:
            raise InstallationBusy() from None
        try:
            return self._run_once_locked(retain_live_health=retain_live_health)
        finally:
            lock.__exit__(None, None, None)

    def _run_once_locked(self, *, retain_live_health: bool = False) -> str:
        paths = self.paths
        from .checkpoint_restore import require_restore_ready
        require_restore_ready(paths)
        if (paths.state_dir / 'cold-restore.json').exists():
            from .cold_restore import require_incarnation
            require_incarnation(self.installation, paths)
            with self.installation.engine.connect() as connection:
                restored_environment = connection.execute(select(self.installation.tables['environment_state'])).mappings().one()
            if restored_environment['snapshot']['cold_restore']['state'] in ('blocked', 'pending'):
                return 'restore-fenced'
        journal = Journal.load(paths.journal_file)
        self.reconcile_interrupted(journal)
        journal = Journal.load(paths.journal_file)
        if journal.attempt_result:
            receipt = self._terminal_receipt(journal)
            subsequent = self.installation.next_after_terminal(receipt) if hasattr(self.installation, 'next_after_terminal') else False
            if subsequent:
                # Missing clearance must not consume approved repair/retry authority
                # or replace the recovered terminal with a permanent blocked result.
                try:
                    self._require_schema_clearance(journal)
                except InstallationFailure as error:
                    return error.reason
                # The previous DB terminal is already durable. Never health-replay
                # or reopen its admission over a newer pending attempt.
                reported = 'replayed'
            elif receipt.get('outcome') == 'applied-normal':
                if retain_live_health and self._reported_healthy(receipt):
                    return 'healthy'
                reported = self._report_normal(receipt)
            elif pre_sync_abort(receipt):
                if journal.phase != PHASE_INSTALLATION_PREPARING:
                    raise PreparationError('abort journal phase mismatch')
                reported = self.installation.report_terminal(receipt)
            else:
                # A served successful recovery holds a shared runtime lease for
                # the recovery web's lifetime, so replay takes a shared lease
                # too (compatible with serving, still excluding installer
                # children) while the durable journal proves completion.
                if journal.phase == PHASE_RECOVERY_SUCCEEDED:
                    with RuntimeLock(paths.state_dir, shared=True):
                        return self.installation.report_terminal(receipt)
                with RuntimeLock(paths.state_dir):
                    return self.installation.report_terminal(receipt)
            if not subsequent:
                return reported
            _atomic_write_json(paths.state_dir / 'attempt-history' / (receipt['identity']['attempt_id'] + '.json'), journal.to_dict())
            journal.attempt_result = None
            journal.phase = PHASE_VERIFIED
            journal.save(paths.journal_file)
        adoption_recovery = bool(
            journal.phase == PHASE_RECOVERY_SUCCEEDED and journal.recovery and
            journal.recovery.result == 'succeeded' and not journal.schema_uncertain and
            not journal.attempt_result and
            (not journal.operation or journal.operation.kind not in ('published-apply', 'published-materialization')) and
            hasattr(self.installation, 'pending_first_adoption') and self.installation.pending_first_adoption())
        if journal.phase not in (PHASE_IDLE, PHASE_VERIFIED) and not adoption_recovery:
            raise PreparationError('local uncertain lifecycle remains fenced')
        # Also fence unclaimed work when a previous rollover was interrupted.
        # Keep the DB request pending; clearance is not installation authority.
        try:
            self._require_schema_clearance(journal)
        except InstallationFailure as error:
            return error.reason
        host_path = paths.state_dir / 'installation-host.json'
        if not host_path.exists():
            _atomic_write_json(host_path, {'id': str(uuid.uuid4())})
        host_id = json.loads(host_path.read_text())['id']
        claim = self.installation.claim_installation(host_id)
        if claim is None:
            return 'idle'
        # Bind BEFORE drain or any external effect. A crash without a terminal
        # result remains uncertain; the DB claim is never lease-reclaimed.
        qualified = bool(claim.snapshot.get('image_baseline'))
        operation = Operation(id=claim.attempt_id, kind='published-apply' if qualified else 'published-materialization',
                              revision=claim.revision_id, binding=claim.identity)
        journal.operation = operation
        journal.phase = PHASE_INSTALLATION_PREPARING
        journal.save(paths.journal_file)
        outcome = 'blocked'
        mutation_started = False
        drain_aborted = False
        checkpoint = None
        checkpoint_started = None
        failure = {'stage': 'gates', 'reason': 'gate-check-failed'}
        try:
            self._require_schema_clearance(journal)
            directory = (self._gates(claim, retain_baseline=False) if
                         claim.checkpoint_requested else self._gates(claim))
            failure = {'stage': 'context', 'reason': 'context-load-failed'}
            context = load_context(paths, start_infrastructure=False)
            failure = {'stage': 'drain', 'reason': 'drain-failed'}
            if not enter_maintenance(context):
                drain_aborted = True
            else:
                failure = {'stage': 'runtime-exclusion', 'reason': 'runtime-exclusion-failed'}
                with RuntimeLock(paths.state_dir):
                    pass
                if claim.checkpoint_requested:
                    failure = {'stage': 'checkpoint', 'reason': 'capture-failed'}
                    checkpoint_started = time.monotonic()
                    from .checkpoints import capture_for_installation, observe_pre_change
                    observe_pre_change(paths, claim)
                    with RuntimeLock(paths.state_dir):
                        checkpoint = capture_for_installation(self.installation, paths, claim, self.store, self.baseline)
                    checkpoint['maintenance_seconds'] = round(time.monotonic() - checkpoint_started, 3)
                    failure = {'stage': 'gates', 'reason': 'gate-check-failed'}
                    self._gates(claim)
                journal = Journal.load(paths.journal_file)
                journal.begin_mutation(operation, schema_unchanged=claim.snapshot.get('schema_mode') == 'unchanged')
                journal.save(paths.journal_file)
                mutation_started = True
                outcome = 'failed-stopped'
                failure = {'stage': 'sync', 'reason': 'sync-failed'}
                result = sync_frozen(directory, paths.runtime_venv, paths.uv_binary, paths.cache_dir,
                    log_dir=paths.log_dir, env_extra={'UV_OFFLINE': '1', 'UV_NO_CONFIG': '1',
                                                  'UV_PYTHON': sys.executable})
                if result.ok:
                    failure = {'stage': 'inventory', 'reason': 'inventory-mismatch'}
                    if verify_environment(paths.runtime_venv, {name: value['version'] for name, value in
                            claim.snapshot['publication']['result']['bundle']['inventory'].items()},
                            json.loads((directory / 'installed-material.json').read_text()) if qualified else None).ok:
                        outcome = 'materialized-stopped'
                        if qualified:
                            outcome = 'failed-stopped'
                            from .published import prepare_published, record_published
                            failure = {'stage': 'normal', 'reason': 'qualification-failed'}
                            # Revalidate at consumption too: drain/sync may
                            # have outlived an operator's schema observation.
                            self._require_schema_clearance(Journal.load(paths.journal_file))
                            readiness = prepare_published(paths, claim)
                            receipt = {'identity': claim.identity, 'outcome': 'applied-normal',
                                'mutation_started': True, 'child_safe': True, 'normal_verified': True,
                                'failure': None, 'readiness': readiness,
                                **({'checkpoint': checkpoint} if checkpoint else {}),
                                'inventory': {name: value['version'] for name, value in
                                    claim.snapshot['publication']['result']['bundle']['inventory'].items()}}
                            record_published(paths, claim, receipt)
                            return self._report_normal(receipt)
        except Exception as error:
            if isinstance(error, InstallationFailure) and error.reason in FAILURE_REASONS['gates']:
                failure = {'stage': 'gates', 'reason': error.reason}
            from .published import NormalQualificationError
            if isinstance(error, NormalQualificationError):
                failure = {'stage': 'normal', 'reason': str(error)}
            # A durable normal receipt must NEVER become a failed receipt on
            # reporting loss. Replay rechecks health, not sync/schema.
            if Journal.load(paths.journal_file).attempt_result:
                raise
            if qualified:
                from .published import close_published
                close_published(paths)
                enter_maintenance(load_context(paths, start_infrastructure=False))
            # Uncertain descendants keep RuntimeLock and forbid a mutation receipt.
        if drain_aborted:
            # False confirms no sync/mutation. The controller owns any
            # service restoration; resumed consumers are NOT installers.
            journal = Journal.load(paths.journal_file)
            if not journal.operation or journal.operation.binding != claim.identity:
                raise PreparationError('journal binding changed')
            journal.attempt_result = {'identity': claim.identity, 'outcome': 'aborted-before-sync',
                'mutation_started': False, 'child_safe': False, 'normal_verified': False,
                'failure': {'stage': 'drain', 'reason': 'drain-aborted'}}
            journal.save(paths.journal_file)
            return self.installation.report_terminal(journal.attempt_result)
        if failure['stage'] == 'checkpoint':
            status_file = paths.state_dir / 'checkpoints' / (claim.attempt_id + '.json')
            checkpoint = (json.loads(status_file.read_text()) if status_file.exists() else
                          {'state': 'failed-incomplete', 'complete': False, 'known_good': False})
            if checkpoint_started is not None:
                checkpoint['maintenance_seconds'] = round(time.monotonic() - checkpoint_started, 3)
        recovery = self._recover_core(claim) if qualified and mutation_started else None
        served = recovery is not None and recovery['result'] == 'succeeded'
        if served:
            # A successful restoration already started restricted administration,
            # which legitimately holds a shared runtime lease. The installer sync
            # completed before it started, so record the failed attempt terminal
            # without the exclusive descendant proof that would deadlock here.
            journal = Journal.load(paths.journal_file)
            if not journal.operation or journal.operation.binding != claim.identity:
                raise PreparationError('journal binding changed')
            journal.attempt_result = {'identity': claim.identity, 'outcome': outcome,
                                      'mutation_started': mutation_started,
                                      'child_safe': True, 'normal_verified': False,
                                      'failure': None if outcome == 'materialized-stopped' else failure,
                                      **({'checkpoint': checkpoint} if checkpoint else {}),
                                      'recovery': recovery}
            journal.save(paths.journal_file)
            return self.installation.report_terminal(journal.attempt_result)
        with RuntimeLock(paths.state_dir):
            journal = Journal.load(paths.journal_file)
            if not journal.operation or journal.operation.binding != claim.identity:
                raise PreparationError('journal binding changed')
            journal.attempt_result = {'identity': claim.identity, 'outcome': outcome,
                                      'mutation_started': mutation_started,
                                      'child_safe': True, 'normal_verified': False,
                                      'failure': None if outcome == 'materialized-stopped' else failure,
                                      **({'checkpoint': checkpoint} if checkpoint else {})}
            if recovery is not None:
                journal.attempt_result['recovery'] = recovery
            journal.save(paths.journal_file)
            # Core restoration never advances normal readiness or applied revision.
            Admission(paths.state_dir).close('published materialization stopped; lifecycle qualification required')
            if qualified:
                from .published import close_published
                close_published(paths)
            return self.installation.report_terminal(journal.attempt_result)

    def _recover_core(self, claim: InstallationClaim) -> dict[str, Any]:
        """One immediate restoration; the durable marker fences crash retries.

        A successful restoration serves restricted administration (ticket 13); a
        failed one stays stopped. No management-table reads, intent writes,
        schema/setup or data restore happen here. Caller retains MutationLock;
        every non-serving receipt follows exclusive descendant exclusion.
        """
        from .controller import _restore_core, _recovery_failed
        from .startup import target_id
        from .status import MODE_RECOVERY
        context = load_context(self.paths, start_infrastructure=False)
        if (not context.journal.operation or context.journal.operation.binding != claim.identity or
                context.journal.recovery_attempted):
            raise PreparationError('recovery already attempted or journal binding changed')
        if claim.snapshot['image_baseline']['image_target'] != target_id(context.manifest):
            raise PreparationError('recovery target mismatch; uncertain work remains fenced')
        with RuntimeLock(self.paths.state_dir):
            pass
        mode = 'maintenance'
        try:
            mode = _restore_core(context, 'published application failed', serve=True)
        except Exception:
            # Do not record completion while an orphaned sync/probe can still run.
            with RuntimeLock(self.paths.state_dir):
                pass
            if not context.journal.recovery_attempted:
                raise
            _recovery_failed(context, 'published core restoration failed')
            mode = 'maintenance'
        if mode != MODE_RECOVERY:
            # A stopped/failed restoration leaves no serving lease; require exclusion.
            with RuntimeLock(self.paths.state_dir):
                pass
        recovery = context.journal.recovery
        if recovery is None or recovery.result not in ('succeeded', 'failed'):
            raise PreparationError('recovery completion is uncertain')
        return self._recovery_receipt(claim, context)

    def _recovery_receipt(self, claim: InstallationClaim, context) -> dict[str, Any]:
        from .startup import target_id
        recovery = context.journal.recovery
        if (recovery is None or recovery.attempt != 1 or recovery.result not in ('succeeded', 'failed') or
                context.journal.phase != 'recovery_' + recovery.result or not context.journal.operation or
                context.journal.operation.binding != claim.identity or
                claim.snapshot['image_baseline']['image_target'] != target_id(context.manifest)):
            raise PreparationError('recovery completion is uncertain')
        from .selected_baseline import load_baselines, core_versions
        retained = load_baselines(self.paths).get('recovery_baseline', {})
        if retained.get('core_bundle'):
            return {'id': claim.attempt_id + ':core:1', 'attempt': 1, 'result': recovery.result,
                    'core_baseline': {'image_target': target_id(context.manifest),
                                      'bundle': 'selected-core', 'bundle_digest': retained['core_bundle']['digest']},
                    'inventory': core_versions(retained)}
        name = context.bundle_name('core', context.manifest['identity']['core_baseline'])
        return {'id': claim.attempt_id + ':core:1', 'attempt': 1,
                'result': recovery.result,
                'core_baseline': {'image_target': target_id(context.manifest),
                                  'bundle': name, 'bundle_digest': context.manifest['bundles'][name]},
                'inventory': context.manifest['inventories'][name]['distributions']}

    def _terminal_receipt(self, journal: Journal) -> dict[str, Any]:
        receipt = journal.attempt_result
        if (not receipt or not journal.operation or journal.operation.binding != receipt.get('identity') or
                journal.operation.id != receipt['identity']['attempt_id'] or
                journal.operation.revision != receipt['identity']['revision_id']):
            raise PreparationError('terminal journal binding mismatch')
        if receipt.get('recovery') and (not journal.recovery or journal.recovery.attempt != 1 or
                journal.recovery.result != receipt['recovery']['result'] or
                journal.phase != 'recovery_' + receipt['recovery']['result']):
            raise PreparationError('recovery journal phase mismatch; uncertain work remains fenced')
        if ((receipt.get('outcome') == 'applied-normal' and journal.phase != PHASE_VERIFIED) or
                (pre_sync_abort(receipt) and journal.phase != PHASE_INSTALLATION_PREPARING)):
            raise PreparationError('terminal journal phase mismatch; uncertain work remains fenced')
        return receipt

    def report_stopped_boot(self, journal: Journal) -> None:
        """Reconcile a lost stopped report before boot invalidates runtime observations."""
        if journal.attempt_result and journal.attempt_result.get('outcome') != 'applied-normal':
            receipt = self._terminal_receipt(journal)
            with RuntimeLock(self.paths.state_dir, shared=journal.phase == PHASE_RECOVERY_SUCCEEDED):
                self.installation.report_terminal(receipt)

    def reconcile_interrupted(self, journal: Journal) -> None:
        """Caller holds MutationLock. Finish recovery/reporting, never repeat desired sync/setup."""
        if (journal.attempt_result or not journal.operation or
                journal.operation.kind != 'published-apply' or
                journal.phase not in ('mutation_started', 'recovery_attempt_started',
                                      'recovery_succeeded', 'recovery_failed')):
            return
        claim = self.installation.interrupted_claim(journal)
        # A surviving installer descendant excludes even a probe or recovery marker.
        with RuntimeLock(self.paths.state_dir):
            pass
        if not journal.recovery_attempted:
            recovery = self._recover_core(claim)
        else:
            context = load_context(self.paths, start_infrastructure=False)
            if journal.recovery_interrupted():
                from .controller import _recovery_failed
                _recovery_failed(context, 'interrupted core restoration; explicit repair required')
            recovery = self._recovery_receipt(claim, context)
        journal = Journal.load(self.paths.journal_file)
        journal.attempt_result = {'identity': claim.identity, 'outcome': 'failed-stopped',
            'mutation_started': True, 'child_safe': True, 'normal_verified': False,
            'failure': {'stage': 'sync', 'reason': 'sync-failed'}, 'recovery': recovery}
        journal.save(self.paths.journal_file)
        self.installation.report_terminal(journal.attempt_result)

    def _reported_healthy(self, receipt: dict[str, Any]) -> bool:
        """Continuous turns retain only already qualified, unchanged live incarnations.

        Do not repeatedly close traffic for serial-service probes. Changed processes,
        lost reporting or invalid bytes go through the full admission/health handoff.
        """
        from .roles import process_identities
        from .status import read_status_file
        try:
            local = read_status_file(self.paths.status_file)
            reported = json.loads((self.paths.state_dir / 'publication-reported.json').read_text())
            current = process_identities(self.paths)
            if (reported != receipt['identity'] or not local.get('normal_ready') or
                    Admission(self.paths.state_dir).state()['mode'] != 'open' or
                    current != local.get('role_processes') or not all(current.values()) or
                    not self.installation.normal_replay_allowed(receipt, reported_only=True)):
                return False
            directory = self.store / receipt['identity']['bundle_digest']
            verify_retained(directory, receipt['identity']['bundle_digest'])
            material = json.loads((directory / 'installed-material.json').read_text())
            return verify_environment(self.paths.runtime_venv, receipt['inventory'], material).ok
        except Exception:
            return False

    def _report_normal(self, receipt: dict[str, Any]) -> str:
        from .published import recheck_published, open_published, close_published
        # Ordinary service consumers may hold shared leases; installer children
        # were excluded before roles launched. MutationLock remains held here.
        directory = self.store / receipt['identity']['bundle_digest']
        try:
            if hasattr(self.installation, 'normal_replay_allowed') and not self.installation.normal_replay_allowed(receipt):
                raise PreparationError('normal observation superseded')
            verify_retained(directory, receipt['identity']['bundle_digest'])
            material = json.loads((directory / 'installed-material.json').read_text())
            current = recheck_published(self.paths, receipt, material)
        except Exception:
            current = False
        if not current:
            close_published(self.paths)
            self.installation.invalidate_normal(receipt['identity'])
            raise PreparationError('normal replay health failed; uncertain work remains fenced')
        reported = self.installation.report_terminal(receipt)
        _atomic_write_json(self.paths.state_dir / 'publication-reported.json', receipt['identity'])
        try:
            open_published(self.paths)
        except Exception:
            close_published(self.paths)
            self.installation.invalidate_normal(receipt['identity'])
            raise
        return reported

    def run(self, poll_seconds: float = 2) -> None:
        if poll_seconds <= 0:
            raise ValueError('invalid polling interval')
        while True:
            print(self.run_once(), flush=True)
            time.sleep(poll_seconds)
