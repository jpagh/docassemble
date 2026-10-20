"""Fail-closed published DB boot reconciliation and opt-in healthy normal resumption.

A previous normal observation is history after process loss, not fresh readiness.
Exact durable normal receipts qualify with fresh health, including lost DB reports.
Interrupted mutation receives its one recovery; desired sync/setup is never resumed.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import create_engine, select, update
from sqlalchemy.engine import URL

from docassemble.base.managed_preparation import Preparation, PreparationError, runtime_status, installation_failure

from .journal import Journal, utcnow
from .paths import ExecutorPaths
from .state import AppliedState, _atomic_write_json
from .status import MODE_MAINTENANCE, write_status

if TYPE_CHECKING:
    from .controller import Context


def published_boot_required(paths: ExecutorPaths, journal: Journal) -> bool:
    from pathlib import Path
    from docassemble.base.managed_qualification import ENVIRONMENT
    descriptor = os.environ.get(ENVIRONMENT)
    return bool((descriptor and (Path(descriptor).exists() or
                                 (paths.state_dir / 'initialized.json').exists())) or
                (paths.state_dir / 'cold-restore.json').exists() or
                (paths.state_dir / 'installation-host.json').exists() or
                (paths.state_dir / 'published-boot.json').exists() or
                (journal.operation and journal.operation.kind in ('published-apply', 'published-materialization')))


def installation_host_id(paths: ExecutorPaths) -> str:
    path = paths.state_dir / 'installation-host.json'
    if not path.exists():
        _atomic_write_json(path, {'id': str(uuid.uuid4())})
    identity = json.loads(path.read_text())['id']
    if not isinstance(identity, str) or str(uuid.UUID(identity)) != identity:
        raise PreparationError('invalid installation host identity')
    return identity


class BootObservation(Preparation):
    def reconcile(self, host_id: str, journal: Journal, applied: AppliedState) -> dict[str, Any]:
        """Caller holds MutationLock; DB environment-first serialization fences previews.

        No attempt is claimed/released. Even a durable normal terminal is not replayed
        here: its original roles no longer establish health after cold boot.
        """
        with self.engine.begin() as connection:
            environment = self._environment(connection)
            desired = connection.execute(select(self.tables['revision']).where(
                self.tables['revision'].c.id == environment['desired_revision_id'])).mappings().first()
            if not desired:
                if environment['desired_revision_id'] is None and environment['snapshot'].get('adoption'):
                    from docassemble.base.managed_adoption import adoption_status
                    return dict(runtime_status(environment), normal_ready=False, recovery_available=False,
                        installation='adopting', adoption=adoption_status(environment['snapshot']),
                        reconciliation='legacy-adoption', local_applied_revision_id=applied.revision)
                raise PreparationError('published desired revision missing')
            if desired['snapshot'].get('publication'):
                self._published_revision(connection, desired['id'])
            attempts = self.tables['installation_attempt']
            attempt = connection.execute(select(attempts).where(
                attempts.c.id == environment['active_attempt_id'])).mappings().first()
            if environment['active_attempt_id'] and not attempt:
                raise PreparationError('active attempt missing')
            if attempt is None:
                attempt = connection.execute(select(attempts).where(
                    attempts.c.revision_id == desired['id']).order_by(
                    attempts.c.record['created_at'].as_string().desc(), attempts.c.id).limit(1)).mappings().first()
            snapshot = dict(environment['snapshot'])
            previous = snapshot.get('runtime', {})
            # Cold boot verifies no runtime here, including any former core-only or
            # override state. Retain its evidence separately, never label it running.
            unknown = {'kind': 'unknown', 'inventory': {}, 'reason': 'published-boot-unverified'}
            receipt = journal.attempt_result
            lost_normal = bool(attempt and environment['active_attempt_id'] == attempt['id'] and receipt and
                receipt.get('outcome') == 'applied-normal' and journal.operation and
                journal.operation.binding == receipt.get('identity') and
                attempt['record'].get('claim') == receipt.get('identity') and
                attempt['record'].get('phase') == 'running' and not attempt['record'].get('terminal') and
                environment['runtime_generation'] == receipt['identity']['runtime_generation'])
            if previous != unknown:
                if previous.get('kind') == 'normal':
                    snapshot['last_normally_verified'] = previous
                snapshot['boot_previous_runtime'] = previous
                snapshot['runtime'] = unknown
                environment.update(snapshot=snapshot, runtime_generation=environment['runtime_generation'] + (0 if lost_normal else 1))
                connection.execute(update(self.tables['environment_state']).where(
                    self.tables['environment_state'].c.id == 1).values(
                    snapshot=snapshot, runtime_generation=environment['runtime_generation']))
            operation = journal.operation
            terminal = journal.attempt_result
            bound = bool(operation and operation.binding and terminal and
                         operation.binding == terminal.get('identity') and
                         operation.id == terminal['identity'].get('attempt_id') and
                         operation.revision == terminal['identity'].get('revision_id'))
            phase = attempt['record'].get('phase') if attempt else None
            phase = phase if phase in ('pending', 'running', 'succeeded', 'failed', 'blocked', 'stopped', 'aborted') else 'unknown'
            active = bool(attempt and attempt['id'] == environment['active_attempt_id'])
            state = 'pending' if active and phase == 'pending' else 'uncertain' if active else 'failed' if phase in (
                'failed', 'blocked', 'aborted') else 'not-verified'
            from docassemble.base.managed_migration import migration_status
            observation = {
                **runtime_status(environment),
                'target': snapshot.get('target'),
                'host_id': host_id,
                'observed_at': self._now(connection).isoformat(),
                'normal_ready': False,
                'installation': state,
                'attempt_id': attempt['id'] if attempt else None,
                'attempt_phase': phase,
                'failure': installation_failure(attempt['record'].get('terminal', {})) if attempt else None,
                'active': active,
                'local_applied_revision_id': applied.revision,
                'local_journal_phase': journal.phase,
                'local_attempt_id': operation.id if operation else None,
                'local_receipt_bound': bound,
                'local_recovery_recorded': journal.in_recovery_state(),
                'schema_uncertain': journal.schema_uncertain,
                'schema_cleared': journal.schema_cleared,
                'schema': None if journal.schema is None else {
                    'attempt_id': journal.schema.attempt_id, 'revision': journal.schema.revision,
                    'identity': journal.schema.identity, 'result': journal.schema.result,
                    'started_at': journal.schema.started_at, 'completed_at': journal.schema.completed_at},
                'recovery_available': False,
                'diverged': applied.revision != desired['id'],
                'reconciliation': 'observed-maintenance',
                'migration': migration_status(snapshot),
            }
            hosts = self.tables['host_environment']
            host = connection.execute(select(hosts).where(hosts.c.id == host_id).with_for_update()).mappings().first()
            generation = host['generation'] + 1 if host else 1
            observation['host_generation'] = generation
            if host:
                connection.execute(update(hosts).where(hosts.c.id == host_id).values(
                    generation=generation, observation=observation))
            else:
                connection.execute(hosts.insert().values(id=host_id, generation=generation, observation=observation))
            return observation

    def resumable(self, observation: dict[str, Any], journal: Journal, applied: AppliedState) -> dict[str, Any] | None:
        """Exact durable success plus schema evidence, either reported or still claim-bound."""
        with self.engine.begin() as connection:
            environment = self._environment(connection)
            receipt = journal.attempt_result
            operation = journal.operation
            if (environment['active_attempt_id'] not in (None, operation.id if operation else None) or
                    environment['runtime_generation'] != observation['runtime_generation'] or
                    environment['desired_revision_id'] != applied.revision or journal.phase != 'verified' or
                    journal.verified_revision != applied.revision or not receipt or not operation or
                    operation.kind != 'published-apply' or operation.binding != receipt.get('identity') or
                    receipt.get('outcome') != 'applied-normal' or receipt.get('normal_verified') is not True or
                    operation.revision != applied.revision or operation.id != receipt['identity']['attempt_id']):
                return None
            attempt = connection.execute(select(self.tables['installation_attempt']).where(
                self.tables['installation_attempt'].c.id == operation.id)).mappings().first()
            if not attempt or attempt['revision_id'] != applied.revision:
                return None
            record = attempt['record']
            reported = (not environment['active_attempt_id'] and record.get('phase') == 'succeeded' and
                        record.get('terminal') == receipt)
            lost = (environment['active_attempt_id'] == operation.id and record.get('phase') == 'running' and
                    not record.get('terminal') and record.get('claim') == receipt['identity'] and
                    environment['runtime_generation'] == receipt['identity']['runtime_generation'])
            if not (reported or lost):
                return None
            revision, _ = self._published_revision(connection, str(applied.revision))
            if revision['snapshot'].get('schema_mode') != 'unchanged' and (
                    journal.schema is None or journal.schema.result != 'succeeded' or
                    journal.schema.attempt_id != operation.id or journal.schema.revision != applied.revision):
                return None
            return revision['snapshot'] if revision['snapshot'].get('image_baseline') else None

    def record_resumed(self, host_id: str, observation: dict[str, Any], receipt: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
        """CAS fresh health under publication's lock; no attempt or slot updates."""
        with self.engine.begin() as connection:
            environment = self._environment(connection)
            if (environment['active_attempt_id'] or environment['desired_revision_id'] != receipt['identity']['revision_id'] or
                    environment['runtime_generation'] != observation['runtime_generation']):
                raise PreparationError('boot resumption superseded')
            current = dict(environment['snapshot'])
            current['runtime'] = {'kind': 'normal', 'inventory': snapshot['publication']['result']['bundle']['inventory'],
                'revision_id': receipt['identity']['revision_id'], 'attempt_id': receipt['identity']['attempt_id']}
            current['last_normally_verified'] = current['runtime']
            environment.update(snapshot=current, runtime_generation=environment['runtime_generation'] + 1)
            connection.execute(update(self.tables['environment_state']).where(self.tables['environment_state'].c.id == 1).values(
                snapshot=current, runtime_generation=environment['runtime_generation']))
            hosts = self.tables['host_environment']
            host = connection.execute(select(hosts).where(hosts.c.id == host_id).with_for_update()).mappings().one()
            resumed = dict(observation, **runtime_status(environment), normal_ready=True, installation='verified-running',
                           reconciliation='resumed-normal', host_generation=host['generation'] + 1,
                           observed_at=self._now(connection).isoformat())
            connection.execute(update(hosts).where(hosts.c.id == host_id).values(
                generation=resumed['host_generation'], observation=resumed))
            return resumed

    def recovery_resumable(self, observation: dict[str, Any], journal: Journal) -> bool:
        from docassemble.base.managed_qualification import installation_retry_eligible
        receipt = journal.attempt_result
        if (not receipt or not installation_retry_eligible(receipt) or not receipt.get('recovery') or
                not journal.operation or journal.operation.binding != receipt.get('identity') or
                journal.phase != 'recovery_succeeded' or not journal.recovery or journal.recovery.attempt != 1 or
                journal.recovery.result != 'succeeded'):
            return False
        with self.engine.begin() as connection:
            environment = self._environment(connection)
            attempt = connection.execute(select(self.tables['installation_attempt']).where(
                self.tables['installation_attempt'].c.id == journal.operation.id)).mappings().first()
            return bool(not environment['active_attempt_id'] and
                environment['desired_revision_id'] == receipt['identity']['revision_id'] and
                environment['runtime_generation'] == observation['runtime_generation'] and attempt and
                attempt['record'].get('terminal') == receipt)

    def record_recovery_resumed(self, observation: dict[str, Any], receipt: dict[str, Any]) -> None:
        with self.engine.begin() as connection:
            environment = self._environment(connection)
            if (environment['active_attempt_id'] or
                    environment['desired_revision_id'] != receipt['identity']['revision_id'] or
                    environment['runtime_generation'] != observation['runtime_generation']):
                raise PreparationError('recovery resumption superseded')
            recovery = receipt['recovery']
            snapshot = dict(environment['snapshot'], runtime={'kind': 'core-only',
                'inventory': recovery['inventory'], 'core_baseline': recovery['core_baseline'],
                'recovery_id': recovery['id'], 'attempt_id': receipt['identity']['attempt_id']})
            connection.execute(update(self.tables['environment_state']).values(
                snapshot=snapshot, runtime_generation=environment['runtime_generation'] + 1))


def reconcile_published_boot(context: Context) -> dict[str, Any]:
    """Boot reconciles durable work before admission; only core recovery may sync."""
    paths = context.paths
    # Persist detection before DB access, so outages/removal of operator descriptor
    # cannot make the next boot fall back to fixture desired.json authority.
    _atomic_write_json(paths.state_dir / 'published-boot.json', {'format': 1, 'detected_at': utcnow()})
    db = context.db
    url = os.environ.get('DA_PREPARATION_DATABASE_URL') or URL.create(
        'postgresql+psycopg2', username=db.user, password=db.password,
        host=db.host, port=db.port, database=db.name)
    engine = None
    try:
        engine = create_engine(url, connect_args={'connect_timeout': 5})
        prefix = context.config.get('db', {}).get('table prefix') or ''
        observer = BootObservation(engine, lambda *_: False, prefix)
        if (paths.state_dir / 'cold-restore.json').exists():
            from .cold_restore_boot import prepare_cold_boot
            prepare_cold_boot(observer, paths)
            # Bootstrap may have archived the historical receipt. Re-read before
            # reconciliation so it cannot masquerade as local boot authority.
            context.journal = Journal.load(paths.journal_file)
        if os.environ.get('DA_DURABLE_WORK_STORE'):
            from pathlib import Path
            from .installation import Installation, InstallationPoller
            installation = Installation(engine, lambda *_: False, prefix)
            poller = InstallationPoller(installation, paths, Path(os.environ['DA_DURABLE_WORK_STORE']),
                                        Path(os.environ['DA_QUALIFIED_IMAGE_BASELINE']))
            poller.reconcile_interrupted(context.journal)
            context.journal = Journal.load(paths.journal_file)
            poller.report_stopped_boot(context.journal)
        observation = observer.reconcile(installation_host_id(paths), context.journal, context.applied)
        for warning in observation.get('cold_restore', {}).get('warnings', []):
            from .controller import log
            log(warning)
        status = {
            'format': 1, 'mode': MODE_MAINTENANCE, 'updated_at': utcnow(),
            **observation, 'publication': observation,
            'desired_revision': observation['desired_revision_id'],
            'applied_revision': observation['local_applied_revision_id'],
            'runtime_state': 'unknown', 'locked_converged': False,
            'detail': 'published DB intent reconciled; runtime restart/apply/recovery remains fenced',
        }
        write_status(paths, status)
        if os.environ.get('DA_DURABLE_WORK_STORE') and observation.get('reconciliation') != 'legacy-adoption':
            status = resume_published_boot(context, observer, observation, status)
            if not status.get('normal_ready'):
                status = resume_recovery_boot(context, observer, observation, status)
        return status
    except Exception:
        # Do not expose URL, credentials, SQL parameters or arbitrary DB errors.
        from .startup import StartupError
        raise StartupError('published DB boot reconciliation unavailable; maintenance required') from None
    finally:
        if engine is not None:
            engine.dispose()


def resume_published_boot(context: Context, observer: BootObservation, observation: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    """Fresh no-mutation health qualification of a durably completed normal receipt."""
    from pathlib import Path
    from .installation import Installation, InstallationClaim, InstallationPoller
    from .inventory import verify_environment
    from .alembic_probe import check_schema
    from .health import probe_runtime
    from .controller import _start_web, _write_status, _mark_ready, _remove_ready
    from .roles import prepare_roles
    from .admission import Admission
    from .status import MODE_NORMAL

    paths = context.paths
    snapshot = observer.resumable(observation, context.journal, context.applied)
    if snapshot is None:
        return status
    try:
        receipt = context.journal.attempt_result
        assert receipt is not None
        identity = receipt["identity"]
        claim = InstallationClaim(identity["attempt_id"], identity["revision_id"], identity["fence"],
                                  identity["host_id"], identity["runtime_generation"], snapshot,
                                  cold_restore=bool(observation.get('cold_restore')))
        installation = Installation(observer.engine, lambda *_: False, context.config.get('db', {}).get('table prefix') or '')
        poller = InstallationPoller(installation, paths, Path(os.environ["DA_DURABLE_WORK_STORE"]),
                                    Path(os.environ["DA_QUALIFIED_IMAGE_BASELINE"]))
        directory = poller._gates(claim, mutation=False)  # Retaining healthy bytes is not an update.
        material = json.loads((directory / "installed-material.json").read_text())
        if not verify_environment(paths.runtime_venv, receipt["inventory"], material).ok:
            raise PreparationError("installed material invalid")
        compatible = () if snapshot.get('selected_baseline') else tuple(context.manifest.get("compatible_schema_revisions", ()))
        if snapshot.get('schema_mode') == 'unchanged':
            from .rollback_schema import check_rollback_schema
            schema = check_rollback_schema(paths.runtime_venv, context.db, directory)
        else:
            schema = check_schema(paths.runtime_venv, context.db, compatible)
        if not schema.usable or not probe_runtime(paths.runtime_venv, paths.config_file, context.expected_core_versions).ok:
            raise PreparationError("runtime unusable")
        Admission(paths.state_dir).prepare()
        if not _start_web(context, recovery=False):
            raise PreparationError("web unavailable")
        checks = prepare_roles(paths)
        if not all(check.ok for check in checks):
            raise PreparationError("roles unavailable")
        if observation.get('active'):
            # Health is fresh before completing the original claim's lost report.
            installation.report_terminal(receipt)
            observation = dict(observation, runtime_generation=observation['runtime_generation'] + 1)
        resumed = observer.record_resumed(installation_host_id(paths), observation, receipt, snapshot)
        _atomic_write_json(paths.state_dir / "publication-reported.json", identity)
        local = _write_status(context, MODE_NORMAL, role_checks=checks, schema=schema)
        status = dict(local, **resumed, publication=resumed, locked_converged=True, runtime_state="locked")
        write_status(paths, status)
        _mark_ready(paths)
        if not Admission(paths.state_dir).open(consumers=True, expected_mode="preparing"):
            raise PreparationError('resumption admission superseded')
        return status
    except Exception:
        Admission(paths.state_dir).close("published boot health failed; recovery/retry remains fenced")
        _remove_ready(paths)
        installation.invalidate_normal(identity)
        status = dict(status, normal_ready=False, locked_converged=False, publication=observation,
                      verified_running_revision_id=None, runtime_kind='unknown', mode=MODE_MAINTENANCE,
                      detail="published boot health failed; recovery/retry remains fenced")
        write_status(paths, status)
        return status


def resume_recovery_boot(context: Context, observer: BootObservation, observation: dict, status: dict) -> dict:
    """Revalidate retained core and restart restricted web; never restore or run setup."""
    if not observer.recovery_resumable(observation, context.journal):
        return status
    from pathlib import Path
    from .controller import _start_web, _write_status, stop_web
    from .inventory import verify_environment
    from .health import probe_runtime
    from .alembic_probe import check_schema
    from .selected_baseline import load_baselines, selected_directory, core_versions, release_versions
    from .startup import target_id
    from .admission import Admission
    from .status import MODE_RECOVERY
    paths = context.paths
    receipt = context.journal.attempt_result
    if receipt is None:
        return status
    recovery = receipt['recovery']
    try:
        retained = load_baselines(paths)
        selected = retained.get('recovery_baseline', {})
        if selected.get('core_bundle'):
            directory = selected_directory(Path(retained['store']), selected)
            inventory = core_versions(selected)
            baseline = {'image_target': target_id(context.manifest), 'bundle': 'selected-core',
                        'bundle_digest': selected['core_bundle']['digest']}
            versions, compatible = release_versions(selected), ()
        else:
            from .image_baseline import verify_image_recovery_core
            verify_image_recovery_core(paths)
            name = context.bundle_name('core', context.manifest['identity']['core_baseline'])
            directory = paths.bundles_dir / name
            inventory = context.manifest['inventories'][name]['distributions']
            baseline = {'image_target': target_id(context.manifest), 'bundle': name,
                        'bundle_digest': context.manifest['bundles'][name]}
            versions = context.manifest['identity'].get('core_versions', {})
            compatible = tuple(context.manifest.get('compatible_schema_revisions', ()))
        material = (json.loads((directory / 'installed-material.json').read_text()) if selected.get('core_bundle')
                    else context.manifest['inventories'][name]['files'])
        if baseline != recovery['core_baseline'] or inventory != recovery['inventory']:
            raise PreparationError('recovery baseline changed')
        if not verify_environment(paths.runtime_venv, inventory, material).ok:
            raise PreparationError('recovery material invalid')
        if retained.get('schema_mode') == 'unchanged' or (retained.get('migration') and not selected.get('core_bundle')):
            from .rollback_schema import check_rollback_schema
            schema = check_rollback_schema(paths.runtime_venv, context.db, directory)
        else:
            schema = check_schema(paths.runtime_venv, context.db, compatible)
        if not schema.usable or not probe_runtime(paths.runtime_venv, paths.config_file, versions).ok:
            raise PreparationError('recovery health failed')
        if not _start_web(context, recovery=True):
            raise PreparationError('recovery web unavailable')
        observer.record_recovery_resumed(observation, receipt)
        local = _write_status(context, MODE_RECOVERY, schema=schema, recovery_available=True)
        Admission(paths.state_dir).open()
        return local
    except Exception:
        stop_web(paths)
        Admission(paths.state_dir).close('published recovery restart failed')
        return status
