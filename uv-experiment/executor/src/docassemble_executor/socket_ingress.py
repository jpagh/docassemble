"""Runtime-free Socket.IO ingress: the actual service owns protocol admission.

Polling requests and websocket upgrades for existing transports must continue
while ordinary HTTP admission is closed. Fresh Engine.IO connections are gated
inside the qualified service. The relay holds no runtime lease of its own: a
transport, its callbacks and background work retain the service's authority.
"""
import select
import socket


def read_body(handler) -> bytes:
    if handler.headers.get('Transfer-Encoding'):
        raise ValueError('unsupported request framing')
    remaining = int(handler.headers.get('Content-Length', '0'))
    if remaining < 0:
        raise ValueError('invalid content length')
    chunks = []
    while remaining:
        chunk = handler.rfile.read(remaining)
        if not chunk:
            raise ValueError('incomplete request body')
        chunks.append(chunk)
        remaining -= len(chunk)
    return b''.join(chunks)


def relay(handler, endpoint: tuple[str, int]):
    started = False
    try:
        body = read_body(handler)
        with socket.create_connection(endpoint, timeout=10) as backend:
            upgrade = handler.headers.get('Upgrade', '').lower() == 'websocket'
            headers = [(key, value) for key, value in handler.headers.items()
                       if key.lower() not in ('connection', 'proxy-connection', 'x-da-accepted-caller')]
            headers.append(('Connection', 'Upgrade' if upgrade else 'close'))
            request = f'{handler.command} {handler.path} HTTP/1.1\r\n'
            request += ''.join(f'{key}: {value}\r\n' for key, value in headers)
            backend.sendall(request.encode('iso-8859-1') + b'\r\n' + body)
            backend.settimeout(3600)
            handler.connection.settimeout(3600)
            while True:
                readable, _, _ = select.select([backend, handler.connection], [], [], 3600)
                if not readable:
                    continue  # A relay timeout is never task completion authority.
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    if source is backend:
                        started = True
                        handler.connection.sendall(data)
                    else:
                        backend.sendall(data)
    except (OSError, ValueError) as error:
        if not started:
            try:
                handler._send(400 if isinstance(error, ValueError) else 503,
                              'text/plain', b'unavailable socket service or invalid framing')
            except OSError:
                pass
    finally:
        handler.close_connection = True
