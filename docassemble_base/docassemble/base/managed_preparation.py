"""Internal PostgreSQL preparation protocol, independent of Flask and live runtime.

Authority is supplied by a trusted DB authorizer, never browser actor fields.
Authenticated adapters use this same authority; installation dispatch is fenced. Preparation-only polling
uses the same serialized claim protocol. Lock order: environment,
candidate, material. A lease permits preparation reclaim, NOT proof of exit.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import json
import os
import stat
from pathlib import Path
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from sqlalchemy import Engine, Connection, select, update, func, table, column

from docassemble.base.managed_records import preparation_tables


UNRESOLVED_CONFIG_SAVE = 'config_save_unresolved'


def config_save_unresolved(environment: dict[str, Any]) -> bool:
    """Marker presence is the fence, including false, null and malformed values."""
    return UNRESOLVED_CONFIG_SAVE in (environment.get('snapshot') or {})


def valid_config_save_actor(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 255 and value.isprintable()


def valid_config_save_digest(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def config_save_status(environment: dict[str, Any]) -> dict[str, Any] | None:
    """Project only validated save identity; malformed markers remain unresolved."""
    if not config_save_unresolved(environment):
        return None
    marker = environment['snapshot'][UNRESOLVED_CONFIG_SAVE]
    marker = marker if isinstance(marker, dict) else {}
    identity = marker.get('id')
    try:
        valid_id = isinstance(identity, str) and str(uuid.UUID(identity)) == identity
    except (ValueError, AttributeError):
        valid_id = False
    created_at = marker.get('created_at')
    try:
        timestamp = datetime.fromisoformat(created_at) if isinstance(created_at, str) else None
        valid_timestamp = timestamp is not None and timestamp.utcoffset() is not None
    except (ValueError, OverflowError):
        valid_timestamp = False
    return {'unresolved': True, 'id': identity if valid_id else None,
            'actor': marker.get('actor') if valid_config_save_actor(marker.get('actor')) else None,
            'created_at': created_at if valid_timestamp else None,
            'write_digest': marker.get('write_digest') if valid_config_save_digest(marker.get('write_digest')) else None}


INSTALLATION_FAILURE_REASONS = {
    'gates': {'managed-disabled', 'updates-disabled', 'baseline-invalid', 'target-mismatch',
              'unsupported-target', 'tool-mismatch', 'bundle-invalid', 'gate-check-failed',
              'schema-clearance-required', 'schema-incompatible', 'schema-unreadable'},
    'context': {'context-load-failed'},
    'drain': {'drain-aborted', 'drain-failed'},
    'runtime-exclusion': {'runtime-exclusion-failed'},
    'checkpoint': {'capture-failed'},
    'sync': {'sync-failed'},
    'inventory': {'inventory-mismatch'},
    'normal': {'schema', 'probe', 'web', 'roles', 'qualification-failed'},
}


def runtime_status(environment: dict[str, Any]) -> dict[str, Any]:
    """Redacted durable observation, never a substitute for live readiness checks."""
    snapshot = environment['snapshot']
    runtime = snapshot.get('runtime', {})
    kind = runtime.get('kind')
    if kind not in ('normal', 'core-only', 'unknown', 'development-override'):
        kind = 'unknown'
    previous = snapshot.get('boot_previous_runtime', {})
    return {
        'desired_revision_id': environment['desired_revision_id'],
        'runtime_kind': kind,
        'runtime_generation': environment['runtime_generation'],
        'verified_running_revision_id': runtime.get('revision_id') if kind == 'normal' else None,
        'last_normally_verified_revision_id': snapshot.get('last_normally_verified', {}).get('revision_id'),
        'recovery_core': runtime.get('core_baseline') if kind == 'core-only' else None,
        'recovery_id': runtime.get('recovery_id') if kind == 'core-only' else None,
        'last_recovery_id': snapshot.get('last_recovery', {}).get('id'),
        'last_recovery_result': snapshot.get('last_recovery', {}).get('result'),
        'materialized_revision_id': runtime.get('materialized_revision_id') if kind == 'unknown' else None,
        'historical_recovery_core': previous.get('core_baseline') if previous.get('kind') == 'core-only' else None,
        'historical_runtime_kind': previous.get('kind'),
        **({'cold_restore': {key: snapshot['cold_restore'].get(key) for key in (
            'incarnation', 'state', 'incomplete_backup', 'unrestorable_revision_ids', 'warnings')}}
           if snapshot.get('cold_restore') else {}),
    }


def installation_failure(terminal: dict[str, Any]) -> dict[str, str] | None:
    failure = terminal.get('failure')
    if (isinstance(failure, dict) and set(failure) == {'stage', 'reason'} and
            isinstance(failure.get('stage'), str) and isinstance(failure.get('reason'), str) and
            failure['reason'] in INSTALLATION_FAILURE_REASONS.get(failure['stage'], set())):
        return failure
    return None


class PreparationError(RuntimeError):
    """Redacted protocol rejection; callers must not substitute legacy writes."""


@dataclass(frozen=True)
class RegistryRequirement:
    declaration: str
    name: str
    normalized_name: str
    extras: tuple[str, ...]
    specifiers: str
    marker: str | None


def parse_registry_requirement(declaration: str) -> RegistryRequirement:
    if not isinstance(declaration, str) or not declaration or len(declaration) > 4096:
        raise PreparationError('submit one PEP 508 registry requirement')
    if any(char in declaration for char in ('\n', '\r', '\x00')):
        raise PreparationError('submit one PEP 508 registry requirement')
    try:
        parsed = Requirement(declaration)
    except InvalidRequirement:
        raise PreparationError('unsupported registry requirement syntax') from None
    if parsed.url is not None:
        raise PreparationError('direct sources are unsupported; submit a registry requirement')
    return RegistryRequirement(declaration, parsed.name, canonicalize_name(parsed.name),
                               tuple(sorted(parsed.extras)), str(parsed.specifier),
                               str(parsed.marker) if parsed.marker else None)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class Claim:
    candidate_id: str
    worker_id: str
    fence: int
    inputs: dict[str, Any]


Authorizer = Callable[[Connection, str, str, dict[str, Any] | None], bool]
GENERATIONS = ('publication_generation', 'input_generation', 'target_generation', 'runtime_generation')


class Preparation:
    def __init__(self, engine: Engine, authorize: Authorizer, prefix: str = ''):
        if engine.dialect.name != 'postgresql':
            raise PreparationError('preparation requires PostgreSQL')
        self.engine = engine
        self.prefix = prefix
        self.authorize = authorize
        self.tables = preparation_tables(prefix)

    def _authorize(self, connection: Connection, actor: str, operation: str,
                   candidate: dict[str, Any] | None = None) -> None:
        if not self.authorize(connection, actor, operation, candidate):
            raise PreparationError('forbidden')
        if candidate and candidate.get('operation') in ('config', 'rollback') and operation in ('approve', 'retry'):
            self._require_config_admin(connection, actor)
            if candidate['requester_id'] != actor and (operation == 'approve' or candidate['operation'] == 'config'):
                raise PreparationError('only requester may approve')

    def _require_config_admin(self, connection, actor):
        try:
            user_id = int(actor)
        except (ValueError, TypeError):
            raise PreparationError('forbidden') from None
        roles = table(self.prefix + 'role', column('id'), column('name'))
        membership = table(self.prefix + 'user_roles', column('user_id'), column('role_id'))
        if not connection.scalar(select(roles.c.id).join(membership, membership.c.role_id == roles.c.id).where(
                membership.c.user_id == user_id, roles.c.name == 'admin')):
            raise PreparationError('forbidden')

    def _environment(self, connection: Connection) -> dict[str, Any]:
        table = self.tables['environment_state']
        row = connection.execute(select(table).where(table.c.id == 1).with_for_update()).mappings().first()
        if not row or not row['managed']:
            raise PreparationError('no explicitly managed baseline')
        return dict(row)

    @staticmethod
    def _require_config_resolved(environment: dict[str, Any]) -> None:
        if config_save_unresolved(environment):
            raise PreparationError('configuration save unresolved; complete the serialized save before preparing')

    def _candidate(self, connection: Connection, candidate_id: str) -> dict[str, Any]:
        table = self.tables['candidate']
        row = connection.execute(select(table).where(table.c.id == candidate_id).with_for_update()).mappings().first()
        if not row:
            raise PreparationError('candidate not found')
        return dict(row)

    @staticmethod
    def _now(connection: Connection):
        return connection.scalar(select(func.clock_timestamp()))

    def _invalidate(self, connection: Connection, candidate_id: str) -> None:
        table = self.tables['candidate_material']
        connection.execute(update(table).where(table.c.candidate_id == candidate_id,
            table.c.committed.is_(False), table.c.shared.is_(False)).values(usable=False))

    def _expire(self, connection: Connection, candidate: dict[str, Any], now) -> None:
        if candidate['state'] == 'ready' and candidate['expires_at'] <= now:
            candidate['state'] = 'expired'
            connection.execute(update(self.tables['candidate']).where(
                self.tables['candidate'].c.id == candidate['id']).values(state='expired'))
            self._invalidate(connection, candidate['id'])

    def prepare_zip(self, actor: str, upload_path: Path, idempotency_key: str, *,
                    artifact_root: Path | None = None, declaration: str | None = None,
                    pre_change_checkpoint: bool = False) -> str:
        """Trusted upload adapter seam; upload access must be checked by its caller."""
        from docassemble.base.managed_artifacts import (
            artifact_root as checked_root, open_regular, file_digest, distribution_identity, retain_artifact)
        configured = artifact_root or os.environ.get('DA_MANAGED_ARTIFACT_ROOT')
        if not configured:
            raise PreparationError('explicit artifact store configuration required')
        root = checked_root(Path(configured))
        with open_regular(Path(upload_path)) as source:
            identity = distribution_identity(source)
            reference = file_digest(source)
            requirement = parse_registry_requirement(declaration or identity['normalized_name'] + '==' + identity['version'])
            if requirement.normalized_name != identity['normalized_name']:
                raise PreparationError('explicit distribution name differs from archive metadata')
            if not Requirement(requirement.declaration).specifier.contains(identity['version'], prereleases=True):
                raise PreparationError('archive version does not satisfy declaration')
            artifact = dict(identity, digest=reference)
            return self.prepare(actor, requirement.declaration, idempotency_key,
                _artifact=artifact, _retain=lambda: retain_artifact(root, reference, source),
                pre_change_checkpoint=pre_change_checkpoint)

    def prepare(self, actor: str, declaration: str | None, idempotency_key: str,
                *, operation: str = 'add', restart: bool = True, assign_owner: bool = False,
                contribution_id: str | None = None, _artifact: dict[str, Any] | None = None,
                _retain: Callable[[], Path] | None = None, _secret_root: Path | None = None,
                release_selection: dict[str, Any] | None = None,
                rollback_revision: str | None = None, assign_owners: list[str] | None = None,
                pre_change_checkpoint: bool = False) -> str:
        if not isinstance(pre_change_checkpoint, bool):
            raise PreparationError('checkpoint selection must be boolean')
        if pre_change_checkpoint and operation not in ('add', 'update', 'upgrade'):
            raise PreparationError('checkpoint requires install or upgrade')
        if operation == 'config':
            return self._prepare_config(actor, declaration, idempotency_key, restart, assign_owner, contribution_id)
        if operation == 'rollback':
            if declaration is not None or assign_owner is not False or contribution_id is not None or restart is not True:
                raise PreparationError('rollback accepts only a retained revision and explicit owner assignments')
            return self._prepare_rollback(actor, rollback_revision, assign_owners, idempotency_key)
        if rollback_revision is not None or assign_owners is not None:
            raise PreparationError('rollback inputs require rollback operation')
        if operation == 'upgrade':
            if declaration is not None or assign_owner is not False or contribution_id is not None or restart is not True:
                raise PreparationError('upgrade accepts no declaration or ownership edits')
            return self._prepare_upgrade(actor, release_selection, idempotency_key, pre_change_checkpoint)
        if operation == 'repair':
            if declaration is not None or assign_owner is not False or contribution_id is not None or restart is not True:
                raise PreparationError('repair accepts no declaration or ownership edits')
            return self._prepare_repair(actor, idempotency_key)
        if operation not in ('add', 'update', 'remove') or restart is not True:
            raise PreparationError('unsupported managed operation')
        if declaration is None:
            raise PreparationError('submit one PEP 508 registry requirement')
        from docassemble.base.managed_git import git_credential_material, parse_supported_requirement, parse_git_requirement
        from docassemble.base.managed_direct import parse_direct_requirement
        requirement = parse_supported_requirement(declaration)
        parsed_url = Requirement(declaration).url
        git_source = git_credential = direct_source = None
        if parsed_url:
            if parsed_url.startswith('git+'):
                _, git_source, git_credential = parse_git_requirement(declaration)
            else:
                _, direct_source, _ = parse_direct_requirement(declaration)
        if (git_source or direct_source) and operation != 'add':
            raise PreparationError('update/remove accepts a distribution name, not source edits')
        if operation != 'add' and (requirement.extras or requirement.specifiers or requirement.marker or assign_owner):
            raise PreparationError('update/remove accepts a distribution name, not requirement or ownership edits')
        if (operation == 'remove') != (isinstance(contribution_id, str) and bool(contribution_id)) or (
                operation != 'remove' and contribution_id is not None):
            raise PreparationError('removal requires one explicit contribution id')
        if not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 255:
            raise PreparationError('idempotency key required')
        if not isinstance(assign_owner, bool):
            raise PreparationError('invalid owner assignment')
        # Public request equality covers only the redacted declaration. Credential
        # equality is separately bound in a private candidate column.
        body = {'operation': operation, 'declaration': requirement.declaration, 'restart': restart}
        if pre_change_checkpoint:
            body['pre_change_checkpoint'] = True
        if assign_owner:
            body['assign_owner'] = True
        if contribution_id is not None:
            body['contribution_id'] = contribution_id
        if _artifact is not None:
            body['artifact'] = _artifact
        body_digest = digest(body)
        staged_secret = None
        with self.engine.begin() as connection:
            environment = self._environment(connection)
            self._require_config_resolved(environment)
            self._authorize(connection, actor, 'prepare',
                            {'requester_id': actor, 'requirement': requirement.__dict__, 'assign_owner': assign_owner,
                             'change_operation': operation, 'artifact': _artifact, 'git_source': git_source,
                             'direct_source': direct_source})
            dedup = self.tables['request_deduplication']
            previous = connection.execute(select(dedup).where(dedup.c.actor_id == actor,
                dedup.c.operation == operation, dedup.c.key == idempotency_key)).mappings().first()
            if previous:
                if previous['request_digest'] != body_digest:
                    raise PreparationError('idempotency key body mismatch')
                candidate = self._candidate(connection, previous['candidate_id'])
                self._authorize(connection, actor, 'status', candidate)
                binding = candidate.get('credential_request_binding')
                material = (git_credential_material(git_source['repository'], git_credential)
                            if git_credential is not None else b'')
                if binding is None:
                    matches = git_credential is None
                else:
                    try:
                        key = bytes.fromhex(binding['key'])
                        fingerprint = hmac.new(key, (b'present:' + material if git_credential is not None
                                                   else b'absent'), hashlib.sha256).hexdigest()
                        matches = len(key) == 32 and hmac.compare_digest(binding['fingerprint'], fingerprint)
                    except (KeyError, TypeError, ValueError):
                        matches = False
                if not matches:
                    raise PreparationError('idempotency key body mismatch')
                return candidate['id']
            credential_binding = None
            if git_source:
                key = secrets.token_bytes(32)
                material = (git_credential_material(git_source['repository'], git_credential)
                            if git_credential is not None else b'')
                credential_binding = {'key': key.hex(), 'fingerprint': hmac.new(
                    key, b'present:' + material if git_credential is not None else b'absent',
                    hashlib.sha256).hexdigest()}
            identities = self.tables['package_identity']
            identity = connection.execute(select(identities).where(
                identities.c.normalized_name == requirement.normalized_name)).mappings().first()
            if assign_owner and (not identity or identity['owner_id'] is not None):
                raise PreparationError('assignment requires an existing ownerless identity')
            if identity and identity['owner_id'] != actor and not assign_owner and operation != 'update':
                raise PreparationError('existing identity requires current owner; ownerless identity requires explicit assignment')
            if operation != 'add' and (not identity or identity['owner_id'] is None):
                raise PreparationError('update/remove requires an owned managed identity')
            contributions = self.tables['requirement_contribution']
            committed = [dict(row) for row in connection.execute(select(contributions).where(
                contributions.c.active.is_(True)).order_by(contributions.c.id)).mappings()]
            if operation != 'add' and not any(row['identity_id'] == identity['id'] for row in committed):
                raise PreparationError('update/remove requires an active contribution')
            if operation == 'remove' and not any(row['id'] == contribution_id and
                    row['identity_id'] == identity['id'] and row['origin'] == 'interactive' for row in committed):
                raise PreparationError('only the selected interactive contribution may be removed')
            snapshot = environment['snapshot']
            if operation != 'add' and requirement.normalized_name in snapshot['core']:
                raise PreparationError('ordinary operations cannot change exact core')
            if not all(key in snapshot for key in ('core', 'target', 'tool', 'config', 'desired_inventory', 'runtime')):
                raise PreparationError('incomplete managed baseline')
            inputs = {'baseline': snapshot, 'parent_id': environment['desired_revision_id'],
                      'generations': {key: environment[key] for key in GENERATIONS},
                      'contributions': committed,
                      'requirement': requirement.__dict__,
                      'ownership_effects': {'normalized_name': requirement.normalized_name,
                                            'owner_id': identity['owner_id'] if identity else actor, 'existing': bool(identity)}}
            if pre_change_checkpoint:
                inputs['pre_change_checkpoint'] = True
            if _artifact is not None:
                if requirement.normalized_name in snapshot['core']:
                    raise PreparationError('ordinary operations cannot change exact core')
                inputs['artifact'] = _artifact
                # Re-upload replaces only the owner's interactive artifact declaration;
                # independent registry/config contributions are still solved together.
                inputs['replace_artifact_contributions'] = [row['id'] for row in committed if
                    identity and row['identity_id'] == identity['id'] and row['origin'] == 'interactive' and row['artifact_ref']]
            # Opaque references are carried, never the secret; preserved recorded
            # sources reuse their committed reference while any revision keeps it.
            references = {name: reference for name, reference in snapshot.get('git_credentials', {}).items()
                          if name in snapshot.get('git_sources', {})}
            if git_source:
                if requirement.normalized_name in snapshot['core']:
                    raise PreparationError('ordinary operations cannot change exact core')
                inputs['git_source'] = git_source
                inputs['replace_git_contributions'] = [row['id'] for row in committed if
                    identity and row['identity_id'] == identity['id'] and row['origin'] == 'interactive' and
                    Requirement(row['declaration']).url is not None]
                recorded = snapshot.get('git_sources', {}).get(requirement.normalized_name)
                if recorded and recorded.get('repository') != git_source['repository']:
                    references.pop(requirement.normalized_name, None)
                if git_credential is not None:
                    configured = _secret_root or os.environ.get('DA_MANAGED_SECRET_ROOT')
                    if not configured:
                        raise PreparationError('explicit candidate secret store configuration required')
                    secret_root = self._secret_root(configured)
                    reference = str(uuid.uuid4())
                    references[requirement.normalized_name] = reference
                    staged_secret = (secret_root, reference,
                                     git_credential_material(git_source['repository'], git_credential))
            if direct_source:
                if requirement.normalized_name in snapshot['core']:
                    raise PreparationError('ordinary operations cannot change exact core')
                inputs['direct_source'] = direct_source
                inputs['replace_direct_contributions'] = [row['id'] for row in committed if
                    identity and row['identity_id'] == identity['id'] and row['origin'] == 'interactive' and
                    Requirement(row['declaration']).url is not None and
                    not Requirement(row['declaration']).url.startswith('git+')]
            if operation != 'add':
                inputs['change_operation'] = operation
            if contribution_id is not None:
                inputs['remove_contribution_id'] = contribution_id
            from docassemble.base.managed_git import source_declarations
            surviving_git = source_declarations(inputs)
            references = {name: reference for name, reference in references.items() if name in surviving_git}
            if references:
                inputs['git_credentials'] = references
            if assign_owner:
                inputs['ownership_effects'].update(owner_id=actor, assignment=True, previous_owner_id=None)
            self._capture_approval_authority(connection, actor, inputs)
            candidate_id = str(uuid.uuid4())
            connection.execute(self.tables['candidate'].insert().values(
                id=candidate_id, requester_id=actor, operation=operation, request_digest=body_digest,
                credential_request_binding=credential_binding, parent_id=environment['desired_revision_id'], **{key: environment[key] for key in GENERATIONS},
                inputs=inputs, state='queued', fence=0))
            if _artifact is not None:
                path = _retain()
                self._register_material(connection, candidate_id, str(uuid.uuid4()), str(path))
                # HTTP ingress cannot attest its own destination. Standalone
                # operator-owned stores retain their single-store protocol.
                import pwd
                split = (os.getuid() == pwd.getpwnam('www-data').pw_uid or
                         bool(os.environ.get('DA_MANAGED_WEB_SECRET_ROOT')))
                inputs['material_stores'] = {'artifact': None if split else str(path.parent)}
                connection.execute(update(self.tables['candidate']).where(
                    self.tables['candidate'].c.id == candidate_id).values(inputs=inputs))
            if staged_secret is not None:
                self._register_material(connection, candidate_id, staged_secret[1],
                                        str(staged_secret[0] / staged_secret[1]))
            connection.execute(dedup.insert().values(actor_id=actor, operation=operation,
                key=idempotency_key, request_digest=body_digest, candidate_id=candidate_id))
        if staged_secret is not None:
            self._stage_queued_secret(candidate_id, *staged_secret)
        return candidate_id

    def _prepare_config(self, actor, declaration, idempotency_key, restart, assign_owner, contribution_id):
        from docassemble.base.managed_config import OBSERVED_CONFIG_ID, config_import_policy
        if declaration is not None or assign_owner is not False or contribution_id is not None or restart is not True:
            raise PreparationError('config import accepts no declaration or ownership edits')
        if not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 255:
            raise PreparationError('idempotency key required')
        body_digest = digest({'operation': 'config', 'declaration': None, 'restart': True})
        with self.engine.begin() as connection:
            environment = self._environment(connection)
            self._require_config_resolved(environment)
            self._authorize(connection, actor, 'prepare',
                            {'requester_id': actor, 'change_operation': 'config'})
            self._require_config_admin(connection, actor)
            dedup = self.tables['request_deduplication']
            previous = connection.execute(select(dedup).where(dedup.c.actor_id == actor,
                dedup.c.operation == 'config', dedup.c.key == idempotency_key)).mappings().first()
            if previous:
                if previous['request_digest'] != body_digest:
                    raise PreparationError('idempotency key body mismatch')
                candidate = self._candidate(connection, previous['candidate_id'])
                self._authorize(connection, actor, 'status', candidate)
                return candidate['id']
            observed_table = self.tables['observed_config']
            observed = connection.execute(select(observed_table).where(
                observed_table.c.id == OBSERVED_CONFIG_ID)).mappings().first()
            if not observed:
                raise PreparationError('config import requires an observed configuration')
            observed_snapshot = observed['snapshot']
            snapshot = environment['snapshot']
            config_import_policy(observed_snapshot, snapshot['config'])
            if not all(key in snapshot for key in ('core', 'target', 'tool', 'config', 'desired_inventory', 'runtime')):
                raise PreparationError('incomplete managed baseline')
            contributions = self.tables['requirement_contribution']
            committed = [dict(row) for row in connection.execute(select(contributions).where(
                contributions.c.active.is_(True)).order_by(contributions.c.id)).mappings()]
            combined = [row for row in committed if row['origin'] != 'config']
            identities = self.tables['package_identity']
            identity_ids = {row['normalized_name']: row['id'] for row in
                            connection.execute(select(identities)).mappings()}
            for observed_requirement in observed_snapshot['requirements']:
                requirement = parse_registry_requirement(observed_requirement['declaration'])
                name = requirement.normalized_name
                if name in snapshot['core']:
                    raise PreparationError('config import cannot change exact core')
                for row in combined:
                    try:
                        parsed = Requirement(row['declaration'])
                    except InvalidRequirement:
                        raise PreparationError('config import has unsupported committed requirements') from None
                    if canonicalize_name(parsed.name) == name:
                        if row['artifact_ref'] is not None:
                            raise PreparationError('conflicting configuration sources require explicit resolution')
                        if parsed.url is not None:
                            if parsed.url.startswith('git+'):
                                from docassemble.base.managed_git import parse_git_requirement
                                try:
                                    _, declared_source, _ = parse_git_requirement(row['declaration'])
                                except PreparationError:
                                    raise PreparationError('conflicting configuration sources require explicit resolution') from None
                                recorded_source = snapshot.get('git_sources', {}).get(name)
                                if not recorded_source or any(recorded_source.get(key) != value for key, value in declared_source.items()):
                                    raise PreparationError('conflicting configuration sources require explicit resolution')
                            else:
                                from docassemble.base.managed_direct import parse_direct_requirement, redact_direct_url
                                try:
                                    _, declared_source, _ = parse_direct_requirement(row['declaration'])
                                except PreparationError:
                                    raise PreparationError('conflicting configuration sources require explicit resolution') from None
                                recorded_source = snapshot.get('direct_sources', {}).get(name)
                                if not recorded_source or redact_direct_url(recorded_source['url']) != declared_source['url']:
                                    raise PreparationError('conflicting configuration sources require explicit resolution')
                combined.append({'id': str(uuid.uuid5(uuid.NAMESPACE_URL, 'docassemble:config:' + name)),
                    'identity_id': identity_ids.get(name) or str(uuid.uuid5(uuid.NAMESPACE_URL, 'docassemble:identity:' + name)),
                    'origin': 'config', 'declaration': requirement.declaration, 'active': True, 'artifact_ref': None,
                    'provenance': observed_requirement})
            inputs = {'baseline': snapshot, 'parent_id': environment['desired_revision_id'],
                      'generations': {key: environment[key] for key in GENERATIONS},
                      'contributions': combined, 'committed_contributions': committed,
                      'change_operation': 'config', 'ownership_effects': {},
                      'config_import': {'generation': observed['generation'], 'snapshot': observed_snapshot,
                                        'digest': observed_snapshot['digest']}}
            references = {name: reference for name, reference in snapshot.get('git_credentials', {}).items()
                          if name in snapshot.get('git_sources', {})}
            if references:
                inputs['git_credentials'] = references
            self._capture_approval_authority(connection, actor, inputs)
            candidate_id = str(uuid.uuid4())
            connection.execute(self.tables['candidate'].insert().values(
                id=candidate_id, requester_id=actor, operation='config', request_digest=body_digest,
                parent_id=environment['desired_revision_id'], **{key: environment[key] for key in GENERATIONS},
                inputs=inputs, state='queued', fence=0))
            connection.execute(dedup.insert().values(actor_id=actor, operation='config', key=idempotency_key,
                                                   request_digest=body_digest, candidate_id=candidate_id))
            return candidate_id

    def _prepare_repair(self, actor, idempotency_key):
        """Prepare a true re-solve of the committed desired intent after a failure.

        A repair is based on desired intent (every active contribution), not an
        invented installed baseline, and only while the runtime is unverified
        recovery state. It produces a new candidate/revision/bundle; the existing
        exact-revision retry remains the separate no-solve path. Recovery never
        rewrites desired intent, and the candidate is bound to the current
        runtime generation so a changed observation invalidates approval.
        """
        if not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 255:
            raise PreparationError('idempotency key required')
        body_digest = digest({'operation': 'repair', 'declaration': None, 'restart': True})
        with self.engine.begin() as connection:
            environment = self._environment(connection)
            self._require_config_resolved(environment)
            self._authorize(connection, actor, 'prepare',
                            {'requester_id': actor, 'change_operation': 'repair'})
            dedup = self.tables['request_deduplication']
            previous = connection.execute(select(dedup).where(dedup.c.actor_id == actor,
                dedup.c.operation == 'repair', dedup.c.key == idempotency_key)).mappings().first()
            if previous:
                if previous['request_digest'] != body_digest:
                    raise PreparationError('idempotency key body mismatch')
                candidate = self._candidate(connection, previous['candidate_id'])
                self._authorize(connection, actor, 'status', candidate)
                return candidate['id']
            snapshot = environment['snapshot']
            if not all(key in snapshot for key in ('core', 'target', 'tool', 'config', 'desired_inventory', 'runtime')):
                raise PreparationError('incomplete managed baseline')
            if snapshot['runtime']['kind'] == 'normal':
                raise PreparationError('repair requires an unverified recovery runtime')
            contributions = self.tables['requirement_contribution']
            committed = [dict(row) for row in connection.execute(select(contributions).where(
                contributions.c.active.is_(True)).order_by(contributions.c.id)).mappings()]
            inputs = {'baseline': snapshot, 'parent_id': environment['desired_revision_id'],
                      'generations': {key: environment[key] for key in GENERATIONS},
                      'contributions': committed, 'committed_contributions': committed,
                      'change_operation': 'repair', 'ownership_effects': {}}
            references = {name: reference for name, reference in snapshot.get('git_credentials', {}).items()
                          if name in snapshot.get('git_sources', {})}
            if references:
                inputs['git_credentials'] = references
            self._capture_approval_authority(connection, actor, inputs)
            candidate_id = str(uuid.uuid4())
            connection.execute(self.tables['candidate'].insert().values(
                id=candidate_id, requester_id=actor, operation='repair', request_digest=body_digest,
                parent_id=environment['desired_revision_id'], **{key: environment[key] for key in GENERATIONS},
                inputs=inputs, state='queued', fence=0))
            connection.execute(dedup.insert().values(actor_id=actor, operation='repair', key=idempotency_key,
                                                   request_digest=body_digest, candidate_id=candidate_id))
            return candidate_id

    def _prepare_upgrade(self, actor, release_selection, idempotency_key, pre_change_checkpoint=False):
        """Prepare a fresh coherent core solve plus the current user requirements.

        Preview-only: upstream release resolution and solving happen in the
        executor builder (no DB transaction, no live runtime/schema mutation). An
        explicit version not newer than the current core is rejected before work.
        """
        from docassemble.base.managed_release import WEBAPP, parse_release_selection, reject_downgrade
        selection = parse_release_selection(release_selection)
        if not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 255:
            raise PreparationError('idempotency key required')
        body_digest = digest({'operation': 'upgrade', 'release': selection.to_dict(),
                              **({'pre_change_checkpoint': True} if pre_change_checkpoint else {})})
        with self.engine.begin() as connection:
            environment = self._environment(connection)
            self._require_config_resolved(environment)
            self._authorize(connection, actor, 'prepare',
                            {'requester_id': actor, 'change_operation': 'upgrade'})
            dedup = self.tables['request_deduplication']
            previous = connection.execute(select(dedup).where(dedup.c.actor_id == actor,
                dedup.c.operation == 'upgrade', dedup.c.key == idempotency_key)).mappings().first()
            if previous:
                if previous['request_digest'] != body_digest:
                    raise PreparationError('idempotency key body mismatch')
                candidate = self._candidate(connection, previous['candidate_id'])
                self._authorize(connection, actor, 'status', candidate)
                return candidate['id']
            snapshot = environment['snapshot']
            if not all(key in snapshot for key in ('core', 'target', 'tool', 'config', 'desired_inventory', 'runtime')):
                raise PreparationError('incomplete managed baseline')
            if snapshot['runtime']['kind'] != 'normal':
                raise PreparationError('upgrade requires a normally verified runtime')
            if selection.version is not None:
                from packaging.version import Version
                if selection.mode == 'exact' and Version(selection.version).is_prerelease:
                    raise PreparationError('prereleases require explicit prerelease selection')
                reject_downgrade({WEBAPP: selection.version}, snapshot['core'])
            contributions = self.tables['requirement_contribution']
            committed = [dict(row) for row in connection.execute(select(contributions).where(
                contributions.c.active.is_(True)).order_by(contributions.c.id)).mappings()]
            inputs = {'baseline': snapshot, 'parent_id': environment['desired_revision_id'],
                      'generations': {key: environment[key] for key in GENERATIONS},
                      'contributions': committed, 'committed_contributions': committed,
                      'change_operation': 'upgrade', 'ownership_effects': {},
                      'release_selection': selection.to_dict()}
            if pre_change_checkpoint:
                inputs['pre_change_checkpoint'] = True
            references = {name: reference for name, reference in snapshot.get('git_credentials', {}).items()
                          if name in snapshot.get('git_sources', {})}
            if references:
                inputs['git_credentials'] = references
            self._capture_approval_authority(connection, actor, inputs)
            candidate_id = str(uuid.uuid4())
            connection.execute(self.tables['candidate'].insert().values(
                id=candidate_id, requester_id=actor, operation='upgrade', request_digest=body_digest,
                parent_id=environment['desired_revision_id'], **{key: environment[key] for key in GENERATIONS},
                inputs=inputs, state='queued', fence=0))
            connection.execute(dedup.insert().values(actor_id=actor, operation='upgrade', key=idempotency_key,
                                                   request_digest=body_digest, candidate_id=candidate_id))
            return candidate_id

    def _prepare_rollback(self, actor, revision_id, assign_owners, idempotency_key):
        if not isinstance(revision_id, str) or not revision_id:
            raise PreparationError('retained revision required')
        assignments = [] if assign_owners is None else assign_owners
        if (not isinstance(assignments, list) or not all(isinstance(name, str) for name in assignments)
                or len(set(assignments)) != len(assignments)):
            raise PreparationError('explicit owner assignments required')
        if not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 255:
            raise PreparationError('idempotency key required')
        body_digest = digest({'operation': 'rollback', 'revision_id': revision_id, 'assign_owners': sorted(assignments)})
        with self.engine.begin() as connection:
            environment = self._environment(connection)
            self._require_config_resolved(environment)
            self._authorize(connection, actor, 'prepare', {'requester_id': actor, 'change_operation': 'rollback'})
            self._require_config_admin(connection, actor)
            dedup = self.tables['request_deduplication']
            previous = connection.execute(select(dedup).where(dedup.c.actor_id == actor,
                dedup.c.operation == 'rollback', dedup.c.key == idempotency_key)).mappings().first()
            if previous:
                if previous['request_digest'] != body_digest:
                    raise PreparationError('idempotency key body mismatch')
                candidate = self._candidate(connection, previous['candidate_id'])
                self._authorize(connection, actor, 'status', candidate)
                return candidate['id']
            revision = connection.execute(select(self.tables['revision']).where(
                self.tables['revision'].c.id == revision_id)).mappings().first()
            if not revision or revision_id == environment['desired_revision_id']:
                raise PreparationError('eligible historical revision required')
            historical = revision['snapshot']
            baseline = environment['snapshot']
            if (any(historical.get(key) != baseline.get(key) for key in ('target', 'tool', 'image_baseline'))
                    or not historical.get('publication', {}).get('result', {}).get('bundle')
                    or not historical.get('core') or 'contributions' not in historical):
                raise PreparationError('historical target or retained material unavailable')
            contributions = self.tables['requirement_contribution']
            committed = [dict(row) for row in connection.execute(select(contributions).where(
                contributions.c.active.is_(True)).order_by(contributions.c.id)).mappings()]
            identities = {row['id']: row for row in connection.execute(select(self.tables['package_identity'])).mappings()}
            effects = {}
            ownerless = set()
            for row in historical['contributions']:
                if row['origin'] != 'interactive':
                    continue
                identity = identities.get(row['identity_id'])
                if not identity:
                    raise PreparationError('historical identity unavailable; never fabricate owners')
                assignment = identity['owner_id'] is None
                if assignment:
                    ownerless.add(identity['normalized_name'])
                effects[identity['id']] = {'owner_id': actor if assignment else identity['owner_id'],
                                           'assignment': assignment, 'name': identity['normalized_name']}
            if set(assignments) != ownerless:
                raise PreparationError('explicit assignment of restored ownerless interactive intent required')
            inputs = {'baseline': baseline, 'parent_id': environment['desired_revision_id'],
                      'generations': {key: environment[key] for key in GENERATIONS},
                      'contributions': historical['contributions'], 'committed_contributions': committed,
                      'change_operation': 'rollback', 'ownership_effects': {'identities': effects},
                      'rollback': {'revision_id': revision_id, 'snapshot': historical}}
            self._capture_approval_authority(connection, actor, inputs)
            candidate_id = str(uuid.uuid4())
            connection.execute(self.tables['candidate'].insert().values(
                id=candidate_id, requester_id=actor, operation='rollback', request_digest=body_digest,
                parent_id=environment['desired_revision_id'], **{key: environment[key] for key in GENERATIONS},
                inputs=inputs, state='queued', fence=0))
            connection.execute(dedup.insert().values(actor_id=actor, operation='rollback', key=idempotency_key,
                                                   request_digest=body_digest, candidate_id=candidate_id))
            return candidate_id

    def _capture_approval_authority(self, connection, actor, inputs):
        """Preview-only authority does not enable publication."""

    def claim(self, candidate_id: str, *, lease_seconds: int = 30) -> Claim:
        if not 1 <= lease_seconds <= 300:
            raise PreparationError('invalid preparation lease')
        with self.engine.begin() as connection:
            self._environment(connection)
            candidate = self._candidate(connection, candidate_id)
            now = self._now(connection)
            if candidate['state'] not in ('queued', 'preparing'):
                raise PreparationError('candidate cannot be claimed')
            if candidate['state'] == 'preparing' and candidate['claim_until'] > now:
                raise PreparationError('preparation busy')
            return self._claim(connection, candidate, now, lease_seconds)

    def claim_next(self, *, lease_seconds: int = 30) -> Claim | None:
        """Atomically discover queued or DB-time expired preparation work only."""
        if not 1 <= lease_seconds <= 300:
            raise PreparationError('invalid preparation lease')
        with self.engine.begin() as connection:
            self._environment(connection)
            table = self.tables['candidate']
            now = self._now(connection)
            row = connection.execute(select(table).where(
                (table.c.state == 'queued') |
                ((table.c.state == 'preparing') & (table.c.claim_until <= now))
            ).order_by(table.c.id).limit(1).with_for_update()).mappings().first()
            return self._claim(connection, dict(row), now, lease_seconds) if row else None

    def _claim(self, connection: Connection, candidate: dict[str, Any], now,
               lease_seconds: int) -> Claim:
        candidate_id = candidate['id']
        fence = candidate['fence'] + 1
        worker_id = str(uuid.uuid4())
        connection.execute(update(self.tables['candidate']).where(
            self.tables['candidate'].c.id == candidate_id).values(
            state='preparing', fence=fence, claim_until=now + timedelta(seconds=lease_seconds)))
        connection.execute(self.tables['preparation_worker'].insert().values(
            id=worker_id, candidate_id=candidate_id, fence=fence, exited=False))
        return Claim(candidate_id, worker_id, fence, candidate['inputs'])

    def _owns_claim(self, connection: Connection, candidate: dict[str, Any], claim: Claim, now) -> bool:
        workers = self.tables['preparation_worker']
        worker = connection.execute(select(workers).where(workers.c.id == claim.worker_id)).mappings().first()
        return bool(worker and not worker['exited'] and worker['candidate_id'] == candidate['id']
                    and worker['fence'] == claim.fence == candidate['fence']
                    and candidate['state'] == 'preparing' and candidate['claim_until'] > now)

    def _fresh(self, environment: dict[str, Any], candidate: dict[str, Any], connection=None) -> bool:
        # An unresolved configuration save is ordering authority: refuse every
        # configuration-bound candidate until a serialized save observation commits.
        if config_save_unresolved(environment):
            return False
        fresh = (environment['desired_revision_id'] == candidate['parent_id'] and
                 all(environment[key] == candidate[key] for key in GENERATIONS))
        if candidate['operation'] == 'config':
            from docassemble.base.managed_config import OBSERVED_CONFIG_ID
            if connection is None:
                return False
            observed = self.tables['observed_config']
            row = connection.execute(select(observed).where(observed.c.id == OBSERVED_CONFIG_ID)).mappings().first()
            bound = candidate['inputs']['config_import']
            fresh = fresh and bool(row and row['generation'] == bound['generation'] and row['snapshot'] == bound['snapshot'])
        return fresh

    def publish_ready(self, claim: Claim, result: dict[str, Any]) -> bool:
        """Accept a complete worker attestation, never solve/build or touch runtime.

        05a2 must verify durable bytes before calling this internal protocol.
        No public adapter can currently submit results or approve candidates.
        """
        self._validate_result(claim.inputs, result)
        result_digest = digest({'inputs': claim.inputs, 'result': result})
        with self.engine.begin() as connection:
            environment = self._environment(connection)
            candidate = self._candidate(connection, claim.candidate_id)
            now = self._now(connection)
            if not self._owns_claim(connection, candidate, claim, now):
                return False
            if claim.inputs != candidate['inputs']:
                raise PreparationError('claim inputs mismatch')
            if not self._fresh(environment, candidate, connection):
                connection.execute(update(self.tables['candidate']).where(
                    self.tables['candidate'].c.id == candidate['id']).values(state='failed'))
                self._invalidate(connection, candidate['id'])
                return False
            connection.execute(update(self.tables['candidate']).where(
                self.tables['candidate'].c.id == candidate['id']).values(state='ready',
                result=result, result_digest=result_digest, ready_at=now,
                expires_at=now + timedelta(minutes=5)))
            return True

    @staticmethod
    def config_diff(inputs: dict[str, Any]) -> dict[str, Any]:
        """Config changes or unchanged repair intent; never expose index URLs.

        A private policy is previewed as the declared deterministic index ordering
        and its explicit bindings, never cross-index newest-version selection.
        Explicit TLS exceptions stay visible as insecure policy.
        """
        repair = inputs.get('change_operation') == 'repair'
        before = [row for row in inputs['committed_contributions'] if repair or row['origin'] == 'config']
        after = [row for row in inputs['contributions'] if repair or row['origin'] == 'config']
        from docassemble.base.managed_config import config_import_policy
        policy = (inputs['baseline']['config'] if repair else
                  config_import_policy(inputs['config_import']['snapshot'], inputs['baseline']['config']))
        source: dict[str, Any] = {'changed': inputs['baseline']['config'] != policy,
                                  'supported_public_registry': 'index_url' in policy}
        if 'private_indexes' in policy:
            source.update(
                private_index_order=list(policy['private_indexes']),
                private_bindings={name: policy['private_bindings'][name]
                                  for name in sorted(policy['private_bindings'])},
                cross_index_newest_version=False,
                insecure_tls_exceptions=list(policy.get('private_tls_exceptions', [])))
        return {'requirements': {'before': [row['declaration'] for row in before],
                                 'after': [row['declaration'] for row in after]},
                'source_policy': source}

    @staticmethod
    def ready_warnings(inputs: dict[str, Any]) -> list[str]:
        warnings = [] if inputs['baseline']['runtime']['kind'] == 'normal' else ['runtime-not-normally-verified']
        config = inputs['baseline'].get('config', {})
        if inputs.get('change_operation') == 'config':
            from docassemble.base.managed_config import config_import_policy
            config = config_import_policy(inputs['config_import']['snapshot'], config)
        if inputs.get('change_operation') == 'adoption':
            warnings.append('legacy-dependency-versions-not-reconstructable')
            warnings.extend('ownerless:' + name for name in sorted(inputs['adoption']['ownerless']))
            for name, source in sorted(inputs['baseline'].get('git_sources', {}).items()):
                warnings.append('adopted-git:' + name + ':' + source['resolved_sha'])
        for label in config.get('private_tls_exceptions', []) or []:
            if not re.fullmatch('[a-z][a-z0-9_]*', label):
                raise PreparationError('invalid private TLS exception')
            warnings.append('private-insecure-tls:' + label)
        bindings = config.get('private_bindings', {})
        for name, label in sorted(bindings.items()):
            if not re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*', name) or not re.fullmatch('[a-z][a-z0-9_]*', label):
                raise PreparationError('invalid private source binding')
            warnings.append('private-source:' + name + ':' + label)
        return warnings

    @staticmethod
    def _validate_result(inputs: dict[str, Any], result: dict[str, Any]) -> None:
        if inputs.get('change_operation') == 'rollback':
            from docassemble.base.managed_rollback import validate_result
            validate_result(inputs, result)
            return
        try:
            expected = {'bundle', 'desired_diff', 'runtime_diff', 'warnings', 'validation', 'ownership_effects'}
            upgrade = inputs.get('change_operation') == 'upgrade'
            if upgrade:
                expected |= {'core_bundle', 'release', 'risk_disclosure'}
                from docassemble.base.managed_release import BASE, DEMO, WEBAPP, upgrade_risk_disclosure
            if inputs.get('change_operation') == 'config':
                expected.add('config_diff')
                if result['config_diff'] != Preparation.config_diff(inputs):
                    raise ValueError()
            from docassemble.base.managed_git import source_declarations, validate_git_sources
            if inputs['baseline'].get('git_sources') or source_declarations(inputs):
                expected.add('git_sources')
                validate_git_sources(inputs, result['git_sources'])
            from docassemble.base.managed_direct import direct_declarations, validate_direct_sources
            if inputs['baseline'].get('direct_sources') or direct_declarations(inputs):
                expected.add('direct_sources')
                validate_direct_sources(inputs, result['direct_sources'])
            if set(result) != expected:
                raise ValueError()
            bundle = result['bundle']
            for candidate in ([result['core_bundle']] if upgrade else []) + [bundle]:
                if set(candidate) != {'digest', 'project_digest', 'lock_digest', 'inventory', 'artifacts'}:
                    raise ValueError()
                for key in ('digest', 'project_digest', 'lock_digest'):
                    if not isinstance(candidate[key], str) or not re.fullmatch('[0-9a-f]{64}', candidate[key]):
                        raise ValueError()
                if not isinstance(candidate['artifacts'], list) or not all(
                    isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) for value in candidate['artifacts']):
                    raise ValueError()
                if not isinstance(candidate['inventory'], dict) or not all(
                    isinstance(name, str) and name == canonicalize_name(name) and
                    re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*', name) and
                    isinstance(value, dict) and set(value) == {'version', 'source_digest'} and
                    isinstance(value['version'], str) and re.fullmatch('[a-zA-Z0-9.!+_-]+', value['version']) and
                    isinstance(value['source_digest'], str) and re.fullmatch('[0-9a-f]{64}', value['source_digest'])
                    for name, value in candidate['inventory'].items()):
                    raise ValueError()
            inventory = bundle['inventory']
            if upgrade:
                release = result['release']
                if set(release) != {'selection', 'resolved', 'webapp', 'base', 'demo', 'core_bundle'}:
                    raise ValueError()
                if (not isinstance(release['selection'], dict) or set(release['selection']) != {'mode', 'version'} or
                        release['resolved'] != release['webapp'] or release['webapp'] != release['base'] or
                        release['webapp'] != release['demo'] or
                        release['core_bundle'] != result['core_bundle']['digest']):
                    raise ValueError()
                coherent = {WEBAPP: release['webapp'], BASE: release['base'], DEMO: release['demo']}
                core_inventory = result['core_bundle']['inventory']
                if any(core_inventory.get(name, {}).get('version') != coherent[name] for name in coherent):
                    raise ValueError()
                if any(inventory.get(name) != value for name, value in core_inventory.items()):
                    raise ValueError()
            elif any(inventory.get(name) != value for name, value in inputs['baseline']['core'].items()):
                raise ValueError()
            if inputs.get('artifact'):
                artifact = inputs['artifact']
                selected = inventory.get(artifact['normalized_name'])
                marker = Requirement(inputs['requirement']['declaration']).marker
                required = marker is None or marker.evaluate()
                if ((required or selected is not None) and selected != {
                        'version': artifact['version'], 'source_digest': artifact['digest']}) or artifact['digest'] not in bundle['artifacts']:
                    raise ValueError()
            for name, source in result.get('git_sources', {}).items():
                if name in inventory and inventory[name]['source_digest'] != digest(source):
                    raise ValueError()
                declarations = [row['declaration'] for row in inputs['contributions'] if
                    row['id'] != inputs.get('remove_contribution_id') and
                    row['id'] not in inputs.get('replace_git_contributions', [])]
                if inputs.get('change_operation', 'add') == 'add':
                    declarations.append(inputs['requirement']['declaration'])
                if name not in inventory and any(canonicalize_name(Requirement(value).name) == name and
                        (Requirement(value).marker is None or Requirement(value).marker.evaluate()) for value in declarations):
                    raise ValueError()
            for name, source in result.get('direct_sources', {}).items():
                if name in inventory and (inventory[name]['source_digest'] != source['digest'] or
                                          inventory[name]['version'] != source['version']):
                    raise ValueError()
                if source['digest'] not in bundle['artifacts']:
                    raise ValueError()
                declarations = [row['declaration'] for row in inputs['contributions'] if
                    row['id'] != inputs.get('remove_contribution_id') and
                    row['id'] not in inputs.get('replace_git_contributions', []) and
                    row['id'] not in inputs.get('replace_artifact_contributions', []) and
                    row['id'] not in inputs.get('replace_direct_contributions', [])]
                if inputs.get('change_operation', 'add') == 'add' and inputs.get('requirement'):
                    declarations.append(inputs['requirement']['declaration'])
                if name not in inventory and any(canonicalize_name(Requirement(value).name) == name and
                        (Requirement(value).marker is None or Requirement(value).marker.evaluate()) for value in declarations):
                    raise ValueError()
            desired = inputs['baseline']['desired_inventory']
            runtime = inputs['baseline']['runtime']
            def diff(before):
                return {name: {'before': before.get(name), 'after': inventory.get(name)}
                        for name in sorted(set(before) | set(inventory)) if before.get(name) != inventory.get(name)}
            if result['desired_diff'] != diff(desired):
                raise ValueError()
            known = runtime['kind'] == 'normal'
            if result['runtime_diff'] != (diff(runtime['inventory']) if known else None):
                raise ValueError()
            if result['warnings'] != Preparation.ready_warnings(inputs):
                raise ValueError()
            if result['ownership_effects'] != inputs['ownership_effects']:
                raise ValueError()
            if upgrade:
                if result['risk_disclosure'] != upgrade_risk_disclosure():
                    raise ValueError()
                if result['validation'] != {'core_matches_release': True, 'requirements_satisfied': True,
                                            'durable_materialization_verified': True}:
                    raise ValueError()
            elif result['validation'] != {'core_unchanged': True, 'requirements_satisfied': True,
                                          'durable_materialization_verified': True}:
                raise ValueError()
        except (KeyError, TypeError, ValueError, AttributeError):
            raise PreparationError('incomplete or invalid ready result') from None

    def renew(self, claim: Claim, *, lease_seconds: int = 30) -> bool:
        if not 1 <= lease_seconds <= 300:
            raise PreparationError('invalid preparation lease')
        with self.engine.begin() as connection:
            self._environment(connection)
            candidate = self._candidate(connection, claim.candidate_id)
            now = self._now(connection)
            if not self._owns_claim(connection, candidate, claim, now):
                return False
            connection.execute(update(self.tables['candidate']).where(
                self.tables['candidate'].c.id == candidate['id']).values(
                claim_until=now + timedelta(seconds=lease_seconds)))
            return True

    def fail(self, claim: Claim) -> bool:
        # Raw builder output may contain credentials; only a redacted terminal
        # state is retained by 05a1. Builder diagnostics belong to 05a2.
        with self.engine.begin() as connection:
            self._environment(connection)
            candidate = self._candidate(connection, claim.candidate_id)
            if not self._owns_claim(connection, candidate, claim, self._now(connection)):
                return False
            connection.execute(update(self.tables['candidate']).where(
                self.tables['candidate'].c.id == candidate['id']).values(state='failed'))
            self._invalidate(connection, candidate['id'])
            return True

    def cancel(self, actor: str, candidate_id: str) -> None:
        with self.engine.begin() as connection:
            self._environment(connection)
            candidate = self._candidate(connection, candidate_id)
            self._authorize(connection, actor, 'cancel', candidate)
            if candidate['state'] == 'published':
                raise PreparationError('published candidate cannot be cancelled')
            self._expire(connection, candidate, self._now(connection))
            if candidate['state'] not in ('cancelled', 'expired', 'failed'):
                connection.execute(update(self.tables['candidate']).where(
                    self.tables['candidate'].c.id == candidate_id).values(state='cancelled'))
            self._invalidate(connection, candidate_id)

    def _config_drift(self, connection: Connection, environment: dict[str, Any]) -> dict[str, Any]:
        """Read-only redacted pending state; never merge it into committed config.

        Config import acknowledges observed_config_generation in the immutable
        desired revision. Missing acknowledgement means an observation is pending.
        """
        from docassemble.base.managed_config import OBSERVED_CONFIG_ID
        table = self.tables['observed_config']
        observed = connection.execute(select(table).where(table.c.id == OBSERVED_CONFIG_ID)).mappings().first()
        revisions = self.tables['revision']
        committed = connection.scalar(select(revisions.c.snapshot).where(
            revisions.c.id == environment['desired_revision_id']))
        acknowledged = committed.get('observed_config_generation') if committed else None
        generation = observed['generation'] if observed else None
        save = config_save_status(environment)
        return {'pending': generation is not None and generation != acknowledged,
                'observed_generation': generation, 'committed_generation': acknowledged,
                **({'config_save': save} if save is not None else {})}

    def status(self, actor: str, candidate_id: str) -> dict[str, Any]:
        with self.engine.begin() as connection:
            environment = self._environment(connection)
            candidate = self._candidate(connection, candidate_id)
            self._authorize(connection, actor, 'status', candidate)
            self._expire(connection, candidate, self._now(connection))
            fresh = self._fresh(environment, candidate, connection)
            from docassemble.base.managed_rollback import disclosure as rollback_disclosure
            from docassemble.base.managed_direct import redact_direct_result
            return {'id': candidate_id, 'candidate_id': candidate_id, 'state': candidate['state'], 'stale': not fresh,
                    'approval_supported': False, 'installation_requested': candidate['state'] == 'published',
                    'result_digest': candidate['result_digest'],
                    'ready_at': candidate['ready_at'], 'expires_at': candidate['expires_at'],
                    'preview': redact_direct_result(candidate['result']) if fresh and candidate['state'] == 'ready' else None,
                    'desired_revision_id': environment['desired_revision_id'],
                    'config_drift': self._config_drift(connection, environment),
                    'runtime': environment['snapshot']['runtime'],
                    'runtime_generation': environment['runtime_generation'],
                    'warnings': (['stale-preview'] if not fresh else []) +
                                (['expired-preview'] if candidate['state'] == 'expired' else []) +
                                ([] if environment['snapshot']['runtime']['kind'] == 'normal' else
                                 ['runtime-not-normally-verified']),
                    'intent_change': {
                        'operation': candidate['operation'],
                        'requirement': (None if candidate['operation'] in ('config', 'repair', 'upgrade', 'rollback', 'adoption', 'migration') else
                                        candidate['inputs']['requirement']['declaration']),
                        **({'config_import': {'generation': candidate['inputs']['config_import']['generation'],
                                              'digest': candidate['inputs']['config_import']['digest']},
                            'config_diff': self.config_diff(candidate['inputs'])} if candidate['operation'] == 'config' else {}),
                        **({'repair_diff': self.config_diff(candidate['inputs'])} if candidate['operation'] == 'repair' else {}),
                        **({'release_selection': candidate['inputs']['release_selection']}
                           if candidate['operation'] == 'upgrade' else {}),
                        **({'rollback': rollback_disclosure(candidate['inputs'])}
                           if candidate['operation'] == 'rollback' else {}),
                        'remove_contribution_id': candidate['inputs'].get('remove_contribution_id'),
                        'retained_contributions': [row for row in candidate['inputs']['contributions']
                            if row['id'] != candidate['inputs'].get('remove_contribution_id') and
                            all(row['id'] not in candidate['inputs'].get(key, []) for key in (
                                'replace_artifact_contributions', 'replace_git_contributions',
                                'replace_direct_contributions'))]},
                    'checkpoint': {'requested': candidate['inputs'].get('pre_change_checkpoint', False),
                        'adds_maintenance_time': candidate['inputs'].get('pre_change_checkpoint', False),
                        'duration_seconds': None, 'retention': 'operator-pruned-no-auto-deletion',
                        'restore': 'operator-executor-only', 'external_side_effects_undone': False},
                    'scope': 'preview-only'}

    def _published_revision(self, connection: Connection, revision_id: str):
        revisions = self.tables['revision']
        revision = connection.execute(select(revisions).where(
            revisions.c.id == revision_id)).mappings().first()
        publications = self.tables['publication_result']
        publication = connection.execute(select(publications).where(
            publications.c.revision_id == revision_id)).mappings().first()
        if not revision or not publication:
            raise PreparationError('interactive published revision not found')
        candidate = self._candidate(connection, publication['candidate_id'])
        if candidate['state'] != 'published' or candidate['operation'] not in ('add', 'update', 'remove', 'config', 'repair', 'upgrade', 'rollback', 'adoption', 'migration'):
            raise PreparationError('unsupported published operation')
        return revision, candidate

    def publication_status(self, actor: str, *, revision_id: str | None = None,
                           attempt_id: str | None = None) -> dict[str, Any]:
        """Authorized allowlist projection, not installation verification.

        Never expose bundles, declarations, executor errors or arbitrary record
        fields. Terminal reports are not proof of current runtime convergence.
        """
        if bool(revision_id) == bool(attempt_id):
            raise PreparationError('provide one revision or attempt id')
        with self.engine.begin() as connection:
            environment = self._environment(connection)
            attempts = self.tables['installation_attempt']
            attempt = None
            if attempt_id:
                attempt = connection.execute(select(attempts).where(
                    attempts.c.id == attempt_id)).mappings().first()
                if not attempt:
                    raise PreparationError('attempt not found')
                revision_id = attempt['revision_id']
            revision, candidate = self._published_revision(connection, revision_id)
            self._authorize(connection, actor, 'status', candidate)
            if attempt is None and environment['active_attempt_id']:
                attempt = connection.execute(select(attempts).where(
                    attempts.c.id == environment['active_attempt_id'],
                    attempts.c.revision_id == revision_id)).mappings().first()
            if attempt is None:
                attempt = connection.execute(select(attempts).where(
                    attempts.c.revision_id == revision_id).order_by(
                    attempts.c.record['created_at'].as_string().desc(), attempts.c.id).limit(1)).mappings().first()
            occupied = bool(attempt and attempt['id'] == environment['active_attempt_id'])
            phase = attempt['record'].get('phase') if attempt else None
            phase = 'reported-terminal' if phase == 'succeeded' else (
                phase if phase in ('pending', 'running', 'failed', 'blocked', 'stopped', 'aborted') else 'unknown')
            runtime = environment['snapshot'].get('runtime', {})
            kind = runtime.get('kind')
            failure = installation_failure(attempt['record'].get('terminal', {})) if attempt else None
            checkpoint = (attempt['record'].get('terminal', {}).get('checkpoint') or
                          attempt['record'].get('checkpoint')) if attempt else None
            checkpoint_status = ({key: checkpoint.get(key) for key in (
                'id', 'state', 'complete', 'known_good', 'duration_seconds', 'maintenance_seconds', 'created_at', 'retention')}
                if checkpoint else None)
            if checkpoint_status is not None:
                try:
                    created_at = datetime.fromisoformat(checkpoint_status['created_at'])
                    checkpoint_status['age_seconds'] = max(0, (self._now(connection) - created_at).total_seconds())
                except (ValueError, TypeError):
                    checkpoint_status['age_seconds'] = None
            return {'revision_id': revision['id'], 'candidate_id': candidate['id'],
                    'publication': 'published', 'desired_revision_id': environment['desired_revision_id'],
                    'is_desired': environment['desired_revision_id'] == revision['id'],
                    'attempt_id': attempt['id'] if attempt else None, 'attempt_phase': phase,
                    'active': occupied, 'installation': ('pending' if occupied and phase == 'pending' else
                        'in-progress' if occupied else 'verified-running' if kind == 'normal' and
                        runtime.get('revision_id') == revision['id'] and runtime.get('attempt_id') == (attempt['id'] if attempt else None)
                        else 'failed' if phase in ('failed', 'blocked', 'aborted') else 'not-verified'),
                    **runtime_status(environment),
                    'config_drift': self._config_drift(connection, environment),
                    'terminal_outcome': (attempt['record'].get('terminal', {}).get('outcome')
                                         if attempt and attempt['record'].get('terminal', {}).get('outcome') in
                                         ('materialized-stopped', 'failed-stopped', 'blocked', 'aborted-before-sync', 'applied-normal') else None),
                    'checkpoint': checkpoint_status,
                    'failure': failure,
                    'approval_supported': False, 'retry_supported': False,
                    'scope': 'publication-status-only'}

    def register_material(self, claim: Claim, material_id: str, path: str) -> None:
        """Register candidate-only restricted material, never promote credentials."""
        with self.engine.begin() as connection:
            self._environment(connection)
            candidate = self._candidate(connection, claim.candidate_id)
            if not self._owns_claim(connection, candidate, claim, self._now(connection)):
                raise PreparationError('preparation claim no longer authorized')
            self._register_material(connection, claim.candidate_id, material_id, path)

    def retain_direct_artifact(self, claim: Claim, root: Path, reference: str, source=None) -> Path:
        """Expose and register fetched bytes under reconciliation's environment lock.

        Network retrieval is already complete. With no source, pin a recorded
        object under the same lock before a new build can use it.
        """
        from docassemble.base.managed_artifacts import retain_artifact, verify_artifact
        with self.engine.begin() as connection:
            self._environment(connection)
            candidate = self._candidate(connection, claim.candidate_id)
            if not self._owns_claim(connection, candidate, claim, self._now(connection)):
                raise PreparationError('preparation claim no longer authorized')
            path = (verify_artifact(root, reference) if source is None else
                    retain_artifact(root, reference, source))
            self._register_material(connection, claim.candidate_id, str(uuid.uuid4()), str(path))
            return path

    def _register_material(self, connection, candidate_id, material_id, path):
        material = self.tables['candidate_material']
        existing = connection.execute(select(material).where(
            material.c.candidate_id == candidate_id, material.c.path == path,
            material.c.deleted.is_(False)).with_for_update()).mappings().first()
        if existing:
            if not existing['usable']:
                raise PreparationError('candidate material no longer usable')
            return
        shared = bool(connection.scalar(select(material.c.id).where(
            material.c.path == path, material.c.deleted.is_(False)).limit(1)))
        connection.execute(material.insert().values(id=material_id, candidate_id=candidate_id, path=path,
            usable=True, shared=shared, committed=False, deleted=False))

    def ingest_registered_material(self, claim: Claim, artifact_ingress: Path, secret_ingress: Path,
                                   artifact_root: Path, secret_root: Path, copy) -> None:
        """Privileged copier is bounded by live claim and durable registered paths.

        Preserve credential IDs when moving their authority row; retain a separate
        invalidated ingress cleanup row. Artifact pins are store-scoped and unique.
        """
        if os.geteuid() != 0:
            raise PreparationError('registered ingress requires privileged dispatch')
        with self.engine.begin() as connection:
            self._environment(connection)
            candidate = self._candidate(connection, claim.candidate_id)
            if not self._owns_claim(connection, candidate, claim, self._now(connection)):
                raise PreparationError('preparation claim no longer authorized')
            from docassemble.base.managed_git import source_declarations
            from docassemble.base.managed_direct import direct_declarations
            excluded = set(claim.inputs.get('replace_artifact_contributions', [])) | set(
                claim.inputs.get('replace_git_contributions', [])) | set(claim.inputs.get('replace_direct_contributions', []))
            references = {row['artifact_ref'] for row in claim.inputs['contributions'] if row.get('artifact_ref') and
                          row['id'] != claim.inputs.get('remove_contribution_id') and row['id'] not in excluded}
            if claim.inputs.get('artifact'):
                references.add(claim.inputs['artifact']['digest'])
            for name in direct_declarations(claim.inputs):
                previous = claim.inputs['baseline'].get('direct_sources', {}).get(name)
                if previous:
                    references.add(previous['digest'])
            material = self.tables['candidate_material']
            for reference in sorted(references):
                rows = connection.execute(select(material).where(material.c.deleted.is_(False),
                    material.c.usable.is_(True), material.c.path.in_(
                        [str(artifact_root / reference), str(artifact_ingress / reference)]),
                    (material.c.candidate_id == claim.candidate_id) | material.c.committed.is_(True)
                ).with_for_update()).mappings().all()
                if not rows:
                    raise PreparationError('unregistered artifact material')
                ingress_rows = [row for row in rows if row['path'] == str(artifact_ingress / reference)]
                destination = str(artifact_root / reference)
                for row in ingress_rows:
                    # Even destination reuse verifies this upload before invalidation.
                    if row['committed']:
                        raise PreparationError('committed ingress material cannot be relocated')
                    try:
                        copied = copy(row['path'], 'artifact')
                    except OSError:
                        raise PreparationError('registered ingress copy refused') from None
                    if copied != destination:
                        raise PreparationError('registered artifact destination mismatch')
                # Every live candidate owns a worker-store pin, including reuse of
                # committed bytes. Publication must never select its HTTP ingress.
                self._register_material(connection, claim.candidate_id, str(uuid.uuid4()), destination)
                for row in ingress_rows:
                    if row['candidate_id'] == claim.candidate_id:
                        connection.execute(update(material).where(material.c.id == row['id']).values(usable=False))
            surviving = source_declarations(claim.inputs)
            for name, reference in (claim.inputs.get('git_credentials') or {}).items():
                if name not in surviving:
                    raise PreparationError('credential not bound to surviving source')
                row = connection.execute(select(material).where(material.c.id == reference,
                    material.c.deleted.is_(False)).with_for_update()).mappings().first()
                if not row or row['shared'] or not (row['committed'] or
                        row['candidate_id'] == claim.candidate_id and row['usable']):
                    raise PreparationError('unregistered credential material')
                if row['path'] == str(secret_root / reference):
                    continue
                if row['path'] != str(secret_ingress / reference) or row['committed']:
                    raise PreparationError('credential outside registered ingress')
                destination = copy(row['path'], 'secret')
                if destination != str(secret_root / reference):
                    raise PreparationError('registered credential destination mismatch')
                connection.execute(update(material).where(material.c.id == reference).values(path=destination))
                connection.execute(material.insert().values(id=str(uuid.uuid4()), candidate_id=claim.candidate_id,
                    path=row['path'], usable=False, shared=False, committed=False, deleted=False))
            inputs = dict(candidate['inputs'])
            inputs['material_stores'] = {'artifact': str(artifact_root), 'secret': str(secret_root),
                'ingress_artifact': str(artifact_ingress), 'ingress_secret': str(secret_ingress)}
            connection.execute(update(self.tables['candidate']).where(
                self.tables['candidate'].c.id == claim.candidate_id).values(inputs=inputs))
        claim.inputs.update(inputs)

    def stage_candidate_secret(self, claim: Claim, root: Path, secret: bytes) -> str:
        """Trusted internal staging primitive for candidate-owned restricted material.

        Persist the cleanup reference before writing, then serialize writing with
        cancellation/cleanup. A crash leaves a registered file (or missing file),
        never an unregistered secret. The restricted root is operator-owned.
        """
        root = self._secret_root(root)
        material_id = str(uuid.uuid4())
        path = root / material_id
        self.register_material(claim, material_id, str(path))
        with self.engine.begin() as connection:
            self._environment(connection)
            candidate = self._candidate(connection, claim.candidate_id)
            if not self._owns_claim(connection, candidate, claim, self._now(connection)):
                raise PreparationError('preparation claim no longer authorized')
            self._stage_secret_file(root, material_id, secret)
        return material_id

    def _stage_queued_secret(self, candidate_id: str, root: Path, material_id: str, secret: bytes) -> None:
        """Write only a still-queued, registered credential under the cleanup lock.

        Registration must commit first: a crash during this transaction leaves a
        durable cleanup reference, even if the file write outlives its rollback.
        A worker that claimed the candidate in the gap owns it instead of us.
        """
        with self.engine.begin() as connection:
            self._environment(connection)
            candidate = self._candidate(connection, candidate_id)
            if candidate['state'] != 'queued' or candidate['fence'] != 0:
                raise PreparationError('candidate secret staging no longer authorized')
            material = self.tables['candidate_material']
            row = connection.execute(select(material).where(
                material.c.id == material_id, material.c.candidate_id == candidate_id,
                material.c.usable.is_(True), material.c.deleted.is_(False),
                material.c.shared.is_(False), material.c.committed.is_(False)
            ).with_for_update()).mappings().first()
            if not row or row['path'] != str(root / material_id):
                raise PreparationError('candidate secret staging no longer authorized')
            self._stage_secret_file(root, material_id, secret)

    @staticmethod
    def _stage_secret_file(root: Path, material_id: str, secret: bytes) -> None:
        """Exclusive owner-only write with a durable directory entry."""
        if not isinstance(secret, bytes) or not secret or len(secret) > 65536:
            raise PreparationError('invalid candidate secret material')
        try:
            fd = os.open(root / material_id, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as handle:
                handle.write(secret)
                handle.flush()
                os.fsync(handle.fileno())
            fd = os.open(root, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            raise PreparationError('candidate secret staging unavailable') from None

    @staticmethod
    def _read_secret_file(root: Path, material_id: str) -> bytes:
        """Read one bounded owner-only regular reference without following symlinks."""
        try:
            if str(uuid.UUID(material_id)) != material_id:
                raise ValueError()
            fd = os.open(root / material_id, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise OSError()
                value = handle.read(65537)
                if not value or len(value) > 65536:
                    raise ValueError()
                return value
        except (OSError, ValueError):
            raise PreparationError('restricted secret material unavailable') from None

    @staticmethod
    def _secret_root(root) -> Path:
        try:
            root = Path(root).resolve(strict=True)
            info = root.stat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise OSError()
            return root
        except OSError:
            raise PreparationError('candidate secret store must be restricted and operator-owned') from None

    def read_candidate_secret(self, claim: Claim, root: Path, material_id: str) -> bytes:
        """New use requires the current claim, not a cached usable flag.

        Trusted consumers must not log returned bytes or reuse them after denial.
        This is not protection from hostile code under the same OS identity.
        """
        root = self._secret_root(root)
        with self.engine.begin() as connection:
            self._environment(connection)
            candidate = self._candidate(connection, claim.candidate_id)
            if not self._owns_claim(connection, candidate, claim, self._now(connection)):
                raise PreparationError('candidate secret use denied')
            table = self.tables['candidate_material']
            row = connection.execute(select(table).where(
                table.c.id == material_id, table.c.candidate_id == claim.candidate_id,
                table.c.usable.is_(True), table.c.deleted.is_(False),
                table.c.shared.is_(False), table.c.committed.is_(False)).with_for_update()).mappings().first()
            if not row or row['path'] != str(root / material_id):
                raise PreparationError('candidate secret use denied')
        try:
            return self._read_secret_file(root, material_id)
        except PreparationError:
            raise PreparationError('candidate secret use denied') from None

    def read_git_credential(self, claim: Claim, root: Path, material_id: str) -> bytes:
        """Resolve one live candidate-owned or retained committed scoped credential.

        Reuse never returns the secret to a caller; a revocation (deleted retained
        material) fails closed with a fixed actionable error and never rewrites history.
        """
        root = self._secret_root(root)
        with self.engine.begin() as connection:
            self._environment(connection)
            candidate = self._candidate(connection, claim.candidate_id)
            owned = self._owns_claim(connection, candidate, claim, self._now(connection))
            table = self.tables['candidate_material']
            row = connection.execute(select(table).where(
                table.c.id == material_id, table.c.deleted.is_(False)).with_for_update()).mappings().first()
            candidate_owned = bool(owned and row and row['candidate_id'] == claim.candidate_id and
                                   row['usable'] and not row['shared'] and not row['committed'])
            # A retained committed credential is usable only under a live owned claim
            # and only when the claim actually references it.
            retained = bool(owned and row and row['committed'] and not row['shared'] and
                            material_id in (candidate['inputs'].get('git_credentials') or {}).values())
            if not row or row['path'] != str(root / material_id) or not (candidate_owned or retained):
                raise PreparationError('Git credential reference unavailable; reauthorize or provide a new source')
        try:
            return self._read_secret_file(root, material_id)
        except PreparationError:
            raise PreparationError('Git credential reference unavailable; reauthorize or provide a new source') from None

    def inject_source_credentials(self, *args, **kwargs) -> None:
        """Raw inline injection stays fenced; credentials persist only as scoped references."""
        raise PreparationError('raw source credential injection is unsupported; submit a scoped HTTPS Git declaration')

    def material_authorized(self, claim: Claim, material_id: str) -> bool:
        # Consumers must check this protocol, not a cached boolean/credential.
        # A cancelled, expired or reclaimed claim grants no new credential use.
        with self.engine.begin() as connection:
            self._environment(connection)
            candidate = self._candidate(connection, claim.candidate_id)
            if not self._owns_claim(connection, candidate, claim, self._now(connection)):
                return False
            table = self.tables['candidate_material']
            return bool(connection.execute(select(table.c.id).where(
                table.c.id == material_id, table.c.candidate_id == claim.candidate_id,
                table.c.usable.is_(True), table.c.deleted.is_(False))).first())

    def worker_exited(self, worker_id: str) -> None:
        """Trusted worker/OS reconciliation evidence only; lease timeout is NOT exit."""
        with self.engine.begin() as connection:
            workers = self.tables['preparation_worker']
            worker = connection.execute(select(workers).where(workers.c.id == worker_id)).mappings().first()
            if not worker:
                raise PreparationError('worker not found')
            self._candidate(connection, worker['candidate_id'])
            connection.execute(update(workers).where(workers.c.id == worker_id).values(exited=True))

    @staticmethod
    def _disposable_ingress(candidate, path: str) -> bool:
        stores = candidate['inputs'].get('material_stores', {})
        ingress = {stores.get('ingress_artifact'), stores.get('ingress_secret')} - {None}
        retained = {stores.get('artifact'), stores.get('secret')} - {None}
        return str(Path(path).parent) in ingress - retained

    def _cleanup_rows(self, connection, candidate):
        """Exit-proven terminal cleanup; publication frees only invalidated ingress.

        Shared ingress rows may be tombstoned, but unlink still requires absence
        of every other retention reference. Committed material is never eligible.
        Environment/candidate locks exclude claim and terminal writes throughout.
        """
        if candidate['state'] not in ('cancelled', 'expired', 'failed', 'published'):
            return []
        workers = self.tables['preparation_worker']
        if connection.execute(select(workers.c.id).where(
                workers.c.candidate_id == candidate['id'], workers.c.exited.is_(False))).first():
            return []
        material = self.tables['candidate_material']
        rows = connection.execute(select(material).where(
            material.c.candidate_id == candidate['id'], material.c.committed.is_(False),
            material.c.usable.is_(False), material.c.deleted.is_(False)
        ).with_for_update()).mappings().all()
        return [row for row in rows if self._disposable_ingress(candidate, row['path']) or
                candidate['state'] != 'published' and not row['shared']]

    def cleanup_plan(self, candidate_id: str) -> list[dict[str, str]]:
        """Deletion eligibility only: 05a2 owns credential use and file deletion.

        Includes orphaned terminal work after explicit worker-exit reconciliation;
        never interprets heartbeat/lease expiry as proof that files are unused.
        """
        with self.engine.begin() as connection:
            self._environment(connection)
            candidate = self._candidate(connection, candidate_id)
            self._expire(connection, candidate, self._now(connection))
            return [{'id': row['id'], 'path': row['path']}
                    for row in self._cleanup_rows(connection, candidate)]

    def reconcile_material(self, root: Path) -> int:
        """Independent retryable terminal/expiry cleanup pass."""
        candidates = self.tables['candidate']
        with self.engine.connect() as connection:
            ids = list(connection.scalars(select(candidates.c.id).where(
                candidates.c.state.in_(('cancelled', 'expired', 'failed', 'ready', 'published')))))
        return sum(self.delete_candidate_material(candidate_id, root) for candidate_id in ids)

    def reconcile_artifacts(self, root: Path) -> int:
        """Remove crash-left ingestion bytes only when no durable row references them.

        Ingestion and this scan hold the same environment lock. Referenced objects
        go through the existing terminal/all-worker cleanup gate instead.
        """
        from docassemble.base.managed_artifacts import artifact_root
        root = artifact_root(root, reader=True)
        with self.engine.connect() as connection:
            material = self.tables['candidate_material']
            ids = {row['candidate_id'] for row in connection.execute(select(material)).mappings()
                   if Path(row['path']).parent == root and re.fullmatch('[0-9a-f]{64}', Path(row['path']).name)}
        removed = sum(self.delete_candidate_material(candidate_id, root, artifact_only=True) for candidate_id in ids)
        with self.engine.begin() as connection:
            self._environment(connection)
            material = self.tables['candidate_material']
            referenced = set(connection.scalars(select(material.c.path).where(material.c.deleted.is_(False))))
            for path in root.iterdir():
                if (path.name.startswith('.ingest-') or re.fullmatch('[0-9a-f]{64}', path.name)) and str(path) not in referenced:
                    if path.is_symlink() or not path.is_file():
                        raise PreparationError('unsupported material storage type')
                    path.unlink()
                    removed += 1
            fd = os.open(root, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return removed

    def delete_candidate_material(self, candidate_id: str, root: Path, *, artifact_only: bool = False) -> int:
        """Trusted flat restricted material store only; never bundles or uv cache.

        Lock eligibility through unlink and tombstone. A crash after unlink is
        retryable; absent files are safe only after the same all-worker gate.
        """
        root = root.resolve(strict=True)
        with self.engine.begin() as connection:
            self._environment(connection)
            candidate = self._candidate(connection, candidate_id)
            self._expire(connection, candidate, self._now(connection))
            material = self.tables['candidate_material']
            rows = self._cleanup_rows(connection, candidate)
            # Each explicit store owns only rows pointing inside that store.
            # A candidate may retain Git secrets and artifacts in separate roots.
            rows = [row for row in rows if Path(row['path']).parent == root]
            if artifact_only:
                rows = [row for row in rows if re.fullmatch('[0-9a-f]{64}', Path(row['path']).name)]
            # Validate the entire plan before deleting anything. UUID filenames
            # exclude traversal and collisions with cache/retained directories.
            for row in rows:
                try:
                    if str(uuid.UUID(row['id'])) != row['id']:
                        raise ValueError()
                except ValueError:
                    raise PreparationError('unsupported material storage identity') from None
                filename = Path(row['path']).name
                if not re.fullmatch('[0-9a-f]{64}', filename):
                    try:
                        if str(uuid.UUID(filename)) != filename:
                            raise ValueError()
                    except ValueError:
                        raise PreparationError('material outside restricted cleanup store') from None
                path = root / filename
                if row['path'] != str(path):
                    raise PreparationError('material outside restricted cleanup store')
                if path.is_symlink() or (path.exists() and not stat.S_ISREG(path.stat().st_mode)):
                    raise PreparationError('unsupported material storage type')
            for row in rows:
                # A second candidate, including an exited or committed one, owns
                # its own retention reference. Never unlink another row's bytes.
                referenced = connection.scalar(select(material.c.id).where(material.c.path == row['path'],
                    material.c.id != row['id'], material.c.deleted.is_(False)).limit(1))
                if not referenced:
                    Path(row['path']).unlink(missing_ok=True)
                fd = os.open(root, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
                connection.execute(update(material).where(material.c.id == row['id']).values(deleted=True))
            return len(rows)

    def approve(self, *args, **kwargs):
        raise PreparationError('approval unsupported until publication and executor qualification')

    def retry(self, *args, **kwargs):
        raise PreparationError('retry unsupported until publication and executor qualification')
