"""Fresh preview adapters and explicit, restricted experimental image approval."""
import fcntl
import hashlib
import json
import os
import stat
from pathlib import Path
import tempfile
import uuid
from collections.abc import Mapping
from packaging.utils import canonicalize_name
from sqlalchemy import table, column, select, Integer, Boolean, Text
from docassemble.base.managed_publication import Publication
from docassemble.base.managed_preparation import Preparation, PreparationError, digest, parse_registry_requirement
from docassemble.base.managed_qualification import (
    configured_baseline, require_qualified_inputs, installation_retry_eligible, ENVIRONMENT)
from docassemble.base.managed import managed_installation


class PreviewAuthorizer:
    """Read SQL authority in each semantic transaction, never ORM user caches.

    Policy and API-key authority are current config/Redis, not SQL records.
    Their writer serialization with publication belongs to 05b.
    """
    def __init__(self, prefix, policy, api_permission=None):
        self.prefix = prefix
        self.policy = policy
        self.api_permission = api_permission

    def __call__(self, connection, actor, operation, candidate):
        try:
            user_id = int(actor)
        except (ValueError, TypeError):
            return False
        users = table(self.prefix + 'user', column('id', Integer),
                      column('active', Boolean), column('social_id', Text))
        roles = table(self.prefix + 'role', column('id', Integer), column('name', Text))
        membership = table(self.prefix + 'user_roles', column('user_id', Integer), column('role_id', Integer))
        user = connection.execute(select(users).where(users.c.id == user_id)).mappings().first()
        if not user or not user['active'] or user['social_id'].startswith('disabled$'):
            return False
        names = set(connection.scalars(select(roles.c.name).join(
            membership, membership.c.role_id == roles.c.id).where(membership.c.user_id == user_id)))
        policy = self.policy()
        admin = 'admin' in names
        permission = 'read_packages' if operation == 'status' else 'manage_packages'
        if self.api_permission is not None and not self.api_permission(actor, permission, admin):
            return False
        allowed = policy['allowed']
        if not (admin or 'developer' in names or any(permission in allowed.get(name, []) or
                (operation == 'status' and 'manage_packages' in allowed.get(name, [])) for name in names)):
            return False
        if operation != 'status' and not policy['allow_updates']:
            return False
        if operation == 'prepare' and not (admin or policy['developer_can_install']):
            return False
        if operation in ('cancel', 'status'):
            return bool(candidate and (candidate['requester_id'] == actor or admin))
        if operation not in ('prepare', 'approve', 'retry') or not candidate:
            return False
        if operation in ('approve', 'retry') and (
                (candidate['requester_id'] != actor and not (operation == 'retry' and admin)) or
                not (admin or policy['developer_can_install'])):
            return False
        if operation == 'prepare' and candidate.get('change_operation') in ('config', 'repair'):
            return admin
        if operation == 'prepare' and candidate.get('change_operation') in ('upgrade', 'rollback'):
            # Preserve existing qualifying webapp upgrade authority: admin only.
            return admin
        if operation == 'approve' and candidate.get('operation') in ('config', 'repair', 'upgrade', 'rollback'):
            return admin and candidate['requester_id'] == actor
        if candidate.get('operation') in ('adoption', 'migration') and operation == 'approve':
            return False
        if operation == 'retry' and candidate.get('operation') in ('config', 'repair', 'upgrade', 'rollback', 'adoption', 'migration'):
            # Administrator-or-still-authorized-original-approver retry.
            return admin
        requirement = candidate['inputs']['requirement'] if operation in ('approve', 'retry') else candidate['requirement']
        change_operation = candidate['inputs'].get('change_operation', 'add') if operation in ('approve', 'retry') else candidate.get('change_operation', 'add')
        assignment = candidate.get('assign_owner', False) if operation == 'prepare' else candidate['inputs']['ownership_effects'].get('assignment', False)
        if assignment and not admin:
            return False
        name = requirement['normalized_name']
        if not admin and name in ('docassemble-base', 'docassemble-demo', 'docassemble-webapp'):
            return False
        # Normalize *all* legacy aliases. No guessed spelling/source bypass or
        # implicit legacy adoption; ownerless assignment must be explicit.
        packages = table(self.prefix + 'package', column('id', Integer), column('name', Text),
                         column('type', Text), column('core', Boolean))
        grants = table(self.prefix + 'package_auth', column('package_id', Integer),
                       column('user_id', Integer), column('authtype', Text))
        for package in connection.execute(select(packages)).mappings():
            if _package_name(package['name']) != name:
                continue
            owners = set(connection.scalars(select(grants.c.user_id).where(
                grants.c.package_id == package['id'], grants.c.authtype == 'owner')))
            if package['type'] != 'pip':
                from docassemble.base.managed_records import preparation_tables
                records = preparation_tables(self.prefix)
                identity = connection.execute(select(records['package_identity']).where(
                    records['package_identity'].c.normalized_name == name)).mappings().first()
                baseline = (candidate['inputs']['baseline'] if operation in ('approve', 'retry') else
                            connection.scalar(select(records['environment_state'].c.snapshot)))
                adopted = {'inputs': {'baseline': baseline}}
                if (not identity or identity['legacy_package_id'] != package['id'] or
                        not _adopted_source(adopted, package)):
                    return False
            if package['core'] or (
                    user_id not in owners and change_operation != 'update' and not (operation == 'retry' and admin) and not (assignment and not owners)):
                return False
        return True


def _package_name(value):
    """Legacy rows may store a complete named direct request, not just a name."""
    from docassemble.base.managed_git import parse_supported_requirement
    try:
        return parse_supported_requirement(value).normalized_name
    except PreparationError:
        return canonicalize_name(value)


def _adopted_source(candidate, package):
    """Only the exact retained legacy type/name may project managed intent."""
    record = candidate['inputs']['baseline'].get('adoption', {})
    if (package['type'] not in ('git', 'github', 'zip') or record.get('state') != 'published' or
            not isinstance(record.get('evidence_digest'), str) or len(record['evidence_digest']) != 64):
        return False
    rows = record.get('evidence', {}).get('package', [])
    return any(row['id'] == package['id'] and row['name'] == _package_name(package['name']) and
               row['type'] == package['type'] and row['core'] == package['core'] for row in rows)


class PackageProjection:
    """Project managed interactive intent, never adopt ambiguous legacy rows.

    SQL-only; publication owns the environment lock and commit. Current grants
    are preserved. Only explicitly previewed administrator assignment can add an
    owner to an existing ownerless managed identity.
    """
    def __init__(self, prefix):
        self.prefix = prefix

    def __call__(self, connection, actor, candidate):
        if candidate.get('operation') == 'config':
            return self._config(connection, candidate)
        if candidate.get('operation') == 'repair':
            return self._repair(connection, candidate)
        if candidate.get('operation') == 'rollback':
            return self._rollback(connection, actor, candidate)
        if candidate.get('operation') == 'upgrade':
            return self._upgrade(connection, candidate)
        from sqlalchemy import update
        from docassemble.base.managed_records import preparation_tables
        packages = table(self.prefix + 'package', column('id', Integer), column('name', Text),
            column('type', Text), column('core', Boolean), column('active', Boolean),
            column('dependency', Boolean), column('limitation', Text))
        grants = table(self.prefix + 'package_auth', column('package_id', Integer),
            column('user_id', Integer), column('authtype', Text))
        identities = preparation_tables(self.prefix)['package_identity']
        requirement = candidate['inputs']['requirement']
        name = requirement['normalized_name']
        identity = connection.execute(select(identities).where(
            identities.c.normalized_name == name)).mappings().first()
        aliases = [row for row in connection.execute(select(packages)).mappings()
                   if _package_name(row['name']) == name]
        if identity:
            assignment = candidate['inputs']['ownership_effects'].get('assignment', False)
            expected_owner = None if assignment else candidate['inputs']['ownership_effects']['owner_id']
            if identity['owner_id'] != expected_owner or len(aliases) != 1 or aliases[0]['id'] != identity['legacy_package_id']:
                raise PreparationError('Package identity or ownership changed')
            package = aliases[0]
            owners = set(connection.scalars(select(grants.c.user_id).where(
                grants.c.package_id == package['id'], grants.c.authtype == 'owner')))
            if owners != (set() if assignment else {int(expected_owner)}) or (package['type'] != 'pip' and not _adopted_source(candidate, package)) or package['core']:
                raise PreparationError('Package source or grants changed')
            package_id = package['id']
            if assignment:
                connection.execute(grants.insert().values(package_id=package_id, user_id=int(actor), authtype='owner'))
            operation = candidate.get('operation', 'add')
            if operation == 'add':
                connection.execute(update(packages).where(packages.c.id == package_id).values(
                    type='pip', active=True, dependency=False, limitation=requirement['specifiers']))
            elif operation == 'remove':
                remaining = any(row['identity_id'] == identity['id'] and row['origin'] == 'interactive' and
                    row['id'] != candidate['inputs']['remove_contribution_id'] for row in candidate['inputs']['contributions'])
                connection.execute(update(packages).where(packages.c.id == package_id).values(active=remaining))
        else:
            if aliases:
                raise PreparationError('legacy Package adoption unsupported')
            package_id = connection.scalar(packages.insert().values(name=requirement['name'],
                type='pip', core=False, active=True, dependency=False,
                limitation=requirement['specifiers']).returning(packages.c.id))
            connection.execute(grants.insert().values(package_id=package_id, user_id=int(actor), authtype='owner'))
        return {'legacy_package_id': package_id,
                'owner_id': candidate['inputs']['ownership_effects']['owner_id']}

    def _rollback(self, connection, actor, candidate):
        from sqlalchemy import update
        from docassemble.base.managed_records import preparation_tables
        from docassemble.base.managed_git import parse_supported_requirement
        identities = preparation_tables(self.prefix)['package_identity']
        packages = table(self.prefix + 'package', column('id', Integer), column('name', Text),
            column('type', Text), column('core', Boolean), column('active', Boolean),
            column('dependency', Boolean), column('limitation', Text))
        grants = table(self.prefix + 'package_auth', column('package_id', Integer),
            column('user_id', Integer), column('authtype', Text))
        restored = {}
        for row in candidate['inputs']['contributions']:
            if row['origin'] == 'interactive':
                restored.setdefault(row['identity_id'], []).append(row)
        current = {row['id']: row for row in connection.execute(select(identities)).mappings()}
        package_rows = list(connection.execute(select(packages)).mappings())
        # Validate every affected current projection before any writes. No grant
        # history or transitive installed inventory creates an owned identity.
        affected = set(restored) | {row['identity_id'] for row in candidate['inputs']['committed_contributions']
                                    if row['origin'] == 'interactive'}
        effects = candidate['inputs']['ownership_effects']['identities']
        for identity_id in affected:
            identity = current.get(identity_id)
            if not identity:
                raise PreparationError('historical identity unavailable')
            aliases = [row for row in package_rows if _package_name(row['name']) == identity['normalized_name']]
            if (len(aliases) != 1 or aliases[0]['id'] != identity['legacy_package_id'] or
                    aliases[0]['core'] or (aliases[0]['type'] != 'pip' and not _adopted_source(candidate, aliases[0]))):
                raise PreparationError('Package identity or source changed')
            owners = set(connection.scalars(select(grants.c.user_id).where(
                grants.c.package_id == aliases[0]['id'], grants.c.authtype == 'owner')))
            expected = set() if identity['owner_id'] is None else {int(identity['owner_id'])}
            if owners != expected:
                raise PreparationError('current owner grants changed')
            if identity_id in effects:
                effect = effects[identity_id]
                if identity['owner_id'] != (None if effect['assignment'] else effect['owner_id']):
                    raise PreparationError('rollback ownership changed; fresh preview required')
                if effect['assignment']:
                    connection.execute(grants.insert().values(package_id=aliases[0]['id'], user_id=int(actor), authtype='owner'))
            rows = restored.get(identity_id, [])
            values = {'active': bool(rows)}
            if rows:
                values.update(dependency=False, limitation=parse_supported_requirement(rows[0]['declaration']).specifiers)
            connection.execute(update(packages).where(packages.c.id == aliases[0]['id']).values(**values))
        return {'rolled_back': candidate['inputs']['rollback']['revision_id']}

    def _upgrade(self, connection, candidate):
        """Coherent core upgrade changes no interactive Package/grant.

        The selected release and freshly solved core come entirely from the
        candidate result; publishing must not project, reinterpret or transfer
        any user intent. Re-read every committed interactive identity/source/grant
        on this connection so a change-away cannot approve a stale core. Desired
        intent (contributions, ownership, grants) is preserved unchanged.
        """
        from docassemble.base.managed_records import preparation_tables
        packages = table(self.prefix + 'package', column('id', Integer), column('name', Text),
            column('type', Text), column('core', Boolean))
        grants = table(self.prefix + 'package_auth', column('package_id', Integer),
            column('user_id', Integer), column('authtype', Text))
        identities = preparation_tables(self.prefix)['package_identity']
        current = {row['id']: row for row in connection.execute(select(identities)).mappings()}
        package_rows = {row['id']: row for row in connection.execute(select(packages)).mappings()}
        for row in candidate['inputs']['contributions']:
            if row['origin'] != 'interactive':
                continue
            identity = current.get(row['identity_id'])
            if not identity:
                raise PreparationError('upgrade contribution identity changed')
            package = package_rows.get(identity['legacy_package_id'])
            if (not package or (package['type'] != 'pip' and not _adopted_source(candidate, package)) or package['core'] or
                    _package_name(package['name']) != identity['normalized_name']):
                raise PreparationError('package identity or source changed')
            owners = set(connection.scalars(select(grants.c.user_id).where(
                grants.c.package_id == package['id'], grants.c.authtype == 'owner')))
            expected = set() if identity['owner_id'] is None else {int(identity['owner_id'])}
            if owners != expected:
                raise PreparationError('current owner grants changed')
        return {'upgraded': True, 'release': candidate['inputs']['release_selection']}

    def _repair(self, connection, candidate):
        """Repair changes no interactive Package/grant; re-read committed identity.

        The candidate re-solves the committed desired intent. Verify every
        committed interactive contribution still projects to a pip, non-core
        Package row with unchanged owner grants before publishing a new bundle;
        never adopt legacy rows or transfer ownership.
        """
        from docassemble.base.managed_records import preparation_tables
        packages = table(self.prefix + 'package', column('id', Integer), column('name', Text),
            column('type', Text), column('core', Boolean))
        grants = table(self.prefix + 'package_auth', column('package_id', Integer),
            column('user_id', Integer), column('authtype', Text))
        identities = preparation_tables(self.prefix)['package_identity']
        current = {row['id']: row for row in connection.execute(select(identities)).mappings()}
        package_rows = {row['id']: row for row in connection.execute(select(packages)).mappings()}
        for row in candidate['inputs']['contributions']:
            if row['origin'] != 'interactive':
                continue
            identity = current.get(row['identity_id'])
            if not identity:
                raise PreparationError('repair contribution identity changed')
            package = package_rows.get(identity['legacy_package_id'])
            if (not package or (package['type'] != 'pip' and not _adopted_source(candidate, package)) or package['core'] or
                    _package_name(package['name']) != identity['normalized_name']):
                raise PreparationError('package identity or source changed')
            owners = set(connection.scalars(select(grants.c.user_id).where(
                grants.c.package_id == package['id'], grants.c.authtype == 'owner')))
            expected = set() if identity['owner_id'] is None else {int(identity['owner_id'])}
            if owners != expected:
                raise PreparationError('current owner grants changed')
        return {'repaired': True}

    def _config(self, connection, candidate):
        """Config is ownerless; Package fields continue to project interactive intent.

        Re-read every affected identity/source/grant on the publication connection.
        Never adopt an unlinked legacy row or rewrite an interactive projection.
        """
        from sqlalchemy import update
        from docassemble.base.managed_records import preparation_tables
        from docassemble.base.managed_config import config_import_policy
        inputs = candidate['inputs']
        config_import_policy(inputs['config_import']['snapshot'], inputs['baseline']['config'])
        tables = preparation_tables(self.prefix)
        identities, contributions = tables['package_identity'], tables['requirement_contribution']
        packages = table(self.prefix + 'package', column('id', Integer), column('name', Text),
            column('type', Text), column('core', Boolean), column('active', Boolean),
            column('dependency', Boolean), column('limitation', Text))
        grants = table(self.prefix + 'package_auth', column('package_id', Integer),
            column('user_id', Integer), column('authtype', Text))
        current = {row['id']: row for row in connection.execute(select(identities)).mappings()}
        package_rows = list(connection.execute(select(packages)).mappings())
        desired = [row for row in inputs['contributions'] if row['origin'] == 'config']
        affected = {row['identity_id'] for row in inputs['committed_contributions'] if row['origin'] == 'config'}
        affected.update(row['identity_id'] for row in desired)
        projected = []
        for identity_id in sorted(affected):
            incoming = next((row for row in desired if row['identity_id'] == identity_id), None)
            identity = current.get(identity_id)
            requirement = parse_registry_requirement(incoming['declaration']) if incoming else None
            name = requirement.normalized_name if requirement else identity['normalized_name']
            if name in inputs['baseline']['core']:
                raise PreparationError('config import cannot change exact core')
            aliases = [row for row in package_rows if _package_name(row['name']) == name]
            if identity:
                if (identity['normalized_name'] != name or len(aliases) != 1 or
                        aliases[0]['id'] != identity['legacy_package_id']):
                    raise PreparationError('Package identity or ownership changed')
                package = aliases[0]
                if (package['type'] != 'pip' and not _adopted_source(candidate, package)) or package['core']:
                    raise PreparationError('Package source or grants changed')
                # Fresh grant reads, not a captured single-requirement authority digest.
                # Config neither assigns ownership nor depends on a particular owner.
                list(connection.execute(select(grants).where(grants.c.package_id == package['id'])))
                package_id = package['id']
            else:
                if aliases:
                    raise PreparationError('legacy Package adoption unsupported')
                package_id = connection.scalar(packages.insert().values(name=requirement.name,
                    type='pip', core=False, active=False, dependency=False, limitation=None).returning(packages.c.id))
                connection.execute(identities.insert().values(id=identity_id, normalized_name=name,
                    display_name=requirement.name, owner_id=None, legacy_package_id=package_id))
            projected.append({'identity_id': identity_id, 'legacy_package_id': package_id})
        desired_ids = {row['id'] for row in desired}
        for row in connection.execute(select(contributions).where(contributions.c.origin == 'config')).mappings():
            if row['active'] and row['id'] not in desired_ids:
                connection.execute(update(contributions).where(contributions.c.id == row['id']).values(active=False))
        for row in desired:
            previous = connection.execute(select(contributions).where(contributions.c.id == row['id'])).mappings().first()
            values = {key: row[key] for key in ('id', 'identity_id', 'origin', 'declaration', 'active', 'artifact_ref')}
            if previous:
                if previous['origin'] != 'config' or previous['identity_id'] != row['identity_id']:
                    raise PreparationError('configuration contribution identity changed')
                connection.execute(update(contributions).where(contributions.c.id == row['id']).values(**values))
            else:
                connection.execute(contributions.insert().values(**values))
        return {'config_identities': projected}


class QualifiedPublication(Publication):
    """Opt-in admin-only bridge; unqualified composite HTTP writers are unreachable.

    Internal operator/plugin writers are NOT qualified by this restriction.
    """
    def __init__(self, engine, authorize, project, prefix, baseline):
        super().__init__(engine, authorize, project, prefix)
        self.prefix, self.baseline = prefix, baseline

    def _authority(self, connection, normalized_name):
        # Bind current SQL accounts/roles/grants, not cached current_user objects.
        records = {}
        for name, fields in {
                'user': ('id', 'active', 'social_id'), 'role': ('id', 'name'),
                'user_roles': ('user_id', 'role_id'),
                'package_auth': ('package_id', 'user_id', 'authtype')}.items():
            relation = table(self.prefix + name, *(column(field) for field in fields))
            rows = [dict(row) for row in connection.execute(select(relation)).mappings()]
            if name == 'package_auth':
                packages = table(self.prefix + 'package', column('id'), column('name'))
                target = {row['id'] for row in connection.execute(select(packages)).mappings()
                          if _package_name(row['name']) == normalized_name}
                rows = [row for row in rows if row['package_id'] not in target]
            records[name] = sorted(rows, key=lambda row: repr(sorted(row.items())))
        return digest(records)

    def _capture_approval_authority(self, connection, actor, inputs):
        require_qualified_inputs(inputs, self.baseline)
        if inputs.get('change_operation') == 'rollback':
            inputs['approval_authority'] = self._authority(connection, None)
            return
        if inputs.get('change_operation') in ('config', 'repair', 'upgrade'):
            return  # Config, repair and upgrade preserve current grants; no single-name ownership effect.
        inputs['approval_authority'] = self._authority(connection, inputs['requirement']['normalized_name'])

    def _authorize(self, connection, actor, operation, candidate=None):
        super()._authorize(connection, actor, operation, candidate)
        if operation in ('status', 'cancel'):
            return
        roles = table(self.prefix + 'role', column('id'), column('name'))
        membership = table(self.prefix + 'user_roles', column('user_id'), column('role_id'))
        if not connection.scalar(select(roles.c.id).join(membership, membership.c.role_id == roles.c.id).where(
                membership.c.user_id == int(actor), roles.c.name == 'admin')):
            raise PreparationError('forbidden')
        if operation in ('approve', 'retry'):
            require_qualified_inputs(candidate['inputs'], self.baseline)
            if candidate['operation'] == 'rollback':
                if (operation == 'approve' and candidate['state'] != 'published' and
                        candidate['inputs'].get('approval_authority') != self._authority(connection, None)):
                    raise PreparationError('approval authority changed; fresh preview required')
                return
            if candidate['operation'] in ('config', 'repair', 'upgrade', 'adoption', 'migration'):
                return  # Current SQL admin/requester checked above; projection checks every affected identity.
            authority = self._authority(connection, candidate['inputs']['requirement']['normalized_name'])
            if ((operation == 'approve' and candidate['requester_id'] != actor) or
                    (operation == 'approve' and candidate['inputs'].get('approval_authority') != authority)):
                raise PreparationError('approval authority changed; fresh preview required')
            packages = table(self.prefix + 'package', column('id'), column('name'))
            grants = table(self.prefix + 'package_auth', column('package_id'), column('user_id'), column('authtype'))
            for package in connection.execute(select(packages)).mappings():
                if _package_name(package['name']) == candidate['inputs']['requirement']['normalized_name']:
                    owners = set(connection.scalars(select(grants.c.user_id).where(
                        grants.c.package_id == package['id'], grants.c.authtype == 'owner')))
                    effects = candidate['inputs']['ownership_effects']
                    # Assignment has already committed in a published revision;
                    # retry checks the resulting owner, not the preapproval vacancy.
                    expected = (set() if effects.get('assignment') and operation == 'approve' else
                                {int(effects['owner_id'])})
                    if owners != expected:
                        raise PreparationError('current owner grants changed')

    def _check_retry(self, connection, environment, revision, candidate):
        attempts = self.tables['installation_attempt']
        previous = connection.execute(select(attempts).where(attempts.c.revision_id == revision['id']).order_by(
            attempts.c.record['created_at'].as_string().desc(), attempts.c.id).limit(1)).mappings().first()
        terminal = previous['record'].get('terminal', {}) if previous else {}
        if not installation_retry_eligible(terminal):
            raise PreparationError('retry unsupported for uncertain or mutated work')

    def status(self, actor, candidate_id):
        result = super().status(actor, candidate_id)
        allowed = False
        with self.engine.begin() as connection:
            self._environment(connection)
            candidate = self._candidate(connection, candidate_id)
            try:
                self._authorize(connection, actor, 'approve', candidate)
                allowed = True
            except PreparationError:
                pass
        result['approval_supported'] = allowed and result['state'] == 'ready' and not result['stale']
        result['scope'] = 'experimental-image-approval'
        return result

    def publication_status(self, actor, *, revision_id=None, attempt_id=None):
        result = super().publication_status(actor, revision_id=revision_id, attempt_id=attempt_id)
        with self.engine.begin() as connection:
            environment = self._environment(connection)
            revision, candidate = self._published_revision(connection, result['revision_id'])
            try:
                self._authorize(connection, actor, 'retry', candidate)
                self._check_retry(connection, environment, revision, candidate)
                result['retry_supported'] = (environment['active_attempt_id'] is None and
                                             environment['desired_revision_id'] == revision['id'])
            except PreparationError:
                pass
        result['scope'] = 'experimental-image-approval'
        return result


def preview_service(api=False):
    return _service(api, publication=False)


def observe_saved_configuration(configuration):
    """Ordinary observer; it cannot clear an unresolved managed save."""
    return internal_publication_service().observe_config(configuration)


def save_managed_configuration(actor, content, configuration, config_file, cloud):
    """Serialize cooperating writers; SQL invalidation precedes cloud/local writes.

    The marker is committed independently and survives any subsequent failure.
    Only matching successful observation clears it; no file/SQL atomicity claim.
    """
    fd = os.open(str(config_file) + '.managed-save.lock',
                 os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'a') as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise PreparationError('configuration save lock is not a regular file')
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        service = internal_publication_service()
        save_id = service.begin_config_save(actor, hashlib.sha256(content.encode('utf-8')).hexdigest())
        if cloud is not None:
            cloud.get_key('config.yml').set_contents_from_string(content)
        with open(config_file, 'w', encoding='utf-8') as target:
            target.write(content)
        return service.observe_config(configuration, save_id=save_id)


def internal_publication_service(api=False):
    """Internal protocol factory; default public adapters remain preview-only.

    Explicit image-bridge mode has its own restricted QualifiedPublication. Neither
    factory qualifies remaining composite/config/plugin writers for production.
    """
    return _service(api, publication=True)


def _service(api, *, publication):
    from flask import current_app
    from flask_login import current_user
    from docassemble.webapp.extensions import db
    from docassemble.webapp.database import dbtableprefix
    from docassemble.webapp import config

    def policy():
        allow_updates = current_app.config['ALLOW_UPDATES']
        if os.environ.get(ENVIRONMENT):
            # Managed setup deliberately disables LEGACY installers globally.
            # Only this restricted semantic path reads the operational setting.
            from docassemble.base.config import daconfig
            allow_updates = (daconfig.get('allow updates', True) is True and
                             os.environ.get('DAALLOWUPDATES', 'true') == 'true')
        return {'allowed': config.allowed, 'allow_updates': allow_updates,
                'developer_can_install': current_app.config['DEVELOPER_CAN_INSTALL']}

    def api_permission(actor, permission, admin):
        from docassemble.webapp.api.helpers import api_verify, get_api_info, get_api_key
        try:
            # Revalidate credential/constraints every time, not current_user.limits.
            if not api_verify() or str(current_user.id) != actor:
                return False
            info = get_api_info(user_id=int(actor), api_key=get_api_key())
            if not info:
                return False
            limits = info.get('permissions', [])
            # Empty scope is the legacy unrestricted token; nonempty scopes
            # constrain every actor, independently of current SQL roles/policy.
            return not limits or permission in limits or (
                permission == 'read_packages' and 'manage_packages' in limits)
        except Exception:
            return False  # Redis outage/revocation must not authorize preview.

    authorizer = PreviewAuthorizer(dbtableprefix, policy, api_permission if api else None)
    if os.environ.get(ENVIRONMENT):
        return QualifiedPublication(db.engine, authorizer, PackageProjection(dbtableprefix), dbtableprefix,
                                    configured_baseline())
    if publication:
        return Publication(db.engine, authorizer, PackageProjection(dbtableprefix), dbtableprefix)
    return Preparation(db.engine, authorizer, dbtableprefix)


RESTART_REQUIRED = (
    'Managed application requires maintenance and restart; restart=false is unsupported. '
    'No intent was changed. To prepare without applying, POST /api/package_preview '
    'with action=prepare, requirement, operation and idempotency_key, omitting restart; '
    'inspect the candidate before a separate explicit approval.'
)


def api_request(package=False):
    """Authenticate identity, then use current semantic authority for every action."""
    from flask import jsonify
    from docassemble.webapp.api.helpers import api_verify
    try:
        authenticated = api_verify()
    except Exception:
        authenticated = False
    if not authenticated:
        return jsonify(error='forbidden', code='forbidden'), 403
    return preview_request(api=True, package=package)


def package_change(upload_field='zip'):
    """Translate legacy request shapes into preparation, never publication."""
    from flask import request
    body = request.get_json(silent=True) if request.is_json else request.form
    if request.method == 'DELETE' and not body:
        body = request.args
    if not isinstance(body, Mapping) or (not body and not request.files):
        raise PreparationError('package request required')
    empty_browser_upload = (upload_field == 'zipfile' and body.get('giturl') and
        set(request.files) == {'zipfile'} and len(request.files.getlist('zipfile')) == 1 and
        not request.files['zipfile'].filename and request.files['zipfile'].stream.read(1) == b'')
    if request.files and not empty_browser_upload:
        allowed = {'restart', 'idempotency_key', 'key', 'requirement', 'pre_change_checkpoint'}
        if upload_field == 'zipfile':
            allowed |= {'csrf_token', 'submit', 'giturl', 'gitbranch', 'pippackage'}
            if body.get('giturl') or body.get('gitbranch') or body.get('pippackage'):
                raise PreparationError('unsupported managed package fields')
        if (request.method != 'POST' or set(request.files) != {upload_field} or
                len(request.files.getlist(upload_field)) != 1 or set(body) - allowed):
            raise PreparationError('unsupported managed package fields')
        return {'action': 'prepare', 'zip_upload': request.files[upload_field],
                'requirement': body.get('requirement'), 'restart': body.get('restart', True),
                'idempotency_key': body.get('idempotency_key'),
                'pre_change_checkpoint': body.get('pre_change_checkpoint', False)}
    if request.method == 'POST' and ('github_url' in body or 'giturl' in body):
        from docassemble.base.managed_git import legacy_git_declaration
        browser = upload_field == 'zipfile'
        url_field, branch_field = ('giturl', 'gitbranch') if browser else ('github_url', 'branch')
        allowed = {url_field, branch_field, 'requirement', 'restart', 'idempotency_key', 'key', 'pre_change_checkpoint'}
        if browser:
            allowed |= {'csrf_token', 'submit', 'pippackage'}
        if set(body) - allowed or body.get('pippackage') or url_field not in body:
            raise PreparationError('unsupported managed package fields')
        declaration = legacy_git_declaration(body.get('requirement'), body[url_field], body.get(branch_field))
        return {'action': 'prepare', 'requirement': declaration, 'restart': body.get('restart', True),
                'idempotency_key': body.get('idempotency_key') or (str(uuid.uuid4()) if browser else None),
                'pre_change_checkpoint': body.get('pre_change_checkpoint', False)}
    allowed = {'package', 'contribution_id'} if request.method == 'DELETE' else {'pip', 'update'}
    if set(body) - allowed - {'restart', 'idempotency_key', 'key', 'pre_change_checkpoint'}:
        raise PreparationError('unsupported managed package fields')
    if request.method == 'DELETE':
        declaration, operation = body.get('package'), 'remove'
    else:
        if ('pip' in body) == ('update' in body):
            raise PreparationError('provide exactly one pip or update instruction')
        operation = 'add' if 'pip' in body else 'update'
        declaration = body.get('pip' if operation == 'add' else 'update')
    return {'action': 'prepare', 'requirement': declaration, 'operation': operation,
            'contribution_id': body.get('contribution_id'),
            'restart': body.get('restart', True), 'idempotency_key': body.get('idempotency_key'),
                'pre_change_checkpoint': body.get('pre_change_checkpoint', False)}


def preview_request(api=False, package=False, upload_field='zip', zip_factory=None):
    from flask import request, jsonify
    from flask_login import current_user
    if not managed_installation():
        return ('File not found', 404)
    if not current_user.is_authenticated:
        return jsonify(error='forbidden', code='forbidden'), 403
    try:
        service = preview_service(api)
        actor = str(current_user.id)
        if request.method == 'GET' and zip_factory is None:
            revision = request.args.get('revision_id')
            attempt = request.args.get('attempt_id')
            if revision or attempt:
                if request.args.get('candidate_id'):
                    raise PreparationError('provide one status identity')
                for identity in (revision, attempt):
                    if identity:
                        validate_candidate_id(identity)
                return jsonify(service.publication_status(actor, revision_id=revision, attempt_id=attempt))
            candidate = request.args.get('candidate_id')
            if not candidate:
                raise PreparationError('candidate id required')
            validate_candidate_id(candidate)
            status = service.status(actor, candidate)
            if status['state'] == 'published':
                with service.engine.begin() as connection:
                    publications = service.tables['publication_result']
                    revision = connection.scalar(select(publications.c.revision_id).where(
                        publications.c.candidate_id == candidate))
                return jsonify(service.publication_status(actor, revision_id=revision))
            return jsonify(status)
        body = package_change(upload_field) if package else (
            request.get_json(silent=True) if request.is_json else request.form)
        if zip_factory is not None:
            body = dict(request.args, **body)
            body['action'] = 'prepare'
        if not isinstance(body, Mapping) or not body:
            raise PreparationError('preview request required')
        action = body.get('action', 'prepare')
        restart = body.get('restart', True)
        if not request.is_json and restart == 'true':
            restart = True
        if action in ('prepare', 'approve', 'retry') and restart is not True:
            raise PreparationError(RESTART_REQUIRED)
        if action == 'prepare':
            if zip_factory is not None:
                with zip_factory() as (path, declaration):
                    candidate = service.prepare_zip(actor, path, body.get('idempotency_key') or str(uuid.uuid4()),
                                                    declaration=declaration)
                return jsonify(service.status(actor, candidate)), 202
            if 'zip_upload' in body:
                # The client supplies bytes, never a filesystem path. The private
                # request temporary is removed even on authorization/build failure.
                with tempfile.TemporaryDirectory(prefix='managed-upload-') as directory:
                    path = Path(directory) / 'upload.zip'
                    body['zip_upload'].save(path)
                    path.chmod(0o600)
                    candidate = service.prepare_zip(actor, path,
                        body.get('idempotency_key') or (str(uuid.uuid4()) if not api else None),
                        declaration=body.get('requirement'),
                        pre_change_checkpoint=(body.get('pre_change_checkpoint', False) if request.is_json else
                                               body.get('pre_change_checkpoint') == 'true'))
                return jsonify(service.status(actor, candidate)), 202
            assignment = body.get('assign_owner', False)
            if not request.is_json and assignment == 'true':
                assignment = True
            release = None
            if body.get('operation') == 'upgrade':
                release = body.get('release')
                if isinstance(release, str):
                    try:
                        release = json.loads(release)
                    except ValueError:
                        raise PreparationError('submit one registry release selection')
                elif release is None:
                    release = {'mode': body.get('release_mode') or 'latest-stable'}
                    if body.get('release_version'):
                        release['version'] = body.get('release_version')
            candidate = service.prepare(actor, body.get('requirement'), body.get('idempotency_key'),
                operation=body.get('operation', 'add'), restart=restart,
                assign_owner=assignment, contribution_id=body.get('contribution_id'),
                release_selection=release, rollback_revision=body.get('rollback_revision'),
                pre_change_checkpoint=(body.get('pre_change_checkpoint', False) if request.is_json else
                                       body.get('pre_change_checkpoint') == 'true'),
                assign_owners=(body.get('assign_owners') if request.is_json else
                               [name.strip() for name in body.get('assign_owners', '').split(',') if name.strip()])
                              if body.get('operation') == 'rollback' else None)
            return jsonify(service.status(actor, candidate)), 202
        candidate = body.get('candidate_id')
        if action == 'cancel' and candidate:
            validate_candidate_id(candidate)
            service.cancel(actor, candidate)
            return jsonify(service.status(actor, candidate))
        if action in ('approve', 'retry'):
            if not isinstance(service, QualifiedPublication):
                raise PreparationError('approval unsupported until publication and executor qualification')
            if body.get('operation', 'add') not in ('add', 'update', 'remove', 'config', 'upgrade', 'rollback'):
                raise PreparationError('unsupported managed operation')
            if action == 'approve':
                validate_candidate_id(candidate)
                acknowledge = body.get('acknowledge_database_unchanged', False)
                if not request.is_json:
                    acknowledge = acknowledge == 'true'
                result = service.approve(actor, candidate, body.get('result_digest'), body.get('idempotency_key'),
                                        acknowledge_database_unchanged=acknowledge)
            else:
                revision = body.get('revision_id')
                validate_candidate_id(revision)
                result = service.retry(actor, revision, body.get('idempotency_key'))
            return jsonify(result), 202
        raise PreparationError('unsupported managed operation')
    except PreparationError as error:
        message = str(error)
        code = {
            'forbidden': 'forbidden', 'stale candidate': 'stale',
            'candidate expired': 'expired', 'installation busy': 'busy',
            'idempotency key body mismatch': 'idempotency-conflict',
            RESTART_REQUIRED: 'restart-required',
        }.get(message, 'rejected')
        # PreparationError messages are fixed, request-independent literals
        # (AST-verified), so they are safe client diagnostics. Unexpected
        # exceptions may embed declarations, SQL, credentials or bundle paths.
        return jsonify(error=message, code=code), 403 if code == 'forbidden' else 409
    except Exception:
        return jsonify(error='Managed request rejected.', code='rejected'), 409


def validate_candidate_id(candidate):
    try:
        if not isinstance(candidate, str) or str(uuid.UUID(candidate)) != candidate:
            raise ValueError()
    except ValueError:
        raise PreparationError('invalid candidate id') from None
