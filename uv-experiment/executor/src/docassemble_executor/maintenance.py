"""Static maintenance/failure endpoint served without runtime imports.

When core restoration or its checks fail, this is the only HTTP surface.  It
never advertises authenticated recovery availability and it keeps workload
startup impossible because no application process is launched.
"""

from __future__ import annotations

import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable


def _page(status: dict) -> bytes:
    failure = status.get("last_failure") or {}
    checks = status.get("checks") or []
    rows = "".join(
        "<li><strong>" + html.escape(str(check.get("name"))) + "</strong>: "
        + ("ok" if check.get("ok") else "failed")
        + " &mdash; " + html.escape(str(check.get("detail", ""))) + "</li>"
        for check in checks
    )
    database = status.get("database") or {}
    body = f"""<!DOCTYPE html>
<html><head><title>docassemble maintenance</title>
<style>body{{font-family:sans-serif;margin:2rem;max-width:60rem}}code{{background:#eee;padding:0.1rem 0.3rem}}</style>
</head><body>
<h1>docassemble is in maintenance mode</h1>
<p><strong>Normal service is not ready.</strong> New normal requests are blocked;
previously accepted requests may still be draining. Authenticated recovery administration
is <strong>not available</strong>.</p>
<h2>Failure evidence</h2>
<p>Phase: <code>{html.escape(str(failure.get("phase", "unknown")))}</code><br>
Revision: <code>{html.escape(str(failure.get("revision", "unknown")))}</code><br>
Message: {html.escape(str(failure.get("message", status.get("detail") or "not recorded")))}</p>
<h2>Desired / applied</h2>
<p>Desired revision: <code>{html.escape(str(status.get("desired_revision")))}</code><br>
Applied revision: <code>{html.escape(str(status.get("applied_revision")))}</code><br>
Core baseline: <code>{html.escape(str(status.get("core_baseline")))}</code></p>
<h2>Database compatibility</h2>
<p>Status: <code>{html.escape(str(database.get("status", "unknown")))}</code><br>
Detail: {html.escape(str(database.get("detail", "")))}</p>
<h2>Checks</h2>
<ul>{rows}</ul>
<p>Machine-readable status: <a href="/status.json">/status.json</a></p>
</body></html>"""
    return body.encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    status_provider: Callable[[], dict]

    def log_message(self, format, *args):  # noqa: A002 - signature from stdlib
        pass

    def _send(self, code: int, content_type: str, payload: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802 - stdlib naming
        path = self.path.split("?", 1)[0]
        status = self.status_provider()
        if path in ("/health_status", "/status.json", "/recovery/status.json"):
            self._send(200, "application/json", json.dumps(status, sort_keys=True).encode("utf-8"))
            return
        if path == "/health_check":
            if "ready=1" in self.path:
                self._send(400, "text/plain", b"not ready")
            else:
                self._send(200, "text/plain", b"OK")
            return
        if path in ("/", "/maintenance", "/recovery"):
            self._send(200, "text/html; charset=utf-8", _page(status))
            return
        self._send(404, "text/plain", b"not found")


def start_maintenance_server(port: int, status_provider: Callable[[], dict]) -> ThreadingHTTPServer:
    handler = type("MaintenanceHandler", (_Handler,), {"status_provider": staticmethod(status_provider)})
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    thread = threading.Thread(target=server.serve_forever, name="maintenance-http", daemon=True)
    thread.start()
    return server
