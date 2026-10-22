"""Observed managed target, shared by executor and application qualification.

Image construction must create both venvs from the same protected interpreter.
Never execute the mutable runtime or infer a target from candidate metadata.
"""
from pathlib import Path
import sys


def python_minor() -> str:
    return '.'.join(str(part) for part in sys.version_info[:2])


def python_target() -> dict[str, str]:
    return {'python': python_minor()}


def python_requires() -> str:
    return '==' + python_minor() + '.*'


def python_site_packages() -> Path:
    return Path('lib') / ('python' + python_minor()) / 'site-packages'
