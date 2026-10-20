"""Load the real Playground generator with only legacy storage/config seams."""
import ast
import datetime
import os
from pathlib import Path
import re
import shutil
import tempfile
from types import SimpleNamespace
import zipfile
import zoneinfo

from packaging import version
import tomli_w

ROOT = Path(__file__).resolve().parents[2]


def functions(path, names, namespace):
    tree = ast.parse((ROOT / path).read_text())
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == names
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, 'exec'), namespace)


def generator(root):
    root.mkdir(parents=True, exist_ok=True)
    def saved(user, fix, section):
        directory = root / section
        directory.mkdir(exist_ok=True)
        return directory
    def no_legacy(*args):
        raise AssertionError('lossy legacy dependency lookup')
    namespace = dict(os=os, re=re, shutil=shutil, tempfile=tempfile, zipfile=zipfile,
        datetime=datetime, zoneinfo=zoneinfo, version=version, tomli_w=tomli_w,
        SavedFile=saved, directory_for=lambda area, project: str(area),
        daconfig={}, DEFAULT_GITIGNORE='', DEFAULT_GITIGNORE_FILE='',
        docassemble=SimpleNamespace(webapp=SimpleNamespace(spdx=SimpleNamespace(LICENSES={'MIT'}))),
        get_package_identifier=no_legacy, logmessage=no_legacy)
    functions('docassemble_webapp/docassemble/webapp/files/savedfile.py',
              {'make_package_zip', 'make_package_dir'}, namespace)
    return namespace


def package_info(**values):
    defaults = dict(version='1.0', description='Playground proof', license='MIT', url='',
        author_name='Fixture', author_email='fixture@example.invalid', modtime=1700000000,
        dependencies=[], interview_files=[], template_files=[], module_files=[],
        static_files=[], sources_files=[])
    return dict(defaults, **values)


def author_info():
    return {'id': 2, 'author name': 'Fixture', 'author email': 'fixture@example.invalid',
            'author name and email': 'Fixture <fixture@example.invalid>'}
