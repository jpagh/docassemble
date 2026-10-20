"""Source-named local installation journal/CLI regression boundaries."""
import pytest

from docassemble_executor.cli import build_parser
from docassemble_executor.journal import Journal, Operation, PHASE_MUTATION_STARTED


def test_installation_binding_and_terminal_survive_journal_round_trip(tmp_path):
    binding = {'attempt_id': 'attempt', 'revision_id': 'revision', 'fence': 1,
               'host_id': 'host', 'runtime_generation': 2, 'bundle_digest': 'a' * 64,
               'target': {'python': '3.12'}}
    journal = Journal()
    journal.begin_mutation(Operation(id='attempt', kind='published-materialization',
                                    revision='revision', binding=binding))
    journal.attempt_result = {'identity': binding, 'outcome': 'materialized-stopped',
                              'child_safe': True, 'normal_verified': False}
    path = tmp_path / 'lifecycle.json'
    journal.save(path)
    recovered = Journal.load(path)
    assert recovered.to_dict() == journal.to_dict()
    assert recovered.phase == PHASE_MUTATION_STARTED
    assert recovered.verified_revision is None  # Frozen bytes are NOT normal readiness.
    recovered.begin_recovery('operator-controlled')
    recovered.finish_recovery(True)
    recovered.save(path)
    assert Journal.load(path).attempt_result == journal.attempt_result
    assert recovered.verified_revision is None


@pytest.mark.parametrize('stage,reason', [
    ('gates', 'updates-disabled'), ('gates', 'baseline-invalid'),
    ('gates', 'target-mismatch'), ('gates', 'bundle-invalid'),
    ('context', 'context-load-failed'), ('drain', 'drain-failed'),
    ('sync', 'sync-failed'), ('inventory', 'inventory-mismatch'),
])
def test_installation_failure_reason_survives_journal_round_trip(tmp_path, stage, reason):
    journal = Journal(attempt_result={'outcome': 'blocked', 'failure': {'stage': stage, 'reason': reason}})
    path = tmp_path / 'lifecycle.json'
    journal.save(path)
    assert Journal.load(path).attempt_result == journal.attempt_result


@pytest.mark.parametrize('reason', ['updates-disabled', 'baseline-invalid', 'target-mismatch', 'bundle-invalid'])
def test_installation_poller_persists_redacted_gate_failure(tmp_path, monkeypatch, reason):
    import json
    from types import SimpleNamespace
    from docassemble_executor.installation import InstallationClaim, InstallationPoller
    from docassemble_executor.paths import ExecutorPaths
    from docassemble_executor.registry_builder import UV_VERSION

    for name, path in {'DA_ROOT': tmp_path, 'DA_MANAGED_STATE_DIR': tmp_path / 'state',
                       'DA_RUN_DIR': tmp_path / 'run', 'UV_CACHE_DIR': tmp_path / 'cache',
                       'DA_CONFIG_FILE': tmp_path / 'config.yml', 'DA_LOG_DIR': tmp_path / 'log'}.items():
        monkeypatch.setenv(name, str(path))
    monkeypatch.setenv('DA_UV_MANAGED', 'true')
    monkeypatch.setenv('DAALLOWUPDATES', 'true')
    paths = ExecutorPaths.from_env()
    paths.config_file.write_text('allow updates: ' + ('false' if reason == 'updates-disabled' else 'true') + '\n')
    baseline = {'core': {}, 'target': {'python': '3.12'}, 'tool': {'uv': UV_VERSION}}
    claim = InstallationClaim('attempt', 'revision', 1, 'host', 2,
        dict(baseline, publication={'result': {'bundle': {'digest': 'a' * 64}}}))
    trusted = dict(baseline, target={'python': '3.13'}) if reason == 'target-mismatch' else baseline
    baseline_path = tmp_path / 'baseline.json'
    if reason != 'baseline-invalid':
        baseline_path.write_text(json.dumps(trusted))
    monkeypatch.setattr('docassemble_executor.installation.subprocess.run',
                        lambda *a, **k: SimpleNamespace(stdout=f'uv {UV_VERSION}'.encode()))
    reports = []
    def report(receipt):
        reports.append(receipt)
        return 'reported'
    installation = SimpleNamespace(claim_installation=lambda _: claim, report_terminal=report)
    assert InstallationPoller(installation, paths, tmp_path / 'store', baseline_path).run_once() == 'reported'
    result = Journal.load(paths.journal_file).attempt_result
    assert result == reports[0]
    assert result['outcome'] == 'blocked'
    assert result['failure'] == {'stage': 'gates', 'reason': reason}
    assert result['mutation_started'] is False


@pytest.mark.parametrize('lost_report', [False, True])
def test_installation_poller_drain_timeout_preserves_normally_serving_runtime(tmp_path, monkeypatch, lost_report):
    import threading
    import urllib.request
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from types import SimpleNamespace

    from docassemble_executor.admission import Admission, start_ingress
    from docassemble_executor.authority import RuntimeLock
    from docassemble_executor.health import Check
    from docassemble_executor.installation import InstallationClaim, InstallationPoller
    from docassemble_executor.journal import PHASE_INSTALLATION_PREPARING
    from docassemble_executor.paths import ExecutorPaths
    from docassemble_executor.roles import RUNTIME_ROLES, INFRASTRUCTURE_ROLES
    from docassemble_executor.status import read_status_file, write_status

    for name, path in {'DA_ROOT': tmp_path, 'DA_MANAGED_STATE_DIR': tmp_path / 'state',
                       'DA_RUN_DIR': tmp_path / 'run', 'UV_CACHE_DIR': tmp_path / 'cache',
                       'DA_LOG_DIR': tmp_path / 'log'}.items():
        monkeypatch.setenv(name, str(path))
    monkeypatch.setenv('DA_DRAIN_TIMEOUT', '0.02')
    paths = ExecutorPaths.from_env()
    paths.ensure_directories()
    paths.runtime_venv.mkdir()
    marker = paths.runtime_venv / 'unchanged'
    marker.write_text('original runtime bytes')
    admission = Admission(paths.state_dir)
    admission.open(consumers=True)
    Journal(phase='verified', verified_revision='baseline').save(paths.journal_file)
    write_status(paths, {'mode': 'normal', 'normal_ready': True})
    (paths.run_dir / 'ready').touch()
    monkeypatch.setattr('docassemble_executor.roles.prepare_roles',
                        lambda *a, **k: [Check(role, True) for role in RUNTIME_ROLES + INFRASTRUCTURE_ROLES])
    monkeypatch.setattr('docassemble_executor.installation.sync_frozen',
                        lambda *a, **k: pytest.fail('drain abort must not sync'))
    started, release = threading.Event(), threading.Event()

    class Backend(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == '/slow':
                started.set()
                release.wait(5)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'unchanged normal service')

    backend = ThreadingHTTPServer(('127.0.0.1', 0), Backend)
    threading.Thread(target=backend.serve_forever, daemon=True).start()
    ingress = start_ingress(0, backend.server_port, admission, lambda: {})
    results = []
    def get(path):
        with urllib.request.urlopen(f'http://127.0.0.1:{ingress.server_port}{path}', timeout=5) as response:
            return response.read()
    client = threading.Thread(target=lambda: results.append(get('/slow')))
    claim = InstallationClaim('attempt', 'revision', 1, 'host', 2,
        {'publication': {'result': {'bundle': {'digest': 'a' * 64}}}, 'target': {'python': '3.12'}})
    reports = []
    def report(receipt):
        if lost_report and not reports:
            reports.append(receipt)
            raise RuntimeError('lost report')
        reports.append(receipt)
        return 'replayed' if len(reports) > 1 else 'reported'
    installation = SimpleNamespace(claim_installation=lambda _: claim, report_terminal=report)
    poller = InstallationPoller(installation, paths, tmp_path, tmp_path)
    monkeypatch.setattr(poller, '_gates', lambda _: tmp_path)
    client.start()
    try:
        assert started.wait(2)
        # A normal application consumer survives the drain. It is not an installer.
        with RuntimeLock(paths.state_dir, shared=True):
            if lost_report:
                with pytest.raises(RuntimeError, match='lost report'):
                    poller.run_once()
            else:
                assert poller.run_once() == 'reported'
            journal = Journal.load(paths.journal_file)
            assert journal.phase == PHASE_INSTALLATION_PREPARING
            assert not journal.mutation_possibly_partial()
            assert journal.attempt_result['outcome'] == 'aborted-before-sync'
            assert journal.attempt_result['mutation_started'] is False
            assert journal.attempt_result['child_safe'] is False
            assert journal.attempt_result['failure'] == {'stage': 'drain', 'reason': 'drain-aborted'}
            assert poller.run_once() == 'replayed'
            assert admission.state()['mode'] == 'open' and admission.state()['consumers'] is True
            assert get('/') == b'unchanged normal service'
            assert read_status_file(paths.status_file)['mode'] == 'normal'
            assert read_status_file(paths.status_file)['normal_ready'] is True
            assert (paths.run_dir / 'ready').exists()
            assert marker.read_text() == 'original runtime bytes'
            release.set()
            client.join(3)
            assert results == [b'unchanged normal service']
    finally:
        release.set()
        client.join(5)
        ingress.shutdown()
        ingress.server_close()
        backend.shutdown()
        backend.server_close()


def test_installation_cli_requires_trusted_store_and_baseline():
    args = build_parser().parse_args(['poll-installation', '--store', '/store',
                                     '--baseline', '/baseline', '--prefix', 'managed_', '--once'])
    assert args.command == 'poll-installation' and args.once
    assert args.store == '/store' and args.baseline == '/baseline' and args.prefix == 'managed_'
