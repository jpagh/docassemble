"""Pinned-uv same-version replacement-wheel refresh without user update-all."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile


def main():
    root = Path('/tmp/da19-core-wheel-probe')
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir()
    os.chdir(root)
    def wheel(name, content, version='1.0'):
        path = root / (name + '-' + version + '-py3-none-any.whl')
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr(name + '.py', repr(content))
            archive.writestr(name + '-' + version + '.dist-info/METADATA', 'Metadata-Version: 2.1\nName: ' + name + '\nVersion: ' + version + '\n')
            archive.writestr(name + '-' + version + '.dist-info/WHEEL',
                            'Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
            archive.writestr(name + '-' + version + '.dist-info/RECORD', '')
        return hashlib.sha256(path.read_bytes()).hexdigest()
    def run(*arguments):
        result = subprocess.run(['uv', *arguments, '--default-index', str(root / 'simple') + '/', '--offline', '--no-config', '--python',
            '/usr/share/docassemble/executor-venv/bin/python'], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    (root / 'pyproject.toml').write_text('''[project]
name="fixture"
version="0"
requires-python="==3.12.*"
dependencies=["corefixture==1.0", "userfixture>=1"]
[tool.uv]
package=false
[tool.uv.sources]
corefixture={path="corefixture-1.0-py3-none-any.whl"}
[[tool.uv.index]]
url="file:///tmp/da19-core-wheel-probe/simple"
default=true
''')
    old = wheel('corefixture', 'old core bytes')
    user = wheel('userfixture', 'unchanged user bytes')
    index = root / 'simple/userfixture'
    index.mkdir(parents=True)
    (index / 'index.html').write_text('<a href="../../userfixture-1.0-py3-none-any.whl#sha256=' + user + '">userfixture 1.0</a>')
    run('lock')
    before = (root / 'uv.lock').read_bytes()
    new = wheel('corefixture', 'new core bytes at same version and filename')
    assert old != new
    newer_user = wheel('userfixture', 'newer registry release must not be selected implicitly', version='2.0')
    with (index / 'index.html').open('a') as handle:
        handle.write('<a href="../../userfixture-2.0-py3-none-any.whl#sha256=' + newer_user + '">userfixture 2.0</a>')
    # --refresh-package cannot be combined with the offline private snapshot;
    # an explicit core-only unlock respects the image's exact core pin instead.
    run('lock', '--upgrade-package', 'corefixture')
    assert (root / 'uv.lock').read_bytes() != before
    run('sync', '--frozen', '--no-build')
    assert hashlib.sha256((root / 'userfixture-1.0-py3-none-any.whl').read_bytes()).hexdigest() == user
    selected_user = subprocess.check_output([str(root / '.venv/bin/python'), '-c',
        'from importlib.metadata import version; print(version("userfixture"))'], text=True).strip()
    assert selected_user == '1.0', 'implicit user update-all'
    print(json.dumps({'preferred_user_version': selected_user, 'newer_available_user_version': '2.0', 'uv': subprocess.check_output(['uv', '--version'], text=True).strip(),
        'old_core_sha256': old, 'new_core_sha256': new, 'unchanged_user_sha256': user,
        'only_unlocked': ['corefixture'], 'frozen_sync': 'passed'}), flush=True)


if __name__ == '__main__':
    main()
