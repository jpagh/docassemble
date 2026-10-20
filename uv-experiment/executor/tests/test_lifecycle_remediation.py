"""Published boot privacy, current startup policy and multi-head regressions."""
from dataclasses import replace
from types import SimpleNamespace
import json
import urllib.request

import pytest
import yaml

from docassemble_executor import controller
from docassemble_executor.config import DatabaseSettings
from docassemble_executor.journal import Journal
from docassemble_executor.paths import ExecutorPaths


def test_boot_yaml_failure_never_exposes_source_in_http_or_logs(tmp_path, monkeypatch, capsys):
    paths = replace(ExecutorPaths.from_env(), state_dir=tmp_path / 'state', run_dir=tmp_path / 'run',
                    config_file=tmp_path / 'config.yml', web_port=0)
    paths.config_file.write_text('db:\n  password: [SENTINEL_BOOT_SECRET\n')
    monkeypatch.setenv('DA_UV_MANAGED', 'true')
    def load(*args):
        yaml.safe_load(paths.config_file.read_text())
    monkeypatch.setattr(controller, 'load_context', load)
    monkeypatch.setattr('docassemble_executor.checkpoint_restore.require_restore_ready', lambda _: None)
    handlers = {}
    monkeypatch.setattr(controller.signal, 'signal', lambda signum, handler: handlers.update({signum: handler}))
    real_start = controller.start_maintenance_server
    received = []
    def start(port, provider):
        server = real_start(port, provider)
        for path in ('/status.json', '/health_status', '/recovery/status.json', '/'):
            with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}{path}') as response:
                received.append(response.read().decode())
        return server
    monkeypatch.setattr(controller, 'start_maintenance_server', start)
    monkeypatch.setattr(controller.time, 'sleep', lambda _: handlers[controller.signal.SIGTERM]())
    assert controller.boot(paths) == 1
    logs = capsys.readouterr()
    for value in [*received, logs.out, logs.err]:
        assert 'SENTINEL_BOOT_SECRET' not in value
        assert 'password:' not in value
    assert json.loads(received[0])['detail'].startswith('managed boot refused: configuration-invalid (line ')
    assert all('configuration-invalid' in value for value in received)


@pytest.mark.parametrize('error', [ValueError('SECRET'), OSError('SECRET'), KeyError('SECRET')])
def test_boot_failure_categories_do_not_include_exception_values(error):
    assert 'SECRET' not in controller.safe_boot_failure(error)


@pytest.mark.parametrize('rows', [[('known',), ('incompatible',)], [('',)], [(None,)]])
def test_database_revision_rejects_multiple_or_empty_heads(monkeypatch, rows):
    import psycopg2
    from docassemble_executor.alembic_probe import read_database_revision
    class Cursor:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def execute(self, sql):
            self.sql = sql
        def fetchone(self):
            return ('alembic_version',)
        def fetchall(self):
            assert self.sql == 'SELECT version_num FROM alembic_version LIMIT 2'
            return rows
    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def cursor(self):
            return Cursor()
    monkeypatch.setattr(psycopg2, 'connect', lambda **kw: Connection())
    with pytest.raises(ValueError, match='exactly one nonempty Alembic head'):
        read_database_revision(DatabaseSettings())


@pytest.mark.parametrize('kind', ['published-apply', 'published-materialization'])
def test_published_legacy_core_restore_is_refused_without_journal_changes(tmp_path, monkeypatch, kind):
    from docassemble_executor.journal import Operation
    paths = replace(ExecutorPaths.from_env(), state_dir=tmp_path, journal_file=tmp_path / 'lifecycle.json')
    journal = Journal(operation=Operation('attempt', kind, 'revision'))
    journal.begin_recovery('failed apply')
    journal.finish_recovery(True)
    journal.attempt_result = {'outcome': 'failed-stopped'}
    journal.save(paths.journal_file)
    before = paths.journal_file.read_bytes()
    context = SimpleNamespace(paths=paths, journal=Journal.load(paths.journal_file))
    monkeypatch.setattr(controller, 'load_context', lambda *a, **k: context)
    monkeypatch.setattr(controller, 'enter_maintenance', lambda *_: pytest.fail('published legacy restore must not drain'))
    assert controller.run_recovery(context, 'administrator-repair') == 'maintenance'
    assert paths.journal_file.read_bytes() == before


@pytest.mark.parametrize('change', [
    {'outcome': 'failed-stopped'}, {'normal_verified': True}, {'mutation_started': True},
    {'child_safe': True}, {'failure': {'stage': 'drain', 'reason': 'drain-failed'}},
])
def test_shared_retry_rule_requires_exact_confirmed_drain_abort(change):
    from docassemble.base.managed_qualification import installation_retry_eligible
    receipt = {'outcome': 'aborted-before-sync', 'normal_verified': False,
               'mutation_started': False, 'child_safe': False,
               'failure': {'stage': 'drain', 'reason': 'drain-aborted'}}
    assert installation_retry_eligible(receipt)
    assert not installation_retry_eligible(dict(receipt, **change))


@pytest.mark.parametrize('field,value', [
    ('result', 'failed'), ('attempt', 2), ('attempt', True), ('id', 'foreign:core:1'),
    ('core_baseline', {}), ('inventory', {}), ('inventory', ['not-a-mapping']),
])
def test_shared_retry_rule_requires_completed_identity_bound_recovery(field, value):
    from docassemble.base.managed_qualification import installation_retry_eligible
    receipt = {'identity': {'attempt_id': 'attempt'}, 'outcome': 'failed-stopped',
        'mutation_started': True, 'child_safe': True, 'normal_verified': False,
        'recovery': {'result': 'succeeded', 'attempt': 1, 'id': 'attempt:core:1',
                     'core_baseline': {'digest': 'core'}, 'inventory': {'core': '1'}}}
    assert installation_retry_eligible(receipt)
    receipt['recovery'][field] = value
    assert not installation_retry_eligible(receipt)


@pytest.mark.parametrize('automatic,cold_restore,expected', [
    (True, False, 'updates-disabled'), (False, True, 'updates-disabled'),
    (False, False, 'baseline-invalid'),
])
def test_execution_rechecks_current_automatic_policy_without_blocking_nonmutating_health(
        tmp_path, monkeypatch, automatic, cold_restore, expected):
    from docassemble_executor.installation import InstallationClaim, InstallationFailure, InstallationPoller
    paths = replace(ExecutorPaths.from_env(), config_file=tmp_path / 'config.yml')
    paths.config_file.write_text('{}')
    claim = InstallationClaim('attempt', 'revision', 1, 'host', 2, {},
                              automatic=automatic, cold_restore=cold_restore)
    monkeypatch.setenv('DA_UV_MANAGED', 'true')
    monkeypatch.setenv('DAALLOWUPDATES', 'true')
    monkeypatch.setenv('DAUPDATEONSTART', 'false')
    poller = InstallationPoller(None, paths, tmp_path, tmp_path / 'missing-baseline.json')
    with pytest.raises(InstallationFailure, match=expected):
        poller._gates(claim)
    # Health resumption still checks mandatory bytes, but is not automatic apply.
    with pytest.raises(InstallationFailure, match='baseline-invalid'):
        poller._gates(claim, mutation=False)
