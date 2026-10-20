"""Non-root, fixed protocol probes. Never imported by the independent controller."""
import http.client
import json
import os
import socket
import struct
import xmlrpc.client
from urllib.parse import urlsplit

from docassemble_executor.capabilities import HEADER, current


def workers():
    # Use the application's client factory, not worker startup/initialize hooks.
    from docassemble.webapp.tasks.app import celery_app
    broker = celery_app.conf.broker_url
    if not isinstance(broker, str) or urlsplit(broker).scheme != 'redis' or ';' in broker:
        return {'celery': False, 'celerysingle': False}
    answers = celery_app.control.broadcast('managed_health', reply=True, timeout=2)
    hosts = {name for answer in answers for name, result in answer.items() if result.get('ready') is True}
    host = socket.gethostname()
    return {'celery': 'da-normal@' + host in hosts, 'celerysingle': 'da-single@' + host in hosts}


def websockets():
    connection = http.client.HTTPConnection('127.0.0.1', 5000, timeout=3)
    try:
        path = '/socket.io/?EIO=4&transport=polling'
        connection.request('GET', path, headers={HEADER: current()})
        response = connection.getresponse()
        body = response.read().decode()
        if response.status != 200 or not body.startswith('0'):
            return False
        sid = json.loads(body.split('\x1e')[0][1:])['sid']
        connection.request('POST', path + '&sid=' + sid, body='1', headers={'Content-Type': 'text/plain'})
        response = connection.getresponse()
        response.read()
        return response.status == 200
    finally:
        connection.close()


def nltk():
    import msgpack
    from docassemble.webapp.config import daconfig
    payload = msgpack.packb(['en', 'pluralize', ['cat'], {}, current()])
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(3)
        connection.connect(daconfig.get('nltk socket') or '/var/run/nltk/da_nltk.sock')
        connection.sendall(struct.pack('>I', len(payload)) + payload)

        def read(size):
            result = b''
            while len(result) < size:
                chunk = connection.recv(size - len(result))
                if not chunk:
                    raise ConnectionError('Pattern closed its response')
                result += chunk
            return result
        response = msgpack.unpackb(read(struct.unpack('>I', read(4))[0]))
        return response.get('result') == 'cats'


def conversion():
    # info() obtains filters from the live UNO instance, not a static PID probe.
    class Transport(xmlrpc.client.Transport):
        def make_connection(self, host):
            connection = super().make_connection(host)
            connection.timeout = 3
            return connection
    with xmlrpc.client.ServerProxy('http://127.0.0.1:2003', allow_none=True,
                                  transport=Transport(headers=[(HEADER, current())])) as proxy:
        info = proxy.info()
        return bool(info['import_filters'] and info['export_filters'])


if __name__ == '__main__':
    result = {}
    try:
        result.update(workers())
    except Exception:
        result.update(celery=False, celerysingle=False)
    needed = set(json.loads(os.environ.get('DA_ROLE_PROBE_KINDS', '["websockets", "nltk", "conversion"]')))
    for name, probe in (('websockets', websockets), ('nltk', nltk), ('conversion', conversion)):
        if name not in needed:
            continue
        try:
            result[name] = bool(probe())
        except Exception:
            result[name] = False
    print('ROLE_HEALTH=' + json.dumps(result, sort_keys=True))
