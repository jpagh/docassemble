"""Actual legacy entrypoints reject managed operations before activation/import."""
from pathlib import Path
import os
import subprocess
import sys

import pytest


REPO = Path(os.environ.get('DA_TEST_REPO', Path(__file__).resolve().parents[3]))
APP_SOURCES = Path(os.environ.get('DA_TEST_APP_SOURCES', REPO))


@pytest.mark.parametrize('script', [
    'reset.sh', 'update.sh', 'sync.sh', 'run-uwsgi.sh', 'run-apache.sh',
    'run-nginx.sh', 'restart-nginx.sh', 'restart-post-logrotate.sh', 'run-certbot.sh',
    'run-postgresql.sh', 'run-redis.sh', 'run-syslogng.sh', 'run-rabbitmq.sh', 'run-nascent.sh',
])
@pytest.mark.parametrize('flag,evidence', [('true', False), ('false', True)])
def test_unsupported_managed_shell_entrypoints_reject_before_activation(tmp_path, script, flag, evidence):
    runtime = tmp_path / 'runtime'
    (runtime / 'bin').mkdir(parents=True)
    state = tmp_path / 'state'
    state.mkdir()
    if evidence:
        (state / 'desired.json').write_text('corrupt')
    entered = tmp_path / 'runtime-entered'
    (runtime / 'bin/activate').write_text(f'touch "{entered}"\nexit 99\n')
    result = subprocess.run(
        ['bash', str(REPO / 'Docker' / script)],
        env=dict(os.environ, DA_UV_MANAGED=flag, DA_MANAGED_STATE_DIR=str(state),
                 DA_PYTHON=str(runtime)),
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 75, result.stdout + result.stderr
    assert not entered.exists()


@pytest.mark.parametrize('module', ['webapp.' + name for name in (
    'update', 'restart', 'create_tables', 'cron', 'cleanup_sessions', 'process_email',
    'socketserver', 'watchdog',
)] + ['base.pattern_server', 'base.read_config'])
@pytest.mark.parametrize('evidence', [None, 'desired.json', 'applied.json', 'lifecycle.json',
                                     'initialized.json', 'admission.json', 'dangling-symlink'])
def test_managed_runtime_cli_rejects_before_application_imports(tmp_path, module, evidence):
    # The real module is executed by a minimal interpreter without app dependencies.
    # A managed refusal must happen first, not as an accidental ImportError.
    if evidence == 'dangling-symlink':
        (tmp_path / 'desired.json').symlink_to(tmp_path / 'missing')
    elif evidence:
        (tmp_path / evidence).write_text('corrupt')
    result = subprocess.run(
        [sys.executable, '-S', '-m', 'docassemble.' + module],
        env=dict(os.environ, DA_UV_MANAGED='false' if evidence else 'true', DA_MANAGED_STATE_DIR=str(tmp_path),
                 PYTHONPATH=os.pathsep.join(str(APP_SOURCES / name) for name in
                                           ('docassemble_base', 'docassemble_webapp'))),
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode != 0
    assert 'unsupported in managed mode' in result.stderr
    assert 'ModuleNotFoundError' not in result.stderr


@pytest.mark.skipif(os.geteuid() != 0 or not Path('/usr/sbin/runuser').exists(),
                    reason='requires native non-root shell caller')
@pytest.mark.parametrize('script', ['reset.sh', 'update.sh', 'sync.sh', 'run-certbot.sh'])
def test_flag_off_unsearchable_evidence_refuses_before_legacy_runtime(tmp_path, script):
    for parent in (tmp_path, tmp_path.parent, tmp_path.parent.parent):
        parent.chmod(0o755)
    state = tmp_path / 'private/state'
    state.mkdir(parents=True)
    (state / 'desired.json').write_text('corrupt')
    state.parent.chmod(0o700)
    runtime = tmp_path / 'runtime/bin'
    runtime.mkdir(parents=True)
    (runtime / 'activate').write_text('echo legacy-runtime-entered\nexit 99\n')
    certbot = runtime / 'certbot'
    certbot.write_text('#!/bin/bash\necho legacy-runtime-entered\nexit 99\n')
    certbot.chmod(0o755)
    result = subprocess.run(
        ['/usr/sbin/runuser', '-u', 'www-data', '--', '/bin/bash', str(REPO / 'Docker' / script)],
        env=dict(os.environ, DA_UV_MANAGED='false', DA_MANAGED_STATE_DIR=str(state),
                 DA_ROOT=str(tmp_path / 'source-fixture'), DA_PYTHON=str(runtime.parent)),
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 75, result.stdout + result.stderr
    assert 'legacy-runtime-entered' not in result.stdout


def test_direct_schema_setup_cannot_reinitialize_an_adopted_installation(tmp_path):
    (tmp_path / 'initialized.json').write_text('corrupt-but-still-adopted')
    result = subprocess.run(
        [sys.executable, '-m', 'docassemble_executor.cli', 'init-db'],
        env=dict(os.environ, DA_UV_MANAGED='true', DA_MANAGED_STATE_DIR=str(tmp_path),
                 DA_BUNDLE_DIR=str(tmp_path / 'missing-bundles')),
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode != 0
    assert 'schema initialization is unsupported after managed adoption' in result.stderr


def test_unadopted_flag_off_preserves_legacy_activation(tmp_path):
    runtime = tmp_path / 'runtime/bin'
    runtime.mkdir(parents=True)
    (runtime / 'activate').write_text('echo unadopted-legacy-activation\nexit 99\n')
    result = subprocess.run(
        ['bash', str(REPO / 'Docker/reset.sh')],
        env=dict(os.environ, DA_UV_MANAGED='false', DA_MANAGED_STATE_DIR=str(tmp_path / 'missing-state'),
                 DA_PYTHON=str(runtime.parent)),
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 99
    assert result.stdout.strip() == 'unadopted-legacy-activation'


@pytest.mark.parametrize('module', ['update', 'restart', 'create_tables'])
def test_unadopted_flag_off_does_not_refuse_legacy_module_imports(tmp_path, module):
    result = subprocess.run(
        [sys.executable, '-S', '-m', 'docassemble.webapp.' + module],
        env=dict(os.environ, DA_UV_MANAGED='false', DA_MANAGED_STATE_DIR=str(tmp_path),
                 PYTHONPATH=os.pathsep.join(str(APP_SOURCES / name) for name in
                                           ('docassemble_base', 'docassemble_webapp'))),
        capture_output=True, text=True, timeout=5,
    )
    assert 'ModuleNotFoundError' in result.stderr  # legacy imports reached, no app deps in -S interpreter
    assert 'unsupported in managed mode' not in result.stderr
