"""Disposable protocol sentinels, installed as sitecustomize only by the proof.

No synthetic service or fake conversion result: barriers wrap actual Pattern
and UNO functions. Websocket events exist only in this disposable sentinel.
"""
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path('/tmp/da-service-proof')


def mark(label, phase, text=''):
    (ROOT / (label + '.' + phase)).write_text(text or str(os.getpid()))


def receipt(label, protocol, result=None):
    from docassemble_executor.admission import Admission
    state = Admission(Path('/var/lib/docassemble/managed')).state()
    mark(label, protocol + '-completed', json.dumps(
        {'uid': os.getuid(), 'mode': state['mode'], 'result': result}))


def barrier(label):
    mark(label, 'started')
    while not (ROOT / (label + '.release')).exists():
        time.sleep(0.05)


def nested(label, trace=None):
    if trace is not None:
        trace('config-import-enter')
    import subprocess
    import docassemble.base.config
    if not docassemble.base.config.daconfig:
        docassemble.base.config.load(arguments=[])
    from docassemble.base import pattern
    from docassemble.base.pandoc import convert_file
    from docassemble.base.managed_services import conversion_environment, conversion_path
    sys.path.insert(0, '/opt/docassemble-fixture')
    from service_client import nltk
    if trace is not None:
        trace('config-import-return')
    # Core Pattern may have selected its legitimate in-process startup fallback.
    # Exercise the wire explicitly too, and require a receiving-side receipt.
    if trace is not None:
        trace('wire-nlp-enter')
    reply = nltk('observe:' + label)
    if trace is not None:
        trace('wire-nlp-return', reply=reply)
    result = reply['result']
    if trace is not None:
        trace('pattern-enter')
    plural = pattern.pattern_en.pluralize('cat')
    if trace is not None:
        trace('pattern-return', result=plural)
    assert result == plural == 'cats'
    source, output = ROOT / (label + '.rtf'), ROOT / (label + '.pdf')
    if trace is not None:
        trace('source-write-enter')
    source.write_text(r'{\rtf1\ansi observe:' + label + ' accepted nested conversion}')
    if trace is not None:
        trace('source-write-return')
    document = ROOT / (label + '.docx')
    if trace is not None:
        trace('convert-file-enter')
    converted = convert_file(str(source), str(document), 'rtf', 'docx')
    if trace is not None:
        trace('convert-file-return', result=converted)
    assert converted
    if trace is not None:
        trace('docx-check-enter')
    assert document.read_bytes().startswith(b'PK')
    if trace is not None:
        trace('docx-check-return')
        trace('unoconvert-enter')
    operation = subprocess.run([conversion_path('/usr/bin/unoconvert'), str(document), str(output)],
                               env=conversion_environment(), capture_output=True, timeout=90)
    if trace is not None:
        trace('unoconvert-return', returncode=operation.returncode,
              stdout=operation.stdout.decode(errors='replace'), stderr=operation.stderr.decode(errors='replace'))
        trace('pdf-check-enter')
    assert operation.returncode == 0, operation.stderr.decode()
    assert output.read_bytes().startswith(b'%PDF')
    if trace is not None:
        trace('pdf-check-return')
        trace('service-completion-write-enter')
    mark(label, 'completed', result + ':' + str(os.getuid()))
    if trace is not None:
        trace('service-completion-write-return')
    return result


if sys.argv[0].endswith('/service_runtime.py'):
    kind = sys.argv[-1]
    if kind == 'nltk':
        from docassemble.base import pattern_server
        original_call = pattern_server._call

        def call(request):
            if request[2] and str(request[2][0]).startswith('observe:'):
                label = request[2][0].split(':', 1)[1]
                request[2][0] = 'cat'
                result = original_call(request)
                receipt(label, 'nlp', result)
                return result
            if request[2] and str(request[2][0]).startswith('proof:'):
                label = request[2][0].split(':', 1)[1]
                barrier(label)
                request[2][0] = 'cat'
                result = original_call(request)
                mark(label, 'completed', result + ':' + str(os.getuid()))
                return result
            return original_call(request)
        pattern_server._call = call
    elif kind == 'conversion':
        from unoserver.converter import UnoConverter
        original_convert = UnoConverter.convert

        def convert(self, *args, **kwargs):
            data = args[1]
            if data is None and args[0]:
                data = Path(args[0]).read_bytes()
            label = None
            if data and b'proof:' in data:
                label = data.split(b'proof:', 1)[1].split(b' ', 1)[0].decode()
                barrier(label)
            result = original_convert(self, *args, **kwargs)
            if label:
                mark(label, 'completed', str(os.getuid()))
            if data and b'observe:' in data:
                receipt(data.split(b'observe:', 1)[1].split(b' ', 1)[0].decode(), 'uno')
            return result
        UnoConverter.convert = convert
    elif kind == 'websockets':
        from gevent import monkey
        monkey.patch_all()
        from docassemble.webapp import socketserver

        @socketserver.socketio.on('proof', namespace='/wsinterview')
        def proof(data):
            label = data['label']
            if data.get('background'):
                def work():
                    barrier(label)
                    if data.get('nested'):
                        nested(label)
                    else:
                        mark(label, 'completed', str(os.getuid()))
                socketserver.socketio.start_background_task(work)
            return {'uid': os.getuid(), 'pid': os.getpid()}
