"""Public HTTP admission and non-destructive maintenance drain."""
import socket
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from docassemble_executor.admission import Admission, start_ingress


def test_maintenance_timeout_preserves_active_request_and_can_resume(tmp_path):
    started, release = threading.Event(), threading.Event()

    class Backend(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            started.set()
            release.wait(5)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'completed active work')

    backend = ThreadingHTTPServer(('127.0.0.1', 0), Backend)
    threading.Thread(target=backend.serve_forever, daemon=True).start()
    admission = Admission(tmp_path)
    admission.open()
    ingress = start_ingress(0, backend.server_port, admission, lambda: {})
    results = []
    client = threading.Thread(target=lambda: results.append(get(ingress.server_port)))
    client.start()
    try:
        assert started.wait(2)
        admission.close('requested', mode='draining')
        assert get(ingress.server_port)[0] == 503
        assert not admission.drain(0.05)
        # Reloading durable state does not reopen admission on controller death.
        assert Admission(tmp_path).state()['mode'] == 'draining'
        admission.open()  # timeout abort resumes the unchanged service
        release.set()
        client.join(3)
        assert results == [(200, b'completed active work')]
        admission.close('requested', mode='draining')
        assert admission.drain(1)
    finally:
        release.set()
        client.join(5)
        ingress.shutdown()
        ingress.server_close()
        backend.shutdown()
        backend.server_close()


def get(port, path='/'):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def test_disconnected_client_does_not_release_running_backend_request(tmp_path):
    started, release = threading.Event(), threading.Event()

    class Backend(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'started')
            self.wfile.flush()
            started.set()
            release.wait(5)
            self.wfile.write(b'completed')

    backend = ThreadingHTTPServer(('127.0.0.1', 0), Backend)
    threading.Thread(target=backend.serve_forever, daemon=True).start()
    admission = Admission(tmp_path)
    admission.open()
    ingress = start_ingress(0, backend.server_port, admission, lambda: {})
    client = socket.create_connection(('127.0.0.1', ingress.server_port))
    try:
        client.sendall(b'GET / HTTP/1.0\r\n\r\n')
        assert started.wait(2)
        client.close()
        admission.close('maintenance', mode='draining')
        assert not admission.drain(0.1)
        release.set()
        assert admission.drain(2)
    finally:
        release.set()
        client.close()
        ingress.shutdown()
        ingress.server_close()
        backend.shutdown()
        backend.server_close()


def test_closed_admission_serves_independent_liveness_without_runtime(tmp_path):
    admission = Admission(tmp_path)
    admission.close('maintenance')
    server = start_ingress(0, 1, admission, lambda: {'normal_ready': False})
    try:
        port = server.server_port
        assert get(port)[0] == 503
        assert get(port, '/health_check')[0] == 200
        assert get(port, '/health_check?ready=1')[0] == 400
        assert get(port, '/status.json')[0] == 200
    finally:
        server.shutdown()
        server.server_close()
