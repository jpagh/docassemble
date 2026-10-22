"""The managed target is an interpreter observation, never configuration."""
import sys

import pytest

from docassemble.base import managed_python


@pytest.mark.parametrize('version', [(3, 14, 4), (3, 13, 9)])
def test_managed_python_helpers_observe_sys_and_return_independent_targets(monkeypatch, version):
    monkeypatch.setattr(sys, 'version_info', version)
    monkeypatch.setenv('UV_PYTHON', 'foreign')
    monkeypatch.setenv('DA_RUNTIME_VENV', '/foreign')
    minor = f'{version[0]}.{version[1]}'
    assert managed_python.python_minor() == minor
    assert managed_python.python_requires() == '==' + minor + '.*'
    assert managed_python.python_site_packages().as_posix() == f'lib/python{minor}/site-packages'
    target = managed_python.python_target()
    target['python'] = 'foreign'
    assert managed_python.python_target() == {'python': minor}


def test_managed_python_import_is_dependency_free():
    import subprocess
    from pathlib import Path
    source = Path(managed_python.__file__)
    subprocess.run([sys.executable, '-I', '-c',
                    'import runpy, sys; runpy.run_path(sys.argv[1]); '
                    'assert not any(n.startswith(("flask", "celery", "docassemble")) for n in sys.modules)',
                    str(source)], check=True)
