"""Frozen does not check freshness: bind both immutable TOML constraints."""
import json

import pytest

from docassemble.base.managed_python import python_requires
from docassemble_executor.bundles import verify_python_target


@pytest.mark.parametrize('project,lock,accepted', [
    ('current', 'current', True), ('old', 'old', False),
    ('current', 'old', False), ('old', 'current', False),
    ('broad', 'current', False), ('current', 'broad', False),
    ('missing', 'current', False), ('current', 'missing', False),
    ('malformed', 'current', False), ('current', 'malformed', False),
    ('list', 'current', False), ('current', 'list', False),
])
def test_verify_python_target_exact_constraints_without_writes(tmp_path, project, lock, accepted):
    constraints = {'current': json.dumps(python_requires()), 'old': '"==3.12.*"',
                   'broad': '">=3.12"', 'list': '["3.14"]'}
    def text(kind):
        if kind == 'missing':
            return ''
        if kind == 'malformed':
            return '['
        return 'requires-python = ' + constraints[kind] + '\n'
    project_file, lock_file = tmp_path / 'pyproject.toml', tmp_path / 'uv.lock'
    project_file.write_text('[project]\n' + text(project))
    lock_file.write_text(text(lock))
    before = project_file.read_bytes(), lock_file.read_bytes()
    assert verify_python_target(project_file, lock_file) is accepted
    assert (project_file.read_bytes(), lock_file.read_bytes()) == before
    project_file.unlink()
    assert not verify_python_target(project_file, lock_file)


@pytest.mark.parametrize('side', ['project', 'lock'])
@pytest.mark.parametrize('declared', [True, False])
def test_verify_python_target_real_tomllib_depth_failure_refuses(tmp_path, side, declared):
    import tomllib
    project, lock = tmp_path / 'pyproject.toml', tmp_path / 'uv.lock'
    project.write_text('[project]\nrequires-python=' + json.dumps(python_requires()))
    lock.write_text('requires-python=' + json.dumps(python_requires()))
    path = project if side == 'project' else lock
    nested = '\nunused = ' + '[' * 2000 + '0' + ']' * 2000
    path.write_text((path.read_text() if declared else '') + nested)
    # Exercise the interpreter's actual parser, not a mocked exception.
    with pytest.raises(RecursionError):
        tomllib.loads(path.read_text())
    before = project.read_bytes(), lock.read_bytes()
    assert verify_python_target(project, lock) is False
    assert (project.read_bytes(), lock.read_bytes()) == before
