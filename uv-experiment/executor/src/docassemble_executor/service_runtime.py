"""Qualified service protocol adapters, imported only after executor admission.

Launch leases/guardians cover the whole process tree. Request/session scopes
cover nested accepted calls, even after closure or client disconnect. Services
stay available to accepted callers until *all* caller scopes finish; only then
may idle infrastructure exit. No accepted workload is signalled to claim drain.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Match socketserver's import order: patch before admission imports HTTP/SSL.
if __name__ == '__main__' and sys.argv[-1:] == ['websockets']:
    from gevent import monkey
    monkey.patch_all()

from docassemble_executor.capabilities import Capabilities, HEADER, caller_context, current  # noqa: E402


def websockets(scopes: Capabilities):
    # Patch before importing threading/socket/SSL-dependent application modules.
    from gevent import monkey
    monkey.patch_all()
    import gevent
    from docassemble.webapp import socketserver as service

    sio = service.socketio
    engine = sio.server.eio
    sessions = {}
    connect, disconnect = engine.handlers['connect'], engine.handlers['disconnect']

    def admit(sid, environ):
        # Only a live scoped controller probe can connect behind preparing.
        # The public ingress strips client-supplied capability headers.
        context = scopes.accept(environ.get('HTTP_X_DA_ACCEPTED_CALLER', ''))
        caller = context.__enter__()
        if caller is None:
            context.__exit__(None, None, None)
            return False
        sessions[sid] = (context, caller)
        try:
            with caller_context(caller.name):
                result = connect(sid, environ)
            if result is False:
                sessions.pop(sid)
                context.__exit__(None, None, None)
            return result
        except BaseException:
            sessions.pop(sid, None)
            context.__exit__(*sys.exc_info())
            raise

    def finish(sid, *args):
        entry = sessions.get(sid)
        try:
            with caller_context(entry[1].name if entry else ''):
                return disconnect(sid, *args)
        finally:
            entry = sessions.pop(sid, None)
            if entry:
                entry[0].__exit__(None, None, None)

    engine.on('connect', admit)
    engine.on('disconnect', finish)
    handle_message = sio.server._handle_eio_message

    def message(eio_sid, data):
        entry = sessions.get(eio_sid)
        if entry is not None:
            with caller_context(entry[1].name):
                return handle_message(eio_sid, data)

    sio.server._handle_eio_message = message
    # Engine.IO has already registered the original message callback.
    engine.on('message', message)
    handle_event = sio.server._handle_event_internal

    def event(server, sid, eio_sid, *args):
        # The scheduling boundary pins current() before a disconnect can
        # remove the transport's session. Accepted queued events still finish.
        with scopes.accept(current()) as caller:
            if caller:
                with caller_context(caller.name):
                    return handle_event(server, sid, eio_sid, *args)

    sio.server._handle_event_internal = event
    start_background = sio.server.start_background_task

    def background(target, *args, **kwargs):
        # Pin before scheduling: disconnect must not expire queued/background work.
        context = scopes.accept(current())
        caller = context.__enter__()
        if caller is None:
            context.__exit__(None, None, None)
            raise RuntimeError('background work has no accepted caller')

        def run():
            try:
                with caller_context(caller.name):
                    return target(*args, **kwargs)
            finally:
                context.__exit__(None, None, None)
        try:
            return start_background(run)
        except BaseException:
            context.__exit__(*sys.exc_info())
            raise

    sio.server.start_background_task = background

    def drain():
        while not scopes.begin_idle_stop():
            gevent.sleep(0.1)
        sio.stop()  # no accepted session, event or background task remains

    gevent.spawn(drain)
    service.main()  # preserve legacy configured binding/exposure behavior


def nltk(scopes: Capabilities):
    import asyncio
    import traceback
    from docassemble.base import pattern_server as service

    async def handle(reader, writer):
        try:
            while True:
                request = await service._read_msg(reader)
                name = request[4] if len(request) == 5 else ''
                with scopes.accept(name) as caller:
                    if caller is None:
                        await service._write_msg(writer, {'error': 'managed service admission is closed'})
                        continue
                    try:
                        with caller_context(caller.name):
                            result = await asyncio.to_thread(service._call, request[:4])
                        await service._write_msg(writer, {'result': result})
                    except Exception:
                        # The call finishes even if the client disconnected.
                        await service._write_msg(writer, {'error': traceback.format_exc()})
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    async def serve():
        server = await asyncio.start_unix_server(handle, path=service.SOCKET_PATH)
        os.chmod(service.SOCKET_PATH, 0o660)
        async with server:
            while not scopes.begin_idle_stop():
                await asyncio.sleep(0.1)
        # Idle persistent connections are not accepted work. asyncio.run closes
        # them only after every in-flight to_thread call has finished.
        Path(service.SOCKET_PATH).unlink(missing_ok=True)

    asyncio.run(serve())


def conversion(scopes: Capabilities):
    import tempfile
    import time
    from xmlrpc.server import SimpleXMLRPCRequestHandler
    from unoserver import server as service

    class Handler(SimpleXMLRPCRequestHandler):
        def do_POST(self):
            with scopes.accept(self.headers.get(HEADER, '')) as caller:
                if caller is None:
                    self.send_error(503, 'managed service admission is closed')
                    return
                with caller_context(caller.name):
                    # Unoserver dispatch waits for actual UNO completion before
                    # writing. A lost HTTP client cannot cancel that conversion.
                    super().do_POST()

    class Server(service.XMLRPCServer):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.RequestHandlerClass = Handler

    service.XMLRPCServer = Server
    with tempfile.TemporaryDirectory(prefix='da-managed-uno-') as profile:
        uno = service.UnoServer(user_installation=Path(profile).as_uri(), conversion_timeout=None)
        process = uno.start(executable='/usr/bin/libreoffice')
        if process is None:
            raise RuntimeError('conversion server failed to start')
        try:
            while process.poll() is None and not scopes.begin_idle_stop():
                time.sleep(0.1)
            uno.stop()  # joins request dispatch; does not signal accepted work
            if process.poll() is None:
                process.terminate()  # only idle LibreOffice, no conversion remains
            process.wait()
        finally:
            # Guardian, not this wrapper, retains any surviving descendants.
            if uno.xmlrcp_server is not None:
                uno.stop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--kind', choices=['websockets', 'nltk', 'conversion'], required=True)
    kind = parser.parse_args().kind
    state = Path(os.environ.get('DA_MANAGED_STATE_DIR', '/var/lib/docassemble/managed'))
    scopes = Capabilities(state, role=kind)
    # Runtime-dependent service implementations are not imported on closed launch.
    globals()[kind](scopes)


if __name__ == '__main__':
    main()
