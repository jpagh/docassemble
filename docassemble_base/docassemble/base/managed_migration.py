"""System-origin image replacement using the ordinary immutable publication CAS.

The old deployment is not kept available. The single-container shared-state
executor lock remains the exclusion authority, not an expired worker heartbeat.
"""
from __future__ import annotations

import os
import re
import uuid

from packaging.version import Version, InvalidVersion
from sqlalchemy import select, update

from docassemble.base.managed_preparation import GENERATIONS, PreparationError, digest
from docassemble.base.managed_publication import Publication

SYSTEM_ACTOR = 'system:replacement-image'
POLICY = 'DA_UV_MANAGED=true:deliberate-image-replacement-v1'


def release_version(core):
    try:
        versions = [Version(core['docassemble-' + name]['version']) for name in ('base', 'webapp', 'demo')]
        if len(set(versions)) != 1:
            raise ValueError()
        return str(versions[0])
    except (KeyError, TypeError, ValueError, InvalidVersion):
        raise PreparationError('unreadable coherent release evidence') from None


def schema_direction(snapshot, target, evidence=None, *, revision_id=None):
    """A recovery runtime never supplies release direction or schema authority."""
    prior = release_version(snapshot['core'])
    high = snapshot.get('schema_release')
    if high is not None:
        validate_schema_release(high)
    if evidence is not None:
        validate_schema_release(evidence)
        if high is None or Version(evidence['release']) > Version(high['release']):
            high = evidence
    # Existing selected records predate successful-setup release journaling.
    # Retain their selected baseline as explicit conservative fallback evidence.
    if high is None:
        high = {'release': prior, 'source': 'selected-baseline-fallback',
                'revision_id': revision_id, 'core_digest': digest(snapshot['core']),
                'image_baseline': snapshot.get('image_baseline')}
    floor = Version(high['release'])
    return ('unchanged' if Version(release_version(target['core'])) <= floor else 'upgrade'), high


def validate_schema_release(evidence):
    try:
        Version(evidence['release'])
        if evidence['source'] == 'successful-setup':
            if (not evidence['revision_id'] or not evidence['attempt_id'] or
                    not re.fullmatch('[a-zA-Z0-9_-]+', evidence['database_revision']) or
                    not re.fullmatch('[0-9a-f]{64}', evidence['schema_identity'])):
                raise ValueError()
        elif evidence['source'] != 'selected-baseline-fallback' or not evidence.get('image_baseline'):
            raise ValueError()
    except (KeyError, TypeError, ValueError, InvalidVersion):
        raise PreparationError('unreadable schema release evidence') from None


def migration_status(snapshot):
    record = snapshot.get('migration')
    return ({key: record.get(key) for key in ('state', 'candidate_id', 'system_actor', 'enabling_policy',
        'fingerprint', 'retry', 'failure', 'schema_mode', 'from_release', 'to_release')} if record else None)


class ImageMigration(Publication):
    def __init__(self, engine, prefix='', *, updates_enabled=True):
        super().__init__(engine, lambda *_: False, lambda *_: {'intent_unchanged': True}, prefix)
        self.updates_enabled = updates_enabled

    def _gates(self):
        if (os.environ.get('DA_UV_MANAGED') != 'true' or
                os.environ.get('CONTAINERROLE', 'all').strip(':') != 'all' or
                os.environ.get('DAREADONLYFILESYSTEM') == 'true'):
            raise PreparationError('replacement requires writable managed single all-role deployment')
        if (os.environ.get('DAUPDATEONSTART', 'true') not in ('true', 'initial') or
                os.environ.get('DAALLOWUPDATES', 'true') != 'true' or not self.updates_enabled):
            raise PreparationError('replacement automatic convergence or updates disabled')

    def _authorize(self, connection, actor, operation, candidate=None):
        if actor != SYSTEM_ACTOR or operation != 'approve' or not candidate or candidate['operation'] != 'migration':
            raise PreparationError('replacement publication is system-only')
        self._gates()

    def _fresh(self, environment, candidate, connection=None):
        return (super()._fresh(environment, candidate, connection) and
                environment['snapshot'].get('migration', {}).get('candidate_id') == candidate['id'] and
                environment['snapshot']['migration']['fingerprint'] == candidate['inputs']['migration']['fingerprint'])

    def observe(self, target, *, schema_evidence=None):
        """Capture replacement, not new committed intent. Unchanged failure is terminal.

        Target generation fences old workers immediately; the desired revision,
        declarations, source policy, owners and grants stay unchanged until publish.
        """
        with self.engine.begin() as connection:
            state = self._environment(connection)
            snapshot = dict(state['snapshot'])
            if not state['desired_revision_id']:
                return None
            if snapshot.get('image_baseline') == target['image_baseline']:
                return migration_status(snapshot)
            if state['active_attempt_id']:
                raise PreparationError('replacement cannot abandon active or uncertain installation')
            self._published_revision(connection, state['desired_revision_id'])
            fingerprint = digest({'parent_id': state['desired_revision_id'], 'target': target,
                                  'config': snapshot['config'], 'input_generation': state['input_generation']})
            previous = snapshot.get('migration', {})
            # Input generation is increased by observation itself; compare the
            # recorded post-observation fingerprint on subsequent ordinary boots.
            if previous.get('fingerprint') == fingerprint:
                return migration_status(snapshot)
            mode, high = schema_direction(snapshot, target, schema_evidence, revision_id=state['desired_revision_id'])
            generation = state['input_generation'] + 1
            fingerprint = digest({'parent_id': state['desired_revision_id'], 'target': target,
                                  'config': snapshot['config'], 'input_generation': generation})
            record = {'state': 'observed', 'fingerprint': fingerprint, 'target': target,
                'system_actor': SYSTEM_ACTOR, 'enabling_policy': POLICY, 'retry': 0,
                'schema_mode': mode, 'schema_release': high,
                'from_release': release_version(snapshot['core']), 'to_release': release_version(target['core'])}
            if previous:
                snapshot['migration_history'] = snapshot.get('migration_history', []) + [previous]
            snapshot['migration'] = record
            previous_runtime = snapshot.get('runtime', {})
            if previous_runtime.get('kind') == 'normal':
                snapshot['last_normally_verified'] = previous_runtime
            snapshot['boot_previous_runtime'] = previous_runtime
            snapshot['runtime'] = {'kind': 'unknown', 'inventory': {}, 'reason': 'published-boot-unverified'}
            # Supersede unpublished authority, including active preparation claims.
            candidates = self.tables['candidate']
            for row in connection.execute(select(candidates).where(candidates.c.state.in_(('queued', 'preparing', 'ready')))).mappings():
                connection.execute(update(candidates).where(candidates.c.id == row['id']).values(state='cancelled', fence=row['fence'] + 1))
                self._invalidate(connection, row['id'])
            connection.execute(update(self.tables['environment_state']).where(self.tables['environment_state'].c.id == 1).values(
                snapshot=snapshot, input_generation=generation, target_generation=state['target_generation'] + 1,
                runtime_generation=state['runtime_generation'] + 1))
            return migration_status(snapshot)

    def prepare_observed(self):
        with self.engine.begin() as connection:
            state = self._environment(connection)
            snapshot = dict(state['snapshot'])
            record = dict(snapshot.get('migration', {}))
            if record.get('state') != 'observed':
                return record.get('state')
            try:
                self._gates()
                target = record['target']
                if snapshot['config'] != target['config']:
                    raise PreparationError('replacement source policy differs; explicit operator repair required')
                baseline = dict(snapshot, **{key: target[key] for key in ('core', 'target', 'tool', 'image_baseline')})
                baseline.pop('selected_baseline', None)
                baseline.pop('migration', None)
                baseline.pop('migration_history', None)
                baseline['recovery_baseline'] = {'image_baseline': target['image_baseline'], 'core': target['core']}
                contributions = [dict(row) for row in connection.execute(select(self.tables['requirement_contribution']).where(
                    self.tables['requirement_contribution'].c.active.is_(True)).order_by(self.tables['requirement_contribution'].c.id)).mappings()]
                inputs = {'baseline': baseline, 'parent_id': state['desired_revision_id'],
                    'generations': {key: state[key] for key in GENERATIONS}, 'change_operation': 'migration',
                    'contributions': contributions, 'committed_contributions': contributions,
                    'ownership_effects': {}, 'migration': record}
                if snapshot.get('git_credentials'):
                    inputs['git_credentials'] = snapshot['git_credentials']
                candidate_id = str(uuid.uuid4())
                connection.execute(self.tables['candidate'].insert().values(id=candidate_id,
                    requester_id=SYSTEM_ACTOR, operation='migration', request_digest=record['fingerprint'],
                    parent_id=state['desired_revision_id'], **inputs['generations'], inputs=inputs, state='queued', fence=0))
                record.update(state='queued', candidate_id=candidate_id)
            except PreparationError as error:
                record.update(state='failed', failure=str(error))
            connection.execute(update(self.tables['environment_state']).where(self.tables['environment_state'].c.id == 1).values(
                snapshot=dict(snapshot, migration=record)))
            return record['state']

    def advance(self):
        with self.engine.begin() as connection:
            state = self._environment(connection)
            record = state['snapshot'].get('migration', {})
            if record.get('state') != 'queued':
                return record.get('state')
            candidate = self._candidate(connection, record['candidate_id'])
            self._expire(connection, candidate, self._now(connection))
            phase = candidate['state']
        if phase == 'ready':
            try:
                self._publish(SYSTEM_ACTOR, candidate['id'], candidate['result_digest'], candidate['id'], system=True)
                return 'published'
            except PreparationError as error:
                if str(error) == 'installation busy':
                    return 'busy'
                phase = 'failed'
        if phase in ('failed', 'expired', 'cancelled'):
            with self.engine.begin() as connection:
                current = self._environment(connection)
                if current['snapshot'].get('migration') == record:
                    connection.execute(update(self.tables['environment_state']).where(self.tables['environment_state'].c.id == 1).values(
                        snapshot=dict(current['snapshot'], migration=dict(record, state='failed',
                            failure='replacement preparation/publication failed; repair inputs or explicitly retry'))))
            return 'failed'
        return phase

    def request_retry(self, actor, *, authorize=None):
        with self.engine.begin() as connection:
            state = self._environment(connection)
            self._require_config_admin(connection, actor)
            if authorize:
                authorize(connection, actor, 'prepare', {'requester_id': actor, 'change_operation': 'repair'})
            self._gates()
            record = dict(state['snapshot'].get('migration', {}))
            if record.get('state') != 'failed' or state['active_attempt_id']:
                raise PreparationError('fresh replacement retry requires failed unpublished preparation')
            record.update(state='observed', retry=record['retry'] + 1, retry_requested_by=actor)
            connection.execute(update(self.tables['environment_state']).where(self.tables['environment_state'].c.id == 1).values(
                snapshot=dict(state['snapshot'], migration=record)))
