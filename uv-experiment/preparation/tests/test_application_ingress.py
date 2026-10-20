"""R6: real file authorization/bytes and Engine.IO admission through public ingress.

The socket case runs with flask-socketio/gevent present and skips only that case
otherwise. Unrelated application plugins are omitted; production file route,
authorization, storage and socket lifetime adapter bodies are unchanged.
"""
# The isolated socket backend must patch before importing HTTP/SSL/threading.
import sys
if __name__ == '__main__':
    from gevent import monkey
    monkey.patch_all()

import ast
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from types import SimpleNamespace, ModuleType
import urllib.error
import urllib.request

import pytest
from flask import Flask, send_file, session
from flask_login import LoginManager, current_user
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import scoped_session, sessionmaker
from werkzeug.serving import make_server

from docassemble.base.managed_qualification import bridge_http_path
from docassemble_executor.admission import Admission, start_ingress
from docassemble_executor.capabilities import caller_context, current

SOURCE = Path(__file__).resolve().parents[3]
WEB = SOURCE / 'docassemble_webapp/docassemble/webapp'
FILE_PATHS = (
    '/storedfile/owner-session/1/document.txt',
    '/storedfiledownload/owner-session/1/document.txt',
    '/tempfile/temporary-code/document.txt',
    '/tempfiledownload/temporary-code/document.txt',
    '/uploadedfile/1/document.txt', '/uploadedfiledownload/1/document.txt',
    '/uploadedfile/1.txt', '/uploadedfiledownload/1.txt', '/uploadedfile/1',
    '/uploadedpage/1/1', '/uploadedpagedownload/1/1',
    '/uploadedpagescreen/1/1', '/uploadedpagescreendownload/1/1',
)


def load(monkeypatch, name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def source_definitions(path, names, namespace):
    """Load exact source bodies without unrelated plugin import dependencies."""
    nodes = [node for node in ast.parse(path.read_text()).body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    assert len(nodes) == len(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace


def fetch(ingress, path, *, data=None, headers=None):
    request = urllib.request.Request(f'http://127.0.0.1:{ingress.server_port}{path}',
                                     data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as error:
        return error.code, error.read(), error.headers


@contextmanager
def public_ingress(tmp_path, monkeypatch, backend_port):
    monkeypatch.setenv('DA_QUALIFIED_IMAGE_BASELINE', '/operator/baseline.json')
    monkeypatch.setenv('CONTAINERROLE', 'all')
    admission = Admission(tmp_path / 'state')
    admission.open(consumers=True)
    ingress = start_ingress(0, backend_port, admission, lambda: {})
    try:
        yield admission, ingress
    finally:
        ingress.shutdown()
        ingress.server_close()


@pytest.fixture
def files_app(tmp_path, monkeypatch):
    # Real PostgreSQL Uploads mapping, scoped per backend request. No grants or
    # access decisions are mocked; anonymous callers need the actual session UID.
    engine = create_engine(os.environ['DA_PREPARATION_TEST_URL'],
                           connect_args={'options': '-csearch_path=application_ingress_test'})
    with engine.begin() as connection:
        connection.execute(text('DROP SCHEMA IF EXISTS application_ingress_test CASCADE'))
        connection.execute(text('CREATE SCHEMA application_ingress_test'))
    orm = scoped_session(sessionmaker(bind=engine))
    webapp = ModuleType('docassemble.webapp')
    webapp.__path__ = [str(WEB)]
    monkeypatch.setitem(sys.modules, 'docassemble.webapp', webapp)
    monkeypatch.setitem(sys.modules, 'docassemble.webapp.database', SimpleNamespace(dbtableprefix='test_'))
    monkeypatch.setitem(sys.modules, 'docassemble.webapp.users.models', SimpleNamespace(UserRoles=None))
    load(monkeypatch, 'docassemble.webapp.db_base', WEB / 'db_base.py')
    models = load(monkeypatch, 'docassemble.webapp.main.models', WEB / 'main/models.py')
    models.Uploads.__table__.create(engine)
    with engine.begin() as connection:
        connection.execute(models.Uploads.__table__.insert().values(indexno=1, key='owner-session',
                            filename='document.txt', private=True, persistent=False))
    monkeypatch.setitem(sys.modules, 'docassemble.webapp.extensions', SimpleNamespace(db=SimpleNamespace(session=orm)))
    monkeypatch.setitem(sys.modules, 'docassemble.webapp.interview.models', SimpleNamespace(UserDictKeys=None))
    monkeypatch.setitem(sys.modules, 'docassemble.base.functions', SimpleNamespace(get_uid=lambda: None))
    common = load(monkeypatch, 'docassemble.webapp.files.common', WEB / 'files/common.py')
    sessions = load(monkeypatch, 'docassemble.webapp.sessions', WEB / 'sessions.py')
    storage = source_definitions(WEB / 'files/savedfile.py', {'SavedFile'}, dict(
        os=os, re=re, cloud=None, UPLOAD_DIRECTORY=str(tmp_path / 'uploads'),
        this_thread=SimpleNamespace(saved_files={})))['SavedFile']
    stored = storage(1, extension='txt', fix=True)
    payload = b'actual private uploaded document\n'
    Path(stored.path).write_bytes(payload)
    Path(stored.path + '.txt').write_bytes(payload)
    # File metadata parsing is irrelevant for text bytes; real extension lookup.
    monkeypatch.setitem(sys.modules, 'docassemble.webapp.hooks.impl', SimpleNamespace(hookimpl=lambda f: f))
    filenames = load(monkeypatch, 'docassemble.webapp.utils.filenames', WEB / 'utils/filenames.py')
    access = source_definitions(WEB / 'files/file_access.py', {'get_info_from_file_number'}, dict(
        current_user=current_user, db=SimpleNamespace(session=orm), select=select, os=os,
        Uploads=models.Uploads, session=session,
        get_uid=lambda: None, SavedFile=storage, get_ext_and_mimetype=filenames.get_ext_and_mimetype,
        logmessage=lambda *_: None, add_info_about_file=lambda *_: None))
    import redis
    redis_client = redis.Redis.from_url(os.environ['DA_API_WRITER_REDIS_URL'])
    redis_client.set('da:tempfile:temporary-code', 'files^1', ex=60)
    helpers = source_definitions(WEB / 'files/helpers.py', {
        'do_serve_stored_file', 'do_serve_temporary_file',
        'do_serve_uploaded_file_with_filename_and_extension',
        'do_serve_uploaded_file_with_extension', 'do_serve_uploaded_file', 'do_serve_uploaded_page'}, dict(
        re=re, os=os, json=json, current_user=current_user, cloud=None, daconfig={},
        werkzeug=__import__('werkzeug'), urllibquote=__import__('urllib.parse', fromlist=['quote']).quote,
        can_access_file_number=common.can_access_file_number,
        get_info_from_file_number=access['get_info_from_file_number'],
        get_session_uids=sessions.get_session_uids, custom_send_file=send_file,
        secure_filename_unicode_ok=filenames.secure_filename_unicode_ok,
        get_ext_and_mimetype=filenames.get_ext_and_mimetype, SavedFile=storage,
        r=redis_client, logmessage=lambda *_: None))
    monkeypatch.setitem(sys.modules, 'docassemble.webapp.files.helpers', SimpleNamespace(**helpers))
    blueprint = load(monkeypatch, 'docassemble.webapp.files.blueprint', WEB / 'files/blueprint.py').files_bp
    load(monkeypatch, 'docassemble.webapp.files.views', WEB / 'files/views.py')
    app = Flask(__name__)
    app.secret_key = 'disposable-file-session'
    LoginManager(app).user_loader(lambda _: None)
    app.register_blueprint(blueprint)
    with app.test_request_context():
        assert common.can_access_file_number('1', uids=['owner-session'])
        assert access['get_info_from_file_number']('1', privileged=True, uids=['owner-session'])['path'] == stored.path
    orm.remove()
    received = []

    @app.before_request
    def record_caller():
        received.append(current())

    @app.teardown_request
    def remove_session(_):
        orm.remove()

    transfer = SimpleNamespace(hold=False, entered=threading.Event(), release=threading.Event())

    def application(environ, start_response):
        # Same complete-response caller scope as the production WSGI adapter.
        with caller_context(environ.get('HTTP_X_DA_ACCEPTED_CALLER', '')):
            response = app(environ, start_response)
            try:
                if transfer.hold:
                    transfer.entered.set()
                    assert transfer.release.wait(5)
                yield from response
            finally:
                response.close()

    backend = make_server('127.0.0.1', 0, application, threaded=True)
    threading.Thread(target=backend.serve_forever, daemon=True).start()
    client = app.test_client()
    with client.session_transaction() as browser_session:
        browser_session['sessions'] = {'interview.yml': {'uid': 'owner-session'}}
    cookie = client.get_cookie('session').value
    try:
        yield SimpleNamespace(app=app, backend=backend, payload=payload, cookie=cookie,
                              received=received, redis=redis_client, transfer=transfer)
    finally:
        backend.shutdown()
        backend.server_close()
        orm.remove()
        redis_client.delete('da:tempfile:temporary-code')
        with engine.begin() as connection:
            connection.execute(text('DROP SCHEMA application_ingress_test CASCADE'))
        engine.dispose()


def test_file_route_inventory_matches_real_flask_routes(files_app):
    for rule in files_app.app.url_map.iter_rules():
        if rule.endpoint.startswith('files.'):
            assert bridge_http_path(rule.rule), rule.rule
            assert not bridge_http_path(rule.rule, recovery=True), rule.rule
    for path in ('/socket.io', '/socket.io/'):
        assert bridge_http_path(path)
        assert not bridge_http_path(path, recovery=True)
    for path in ('/uploadedfilewriter/1', '/storedfileadmin/1', '/socket.io/other',
                 '/user/register', '/user/invite', '/privileges', '/api/user', '/manage_api'):
        assert not bridge_http_path(path)
        assert not bridge_http_path(path, recovery=True)


def test_real_private_file_fetch_keeps_authorization_and_lifecycle_fences(tmp_path, monkeypatch, files_app):
    with public_ingress(tmp_path, monkeypatch, files_app.backend.server_port) as (admission, ingress):
        # Anonymous access to a private file remains concealed by the real ORM gate.
        assert fetch(ingress, '/uploadedfile/1')[0:2] == (404, b'File not found')
        assert fetch(ingress, '/storedfile/foreign-session/1/document.txt')[0:2] == (404, b'File not found')
        headers = {'Cookie': 'session=' + files_app.cookie, 'X-DA-Accepted-Caller': 'forged'}
        for path in FILE_PATHS[:9]:
            code, body, response_headers = fetch(ingress, path, headers=headers)
            assert (code, body) == (200, files_app.payload), path
            if 'download' in path:
                assert response_headers['Content-Disposition'].startswith('attachment;')
        assert all(name and name != 'forged' for name in files_app.received)
        assert fetch(ingress, '/tempfile/missing-code/document.txt')[0:2] == (404, b'File not found')
        # Page variants use the same real access gate before thumbnail generation.
        for path in FILE_PATHS[9:]:
            assert fetch(ingress, path)[0:2] == (404, b'File not found')
        for mode in ('recovery', 'split', 'draining', 'closed'):
            admission.open(consumers=mode != 'recovery')
            monkeypatch.setenv('CONTAINERROLE', 'web:celery' if mode == 'split' else 'all')
            if mode in ('draining', 'closed'):
                admission.close('test lifecycle fence', mode=mode)
            before = len(files_app.received)
            for path in FILE_PATHS:
                assert fetch(ingress, path, headers=headers)[0] == (403 if mode in ('recovery', 'split') else 503)
            assert len(files_app.received) == before


def test_real_file_transfer_holds_drain_until_response_finishes(tmp_path, monkeypatch, files_app):
    with public_ingress(tmp_path, monkeypatch, files_app.backend.server_port) as (admission, ingress):
        transfer = files_app.transfer
        transfer.hold = True
        replies = []
        client = threading.Thread(target=lambda: replies.append(fetch(ingress, '/uploadedfile/1',
            headers={'Cookie': 'session=' + files_app.cookie})))
        client.start()
        try:
            assert transfer.entered.wait(2)
            admission.close('concurrent file drain', mode='draining')
            assert fetch(ingress, '/uploadedfile/1')[0] == 503
            assert not admission.drain(0.02)
            transfer.release.set()
            client.join(3)
            assert not client.is_alive()
            assert len(replies) == 1
            assert replies[0][0:2] == (200, files_app.payload)
            assert admission.drain(1)
        finally:
            transfer.release.set()
            client.join(5)


def socket_backend(state_dir, ready_path):
    """Isolated full socketserver Flask setup; only unrelated domain imports stubbed."""
    import redis
    import docassemble.base
    from docassemble_executor.capabilities import Capabilities
    from docassemble_executor.service_runtime import websockets
    from pytest import MonkeyPatch
    patch = MonkeyPatch()
    webapp = ModuleType('docassemble.webapp')
    webapp.__path__ = [str(WEB)]
    patch.setitem(sys.modules, 'docassemble.webapp', webapp)
    redis_client = redis.Redis.from_url(os.environ['DA_API_WRITER_REDIS_URL'])
    config = SimpleNamespace(load=lambda **_: None)
    patch.setitem(sys.modules, 'docassemble.base.config', config)
    patch.setattr(docassemble.base, 'config', config, raising=False)
    modules = {
        'netifaces': SimpleNamespace(),
        'docassemble.base.functions': SimpleNamespace(server_context=SimpleNamespace(set_context=lambda _: None)),
        'docassemble.base.language.words': SimpleNamespace(word=lambda value: value),
        'docassemble.base.logger': SimpleNamespace(logmessage=lambda *_: None),
        'docassemble.base.thread_context': SimpleNamespace(this_thread=SimpleNamespace()),
        'docassemble.webapp.config': SimpleNamespace(daconfig={'secretkey': 'disposable',
            'websockets ip': '127.0.0.1', 'websockets port': 5000}),
        'docassemble.webapp.daredis': SimpleNamespace(r=redis_client, redis_host='127.0.0.1',
            redis_port=56379, redis_offset=0, r_store=redis_client),
        'docassemble.webapp.database': SimpleNamespace(alchemy_connection_string=lambda:
            os.environ['DA_PREPARATION_TEST_URL'].replace('+psycopg2', ''), connect_args=lambda: {}),
        'docassemble.webapp.interview.user_dict': SimpleNamespace(fetch_user_dict=None),
        'docassemble.webapp.monitor.hooks': SimpleNamespace(get_chat_log=None),
        'docassemble.webapp.monitor.models': SimpleNamespace(ChatLog=None),
        'docassemble.webapp.users.models': SimpleNamespace(UserModel=None),
        'docassemble.webapp.utils.fixpickle': SimpleNamespace(fix_pickle_obj=None),
        'docassemble.webapp.utils.encryption': SimpleNamespace(encrypt_phrase=None, pack_phrase=None),
        'docassemble.webapp.utils.helpers': SimpleNamespace(decode_dict=None, nice_utc_date=None, get_session=None),
    }
    for name, module in modules.items():
        patch.setitem(sys.modules, name, module)
    service = load(patch, 'docassemble.webapp.socketserver', WEB / 'socketserver.py')
    webapp.socketserver = service
    # Listener creation is real SocketIO.run; signal adapter installation before
    # entering it. The parent separately waits for the actual bound TCP socket.
    Path(ready_path).touch()
    websockets(Capabilities(Path(state_dir), role='websockets'))


def test_real_socket_handshake_retains_transport_scope_and_refuses_fresh_work(tmp_path, monkeypatch):
    pytest.importorskip('flask_socketio', reason='actual Socket.IO integration requires socket dependencies')
    pytest.importorskip('gevent', reason='actual Socket.IO integration requires socket dependencies')
    import socket
    with public_ingress(tmp_path, monkeypatch, 1) as (admission, ingress):
        ready = tmp_path / 'socket-ready'
        output = (tmp_path / 'socket.log').open('w+')
        backend = subprocess.Popen([sys.executable, str(Path(__file__)), str(admission.state_dir), str(ready)],
                                   stdout=output, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 10
            while True:
                assert backend.poll() is None, (output.seek(0), output.read())
                try:
                    with socket.create_connection(('127.0.0.1', 5000), timeout=0.1):
                        if ready.exists():
                            break
                except OSError:
                    pass
                assert time.monotonic() < deadline, 'socket backend never bound'
                time.sleep(0.05)
            handshake = '/socket.io/?EIO=4&transport=polling'
            code, body, _ = fetch(ingress, handshake, headers={'X-DA-Accepted-Caller': 'forged'})
            assert code == 200, body
            assert body.startswith(b'0'), body
            opened = json.loads(body[1:])
            sid = opened['sid']
            assert sid and opened['pingInterval'] > 0
            assert not admission.drain(0.02)  # Transport owns real service authority.
            admission.close('draining accepted transport', mode='draining')
            # Fresh handshake is refused by the production Engine.IO gate, not
            # by a fake path filter; a forged bearer cannot bypass closed admission.
            assert fetch(ingress, handshake, headers={'X-DA-Accepted-Caller': 'forged'})[0] == 401
            # Existing transport is still routed while ordinary admission is closed.
            code, body, _ = fetch(ingress, handshake + '&sid=' + sid, data=b'1')
            assert code == 200, body
            assert admission.drain(2)
            # Recovery/split are refused *at ingress* without minting a transport.
            for mode in ('recovery', 'split'):
                admission.open(consumers=mode != 'recovery')
                monkeypatch.setenv('CONTAINERROLE', ':web:celery:' if mode == 'split' else ':all:')
                for path in ('/socket.io?EIO=4&transport=polling', handshake):
                    assert fetch(ingress, path)[0] == 403
        finally:
            backend.terminate()
            backend.wait(timeout=5)
            output.close()


if __name__ == '__main__':
    socket_backend(sys.argv[1], sys.argv[2])
