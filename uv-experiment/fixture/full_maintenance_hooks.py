"""Test-only barrier around the actual Pattern readiness request."""
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path('/tmp/da-full-readiness-proof')
if sys.argv[0].endswith('/service_runtime.py') and sys.argv[-1] == 'nltk':
    from docassemble.base import pattern_server
    original = pattern_server._call

    def call(request):
        if request[2] == ['cat'] and not (ROOT / 'release').exists():
            (ROOT / 'started').write_text(str(os.getpid()))
            while not (ROOT / 'release').exists():
                time.sleep(0.05)
        result = original(request)
        if request[2] == ['cat']:
            from docassemble_executor.admission import Admission
            try:
                with (ROOT / 'completed').open('x') as receipt:
                    json.dump({'uid': os.getuid(), 'result': result,
                               'mode': Admission(Path('/var/lib/docassemble/managed')).state()['mode']}, receipt)
            except FileExistsError:
                pass
        return result

    pattern_server._call = call
