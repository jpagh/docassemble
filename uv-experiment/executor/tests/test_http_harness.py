"""The proof must observe authentication redirects, not silently follow them."""

import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scenarios"))
from harness import Http


@pytest.fixture
def website():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/restricted":
                self.send_response(302)
                self.send_header("Location", "/login")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"login page")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield Http("http://127.0.0.1:" + str(server.server_port))
    server.shutdown()
    server.server_close()
    thread.join()


def test_proof_observes_rejected_access_without_following_redirect(website):
    status, headers, _ = website.get("/restricted")
    assert (status, headers.get("Location")) == (302, "/login")


def test_redirect_following_is_explicit(website):
    status, _, body = website.request("GET", "/restricted", follow=True)
    assert (status, body) == (200, "login page")


def test_runtime_layout_comes_from_protected_container_executor_not_host(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from harness import Docker, Evidence
    docker = Docker(Evidence(tmp_path))
    calls = []
    payload = {'venv': '/image/managed-runtime3.14', 'python': '/image/managed-runtime3.14/bin/python',
               'site_packages': '/image/managed-runtime3.14/lib/python3.14/site-packages'}
    def execute(container, command):
        calls.append((container, command))
        return SimpleNamespace(stdout=json.dumps(payload))
    monkeypatch.setattr(docker, 'exec', execute)
    monkeypatch.setattr(sys, 'version_info', (3, 10, 9))
    layout = docker.runtime_layout('already-running')
    assert layout.site_packages == payload['site_packages']
    assert layout.python == payload['python']
    assert calls[0][1][0] == '/usr/share/docassemble/executor-venv/bin/python'
    assert 'ExecutorPaths' in calls[0][1][2] and 'python_site_packages' in calls[0][1][2]
    assert len(calls) == 1 and not docker.owned_containers
    assert json.loads((tmp_path / 'runtime-layout.json').read_text()) == payload
