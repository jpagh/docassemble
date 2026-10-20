"""I1/I2: parent web-store identity and fenced application ingress."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os
import threading
import urllib.error
import urllib.request

import pytest

from docassemble_executor.admission import Admission, start_ingress
from docassemble_executor.paths import ExecutorPaths
from docassemble_executor.webapp import managed_ingress_environment


def layout(tmp_path, monkeypatch):
    names = ('DA_MANAGED_ARTIFACT_ROOT', 'DA_MANAGED_ARTIFACT_UID', 'DA_MANAGED_WEB_SECRET_ROOT',
             'DA_EXECUTOR_ARTIFACT_ROOT', 'DA_MANAGED_SECRET_ROOT', 'DA_QUALIFIED_IMAGE_BASELINE',
             'DA_DURABLE_WORK_STORE', 'DA_INDEX_CREDENTIAL_ROOT')
    for name in names:
        monkeypatch.delenv(name, raising=False)
    paths = ExecutorPaths.from_env()
    roots = {}
    for name, suffix in (('DA_MANAGED_ARTIFACT_ROOT', 'http-zip'),
                         ('DA_EXECUTOR_ARTIFACT_ROOT', 'worker-zip'),
                         ('DA_MANAGED_SECRET_ROOT', 'worker-git')):
        root = tmp_path / suffix
        root.mkdir(mode=0o700)
        roots[name] = str(root)
    roots['DA_MANAGED_ARTIFACT_UID'] = '33'
    if os.geteuid() == 0:
        os.chown(roots['DA_MANAGED_ARTIFACT_ROOT'], 33, 33)
    return paths, roots


@pytest.mark.skipif(os.geteuid() != 0, reason='actual www-data ingress provisioning needs root')
def test_default_secret_ingress_is_private_idempotent_and_not_worker_store(tmp_path, monkeypatch):
    paths, parent = layout(tmp_path, monkeypatch)
    original = dict(parent)
    forwarded = managed_ingress_environment(paths, parent)
    secret = Path(parent['DA_MANAGED_ARTIFACT_ROOT'] + '-secrets')
    assert secret.stat().st_uid == 33
    assert secret.stat().st_mode & 0o777 == 0o700
    assert forwarded == {'DA_MANAGED_ARTIFACT_ROOT': parent['DA_MANAGED_ARTIFACT_ROOT'],
        'DA_MANAGED_ARTIFACT_UID': '33', 'DA_MANAGED_SECRET_ROOT': str(secret),
        'DA_MANAGED_WEB_SECRET_ROOT': str(secret)}
    assert managed_ingress_environment(paths, parent) == forwarded
    assert parent == original
    assert 'DA_EXECUTOR_ARTIFACT_ROOT' not in forwarded
    assert forwarded['DA_MANAGED_SECRET_ROOT'] != parent['DA_MANAGED_SECRET_ROOT']


@pytest.mark.parametrize('fault', ['wrong-owner', 'wrong-mode', 'symlink', 'explicit-missing',
    'overlap', 'worker-overlap', 'cache-overlap', 'relative', 'wrong-uid'])
@pytest.mark.skipif(os.geteuid() != 0, reason='actual www-data ingress validation needs root')
def test_unsafe_ingress_refused_without_repair(tmp_path, monkeypatch, fault):
    paths, parent = layout(tmp_path, monkeypatch)
    secret = Path(parent['DA_MANAGED_ARTIFACT_ROOT'] + '-secrets')
    if fault in ('wrong-owner', 'wrong-mode'):
        secret.mkdir(mode=0o700)
        if fault == 'wrong-mode':
            os.chown(secret, 33, 33)
            secret.chmod(0o755)
    elif fault == 'symlink':
        secret.symlink_to(parent['DA_MANAGED_SECRET_ROOT'])
    elif fault == 'explicit-missing':
        parent['DA_MANAGED_WEB_SECRET_ROOT'] = str(tmp_path / 'explicit-missing')
    elif fault == 'overlap':
        parent['DA_MANAGED_WEB_SECRET_ROOT'] = parent['DA_MANAGED_ARTIFACT_ROOT']
    elif fault == 'worker-overlap':
        parent['DA_MANAGED_WEB_SECRET_ROOT'] = parent['DA_MANAGED_SECRET_ROOT']
    elif fault == 'cache-overlap':
        parent['DA_MANAGED_WEB_SECRET_ROOT'] = '/var/cache/docassemble/uv/http-secret'
    elif fault == 'relative':
        parent['DA_MANAGED_ARTIFACT_ROOT'] = 'relative'
    else:
        parent['DA_MANAGED_ARTIFACT_UID'] = '0'
    with pytest.raises(RuntimeError, match='unsafe managed web ingress'):
        managed_ingress_environment(paths, parent)
    if fault == 'wrong-mode':
        assert secret.stat().st_mode & 0o777 == 0o755
    if fault == 'wrong-owner':
        assert secret.stat().st_uid == 0
    if fault == 'explicit-missing':
        assert not Path(parent['DA_MANAGED_WEB_SECRET_ROOT']).exists()


@pytest.mark.parametrize('route', ['/config', '/', '/interview', '/i', '/list', '/logs', '/logfile/web.log'])
def test_required_routes_forward_with_writer_fencing(tmp_path, monkeypatch, route):
    monkeypatch.setenv('DA_QUALIFIED_IMAGE_BASELINE', '/operator/baseline.json')
    entered, release = threading.Event(), threading.Event()
    received = []

    class Backend(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            received.append((self.path, self.headers.get('X-DA-Accepted-Caller')))
            entered.set()
            assert release.wait(5)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'completed')

    backend = ThreadingHTTPServer(('127.0.0.1', 0), Backend)
    threading.Thread(target=backend.serve_forever, daemon=True).start()
    admission = Admission(tmp_path)
    admission.open(consumers=True)
    ingress = start_ingress(0, backend.server_port, admission, lambda: {})
    replies = []

    def call(path, method='POST'):
        req = urllib.request.Request(f'http://127.0.0.1:{ingress.server_port}{path}', method=method,
                                     headers={'X-DA-Accepted-Caller': 'forged'})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    client = threading.Thread(target=lambda: replies.append(call(route)))
    client.start()
    try:
        assert entered.wait(2)
        assert received[0][0] == route
        assert received[0][1] and received[0][1] != 'forged'
        for writer in ('/user/register', '/user/edit_user_profile', '/user/invite', '/manage_api',
                       '/privileges', '/userlist', '/api/user', '/api/privileges'):
            assert call(writer)[0] == 403, writer
        admission.close('mutation fence', mode='draining')
        assert call(route)[0] == 503
        assert not admission.drain(0.02)
        release.set()
        client.join(3)
        assert replies == [(200, b'completed')]
        assert admission.drain(1)
    finally:
        release.set()
        client.join(5)
        ingress.shutdown()
        ingress.server_close()
        backend.shutdown()
        backend.server_close()
