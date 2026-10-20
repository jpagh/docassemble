"""Unchanged ML replay preserves source mtimes and real Playground ZIP bytes."""
import importlib.util
import json
import os
from pathlib import Path
import re
import pytest
from types import SimpleNamespace

from sqlalchemy import column

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('ml_playground_fixture', ROOT / 'uv-experiment/preparation/playground_fixture.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


@pytest.mark.parametrize('existing_format', ['canonical', 'compact'])
def test_unchanged_ml_hook_preserves_zip_bytes_and_changed_content_updates(tmp_path, existing_format):
    generated = fixture.generator(tmp_path / 'generator')
    directory = generated['SavedFile'](2, True, 'playgroundsources')
    source = directory / 'ml-example.json'
    payload = {'training': [{'independent': None, 'dependent': None}]}
    source.write_text(json.dumps(payload, sort_keys=True, indent=2 if existing_format == 'canonical' else None))
    os.utime(source, (1700000000, 1700000000))
    records = [SimpleNamespace(group_id='docassemble.playground2:data/sources/ml-example.json:training',
                               independent=None, dependent=None, key=None)]
    session = SimpleNamespace(execute=lambda stmt: SimpleNamespace(all=lambda: records))
    finalized = []
    class Playground:
        def __init__(self, *args, **kwargs):
            self.directory = str(directory)
        def list_of_files(self):
            return [source.name]
        def fix(self):
            pass
        def write_as_json(self, obj, **kwargs):
            source.write_text(json.dumps(obj, sort_keys=True, indent=2))
        def finalize(self):
            finalized.append(True)
    namespace = {'re': re, 'os': os, 'json': json, 'db': SimpleNamespace(session=session),
                 'select': __import__('sqlalchemy').select,
                 'MachineLearning': SimpleNamespace(**{key: column(key) for key in (
                     'id', 'group_id', 'independent', 'dependent', 'key')}),
                 'is_package_ml': lambda parts: True, 'project_name': lambda project: '',
                 'directory_for': lambda area, project: area.directory, 'SavedFile': Playground,
                 'logmessage': lambda message: (_ for _ in ()).throw(AssertionError(message))}
    fixture.functions('docassemble_webapp/docassemble/webapp/ml/hooks.py',
                      {'write_ml_source', 'fix_ml_files'}, namespace)
    info = fixture.package_info(sources_files=[source.name])
    def archive():
        with generated['make_package_zip']('Example', info, fixture.author_info(), 'UTC', managed=True) as handle:
            handle.seek(0)
            return handle.read()
    before = archive()
    stamp = source.stat().st_mtime_ns
    namespace['fix_ml_files'](2, 'default')
    namespace['fix_ml_files'](2, 'default')
    assert source.stat().st_mtime_ns == stamp
    assert archive() == before
    assert finalized == []
    records[0].key = 'changed'
    namespace['fix_ml_files'](2, 'default')
    assert source.stat().st_mtime_ns != stamp
    assert json.loads(source.read_text())['training'][0]['key'] == 'changed'
    assert archive() != before
    assert finalized == [True]
