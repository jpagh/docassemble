"""First adoption of legacy evidence; never an interactive approval or inventory import.

Original Package/grant/upload/Install rows are read, never rewritten. Classification
is deliberately conservative: dependency flags and installed versions prove no
request. The environment lock serializes observations, publication and repair.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
import uuid

from packaging.requirements import Requirement
from sqlalchemy import MetaData, Table, select, update

from docassemble.base.managed_preparation import GENERATIONS, PreparationError, digest, parse_registry_requirement
from docassemble.base.managed_publication import Publication
from docassemble.base.managed_git import (parse_supported_requirement, parse_git_requirement,
    legacy_git_declaration, git_credential_material, registry_declaration)
from docassemble.base.managed_direct import parse_direct_requirement
from docassemble.base.managed_config import dependency_snapshot, config_import_policy

SYSTEM_ACTOR = 'system:legacy-adoption'
POLICY = 'DA_UV_MANAGED=true:unambiguous-legacy-intent-v1'
DISCLOSURE = 'Historical dependency versions cannot be reconstructed; a new complete environment is resolved.'


class AdoptionBlocked(PreparationError):
    pass


def legacy_evidence(connection, prefix):
    """Restricted original records; no app imports, Installed inventory is evidence only."""
    metadata = MetaData()
    records = {}
    for name in ('package', 'package_auth', 'install', 'uploads', 'user'):
        relation = Table(prefix + name, metadata, autoload_with=connection)
        query = select(relation.c.id) if name == 'user' else select(relation)
        records[name] = sorted([dict(row) for row in connection.execute(query).mappings()], key=lambda row: repr(sorted(row.items())))
    return json.loads(json.dumps(records, default=str))


def configuration_intent(configuration, environ, baseline):
    """Use the existing config policy parser, retaining source declarations separately."""
    raw = dict(configuration)
    if raw.get('config from') is None:
        raw.pop('config from', None)  # config.load treats the default null overlay as absent.
    if 'config from' in raw:
        overlay = raw['config from']
        if not isinstance(overlay, dict):
            raise AdoptionBlocked('resolve dependency configuration overlay before adoption')
        raw.update(overlay)
    raw = {key.replace('_', ' ') if isinstance(key, str) else key: value for key, value in raw.items()}
    declarations = raw.get('python packages')
    source_requirements = None
    if isinstance(declarations, list):
        # Parse supported sources structurally, but never feed credentials into the
        # public config-policy projection or registry solver.
        source_requirements = [parse_supported_requirement(value) for value in declarations]
        raw['python packages'] = [registry_declaration(value) for value in declarations]
    observed = dependency_snapshot(raw, environ=environ, image_config=baseline['config'])
    policy = config_import_policy(observed, baseline['config'])
    rows = observed['requirements']
    if source_requirements is not None:
        rows = [dict(row, declaration=value) for row, value in zip(rows, declarations)]
    return policy, observed, rows


def classify(evidence, config_rows, core):
    """Return all direct/config contributions and identities, or block the whole import."""
    users = {row['id'] for row in evidence['user']}
    identities, contributions, secrets, direct = {}, [], {}, {}

    def contribute(declaration, origin, package=None, artifact_ref=None):
        requirement = parse_supported_requirement(declaration)
        name = requirement.normalized_name
        if name in core:
            raise AdoptionBlocked('legacy request conflicts with image core; repair required')
        parsed = Requirement(declaration)
        if parsed.url:
            if parsed.url.startswith('git+'):
                _, source, credential = parse_git_requirement(declaration)
                if credential:
                    payload = git_credential_material(source['repository'], credential)
                    if name in secrets and secrets[name] != payload:
                        raise AdoptionBlocked('conflicting legacy Git credentials; repair required')
                    secrets[name] = payload
            else:
                _, source, _ = parse_direct_requirement(declaration)
                if name in direct and direct[name] != source:
                    raise AdoptionBlocked('conflicting legacy direct sources; repair required')
                direct[name] = source
        if name not in identities:
            identities[name] = dict(id=str(uuid.uuid4()), normalized_name=name,
                display_name=requirement.name, legacy_package_id=None, owner_id=None)
        identity = identities[name]
        if package:
            owners = {row['user_id'] for row in evidence['package_auth'] if
                row['package_id'] == package['id'] and row['authtype'] == 'owner' and row['user_id'] in users}
            if len(owners) > 1:
                raise AdoptionBlocked('ambiguous legacy owners; repair required')
            identity.update(legacy_package_id=package['id'], owner_id=str(next(iter(owners))) if owners else None)
        contributions.append(dict(id=str(uuid.uuid4()), identity_id=identity['id'], origin=origin,
            declaration=requirement.declaration, active=True, artifact_ref=artifact_ref))
        return name

    names = set()
    uploads = {}
    for package in evidence['package']:
        if not package['active'] or package['core']:
            continue
        requested = parse_supported_requirement(package['name'])
        name = requested.normalized_name
        if name in names:
            raise AdoptionBlocked('duplicate normalized legacy Package names; repair required')
        names.add(name)
        owners = any(row['package_id'] == package['id'] and row['authtype'] == 'owner' and
                     row['user_id'] in users for row in evidence['package_auth'])
        kind = package['type']
        limitation = package.get('limitation')
        explicit_source = kind in ('git', 'github', 'zip') or Requirement(package['name']).url is not None
        if not (owners or limitation or explicit_source):
            raise AdoptionBlocked('ambiguous legacy direct/dependency classification; repair required')
        if kind not in ('pip', 'git', 'github', 'zip'):
            raise AdoptionBlocked('ambiguous legacy source classification; repair required')
        declaration = package['name']
        if kind in ('git', 'github'):
            declaration = legacy_git_declaration(package['name'], package.get('giturl'), package.get('gitbranch'))
            if package.get('gitsubdir'):
                if '#' in declaration:
                    raise AdoptionBlocked('ambiguous legacy Git subdirectory; repair required')
                declaration += '#subdirectory=' + package['gitsubdir']
        elif kind == 'zip':
            upload = package.get('upload')
            if not isinstance(upload, int) or not any(row['indexno'] == upload for row in evidence['uploads']):
                raise AdoptionBlocked('legacy ZIP upload missing; restore or explicitly replace the request')
            uploads[name] = upload
        elif package.get('giturl') or package.get('upload') is not None:
            raise AdoptionBlocked('ambiguous legacy source fields; repair required')
        if kind == 'pip' and limitation and limitation.lstrip().startswith('@'):
            declaration = package['name'] + ' ' + limitation
            # Legacy direct references can be stored in Package.limitation.
            parse_supported_requirement(declaration)
            limitation = None
        contribute(declaration, 'interactive', package)
        if limitation:
            # Keep independent constraints even for Git/ZIP/direct requests.
            bound = parse_registry_requirement(requested.name + limitation)
            contribute(bound.declaration, 'interactive')
    for row in config_rows:
        contribute(row['declaration'], 'config')
    inputs = {'baseline': {'core': core}, 'contributions': contributions, 'change_operation': 'adoption'}
    from docassemble.base.managed_git import source_declarations
    from docassemble.base.managed_direct import direct_declarations
    source_declarations(inputs)
    direct_declarations(inputs)
    for name in identities:
        aliases = []
        for package in evidence['package']:
            try:
                if parse_supported_requirement(package['name']).normalized_name == name:
                    aliases.append(package)
            except PreparationError:
                continue  # Unrelated inactive inventory remains evidence only.
        if len(aliases) > 1 or (aliases and identities[name]['legacy_package_id'] is None):
            raise AdoptionBlocked('ambiguous legacy/config identity collision; repair required')
    return list(identities.values()), contributions, secrets, direct, uploads


def effective_storage(configuration, environ):
    """Credential-free SavedFile mode, evaluated before provider sanitization."""
    providers = {}
    precedence = str(environ.get('ENVIRONMENT_TAKES_PRECEDENCE', 'false')).lower().strip() == 'true'
    for provider, overrides in (
            ('s3', {'S3ENABLE': 'enable', 'S3ACCESSKEY': 'access key id',
                    'S3SECRETACCESSKEY': 'secret access key', 'S3BUCKET': 'bucket',
                    'S3REGION': 'region', 'S3ENDPOINTURL': 'endpoint url'}),
            ('azure', {'AZUREENABLE': 'enable', 'AZUREACCOUNTKEY': 'account key',
                       'AZUREACCOUNTNAME': 'account name', 'AZURECONTAINER': 'container',
                       'AZURECONNECTIONSTRING': 'connection string'})):
        value = configuration.get(provider, {})
        if not isinstance(value, dict):
            raise AdoptionBlocked('unsupported legacy storage configuration; repair required')
        value = dict(value)
        if precedence:
            for variable, key in overrides.items():
                if variable not in environ or environ[variable].strip() == '':
                    continue
                raw = environ[variable].strip()
                translated = {'true': True, 'True': True, 'false': False, 'False': False,
                              'null': None, 'None': None, 'Null': None}.get(raw, raw)
                if isinstance(translated, str) and re.fullmatch(r'-?[0-9]+', translated):
                    translated = int(translated)
                value[key] = translated
            if provider == 's3' and any(environ.get(variable, '').strip() != '' for variable in (
                    'S3_SSE_ALGORITHM', 'S3_SSE_CUSTOMER_ALGORITHM', 'S3_SSE_CUSTOMER_KEY', 'S3_SSE_KMS_KEY_ID')):
                # The application creates the nested provider mapping even if
                # encryption is its only environment-selected setting.
                value['server side encryption'] = True
        enabled = bool(value) and not ('enable' in value and not value['enable'])
        if provider == 'azure':
            enabled = enabled and value.get('account name') is not None and value.get('account key') is not None
        providers[provider] = {'enable': enabled}
    return providers


def upload_path(configuration, number):
    """SavedFile's local files/<12-digit hex, split every 3>/file.zip layout.

    Cloud-backed SavedFile downloads use application cloud clients. Do not mistake
    a stale /tmp cache for accessible durable cloud bytes: require restoration to
    local upload storage before adopting those requests.
    """
    if configuration.get('s3', {}).get('enable') or configuration.get('azure', {}).get('enable'):
        raise AdoptionBlocked('restore required ZIP bytes to local uploads before adoption')
    root = Path(configuration.get('uploads', '/usr/share/docassemble/files'))
    if not root.is_absolute() or number < 0 or number >= 16 ** 12:
        raise AdoptionBlocked('invalid legacy ZIP storage identity')
    digits = f'{number:012x}'
    path = root.joinpath(*(digits[index:index + 3] for index in range(0, 12, 3)), 'file.zip')
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise AdoptionBlocked('unsafe legacy ZIP storage; restore upload bytes')
    return path


class LegacyAdoption(Publication):
    def __init__(self, engine, prefix=''):
        super().__init__(engine, lambda *_: False, self._project, prefix)

    def _authorize(self, connection, actor, operation, candidate=None):
        if actor != SYSTEM_ACTOR or operation != 'approve' or not candidate or candidate['operation'] != 'adoption':
            raise PreparationError('automatic adoption is system-only')
        if os.environ.get('DA_UV_MANAGED') != 'true':
            raise PreparationError('managed experiment disabled')

    def _fresh(self, environment, candidate, connection=None):
        if not super()._fresh(environment, candidate, connection):
            return False
        if candidate['operation'] != 'adoption' or connection is None:
            return False
        return digest(legacy_evidence(connection, self.prefix)) == candidate['inputs']['adoption']['evidence_digest']

    def observe(self, baseline, configuration, *, environ=None):
        """Persist input/target generation and suppress unchanged restart failures.

        Caller refreshes configuration before observation/publication; no IO/solve
        runs under this transaction. A prior managed revision is never re-adopted.
        """
        if os.environ.get('DA_UV_MANAGED') != 'true':
            raise PreparationError('managed experiment disabled')
        environ = dict(os.environ if environ is None else environ)
        configuration = dict(configuration)
        overlay = configuration.get('config from')
        if isinstance(overlay, dict):
            configuration.update(overlay)
            configuration.pop('config from', None)
        keys = {'python packages', 'pip index url', 'pip extra index urls', 'pip trusted host',
                'private indexes', 'private bindings', 'private tls exceptions', 'config from',
                'uploads', 's3', 'azure', 'allow updates'}
        configuration = {key.replace('_', ' '): value for key, value in configuration.items()
                         if isinstance(key, str) and key.replace('_', ' ') in keys}
        configuration.update(effective_storage(configuration, environ))
        with self.engine.begin() as connection:
            # Serializes even the first singleton creation across executors.
            from sqlalchemy import text
            connection.execute(text('SELECT pg_advisory_xact_lock(160016)'))
            states = self.tables['environment_state']
            state = connection.execute(select(states).where(states.c.id == 1).with_for_update()).mappings().first()
            if state and state['desired_revision_id']:
                return {'state': 'managed'}
            evidence = legacy_evidence(connection, self.prefix)
            # Restrict captured env to the dependency inputs consumed by config.
            env_inputs = {key: environ[key] for key in ('PYTHONPACKAGES', 'PIPINDEXURL', 'PIPEXTRAINDEXURLS',
                'ENVIRONMENT_TAKES_PRECEDENCE', 'DAUPDATEONSTART', 'DAALLOWUPDATES') if key in environ}
            captured = {'evidence': evidence, 'configuration': configuration, 'environ': env_inputs, 'baseline': baseline}
            fingerprint = digest(captured)
            snapshot = dict(state['snapshot']) if state else dict(baseline,
                desired_inventory=baseline['core'], runtime={'kind': 'unknown', 'inventory': {}})
            previous = snapshot.get('adoption', {})
            if previous.get('fingerprint') == fingerprint:
                return dict(previous)
            if state and (state['active_attempt_id'] or not previous):
                raise PreparationError('existing managed evidence requires repair, not adoption')
            generation = state['input_generation'] + 1 if state else 1
            record = dict(captured, fingerprint=fingerprint, generation=generation, state='observed',
                system_actor=SYSTEM_ACTOR, enabling_policy=POLICY, selected_git={}, retry=0)
            if previous:
                snapshot['adoption_history'] = snapshot.get('adoption_history', []) + [previous]
            snapshot.update(baseline, adoption=record)
            if state:
                connection.execute(update(states).where(states.c.id == 1).values(snapshot=snapshot,
                    input_generation=generation, target_generation=state['target_generation'] + 1))
            else:
                connection.execute(states.insert().values(id=1, managed=True, desired_revision_id=None,
                    active_attempt_id=None, publication_generation=0, input_generation=1,
                    target_generation=1, runtime_generation=1, snapshot=snapshot))
            return record

    def request_retry(self, actor, *, authorize=None):
        """Administrator-authorized fresh preparation, NOT exact published retry."""
        with self.engine.begin() as connection:
            state = self._environment(connection)
            self._require_config_admin(connection, actor)
            if authorize:
                authorize(connection, actor, 'prepare', {'requester_id': actor, 'change_operation': 'repair'})
            if os.environ.get('DA_UV_MANAGED') != 'true':
                raise PreparationError('managed experiment disabled')
            record = dict(state['snapshot'].get('adoption', {}))
            if state['desired_revision_id'] or record.get('state') != 'failed':
                raise PreparationError('fresh adoption retry requires failed unpublished preparation')
            record.update(state='observed', retry=record['retry'] + 1, retry_requested_by=actor)
            snapshot = dict(state['snapshot'], adoption=record)
            connection.execute(update(self.tables['environment_state']).where(
                self.tables['environment_state'].c.id == 1).values(snapshot=snapshot))

    def _record(self, generation, **values):
        with self.engine.begin() as connection:
            state = self._environment(connection)
            if state['desired_revision_id'] or state['input_generation'] != generation:
                raise PreparationError('stale adoption inputs')
            if 'selected_git' in values:
                existing = state['snapshot']['adoption']['selected_git']
                if any(name in values['selected_git'] and values['selected_git'][name] != source
                       for name, source in existing.items()):
                    raise PreparationError('adoption source selection changed')
                values['selected_git'] = {**existing, **values['selected_git']}
            record = dict(state['snapshot']['adoption'], **values)
            connection.execute(update(self.tables['environment_state']).where(
                self.tables['environment_state'].c.id == 1).values(snapshot=dict(state['snapshot'], adoption=record)))
            return record

    def prepare_observed(self, *, resolve_git, artifact_root=None, secret_root=None,
                         retain_upload=None, stage_secret=None):
        """Prepare all original contributions, retaining ZIPs before queued solve.

        Network/ref selection and archive metadata inspection run outside SQL.
        Selected refs persist before solving, so failures/restarts cannot advance
        an already selected ref. A stale generation cannot publish its results.
        """
        with self.engine.begin() as connection:
            state = self._environment(connection)
            record = state['snapshot'].get('adoption', {})
            if state['desired_revision_id'] or record.get('state') != 'observed':
                return record.get('state', 'managed')
            generation = state['input_generation']
        staged = []
        candidate_id = None
        try:
            mode = record['environ'].get('DAUPDATEONSTART', 'true')
            if mode not in ('true', 'initial'):
                raise AdoptionBlocked('automatic adoption requires DAUPDATEONSTART=true or initial; no verified managed initialization exists')
            if record['configuration'].get('allow updates', True) is not True or record['environ'].get('DAALLOWUPDATES', 'true') != 'true':
                raise AdoptionBlocked('updates disabled; enable updates before adopting legacy requests')
            policy, observed, config_rows = configuration_intent(record['configuration'], record['environ'], record['baseline'])
            identities, contributions, secrets, direct, uploads = classify(record['evidence'], config_rows, record['baseline']['core'])
            from docassemble.base.managed_git import source_declarations
            baseline = dict(state['snapshot'], config=policy)
            baseline.pop('adoption', None)
            baseline.pop('adoption_history', None)
            inputs = dict(baseline=baseline, parent_id=None, generations={key: state[key] for key in GENERATIONS},
                change_operation='adoption', contributions=contributions, committed_contributions=[],
                ownership_effects={}, adoption={'evidence_digest': digest(record['evidence']), 'fingerprint': record['fingerprint'],
                    'identities': identities, 'generation': generation, 'retry': record['retry'],
                    'system_actor': SYSTEM_ACTOR, 'enabling_policy': POLICY, 'disclosure': DISCLOSURE,
                    'ownerless': [row['normalized_name'] for row in identities if row['owner_id'] is None]},
                adoption_direct_sources=direct, config_import={'snapshot': observed, 'generation': generation,
                    'digest': observed['digest']})
            selected = dict(record['selected_git'])
            for name, source in source_declarations(inputs).items():
                if name not in selected:
                    selected[name] = resolve_git(name, source, secrets.get(name))
                    self._record(generation, selected_git=selected)
            if selected:
                baseline['git_sources'] = selected
                inputs['adoption']['selected_git'] = selected
            from docassemble.base.managed_artifacts import (artifact_root as checked_root, open_regular,
                file_digest, distribution_identity, retain_artifact)
            artifacts = []
            if uploads:
                if artifact_root is None:
                    raise AdoptionBlocked('explicit artifact store required for legacy ZIPs')
                root = Path(artifact_root) if retain_upload else checked_root(Path(artifact_root))
                for name, number in uploads.items():
                    path = upload_path(record['configuration'], number)
                    with open_regular(path) as handle:
                        identity = distribution_identity(handle)
                        reference = file_digest(handle)
                    if identity['normalized_name'] != name:
                        raise AdoptionBlocked('legacy ZIP metadata identity differs; repair required')
                    for row in contributions:
                        if row['identity_id'] == next(item['id'] for item in identities if item['normalized_name'] == name):
                            row['artifact_ref'] = reference
                    artifacts.append((path, reference))
            from docassemble.base.managed_qualification import require_qualified_inputs
            require_qualified_inputs(inputs, record['baseline'])
            if secrets and secret_root is None:
                raise AdoptionBlocked('explicit Git credential store required for adoption')
            candidate_id = str(uuid.uuid4())
            with self.engine.begin() as connection:
                current = self._environment(connection)
                if (current['input_generation'] != generation or current['desired_revision_id'] or
                        current['snapshot']['adoption']['state'] != 'observed' or
                        legacy_evidence(connection, self.prefix) != record['evidence']):
                    raise PreparationError('stale adoption inputs')
                # Another preparer may have selected a ref while this one was outside
                # SQL. Never overwrite the first durable source selection.
                if current['snapshot']['adoption']['selected_git'] != selected:
                    raise PreparationError('adoption source selection changed')
                for name, payload in secrets.items():
                    reference = str(uuid.uuid4())
                    root_secret = Path(secret_root) if stage_secret else self._secret_root(secret_root)
                    inputs.setdefault('git_credentials', {})[name] = reference
                    staged.append((root_secret, reference, payload))
                connection.execute(self.tables['candidate'].insert().values(id=candidate_id,
                    requester_id=SYSTEM_ACTOR, operation='adoption', request_digest=record['fingerprint'],
                    parent_id=None, **inputs['generations'], inputs=inputs, state='queued', fence=0))
                for path, reference in artifacts:
                    if retain_upload:
                        retained = retain_upload(root, reference, path)
                    else:
                        with open_regular(path) as handle:
                            retained = retain_artifact(root, reference, handle)
                    self._register_material(connection, candidate_id, str(uuid.uuid4()), str(retained))
                for root_secret, reference, _ in staged:
                    self._register_material(connection, candidate_id, reference, str(root_secret / reference))
                snapshot = dict(current['snapshot'], adoption=dict(current['snapshot']['adoption'],
                    state='queued', candidate_id=candidate_id))
                connection.execute(update(self.tables['environment_state']).where(
                    self.tables['environment_state'].c.id == 1).values(snapshot=snapshot))
            for material in staged:
                if stage_secret:
                    stage_secret(candidate_id, *material)
                else:
                    self._stage_queued_secret(candidate_id, *material)
            return 'queued'
        except Exception as error:
            failure = (str(error) if isinstance(error, AdoptionBlocked) else
                'legacy intent/source preparation blocked; inspect original records, constraints and required uploads, repair then request fresh preparation')
            with self.engine.begin() as connection:
                current = self._environment(connection)
                current_record = current['snapshot'].get('adoption', {})
                if (current['desired_revision_id'] or current['input_generation'] != generation or
                        current_record.get('retry') != record['retry'] or
                        (current_record.get('state') != 'observed' and current_record.get('candidate_id') != candidate_id)):
                    return 'fenced'
                if candidate_id and current_record.get('candidate_id') == candidate_id:
                    candidate = self._candidate(connection, candidate_id)
                    if candidate['state'] in ('queued', 'preparing'):
                        connection.execute(update(self.tables['candidate']).where(
                            self.tables['candidate'].c.id == candidate_id).values(state='failed'))
                        self._invalidate(connection, candidate_id)
                connection.execute(update(self.tables['environment_state']).where(
                    self.tables['environment_state'].c.id == 1).values(snapshot=dict(current['snapshot'],
                        adoption=dict(current_record, state='failed', failure=failure))))
            return 'failed'

    def advance(self):
        """Publish an immutable ready result, or durably stop an unchanged failure."""
        with self.engine.begin() as connection:
            state = self._environment(connection)
            record = state['snapshot'].get('adoption', {})
            if state['desired_revision_id']:
                return 'managed'
            if record.get('state') != 'queued':
                return record.get('state', 'unavailable')
            candidate = self._candidate(connection, record['candidate_id'])
            self._expire(connection, candidate, self._now(connection))
            phase = candidate['state']
        if phase in ('failed', 'expired', 'cancelled'):
            self._record(record['generation'], state='failed', failure='complete environment preparation failed; repair inputs or request fresh preparation')
            return 'failed'
        if phase == 'ready':
            try:
                self._publish(SYSTEM_ACTOR, candidate['id'], candidate['result_digest'], candidate['id'], system=True)
            except PreparationError as error:
                if str(error) == 'installation busy':
                    return 'busy'
                self._record(record['generation'], state='failed', failure='adoption publication stale or blocked; refresh inputs and request fresh preparation')
                return 'failed'
            return 'published'
        return phase

    def _project(self, connection, actor, candidate):
        inputs = candidate['inputs']
        if digest(legacy_evidence(connection, self.prefix)) != inputs['adoption']['evidence_digest']:
            raise PreparationError('legacy evidence changed')
        for name in ('package_identity', 'requirement_contribution'):
            if connection.execute(select(self.tables[name])).first():
                raise PreparationError('partial managed records require explicit repair')
        packages = Table(self.prefix + 'package', MetaData(), autoload_with=connection)
        for captured in inputs['adoption']['identities']:
            identity = dict(captured)
            if identity['legacy_package_id'] is None:
                # As in config import, a compatibility projection is ownerless
                # and inactive. It does not invent interactive intent or grants.
                identity['legacy_package_id'] = connection.scalar(packages.insert().values(
                    name=identity['display_name'], type='pip', active=False, core=False,
                    dependency=False, limitation=None).returning(packages.c.id))
            connection.execute(self.tables['package_identity'].insert().values(**identity))
        for contribution in inputs['contributions']:
            connection.execute(self.tables['requirement_contribution'].insert().values(**contribution))
        materials = self.tables['candidate_material']
        for contribution in inputs['contributions']:
            if contribution['artifact_ref']:
                rows = connection.execute(select(materials).where(materials.c.candidate_id == candidate['id'],
                    materials.c.usable.is_(True), materials.c.deleted.is_(False))).mappings().all()
                matches = [row for row in rows if row['path'].endswith('/' + contribution['artifact_ref'])]
                if len(matches) != 1:
                    raise PreparationError('retained legacy ZIP unavailable')
                connection.execute(update(materials).where(materials.c.id == matches[0]['id']).values(committed=True))
        return {'legacy_records_unchanged': True, 'ownerless': inputs['adoption']['ownerless']}


def retained_adoption_record(record):
    """Retain verifiable provenance, not duplicate Git authentication in revisions.

    Original rows remain untouched in the legacy DB; required authentication is
    promoted separately as scoped material. Failed raw capture stays restricted
    until publication and never enters a worker input or public projection.
    """
    retained = {key: value for key, value in record.items()
                if key not in ('configuration', 'environ', 'baseline', 'evidence')}
    original = record.get('evidence', {})
    retained['evidence_digest'] = digest(original)
    packages = []
    for package in original.get('package', []):
        try:
            name = parse_supported_requirement(package['name']).normalized_name
        except PreparationError:
            continue  # Unrelated invalid inventory has no managed projection.
        packages.append({key: package[key] for key in ('id', 'type', 'core')} | {'name': name})
    retained['evidence'] = {'package': packages}
    return retained


def adoption_status(snapshot):
    """Fixed public projection: never original records, URLs, paths or credentials."""
    record = snapshot.get('adoption', {})
    if not record:
        return None
    return {key: record.get(key) for key in ('state', 'generation', 'candidate_id', 'failure',
        'system_actor', 'enabling_policy', 'retry', 'disclosure', 'ownerless', 'selected_git')}
