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
