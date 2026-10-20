"""Supervisor-owned durable work discovery for the explicitly configured image bridge.

No application imports or broker dispatch. Unknown installation work stays fenced;
Supervisor restart is not permission to reclaim it or retry schema/setup.
"""
from __future__ import annotations

import os
from pathlib import Path
import time

from sqlalchemy import create_engine, select, update
from sqlalchemy.engine import URL

from docassemble.base.managed_preparation import runtime_status, installation_failure

from .admission import Admission
from .boot_observation import installation_host_id
from .config import load_config, database_settings
from .controller import _remove_ready
from .installation import Installation, InstallationBusy, InstallationPoller
from .journal import utcnow
from .paths import ExecutorPaths
from .preparation_poller import guarded_preparation_once
from .status import read_status_file, write_status
from docassemble.base.managed_adoption import adoption_status
from docassemble.base.managed_migration import migration_status


def configuration(paths: ExecutorPaths):
    config = load_config(paths.config_file)
    db = database_settings(config)
    url = os.environ.get('DA_PREPARATION_DATABASE_URL') or URL.create(
        'postgresql+psycopg2', username=db.user, password=db.password,
        host=db.host, port=db.port, database=db.name)
    return url, config.get('db', {}).get('table prefix') or ''


def publish_live_status(installation: Installation, paths: ExecutorPaths) -> None:
    """DB truth overlays local role qualification; history never implies readiness."""
    with installation.engine.begin() as connection:
        environment = installation._environment(connection)
        attempts = installation.tables['installation_attempt']
        attempt = connection.execute(select(attempts).where(
            attempts.c.id == environment['active_attempt_id'])).mappings().first()
        if environment['active_attempt_id'] and not attempt:
            raise ValueError('active attempt missing')
        if attempt is None:
            attempt = connection.execute(select(attempts).where(
                attempts.c.revision_id == environment['desired_revision_id']).order_by(
                attempts.c.record['created_at'].as_string().desc(), attempts.c.id).limit(1)).mappings().first()
        current = read_status_file(paths.status_file)
        runtime = runtime_status(environment)
        ready = bool(current.get('normal_ready') and not environment['active_attempt_id'] and
                     runtime['verified_running_revision_id'] == environment['desired_revision_id'] == current.get('applied_revision') and
                     Admission(paths.state_dir).state()['mode'] == 'open')
        from .controller import _web_alive
        adopting = bool((not environment['desired_revision_id'] and environment['snapshot'].get('adoption')) or
                        environment['snapshot'].get('migration', {}).get('state') == 'failed')
        recovery_available = bool(
            not ready and not environment['active_attempt_id'] and
            ((runtime['runtime_kind'] == 'core-only' and runtime['last_recovery_result'] == 'succeeded') or adopting) and
            current.get('recovery_available') and _web_alive(paths, 'recovery'))
        if not ready and not recovery_available:
            Admission(paths.state_dir).close('durable runtime is not normally verified; mutation/recovery remains fenced')
            _remove_ready(paths)
        elif recovery_available:
            # Successful core recovery keeps restricted administration reachable; no
            # normal workload/interview is admitted and no automatic retry is run.
            Admission(paths.state_dir).open()
        phase = attempt['record'].get('phase') if attempt else None
        terminal = attempt['record'].get('terminal', {}) if attempt else {}
        observation = {**runtime, 'normal_ready': ready, 'recovery_available': recovery_available,
            'installation': 'pending' if phase == 'pending' else 'in-progress' if environment['active_attempt_id'] else
                            'verified-running' if ready else 'failed' if phase in ('failed', 'blocked', 'aborted') else 'not-verified',
            'active': bool(environment['active_attempt_id']), 'attempt_id': attempt['id'] if attempt else None,
            'attempt_phase': phase, 'failure': installation_failure(terminal), 'terminal_outcome': terminal.get('outcome'),
            'adoption': adoption_status(environment['snapshot']),
            'migration': migration_status(environment['snapshot']),
            'diverged': current.get('applied_revision') != environment['desired_revision_id'],
            'observed_at': installation._now(connection).isoformat()}
        hosts = installation.tables['host_environment']
        host_id = installation_host_id(paths)
        host = connection.execute(select(hosts).where(hosts.c.id == host_id).with_for_update()).mappings().first()
        generation = host['generation'] + 1 if host else 1
        observation.update(host_id=host_id, host_generation=generation)
        if host:
            connection.execute(update(hosts).where(hosts.c.id == host_id).values(generation=generation, observation=observation))
        else:
            connection.execute(hosts.insert().values(id=host_id, generation=generation, observation=observation))
        write_status(paths, dict(current, **observation, publication=observation,
            desired_revision=environment['desired_revision_id'], updated_at=utcnow(),
            mode='normal' if ready else 'recovery' if recovery_available else 'maintenance',
            locked_converged=ready,
            runtime_state='locked' if ready else 'unknown'))


def run_durable_work(paths: ExecutorPaths, kind: str) -> None:
    store = Path(os.environ['DA_DURABLE_WORK_STORE'])
    baseline = Path(os.environ['DA_QUALIFIED_IMAGE_BASELINE'])
    if not store.is_absolute() or not baseline.is_absolute() or os.environ.get('DA_UV_MANAGED') != 'true':
        raise ValueError('durable work requires explicit managed image bridge paths')
    url, prefix = configuration(paths)
    engine = create_engine(url, connect_args={'connect_timeout': 5})
    try:
        installation = Installation(engine, lambda *_: False, prefix)
        poller = None
        if kind == 'preparation':
            from .legacy_adoption import worker_root
            preparation_arguments = ['--store', str(store), '--image-baseline', str(baseline), '--prefix', prefix]
            for name, option in (('DA_EXECUTOR_ARTIFACT_ROOT', '--artifact-root'),
                                 ('DA_MANAGED_SECRET_ROOT', '--material-root')):
                if os.environ.get(name):
                    preparation_arguments += [option, str(worker_root(name))]
            if os.environ.get('DA_INDEX_CREDENTIAL_ROOT'):
                preparation_arguments += ['--index-credential-root', os.environ['DA_INDEX_CREDENTIAL_ROOT']]
            preparation_env = dict(os.environ, DA_PREPARATION_DATABASE_URL=(
                url.render_as_string(hide_password=False) if isinstance(url, URL) else url))
        elif kind == 'installation':
            poller = InstallationPoller(installation, paths, store, baseline)
        else:
            raise ValueError('unsupported durable work kind')
        while True:
            try:
                from .cold_restore import require_incarnation
                require_incarnation(installation, paths)
                adoption = None
                if kind == 'installation':
                    from .controller import load_context
                    from .legacy_adoption import observe_adoption, serve_adoption_failure
                    context = load_context(paths, start_infrastructure=False)
                    adoption = observe_adoption(context)
                    if adoption is None:
                        from .image_migration import observe_migration
                        adoption = observe_migration(context)
                    if adoption and adoption['state'] == 'failed':
                        serve_adoption_failure(context)
                mutation_busy = False
                if adoption and adoption['state'] != 'published':
                    # No desired revision exists yet. In particular a successful
                    # unbound core recovery is not a published installation to replay.
                    result = 'adoption-' + adoption['state']
                elif poller is not None:
                    try:
                        result = poller.run_once(retain_live_health=True)
                    except InstallationBusy:
                        mutation_busy = True
                        result = 'mutation-busy'
                else:
                    result = 'preparation-exit-' + str(guarded_preparation_once(paths, preparation_arguments, env=preparation_env))
                if kind == 'installation' and not mutation_busy:
                    publish_live_status(installation, paths)
                print(result, flush=True)
            except Exception:
                if kind == 'installation':
                    Admission(paths.state_dir).close('durable installation observation unavailable or fenced')
                    _remove_ready(paths)
                    current = read_status_file(paths.status_file)
                    # No stale current verification in local outage/health status.
                    write_status(paths, dict(current, mode='maintenance', normal_ready=False,
                        verified_running_revision_id=None, runtime_kind='unknown', runtime_state='unknown',
                        locked_converged=False, recovery_available=False,
                        publication=None, detail='durable installation observation unavailable or fenced'))
                    try:
                        publish_live_status(installation, paths)
                    except Exception:
                        pass  # DB outage: retain only the explicit local unknown projection.
                # Fixed diagnostic only; raw DB/build/config errors may contain secrets.
                print('durable work unavailable or fenced', flush=True)
            time.sleep(2)
    finally:
        engine.dispose()
