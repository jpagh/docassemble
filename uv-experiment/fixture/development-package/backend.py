"""Offline test package: observe real build UID and optional surviving child."""
import os
from pathlib import Path
import subprocess
import sys
import zipfile


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    root = Path(os.environ.get('DA_DEVELOPMENT_PROOF_ROOT', '/tmp/da-development-proof'))
    root.mkdir(exist_ok=True)
    (root / 'build-uid').write_text(str(os.geteuid()))
    if os.environ.get('DA_DEVELOPMENT_PROOF_CHILD') == 'true':
        subprocess.Popen([sys.executable, '-c', '''
import os, time
from pathlib import Path
os.setsid()
os.closerange(3, 1024)
root = Path(os.environ['DA_DEVELOPMENT_PROOF_ROOT'])
(root / 'child-started').write_text(str(os.getpid()))
while not (root / 'child-release').exists():
    time.sleep(0.1)
(root / 'child-completed').write_text(str(os.geteuid()))
'''], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.environ.get('DA_DEVELOPMENT_PROOF_FAIL') == 'true':
        raise RuntimeError('deliberate development build failure')
    name = 'da_development_probe-1.0-py3-none-any.whl'
    with zipfile.ZipFile(Path(wheel_directory) / name, 'w') as wheel:
        wheel.writestr('da_development_probe.py', "VALUE = 'deliberate override'\n")
        wheel.writestr('da_development_probe-1.0.dist-info/METADATA',
                       'Metadata-Version: 2.1\nName: da-development-probe\nVersion: 1.0\n')
        wheel.writestr('da_development_probe-1.0.dist-info/WHEEL',
                       'Wheel-Version: 1.0\nGenerator: proof\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        wheel.writestr('da_development_probe-1.0.dist-info/RECORD', '')
    return name
