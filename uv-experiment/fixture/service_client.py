"""Actual websocket, Pattern msgpack and UNO XMLRPC clients for the proof."""
from contextlib import closing
import http.client
import json
import os
import socket
import struct
import sys
import time
import urllib.parse
import xmlrpc.client

sys.path.insert(0, '/opt/docassemble-executor/src')
from docassemble_executor.capabilities import HEADER, current  # noqa: E402
from service_probe_hooks import ROOT, barrier, mark, nested  # noqa: E402


def nltk(label='cat', disconnect=False):
    import msgpack
    payload = msgpack.packb(['en', 'pluralize', [label], {}, current()])
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect('/var/run/nltk/da_nltk.sock')
        connection.sendall(struct.pack('>I', len(payload)) + payload)
        if disconnect:
            return
        def read(n):
            result = b''
            while len(result) < n:
                chunk = connection.recv(n - len(result))
                if not chunk:
                    raise ConnectionError('NLTK closed connection')
                result += chunk
            return result
        return msgpack.unpackb(read(struct.unpack('>I', read(4))[0]))


def convert(label=None, disconnect=False):
    data = (r'{\rtf1\ansi ' + ('proof:' + label + ' ' if label else '') + 'real conversion}').encode()
    args = (None, xmlrpc.client.Binary(data), None, 'pdf', None, [], False, None, None)
    headers = [(HEADER, current())] if current() else []
    if disconnect:
        connection = http.client.HTTPConnection('127.0.0.1', 2003)
        connection.request('POST', '/RPC2', body=xmlrpc.client.dumps(args, 'convert', allow_none=True),
                           headers={'Content-Type': 'text/xml', **dict(headers)})
        connection.close()
        return
    with xmlrpc.client.ServerProxy('http://127.0.0.1:2003', allow_none=True, headers=headers) as proxy:
        result = proxy.convert(*args)
        assert result.data.startswith(b'%PDF')
        return {'pdf': True, 'bytes': len(result.data)}


def websocket(label, background=False, do_nested=False, port=80):
    from simple_websocket import Client
    query = urllib.parse.urlencode({'EIO': '4', 'transport': 'websocket',
                                   'i': 'docassemble.sentinel_fixture:data/questions/sentinel.yml'})
    client = Client.connect('ws://127.0.0.1:' + str(port) + '/socket.io/?' + query)

    def packet(prefix):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            message = client.receive(timeout=1)
            if message == '2':
                client.send('3')
            elif message and message.startswith(prefix):
                return message[len(prefix):]
        raise TimeoutError('websocket acknowledgement did not arrive')

    def call(number, data):
        client.send('42/wsinterview,' + str(number) + json.dumps(['proof', data]))
        return json.loads(packet('43/wsinterview,' + str(number)))[0]

    try:
        packet('0')
        client.send('40/wsinterview,')
        packet('40/wsinterview,')
        result = call(1, {'label': label, 'background': background, 'nested': do_nested})
        mark(label, 'connected', json.dumps(result))
        while not (ROOT / (label + '.disconnect')).exists():
            message = client.receive(timeout=0.05)
            if message == '2':
                client.send('3')
        # Existing sessions still dispatch callbacks while admission is closed.
        second = call(2, {'label': label})
        assert second['pid'] == result['pid']
        client.send('41/wsinterview,')
        client.send('1')
        mark(label, 'disconnected')
        return result
    finally:
        client.close()


def polling(label):
    query = {'EIO': '4', 'transport': 'polling',
             'i': 'docassemble.sentinel_fixture:data/questions/sentinel.yml'}

    def request(method, body=None):
        with closing(http.client.HTTPConnection('127.0.0.1', 80, timeout=35)) as connection:
            connection.request(method, '/socket.io/?' + urllib.parse.urlencode(query), body=body,
                               headers={'Content-Type': 'text/plain'})
            response = connection.getresponse()
            data = response.read().decode()
            assert response.status == 200, (response.status, data)
            return data.split('\x1e')

    opened = request('GET')[0]
    assert opened.startswith('0')
    query['sid'] = json.loads(opened[1:])['sid']

    def packet(prefix):
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            for message in request('GET'):
                if message == '2':
                    request('POST', '3')
                elif message.startswith(prefix):
                    return message[len(prefix):]
        raise TimeoutError('polling acknowledgement did not arrive')

    def call(number):
        request('POST', '42/wsinterview,' + str(number) + json.dumps(['proof', {'label': label}]))
        return json.loads(packet('43/wsinterview,' + str(number)))[0]

    try:
        request('POST', '40/wsinterview,')
        packet('40/wsinterview,')
        result = call(1)
        mark(label, 'connected', json.dumps(result))
        while not (ROOT / (label + '.disconnect')).exists():
            time.sleep(0.05)
        assert call(2)['pid'] == result['pid']
        request('POST', '41/wsinterview,')
        request('POST', '1')
        mark(label, 'disconnected')
        return result
    finally:
        try:
            request('POST', '1')
        except (OSError, AssertionError):
            pass


def main():
    action = sys.argv[1]
    payload = json.loads(os.environ.get('DA_SERVICE_PROOF_ARGS', '{}'))
    if action == 'core-conversion':
        import docassemble.base.config
        docassemble.base.config.load(arguments=[])
        from docassemble.base.pandoc import convert_file
        label = payload['label']
        source, output = ROOT / (label + '.rtf'), ROOT / (label + '.docx')
        source.write_text(r'{\rtf1\ansi proof:' + label + ' actual core conversion}')
        (ROOT / (label + '.release')).touch()
        assert convert_file(str(source), str(output), 'rtf', 'docx')
        assert output.read_bytes().startswith(b'PK')
        assert (ROOT / (label + '.started')).exists(), 'core API bypassed qualified UNO protocol'
        return {'docx': True, 'uid': os.getuid()}
    if action == 'normal':
        # Readiness is actual protocol behavior, not a listening PID. This also
        # avoids racing slower websocket startup against faster NLP/UNO startup.
        (ROOT / '__readiness.disconnect').touch()
        return {'nltk': nltk(), 'conversion': convert(), 'websocket': websocket('__readiness')}
    if action == 'websocket':
        return websocket(**payload)
    if action == 'polling':
        return polling(**payload)
    if action == 'nltk':
        return nltk(**payload)
    if action == 'conversion':
        return convert(**payload)
    if action == 'nested':
        barrier(payload['label'])
        return nested(payload['label'])
    if action == 'tree':
        if os.fork() == 0:
            os.setsid()
            os.closerange(3, 1024)
            barrier(payload['label'])
            nested(payload['label'])
            os._exit(0)
        return {'uid': os.getuid()}
    raise ValueError('unknown proof action')


if __name__ == '__main__':
    print('PROOF_JSON=' + json.dumps(main()))
