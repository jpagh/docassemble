"""Independent replacement-image startup, never historical-lock application."""
from __future__ import annotations

import json
import os
from pathlib import Path
from contextlib import nullcontext

from sqlalchemy import create_engine

from docassemble.base.managed_migration import ImageMigration, migration_status
from docassemble.base.managed_preparation import PreparationError

from .authority import MutationLock, RuntimeLock
from .config import load_config
from .journal import Journal
from .selected_baseline import load_baselines
from .state import _atomic_write_json


def observe_migration(context, *, locked=False):
    """Use the shared-state host lock before fencing old target authority.

    A completed historical receipt is archived, not replayed against a new
    target. Schema uncertainty/clearance survives independently of that receipt.
    """
    from .durable_work import configuration
    from .image_baseline import load_image_wheels, verify_image_recovery_core
    url, prefix = configuration(context.paths)
    target, _ = load_image_wheels(Path(os.environ['DA_QUALIFIED_IMAGE_BASELINE']), context.paths)
    engine = create_engine(url, connect_args={'connect_timeout': 5})
    try:
        config = load_config(context.paths.config_file)
        service = ImageMigration(engine, prefix, updates_enabled=config.get('allow updates', True) is True)
        with service.engine.begin() as connection:
            current = service._environment(connection)
            if not current['desired_revision_id'] or current['snapshot'].get('image_baseline') == target['image_baseline']:
                return migration_status(current['snapshot'])
        with nullcontext() if locked else MutationLock(context.paths.state_dir):
            journal = Journal.load(context.paths.journal_file)
            terminal = journal.attempt_result
            if journal.phase not in ('idle', 'verified') and not terminal and not journal.recovery:
                raise PreparationError('uncertain historical mutation requires reconciliation before replacement')
            evidence = journal.to_dict().get('schema_release')
            record = service.observe(target, schema_evidence=evidence)
            if not record or record['state'] == 'published':
                return record
            verify_image_recovery_core(context.paths)
            marker = context.paths.state_dir / 'replacement-image.json'
            identity = target['image_baseline']
            previous = json.loads(marker.read_text()) if marker.exists() else None
            if previous != identity:
                with RuntimeLock(context.paths.state_dir):
                    pass
                history = context.paths.state_dir / 'attempt-history' / ('replacement-' + identity['image_target'] + '.json')
                if not history.exists():
                    _atomic_write_json(history, journal.to_dict())
                journal.operation = None
                journal.attempt_result = None
                journal.phase = 'idle'
                journal.failure = None
                journal.recovery = None
                journal.save(context.paths.journal_file)
                # Never allow a previous image/web-selected recovery lock to
                # become fallback on this target, even when preparation fails.
                _atomic_write_json(context.paths.state_dir / 'published-baselines.json', {
                    'image_baseline': identity, 'selected_baseline': None,
                    'recovery_baseline': {'image_baseline': identity, 'core': target['core']},
                    'schema_mode': record['schema_mode'], 'migration': True,
                    'store': str(Path(os.environ['DA_DURABLE_WORK_STORE']).resolve())})
                _atomic_write_json(marker, identity)
            elif load_baselines(context.paths).get('image_baseline') != identity:
                raise PreparationError('replacement recovery evidence changed')
            service.prepare_observed()
            service.advance()
            with service.engine.begin() as connection:
                return migration_status(service._environment(connection)['snapshot'])
    finally:
        engine.dispose()
