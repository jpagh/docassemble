"""Independent HTTP admission; accepted requests retain drain leases to completion.

The admission record is durable controller authority, not a readiness projection.
A short OS mutex serializes closing admission with acquiring a request lease.
The lease lasts through forwarding the complete response, including client errors.
"""
from __future__ import annotations

import fcntl
import http.client
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from .maintenance import _page
from .state import _atomic_write_json


class Admission:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.path = state_dir / 'admission.json'
        self.mutex = state_dir / 'admission.lock'
        self.traffic = state_dir / 'traffic.lock'

    def state(self) -> dict:
        try:
            raw = json.loads(self.path.read_text())
            if raw['mode'] not in ('open', 'closed', 'preparing', 'draining', 'mutating'):
                raise ValueError('unknown admission mode')
            return raw
        except (OSError, ValueError, KeyError, TypeError):
            return {'mode': 'closed', 'detail': 'admission is missing or invalid'}

    def close(self, detail: str, mode: str = 'closed') -> None:
        if mode not in ('closed', 'draining', 'mutating'):
            raise ValueError('invalid closed admission mode')
        with self._mutex():
            _atomic_write_json(self.path, {'mode': mode, 'detail': detail}, mode=0o644)

    def prepare(self, *, expected_mode: str | None = None) -> bool:
        """Start fixed roles/probes only; public work remains closed."""
        from .capabilities import Capabilities
        Capabilities(self.state_dir).prepare()
        with self._mutex():
            if expected_mode is not None and self.state()['mode'] != expected_mode:
                return False
            for path in (self.traffic, self.state_dir / 'runtime.lock'):
                path.touch(exist_ok=True)
            _atomic_write_json(self.path, {'mode': 'preparing', 'detail': 'verifying all supported roles'}, mode=0o644)
            return True

    def open(self, *, consumers: bool = False, expected_mode: str | None = None) -> bool:
        from .capabilities import Capabilities
        Capabilities(self.state_dir).prepare()
        with self._mutex():
            if expected_mode is not None and self.state()['mode'] != expected_mode:
                return False
            # Read-only descriptors suffice for flock readers. Only the
            # controller can create/replace these root-owned authority files.
            for path in (self.traffic, self.state_dir / 'runtime.lock'):
                path.touch(exist_ok=True)
            _atomic_write_json(self.path, {'mode': 'open', 'detail': 'verified service', 'consumers': consumers}, mode=0o644)
            return True

    @contextmanager
    def _mutex(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with self.mutex.open('r' if self.mutex.exists() else 'a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    @contextmanager
    def request(self):
        with self.lease() as fd:
            yield fd is not None

    @contextmanager
    def lease(self, *, consumer: bool = False, launch: bool = False):
        def permitted(state):
            return ((launch and state['mode'] == 'preparing') or
                    (not launch and state['mode'] == 'open' and (not consumer or state.get('consumers') is True)))
        state = self.state()
        if not permitted(state):
            yield None
            return
        try:
            lease = self.traffic.open('r')
        except OSError:
            yield None
            return
        with lease:
            try:
                with self._mutex():
                    state = self.state()
                    allowed = permitted(state)
                    if allowed:
                        fcntl.flock(lease, fcntl.LOCK_SH)
            except OSError:
                allowed = False  # Unavailable authority fails closed.
            yield lease.fileno() if allowed else None  # Never explicitly unlock.

    def drain(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self.traffic.open('a') as handle:
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return True
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        return False
                    time.sleep(0.02)


_HOP_HEADERS = {'connection', 'keep-alive', 'proxy-authenticate',
                'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade'}


def start_ingress(port: int, backend_port: int, admission: Admission,
                  status_provider: Callable[[], dict]) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        # Do not read ahead: websocket frames arriving with the upgrade headers
        # must remain on the socket for the independent byte relay.
        rbufsize = 0

        def log_message(self, format, *args):  # noqa: A002
            pass

        def _send(self, code: int, content_type: str, payload: bytes):
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(payload)

        def _handle(self):
            path = self.path.split('?', 1)[0]
            import os
            from docassemble.base.managed_qualification import ENVIRONMENT, bridge_http_path
            state = admission.state()
            recovery = state['mode'] == 'open' and state.get('consumers') is not True
            if os.environ.get(ENVIRONMENT):
                if os.environ.get('CONTAINERROLE', 'all').strip(':') != 'all':
                    self._send(403, 'text/plain', b'unsupported experimental image-bridge topology')
                    return
                if not bridge_http_path(path, recovery=recovery):
                    self._send(403, 'text/plain', b'unsupported experimental image-bridge route')
                    return
            if path in ('/socket.io', '/socket.io/'):
                from .socket_ingress import relay
                relay(self, ('127.0.0.1', 5000))
                return
            if path == '/health_check' and 'ready=1' not in self.path:
                self._send(200, 'text/plain', b'OK')
                return
            projection = dict(status_provider(), admission=state, controller_alive=True)
            if state['mode'] != 'open':
                projection.update(normal_ready=False, recovery_available=False)
            if path == '/status.json' or (path == '/health_status' and state['mode'] != 'open'):
                if path == '/health_status':
                    projection['ok'] = projection.get('normal_ready') is True if 'ready=1' in self.path else True
                self._send(200, 'application/json', json.dumps(projection).encode())
                return
            if path == '/health_check':
                self._send(200 if projection.get('normal_ready') is True else 400, 'text/plain',
                           b'OK' if projection.get('normal_ready') is True else b'not ready')
                return
            from .capabilities import Capabilities, HEADER
            # Admission and caller issuance share one linearization point.
            # Recovery HTTP leases carry no normal-service capability.
            with Capabilities(admission.state_dir, role='web').accept(recovery_http=True) as caller:
                if caller is None:
                    projection = dict(status_provider(), admission=admission.state(), controller_alive=True,
                                      normal_ready=False, recovery_available=False)
                    if path == '/health_check':
                        self._send(400, 'text/plain', b'not ready')
                    elif path == '/health_status':
                        self._send(200, 'application/json', json.dumps(projection).encode())
                    else:
                        self._send(503, 'text/html; charset=utf-8', _page(status_provider()))
                    return
                connection = http.client.HTTPConnection('127.0.0.1', backend_port, timeout=3600)
                self._response_started = False
                try:
                    # This web-only slice deliberately excludes websocket upgrades.
                    if self.headers.get('Transfer-Encoding') or self.headers.get('Upgrade'):
                        self._send(400, 'text/plain', b'unsupported experimental request framing')
                        return
                    from .socket_ingress import read_body
                    body = read_body(self) or None
                    headers = {k: v for k, v in self.headers.items() if k.lower() not in _HOP_HEADERS}
                    # Never forward an untrusted client-supplied bearer.
                    headers = {k: v for k, v in headers.items() if k.lower() != HEADER.lower()}
                    if caller.name:
                        headers[HEADER] = caller.name
                    self._forward(connection, body, headers)
                    return
                except (OSError, ValueError, http.client.HTTPException) as error:
                    if not self._response_started:
                        try:
                            self._send(400 if isinstance(error, ValueError) else 502,
                                       'text/plain', b'invalid request or unavailable backend')
                        except OSError:
                            pass
                finally:
                    connection.close()
                    self.close_connection = True

        def _forward(self, connection, body, headers):
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            self._response_started = True
            connected = True
            try:
                self.send_response(response.status)
                for key, value in response.getheaders():
                    if key.lower() not in _HOP_HEADERS:
                        self.send_header(key, value)
                self.send_header('Connection', 'close')
                self.end_headers()
            except OSError:
                connected = False
            while chunk := response.read(65536):
                if connected and self.command != 'HEAD':
                    try:
                        self.wfile.write(chunk)
                    except OSError:
                        connected = False  # Still consume the accepted backend response.

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _handle

    server = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    threading.Thread(target=server.serve_forever, name='admission-http', daemon=True).start()
    return server
