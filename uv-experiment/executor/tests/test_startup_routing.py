"""Startup routing is evaluated before the legacy runtime is activated."""

import os
from pathlib import Path
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / 'Docker' / 'managed-startup.sh'


def route(tmp_path, flag, evidence=False):
    state = tmp_path / 'state'
    state.mkdir()
    if evidence:
        (state / 'desired.json').write_text('corrupt')
    env = dict(os.environ, DA_UV_MANAGED=flag, DA_MANAGED_STATE_DIR=str(state),
               DA_EXECUTOR_ENTRYPOINT=str(tmp_path / 'missing'))
    return subprocess.run(['bash', '-c', f'source "{SCRIPT}"; echo legacy'],
                          env=env, capture_output=True, text=True)


def test_unadopted_flag_off_reaches_unchanged_legacy_path(tmp_path):
    result = route(tmp_path, 'false')
    assert result.returncode == 0
    assert result.stdout.strip() == 'legacy'


@pytest.mark.parametrize('flag,evidence', [('false', True), ('true', False)])
def test_managed_evidence_or_opt_in_never_falls_through_to_legacy(tmp_path, flag, evidence):
    result = route(tmp_path, flag, evidence)
    assert result.returncode != 0
    assert 'legacy\n' not in result.stdout


@pytest.mark.parametrize('failure', [None, 'unset-root', 'unset-uid', 'wrong-uid', 'public', 'symlink'])
def test_managed_startup_requires_explicit_restricted_api_writer_store(tmp_path, failure):
    import pwd
    import sys
    root = tmp_path / 'artifacts'
    root.mkdir(mode=0o700)
    uid = pwd.getpwnam('www-data').pw_uid
    if os.getuid() == 0:
        os.chown(root, uid, uid)
    executor = tmp_path / 'da-executor'
    executor.write_text('#!/bin/bash\necho independent "$@"\n')
    executor.chmod(0o755)
    python = tmp_path / 'python'
    python.write_text('#!/bin/bash\nexec "' + sys.executable + '" "$@"\n')
    python.chmod(0o755)
    env = dict(os.environ, PYTHONPATH=str(SCRIPT.parents[1] / 'docassemble_base'),
        DA_UV_MANAGED='true', DA_MANAGED_STATE_DIR=str(tmp_path / 'state'),
        DA_EXECUTOR_ENTRYPOINT=str(executor), DA_MANAGED_ARTIFACT_ROOT=str(root),
        DA_MANAGED_ARTIFACT_UID=str(uid))
    if failure == 'unset-root':
        del env['DA_MANAGED_ARTIFACT_ROOT']
    elif failure == 'unset-uid':
        del env['DA_MANAGED_ARTIFACT_UID']
    elif failure == 'wrong-uid':
        env['DA_MANAGED_ARTIFACT_UID'] = '0'
    elif failure == 'public':
        root.chmod(0o755)
    elif failure == 'symlink':
        link = tmp_path / 'linked'
        link.symlink_to(root)
        env['DA_MANAGED_ARTIFACT_ROOT'] = str(link)
    result = subprocess.run(['bash', '-c', f'source "{SCRIPT}"; echo legacy'],
                            env=env, text=True, capture_output=True)
    assert 'legacy\n' not in result.stdout
    if failure:
        assert result.returncode != 0
        assert 'independent boot' not in result.stdout
    else:
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == 'independent boot'


def test_fixture_provisions_explicit_www_data_artifact_store():
    dockerfile = (SCRIPT.parents[1] / 'uv-experiment/fixture/Dockerfile').read_text()
    assert 'DA_MANAGED_ARTIFACT_ROOT=/var/lib/docassemble/artifacts' in dockerfile
    assert 'DA_MANAGED_ARTIFACT_UID=33' in dockerfile
    assert 'install -d -m 700 -o www-data -g www-data "${DA_MANAGED_ARTIFACT_ROOT}"' in dockerfile
    assert 'test "$(id -u www-data)" = "${DA_MANAGED_ARTIFACT_UID}"' in dockerfile
