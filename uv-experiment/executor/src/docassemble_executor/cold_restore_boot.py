"""Frozen startup of restored committed desired work; never preparation/solving."""
from __future__ import annotations

import json
import os
from pathlib import Path
import uuid

from sqlalchemy import update

from .cold_restore import ColdRestoreError, require_incarnation
from .journal import Journal, utcnow
from .managed_backup import _sha
from .state import _atomic_write_json


def prepare_cold_boot(service, paths) -> None:
    """Caller holds MutationLock. Preflight outside DB; CAS before durable work.

    Restart of an uncertain restore does not reissue work. Normal executor claim,
    journal and schema safety gates continue to own all mutation authority.
    """
    require_incarnation(service, paths)
    local = json.loads((paths.state_dir / 'cold-restore.json').read_text())
    if local.get('state') == 'restoring':
        raise ColdRestoreError('interrupted input restore; explicit operator recovery required')
    if os.environ.get('CONTAINERROLE', 'all').strip(':') != 'all' or os.environ.get('DAREADONLYFILESYSTEM') == 'true':
        raise ColdRestoreError('cold restore requires writable single all-role deployment')
    mode = os.environ.get('DAUPDATEONSTART', 'true')
    if mode not in ('true', 'initial', 'false'):
        raise ColdRestoreError('invalid restore startup flag')
    with service.engine.begin() as connection:
        environment = service._environment(connection)
        restored = environment['snapshot']['cold_restore']
        if restored['state'] != 'pending':
            return
        revision, _ = service._published_revision(connection, environment['desired_revision_id'])
    if mode == 'false':
        raise ColdRestoreError('DAUPDATEONSTART=false cannot serve unverified restored intent')
    if os.environ.get('DAALLOWUPDATES', 'true') != 'true':
        raise ColdRestoreError('updates disabled; restored intent is not a local health proof')
    from .installation import InstallationClaim, InstallationPoller
    claim = InstallationClaim('restore-preflight', revision['id'], 0, restored['incarnation'],
                              environment['runtime_generation'], revision['snapshot'], cold_restore=True)
    poller = InstallationPoller(service, paths, Path(os.environ['DA_DURABLE_WORK_STORE']),
                                Path(os.environ['DA_QUALIFIED_IMAGE_BASELINE']))
    poller._gates(claim)
    inventory = json.loads((paths.state_dir / 'cold-restore-inventory.json').read_text())
    # Journals are mutable evidence, not immutable runtime objects. Other
    # mandatory bytes were classified at bootstrap; recheck before mutation.
    for name, entry in inventory['files'].items():
        if name.startswith('state/') or name in restored['unavailable_objects']:
            continue
        if _sha(Path(entry['source'])) != entry['sha256']:
            raise ColdRestoreError('restored managed material changed before startup')
    journal = Journal.load(paths.journal_file)
    if journal.schema_uncertain or journal.failure:
        raise ColdRestoreError('restored failure/schema evidence requires explicit repair')
    history = paths.state_dir / 'attempt-history' / ('restore-' + restored['incarnation'] + '.json')
    if not history.exists():
        _atomic_write_json(history, journal.to_dict())
    with service.engine.begin() as connection:
        current = service._environment(connection)
        if (current['snapshot'].get('cold_restore') != restored or current['desired_revision_id'] != revision['id'] or
                current['runtime_generation'] != environment['runtime_generation'] or current['active_attempt_id']):
            raise ColdRestoreError('restore preflight superseded')
        # Historical receipts cannot replay as new execution authority. Keep
        # their schema evidence separately and in the new installation request.
        journal.operation = None
        journal.attempt_result = None
        journal.phase = 'idle'
        journal.save(paths.journal_file)
        attempt_id = str(uuid.uuid4())
        record = {'phase': 'pending', 'reason': 'cold-restore', 'fence': 0,
                  'created_at': utcnow(), 'incarnation': restored['incarnation'],
                  'source_attempt_id': restored['source_attempt_id'], 'restore_schema': restored.get('schema_success'),
                  'checkpoint_database_revision': restored.get('checkpoint_database_revision')}
        connection.execute(service.tables['installation_attempt'].insert().values(
            id=attempt_id, revision_id=revision['id'], record=record))
        snapshot = dict(current['snapshot'], cold_restore=dict(restored, state='requested', attempt_id=attempt_id))
        connection.execute(update(service.tables['environment_state']).where(service.tables['environment_state'].c.id == 1).values(
            snapshot=snapshot, active_attempt_id=attempt_id))
