"""Internal publication protocol; NOT wired to public adapters.

Lock order is EnvironmentState -> candidate -> current authority/projection rows.
Every cooperating authority writer must take the environment lock BEFORE reads
or writes, on the SAME connection/transaction. Redis API-key writers hold an
independent environment-lock transaction across external IO; Redis is not SQL-
atomic. Config and remaining composites are unqualified; production publication
remains fenced. Projection and fresh DB authorizer callbacks are trusted
integration seams; bounded real SQL Package and user-helper integrations are
qualified separately from fixture protocol tests.
No file IO, solve, dispatch, heartbeat release or installation success occurs here.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import uuid
from typing import Callable, Any

from sqlalchemy import Connection, select, update

from docassemble.base.managed_preparation import (
    Preparation, PreparationError, digest, UNRESOLVED_CONFIG_SAVE,
    config_save_unresolved, config_save_status, valid_config_save_actor, valid_config_save_digest)

Projection = Callable[[Connection, str, dict[str, Any]], dict[str, Any]]


class Publication(Preparation):
    """Internal semantic publisher pending complete production writer wiring."""

    def __init__(self, engine, authorize, project: Projection, prefix=''):
        super().__init__(engine, authorize, prefix)
        self.project = project

    @contextmanager
    def authority_write(self):
        """Cooperating DB writer transaction; caller must not commit separately."""
        with self.engine.begin() as connection:
            self._environment(connection)
            yield connection

    def begin_config_save(self, actor: str, write_digest: str) -> str:
        """Commit the unresolved-save marker BEFORE any configuration write.

        The marker is ordering authority for cooperating writers: while it is
        present no configuration-bound candidate may be prepared, approved or
        bound, even if a later observation failed or the writer died. Only a
        serialized save whose observation commits may clear it.
        """
        if not valid_config_save_actor(actor):
            raise PreparationError('configuration save requires an authenticated actor')
        if not valid_config_save_digest(write_digest):
            raise PreparationError('configuration save requires the intended write digest')
        save_id = str(uuid.uuid4())
        with self.authority_write() as connection:
            environment = self._environment(connection)
            snapshot = dict(environment['snapshot'])
            snapshot[UNRESOLVED_CONFIG_SAVE] = {'id': save_id, 'actor': actor,
                'created_at': self._now(connection).isoformat(), 'write_digest': write_digest}
            connection.execute(update(self.tables['environment_state']).where(
                self.tables['environment_state'].c.id == 1).values(snapshot=snapshot))
        return save_id

    def observe_config(self, configuration, *, environ=None, save_id=None) -> dict[str, Any]:
        """Trusted save observer; serialized with publication, never imports intent.

        File/cloud saves are NOT DB-atomic. Any exception must be reported by the
        caller as an unsuccessful observation, not a successful managed save.
        A serialized save passes its `save_id`; the marker is cleared only in the
        same transaction as the successful observation, so a failure leaves it
        committed and every configuration-bound path stays refused.
        """
        from docassemble.base.managed_config import dependency_snapshot, OBSERVED_CONFIG_ID
        with self.authority_write() as connection:
            environment = self._environment(connection)
            state = environment['snapshot']
            if config_save_unresolved(environment):
                marker = config_save_status(environment)
                if (not isinstance(save_id, str) or not save_id or marker['id'] != save_id or
                        any(marker[key] is None for key in ('id', 'actor', 'created_at', 'write_digest'))):
                    raise PreparationError('configuration save unresolved; complete the serialized save before observing')
            elif save_id is not None:
                raise PreparationError('configuration save marker missing; serialized save ordering violated')
            snapshot = dependency_snapshot(configuration, environ=environ,
                                           image_config=state['config'])
            table = self.tables['observed_config']
            previous = connection.execute(select(table).where(
                table.c.id == OBSERVED_CONFIG_ID).with_for_update()).mappings().first()
            generation = previous['generation'] if previous else 0
            if not previous or previous['snapshot'] != snapshot:
                generation += 1
                if previous:
                    connection.execute(update(table).where(table.c.id == OBSERVED_CONFIG_ID).values(
                        generation=generation, snapshot=snapshot))
                else:
                    connection.execute(table.insert().values(id=OBSERVED_CONFIG_ID,
                        generation=generation, snapshot=snapshot))
            if save_id is not None:
                cleared = dict(state)
                cleared.pop(UNRESOLVED_CONFIG_SAVE, None)
                connection.execute(update(self.tables['environment_state']).where(
                    self.tables['environment_state'].c.id == 1).values(snapshot=cleared))
        return {'generation': generation, 'digest': snapshot['digest']}

    @staticmethod
    def _artifact_material_matches(candidate, row, reference):
        stores = candidate['inputs'].get('material_stores')
        if stores is not None:
            root = stores.get('artifact')
            if not isinstance(root, str) or not Path(root).is_absolute() or row['path'] != str(Path(root) / reference):
                return False
            if root == stores.get('ingress_artifact'):
                return False
        else:
            # Legacy single-store direct-source producers have no ingestion pin.
            # Uploaded candidates always record a destination (or unresolved pin).
            if candidate['inputs'].get('artifact') or os.environ.get('DA_MANAGED_WEB_SECRET_ROOT'):
                return False
            if not row['path'].endswith('/' + reference):
                return False
        worker = os.environ.get('DA_EXECUTOR_ARTIFACT_ROOT')
        if worker and row['path'] != str(Path(worker) / reference):
            return False
        ingress = os.environ.get('DA_MANAGED_ARTIFACT_ROOT')
        if (os.environ.get('DA_MANAGED_WEB_SECRET_ROOT') and ingress and
                row['path'] == str(Path(ingress) / reference)):
            return False
        return True

    def approve(self, actor: str, candidate_id: str, result_digest: str,
                idempotency_key: str, *, acknowledge_database_unchanged: bool = False) -> dict[str, str]:
        return self._publish(actor, candidate_id, result_digest, idempotency_key,
            acknowledge_database_unchanged=acknowledge_database_unchanged)

    def _publish(self, actor, candidate_id, result_digest, idempotency_key, *,
                 acknowledge_database_unchanged=False, system=False):
        """Shared immutable CAS/slot transaction; system authority is not human approval."""
        if not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 255:
            raise PreparationError('idempotency key required')
        body = {'candidate_id': candidate_id, 'result_digest': result_digest}
        if acknowledge_database_unchanged:
            body['acknowledge_database_unchanged'] = True
        body_digest = digest(body)
        with self.engine.begin() as connection:
            environment = self._environment(connection)
            records = self.tables['publication_result']
            previous = connection.execute(select(records).where(
                records.c.actor_id == actor, records.c.key == idempotency_key)).mappings().first()
            if previous:
                candidate = self._candidate(connection, previous['candidate_id'])
                self._authorize(connection, actor, 'approve', candidate)
                if previous['request_digest'] != body_digest:
                    raise PreparationError('idempotency key body mismatch')
                return self._result(previous)
            candidate = self._candidate(connection, candidate_id)
            if (candidate['operation'] in ('adoption', 'migration')) != system:
                raise PreparationError('system publication requires automatic adoption/replacement authority')
            self._authorize(connection, actor, 'approve', candidate)
            if candidate['requester_id'] != actor:
                raise PreparationError('only requester may approve')
            if candidate['operation'] == 'rollback' and acknowledge_database_unchanged is not True:
                raise PreparationError('explicit acknowledgement that the database is unchanged required')
            now = self._now(connection)
            if candidate['state'] == 'expired' or (candidate['expires_at'] is not None and candidate['expires_at'] <= now):
                raise PreparationError('candidate expired')
            if candidate['state'] != 'ready':
                raise PreparationError('candidate not ready')
            if candidate['result_digest'] != result_digest or result_digest != digest({
                    'inputs': candidate['inputs'], 'result': candidate['result']}):
                raise PreparationError('readiness digest mismatch')
            if not self._fresh(environment, candidate, connection):
                raise PreparationError('stale candidate')
            if environment['active_attempt_id'] is not None:
                raise PreparationError('installation busy')
            # Trusted callback rechecks name/source/current ownership, then performs
            # Package/current-grant projection on this connection. Any exception
            # rolls back ALL intent, including the callback's writes.
            contributions = self.tables['requirement_contribution']
            current_contributions = [dict(row) for row in connection.execute(select(contributions).where(
                contributions.c.active.is_(True)).order_by(contributions.c.id)).mappings()]
            committed = candidate['inputs'].get('committed_contributions', candidate['inputs']['contributions'])
            if current_contributions != committed:
                raise PreparationError('committed contributions changed')
            projection = self.project(connection, actor, candidate)
            if candidate['operation'] in ('add', 'update', 'remove'):
                owner = candidate['inputs']['ownership_effects']['owner_id']
                if projection['owner_id'] != owner:
                    raise PreparationError('Package projection ownership mismatch')
                identities = self.tables['package_identity']
                requirement = candidate['inputs']['requirement']
                identity = connection.execute(select(identities).where(
                    identities.c.normalized_name == requirement['normalized_name'])).mappings().first()
                effects = candidate['result']['ownership_effects']
                if effects != candidate['inputs']['ownership_effects'] or (
                        candidate['operation'] != 'update' and owner != actor):
                    raise PreparationError('ownership effects changed')
                expected_owner = None if effects.get('assignment', False) else owner
                if bool(identity) != effects['existing'] or (identity and identity['owner_id'] != expected_owner):
                    raise PreparationError('ownership effects changed')
                if identity:
                    identity_id = identity['id']
                    if identity['legacy_package_id'] != projection['legacy_package_id']:
                        raise PreparationError('Package projection identity mismatch')
                    if effects.get('assignment', False):
                        connection.execute(update(identities).where(identities.c.id == identity_id).values(owner_id=actor))
                else:
                    identity_id = str(uuid.uuid4())
                    connection.execute(identities.insert().values(id=identity_id,
                        normalized_name=requirement['normalized_name'], display_name=requirement['name'],
                        owner_id=actor, legacy_package_id=projection['legacy_package_id']))
                if candidate['operation'] == 'add':
                    artifact = candidate['inputs'].get('artifact')
                    if artifact:
                        materials = self.tables['candidate_material']
                        retained = connection.execute(select(materials).where(
                            materials.c.candidate_id == candidate_id, materials.c.usable.is_(True),
                            materials.c.deleted.is_(False)).with_for_update()).mappings().all()
                        retained = [row for row in retained if self._artifact_material_matches(
                            candidate, row, artifact['digest'])]
                        if len(retained) != 1:
                            raise PreparationError('retained distribution artifact unavailable')
                        connection.execute(update(materials).where(materials.c.id == retained[0]['id']).values(committed=True))
                        replaced = candidate['inputs']['replace_artifact_contributions']
                        if replaced:
                            connection.execute(update(contributions).where(contributions.c.id.in_(replaced),
                                contributions.c.identity_id == identity_id,
                                contributions.c.origin == 'interactive').values(active=False))
                    replaced_git = candidate['inputs'].get('replace_git_contributions', [])
                    if replaced_git:
                        connection.execute(update(contributions).where(contributions.c.id.in_(replaced_git),
                            contributions.c.identity_id == identity_id,
                            contributions.c.origin == 'interactive').values(active=False))
                    replaced_direct = candidate['inputs'].get('replace_direct_contributions', [])
                    if replaced_direct:
                        connection.execute(update(contributions).where(contributions.c.id.in_(replaced_direct),
                            contributions.c.identity_id == identity_id,
                            contributions.c.origin == 'interactive').values(active=False))
                    connection.execute(contributions.insert().values(id=str(uuid.uuid4()), identity_id=identity_id,
                        origin='interactive', declaration=requirement['declaration'], active=True,
                        artifact_ref=artifact['digest'] if artifact else None))
                elif candidate['operation'] == 'remove':
                    removed = connection.execute(update(contributions).where(
                        contributions.c.id == candidate['inputs']['remove_contribution_id'],
                        contributions.c.identity_id == identity_id, contributions.c.origin == 'interactive',
                        contributions.c.active.is_(True)).values(active=False))
                    if removed.rowcount != 1:
                        raise PreparationError('removal contribution changed')
            if candidate['operation'] == 'rollback':
                self._restore_contributions(connection, actor, candidate)
            revision_id, attempt_id = str(uuid.uuid4()), str(uuid.uuid4())
            snapshot = dict(environment['snapshot'])
            snapshot.update(desired_inventory=candidate['result']['bundle']['inventory'],
                publication={'candidate_id': candidate_id, 'approver_id': actor,
                             'result_digest': result_digest, 'inputs': candidate['inputs'],
                             'result': candidate['result'], 'projection': projection},
                contributions=[dict(row) for row in connection.execute(select(contributions).where(
                    contributions.c.active.is_(True)).order_by(contributions.c.id)).mappings()])
            if candidate['operation'] == 'adoption':
                from docassemble.base.managed_adoption import POLICY, retained_adoption_record
                snapshot['publication'].pop('approver_id')
                snapshot['publication'].update(system_actor=actor, enabling_policy=POLICY)
                snapshot.update(config=candidate['inputs']['baseline']['config'],
                    observed_config=candidate['inputs']['config_import']['snapshot'],
                    observed_config_generation=candidate['inputs']['config_import']['generation'],
                    adoption=dict(retained_adoption_record(snapshot['adoption']), state='published',
                        disclosure=candidate['inputs']['adoption']['disclosure'],
                        ownerless=candidate['inputs']['adoption']['ownerless'],
                        selected_git=candidate['result'].get('git_sources', {})))
                if 'adoption_history' in snapshot:
                    snapshot['adoption_history'] = [retained_adoption_record(record) for record in snapshot['adoption_history']]
            if candidate['operation'] == 'migration':
                from docassemble.base.managed_migration import POLICY
                target = candidate['inputs']['migration']['target']
                snapshot['publication'].pop('approver_id')
                snapshot['publication'].update(system_actor=actor, enabling_policy=POLICY)
                snapshot.update(**{key: target[key] for key in ('core', 'target', 'tool', 'image_baseline')},
                    selected_baseline=None,
                    recovery_baseline={'image_baseline': target['image_baseline'], 'core': target['core']},
                    schema_mode=candidate['inputs']['migration']['schema_mode'],
                    schema_release=candidate['inputs']['migration']['schema_release'],
                    migration=dict(snapshot['migration'], state='published'))
            if candidate['operation'] == 'rollback':
                historical = candidate['inputs']['rollback']['snapshot']
                snapshot['recovery_baseline'] = snapshot.get('selected_baseline') or {
                    'image_baseline': snapshot.get('image_baseline'), 'core': snapshot['core']}
                snapshot.update(core=historical['core'], selected_baseline=historical.get('selected_baseline'),
                                schema_mode='unchanged')
                # Only the managed dependency snapshot is restored, never YAML
                # or unrelated live application settings.
                for key in ('config', 'observed_config_generation', 'observed_config'):
                    if key in historical:
                        snapshot[key] = historical[key]
                    else:
                        snapshot.pop(key, None)
            if candidate['operation'] == 'upgrade':
                snapshot['schema_mode'] = 'upgrade'
                if snapshot.get('schema_release'):
                    from docassemble.base.managed_migration import validate_schema_release
                    from packaging.version import Version
                    evidence = snapshot['schema_release']
                    validate_schema_release(evidence)
                    if Version(candidate['result']['release']['webapp']) <= Version(evidence['release']):
                        snapshot['schema_mode'] = 'unchanged'
                snapshot['schema_policy'] = {}
                # Selecting desired core is atomic with publication, not evidence
                # that it is safe to recover to it. Only normal success promotes it.
                snapshot.setdefault('recovery_baseline', snapshot.get('selected_baseline') or {
                    'image_baseline': snapshot.get('image_baseline'), 'core': snapshot['core']})
                snapshot['selected_baseline'] = {key: candidate['result'][key]
                                                 for key in ('core_bundle', 'release')}
                snapshot['core'] = candidate['result']['core_bundle']['inventory']
            if 'git_sources' in candidate['result']:
                snapshot['git_sources'] = candidate['result']['git_sources']
                # Promote only the credentials required by the published sources;
                # retained references stay usable for future unattended access.
                references = candidate['inputs'].get('git_credentials', {})
                retained = {name: references[name] for name in snapshot['git_sources'] if name in references}
                materials = self.tables['candidate_material']
                for reference in retained.values():
                    connection.execute(update(materials).where(
                        materials.c.id == reference, materials.c.candidate_id == candidate_id,
                        materials.c.usable.is_(True), materials.c.deleted.is_(False)).values(committed=True))
                if retained:
                    snapshot['git_credentials'] = retained
                else:
                    snapshot.pop('git_credentials', None)
            if 'direct_sources' in candidate['result'] and candidate['operation'] != 'rollback':
                snapshot['direct_sources'] = candidate['result']['direct_sources']
                # Promote the exact bytes of every published direct source; a
                # retained shared/committed object is never unlinked by cleanup.
                materials = self.tables['candidate_material']
                retained = connection.execute(select(materials).where(
                    materials.c.candidate_id == candidate_id, materials.c.usable.is_(True),
                    materials.c.deleted.is_(False)).with_for_update()).mappings().all()
                for source in snapshot['direct_sources'].values():
                    matches = [row for row in retained if self._artifact_material_matches(
                        candidate, row, source['digest'])]
                    if len(matches) != 1:
                        raise PreparationError('retained direct artifact unavailable')
                    connection.execute(update(materials).where(materials.c.id == matches[0]['id']).values(committed=True))
            if candidate['operation'] == 'rollback':
                for key in ('git_sources', 'direct_sources'):
                    snapshot[key] = candidate['inputs']['rollback']['snapshot'].get(key, {})
                # Credentials are current authority, never restored historical accounts/secrets.
                snapshot['git_credentials'] = {name: ref for name, ref in environment['snapshot'].get('git_credentials', {}).items()
                                               if name in snapshot['git_sources']}
            if candidate['operation'] == 'config':
                from docassemble.base.managed_config import config_import_policy
                bound = candidate['inputs']['config_import']
                snapshot.update(config=config_import_policy(bound['snapshot'], snapshot['config']),
                    observed_config_generation=bound['generation'], observed_config=bound['snapshot'])
            from docassemble.base.managed_checkpoint import package_projection
            snapshot['package_projection'] = package_projection(connection, self.prefix)
            connection.execute(self.tables['revision'].insert().values(id=revision_id,
                parent_id=candidate['parent_id'], snapshot=snapshot))
            connection.execute(self.tables['installation_attempt'].insert().values(id=attempt_id,
                revision_id=revision_id, record={'actor_id': actor, 'reason': ('automatic-' + candidate['operation']) if system else 'approval',
                    'phase': 'pending', 'fence': 0, 'created_at': now.isoformat()}))
            connection.execute(update(self.tables['environment_state']).where(
                self.tables['environment_state'].c.id == 1).values(desired_revision_id=revision_id,
                publication_generation=environment['publication_generation'] + 1,
                active_attempt_id=attempt_id, snapshot=snapshot))
            connection.execute(update(self.tables['candidate']).where(
                self.tables['candidate'].c.id == candidate_id).values(state='published'))
            row = dict(actor_id=actor, key=idempotency_key, request_digest=body_digest,
                       candidate_id=candidate_id, revision_id=revision_id, attempt_id=attempt_id)
            connection.execute(records.insert().values(**row))
            return self._result(row)

    def _restore_contributions(self, connection, actor, candidate):
        contributions = self.tables['requirement_contribution']
        identities = self.tables['package_identity']
        effects = candidate['inputs']['ownership_effects']['identities']
        for identity_id, effect in effects.items():
            identity = connection.execute(select(identities).where(identities.c.id == identity_id)).mappings().first()
            expected_owner = None if effect['assignment'] else effect['owner_id']
            if not identity or identity['owner_id'] != expected_owner:
                raise PreparationError('rollback ownership changed; fresh preview required')
            if effect['assignment']:
                connection.execute(update(identities).where(identities.c.id == identity_id).values(owner_id=actor))
        connection.execute(update(contributions).values(active=False))
        for row in candidate['inputs']['contributions']:
            previous = connection.execute(select(contributions).where(contributions.c.id == row['id'])).mappings().first()
            values = {key: row[key] for key in ('id', 'identity_id', 'origin', 'declaration', 'artifact_ref')}
            if not previous:
                raise PreparationError('retained historical contribution changed')
            if row['origin'] == 'config':
                from docassemble.base.managed_preparation import parse_registry_requirement
                requirement = parse_registry_requirement(row['declaration'])
                identity = connection.execute(select(identities).where(
                    identities.c.id == row['identity_id'])).mappings().first()
                if (not identity or identity['normalized_name'] != requirement.normalized_name or
                        row['artifact_ref'] is not None or
                        any(previous[key] != values[key] for key in ('id', 'identity_id', 'origin', 'artifact_ref'))):
                    raise PreparationError('retained historical configuration contribution changed')
                connection.execute(update(contributions).where(contributions.c.id == row['id']).values(
                    declaration=requirement.declaration, active=True))
            else:
                if any(previous[key] != value for key, value in values.items()):
                    raise PreparationError('retained historical contribution changed')
                connection.execute(update(contributions).where(contributions.c.id == row['id']).values(active=True))

    def retry(self, actor: str, revision_id: str, idempotency_key: str) -> dict[str, str]:
        """Internal only: reuse approved bytes, never solve or release a slot.

        A free slot must come from qualified terminal reporting (05c), not this
        method. Retry authority is distinct from permission to view the revision.
        """
        if not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 255:
            raise PreparationError('idempotency key required')
        body_digest = digest({'revision_id': revision_id})
        with self.engine.begin() as connection:
            environment = self._environment(connection)
            records = self.tables['retry_result']
            previous = connection.execute(select(records).where(
                records.c.actor_id == actor, records.c.key == idempotency_key)).mappings().first()
            revision, candidate = self._published_revision(
                connection, previous['revision_id'] if previous else revision_id)
            self._authorize(connection, actor, 'retry', candidate)
            if previous:
                if previous['request_digest'] != body_digest:
                    raise PreparationError('idempotency key body mismatch')
                return self._retry_result(previous)
            if environment['desired_revision_id'] != revision_id:
                raise PreparationError('retry requires current desired revision')
            if any(environment['snapshot'][key] != revision['snapshot'][key]
                   for key in ('target', 'tool', 'core')):
                raise PreparationError('retry target or baseline changed')
            if environment['active_attempt_id'] is not None:
                raise PreparationError('installation busy')
            self._check_retry(connection, environment, revision, candidate)
            attempt_id = str(uuid.uuid4())
            connection.execute(self.tables['installation_attempt'].insert().values(
                id=attempt_id, revision_id=revision_id, record={'actor_id': actor,
                    'reason': 'retry', 'phase': 'pending', 'fence': 0,
                    'created_at': self._now(connection).isoformat()}))
            connection.execute(update(self.tables['environment_state']).where(
                self.tables['environment_state'].c.id == 1).values(active_attempt_id=attempt_id))
            row = dict(actor_id=actor, key=idempotency_key, request_digest=body_digest,
                       revision_id=revision_id, attempt_id=attempt_id)
            connection.execute(records.insert().values(**row))
            return self._retry_result(row)

    def _check_retry(self, connection, environment, revision, candidate):
        """Internal protocol users supply their own qualified execution boundary."""

    @staticmethod
    def _retry_result(row):
        return {'revision_id': row['revision_id'], 'attempt_id': row['attempt_id'],
                'state': 'published', 'installation': 'pending'}

    @staticmethod
    def _result(row):
        return {'candidate_id': row['candidate_id'], 'revision_id': row['revision_id'],
                'attempt_id': row['attempt_id'], 'state': 'published', 'installation': 'pending'}
