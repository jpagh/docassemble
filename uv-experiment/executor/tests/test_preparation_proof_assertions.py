"""Short native regressions for stale/non-discriminating container proof checks."""
import ast
import base64
import copy
import importlib.util
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import ssl
import subprocess
import threading
from types import SimpleNamespace

import pytest

from docassemble.base.managed_direct import direct_declarations
from docassemble_executor.direct_sources import download_source
from docassemble_executor.preparation import PreparationError

PREPARATION = Path(__file__).resolve().parents[2] / 'preparation'


def load(name, monkeypatch):
    monkeypatch.syspath_prepend(str(PREPARATION))
    spec = importlib.util.spec_from_file_location(name, PREPARATION / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('mutates', [False, True])
def test_builder_proof_conflict_reaches_validation_and_checks_submitted_input_unchanged(monkeypatch, mutates):
    proof = load('builder_proof', monkeypatch)
    inputs = {'baseline': {}, 'contributions': [], 'requirement': {'declaration': 'six==1.17.0'}}
    original = copy.deepcopy(inputs)
    class Builder:
        def build(self, conflict):
            assert direct_declarations(conflict) == {}
            assert conflict['contributions'] == [{'id': 'conflicting-six', 'declaration': 'six==1.16.0'}]
            assert conflict['requirement']['declaration'] == 'six==1.17.0'
            if mutates:
                conflict['contributions'].clear()
            raise PreparationError('registry preparation command failed')
    if mutates:
        with pytest.raises(AssertionError):
            proof.assert_conflict_rejected(Builder(), inputs)
    else:
        proof.assert_conflict_rejected(Builder(), inputs)
    assert inputs == original


@pytest.mark.parametrize('change', [None, {'normal_ready': True}, {'recovery_available': False},
                                  {'runtime_kind': 'normal'}, {'attempt_phase': 'succeeded'}])
def test_image_bridge_proof_requires_serving_restricted_recovery(monkeypatch, change):
    proof = load('image_bridge_proof', monkeypatch)
    status = {'normal_ready': False, 'recovery_available': True, 'runtime_kind': 'core-only', 'attempt_phase': 'failed'}
    if change:
        status.update(change)
        with pytest.raises(AssertionError):
            proof.assert_recovery_status(status)
    else:
        proof.assert_recovery_status(status)


@pytest.mark.parametrize('code,ready', [(400, False), (503, False), (200, False), (400, True)])
def test_image_bridge_proof_requires_established_nonready_health_and_absent_ready_file(
        monkeypatch, tmp_path, code, ready):
    proof = load('image_bridge_proof', monkeypatch)
    if ready:
        (tmp_path / 'ready').touch()
    requested = []
    def request(path):
        requested.append(path)
        return code, ''
    if code == 400 and not ready:
        proof.assert_recovery_readiness(request, SimpleNamespace(run_dir=tmp_path))
    else:
        with pytest.raises(AssertionError):
            proof.assert_recovery_readiness(request, SimpleNamespace(run_dir=tmp_path))
    assert requested == ['/health_check?ready=1']


@pytest.mark.parametrize('wrong', [None, 'missing', 'foreign-attempt', 'foreign-schema', 'stale', 'matching', 'state'])
def test_image_bridge_proof_requires_clearance_matrix_and_preserved_pending_authority(monkeypatch, tmp_path, wrong):
    proof = load('image_bridge_proof', monkeypatch)
    clearance = {'format': 1, 'attempt_id': 'failed', 'schema_identity': 'schema-job', 'observed_schema': 'current'}
    path = tmp_path / 'schema-clearance.json'
    seen, checked = [], []
    labels = ['missing', 'foreign-attempt', 'foreign-schema', 'stale', 'matching']
    def run_once():
        document = json.loads(path.read_text()) if path.exists() else None
        seen.append(document)
        if document is not None:
            assert path.stat().st_mode & 0o777 == 0o600
        label = labels[len(seen) - 1]
        return 'reported' if label == wrong or document == clearance else 'schema-clearance-required'
    def unchanged():
        checked.append(len(seen))
        assert wrong != 'state', 'pending authority was consumed'
    poller = SimpleNamespace(paths=SimpleNamespace(state_dir=tmp_path), run_once=run_once)
    if wrong is None:
        proof.assert_retry_clearance_matrix(poller, clearance, unchanged)
        assert seen == [None, dict(clearance, attempt_id='foreign-attempt'),
                        dict(clearance, schema_identity='foreign-schema-job'),
                        dict(clearance, observed_schema='stale-database-revision'), clearance]
        assert checked == [1, 2, 3, 4]
    elif wrong == 'matching':
        # A correct clearance must proceed; another refusal is a failed proof.
        poller.run_once = lambda: 'schema-clearance-required'
        with pytest.raises(AssertionError):
            proof.assert_retry_clearance_matrix(poller, clearance, unchanged)
    else:
        with pytest.raises(AssertionError):
            proof.assert_retry_clearance_matrix(poller, clearance, unchanged)


def test_image_bridge_proof_does_not_overwrite_preexisting_operator_clearance(monkeypatch, tmp_path):
    proof = load('image_bridge_proof', monkeypatch)
    path = tmp_path / 'schema-clearance.json'
    path.write_bytes(b'operator evidence')
    poller = SimpleNamespace(paths=SimpleNamespace(state_dir=tmp_path), run_once=lambda: pytest.fail('executor ran'))
    with pytest.raises(AssertionError, match='preexisting operator clearance'):
        proof.assert_retry_clearance_matrix(poller, {}, lambda: pytest.fail('state callback ran'))
    assert path.read_bytes() == b'operator evidence'


@pytest.mark.parametrize('mutation', [None, 'revision', 'contribution', 'prior-terminal', 'slot', 'extra-attempt',
                                      'wrong-revision', 'wrong-phase', 'wrong-reason', 'replay'])
def test_core_recovery_retry_proof_requires_exact_retry_immutable_state_and_one_attempt(monkeypatch, mutation):
    proof = load('core_recovery_retry_proof', monkeypatch)
    before = {'environment_state': [{'id': 1, 'desired_revision_id': 'revision', 'active_attempt_id': None}],
              'revision': [{'id': 'revision', 'snapshot': {'immutable': True}}],
              'package_identity': [{'id': 'package'}],
              'requirement_contribution': [{'id': 'contribution', 'declaration': 'package==1'}],
              'installation_attempt': [{'id': 'failed', 'record': {'terminal': {'recovery': 'succeeded'}}}]}
    state = copy.deepcopy(before)
    retry = {'revision_id': 'revision', 'attempt_id': 'retry'}
    calls = []
    def publish(actor, revision, key):
        calls.append((actor, revision, key))
        if len(calls) == 1:
            state['environment_state'][0]['active_attempt_id'] = 'retry'
            state['installation_attempt'].append({'id': 'retry', 'revision_id': revision,
                                                 'record': {'phase': 'pending', 'reason': 'retry'}})
            if mutation == 'revision':
                state['revision'][0]['snapshot']['immutable'] = False
            elif mutation == 'contribution':
                state['requirement_contribution'][0]['declaration'] = 'package==2'
            elif mutation == 'prior-terminal':
                state['installation_attempt'][0]['record']['terminal'].clear()
            elif mutation == 'slot':
                state['environment_state'][0]['active_attempt_id'] = None
            elif mutation == 'extra-attempt':
                state['installation_attempt'].append({'id': 'extra'})
            elif mutation == 'wrong-revision':
                retry['revision_id'] = 'different'
            elif mutation in ('wrong-phase', 'wrong-reason'):
                field = 'phase' if mutation == 'wrong-phase' else 'reason'
                state['installation_attempt'][-1]['record'][field] = 'wrong'
        elif mutation == 'replay':
            state['installation_attempt'].append({'id': 'duplicate'})
        return retry
    monkeypatch.setattr(proof, 'retry_state', lambda _: copy.deepcopy(state))
    service = SimpleNamespace(retry=publish)
    if mutation is None:
        assert proof.assert_exact_revision_retry(service, 'admin', 'revision', 'key') == retry
        assert calls == [('admin', 'revision', 'key')] * 2
    else:
        with pytest.raises(AssertionError):
            proof.assert_exact_revision_retry(service, 'admin', 'revision', 'key')


@pytest.mark.parametrize('diagnostic', [
    'current environment or required recovery material unavailable; service blocked',
    'unlisted managed backup objects refused', None,
])
def test_cold_restore_missing_object_proof_discriminates_and_restores_bytes(monkeypatch, tmp_path, diagnostic):
    proof = load('cold_restore_proof', monkeypatch)
    directory = tmp_path / 'database.managed'
    target = directory / 'objects/image/core-r1/uv.lock'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'mandatory bytes')
    def inspect(dump, actual_directory, prefix):
        assert dump == tmp_path / 'database' and actual_directory == directory and prefix == 'test_'
        assert not list((directory / 'objects').rglob('*.held'))
        assert not any(path.is_file() for path in (directory / 'objects').rglob('*'))
        assert not target.exists()
        if diagnostic:
            raise proof.ColdRestoreError(diagnostic)
    monkeypatch.setattr(proof, 'inspect_backup', inspect)
    if diagnostic and diagnostic.startswith('current environment'):
        proof.assert_required_material_missing(tmp_path / 'database', directory, 'test_', 'image/core-r1/uv.lock')
    else:
        with pytest.raises(AssertionError):
            proof.assert_required_material_missing(tmp_path / 'database', directory, 'test_', 'image/core-r1/uv.lock')
    assert target.read_bytes() == b'mandatory bytes'
    assert not list(tmp_path.glob('held-restore-object-*'))


@pytest.mark.parametrize('archive', [False, True], ids=['wheel', 'zip-sdist'])
def test_direct_url_proof_fixture_requires_real_authenticated_tls_retrieval(monkeypatch, tmp_path, archive):
    proof = load('direct_url_proof', monkeypatch)
    if archive:
        fixture = load('zip_fixture', monkeypatch)
        data = fixture.distribution(tmp_path / 'ZipExample-1.0.zip', name='ZipExample', wheel=False).read_bytes()
    else:
        _, data = proof.wheel('ZipExample')
    certificate, key = tmp_path / 'ca.pem', tmp_path / 'key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key),
                    '-out', str(certificate), '-days', '1', '-subj', '/CN=localhost',
                    '-addext', 'subjectAltName=DNS:localhost'], check=True, capture_output=True)
    state = {'bytes': data, 'status': 200, 'requests': 0, 'authenticated_requests': 0,
             'authorization': 'Basic ' + base64.b64encode(('user:' + proof.TOKEN).encode()).decode()}
    server = ThreadingHTTPServer(('127.0.0.1', 0), proof.artifact_handler(state))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = 'https://localhost:' + str(server.server_port) + '/artifact'
    try:
        for rejected in (url, url.replace('https://', 'https://user:WRONG@')):
            with pytest.raises(PreparationError, match='artifact unavailable'):
                download_source(rejected, ca_file=certificate)
        assert state['authenticated_requests'] == 0
        credentialed = url.replace('https://', 'https://user:' + proof.TOKEN + '@')
        assert download_source(credentialed, ca_file=certificate) == data
        assert state['authenticated_requests'] == 1 and state['requests'] == 3
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_cold_restore_replacement_provisions_its_own_image_owned_bridge():
    """Restore never writes image-owned material, so the replacement must provision it."""
    tree = ast.parse((PREPARATION / 'cold_restore_proof.py').read_text())
    provisioned = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                   and getattr(node.func, 'id', None) == 'provision_image_baseline']
    assert len(provisioned) == 1, 'restore must provision exactly one bridge from its own image'
    assert [ast.unparse(argument) for argument in provisioned[0].args] == ['paths', 'DESCRIPTOR.parent']
    cleared = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
               and getattr(node.func, 'id', None) == 'prepare_cold_boot']
    assert len(cleared) == 1
    assert provisioned[0].lineno < cleared[0].lineno, 'the bridge must exist before any boot reads it'


def test_image_migration_proof_derives_the_release_from_the_provisioned_image_core():
    """The documented replacement pair is built with --build-arg RELEASE, not a literal."""
    source = (PREPARATION / 'image_migration_proof.py').read_text()
    assert "'1.10.15'" not in source
    calls = [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call)
             and getattr(node.func, 'id', None) == 'release_version']
    assert len(calls) == 1
    assert "json.loads(DESCRIPTOR.read_text())['baseline']['core']" in ast.unparse(calls[0])
