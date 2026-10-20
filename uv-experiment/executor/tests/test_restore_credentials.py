"""Restore credentials use environment channels, never argv or diagnostics."""
import os
from pathlib import Path
import shlex
import subprocess
import sys
import traceback
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from sqlalchemy.engine import make_url

from docassemble_executor import checkpoint_restore, cli, cold_restore
from docassemble_executor.cloud_checkpoints import CloudCheckpoint
from docassemble_executor.managed_backup import ManagedBackupError


APP_SECRET = 'APP-SENTINEL@:/secret'
OPERATOR_SECRET = 'OPERATOR-SENTINEL@:/secret'
APP_URL = 'postgresql://recorded_app:' + quote(APP_SECRET, safe='') + '@127.0.0.1:55432/application'
OPERATOR_URL = 'postgresql://operator:' + quote(OPERATOR_SECRET, safe='') + '@127.0.0.1:55432/postgres'


def restore_arguments(command):
    if command == 'restore':
        return ['--dump', 'database', '--files', 'files.tar', '--authorization', 'authorization']
    return ['--checkpoint', 'checkpoint', '--authorization', 'authorization']


@pytest.mark.parametrize('command', ['restore', 'restore-checkpoint'])
@pytest.mark.parametrize('entrypoint', ['cli', 'standalone'])
@pytest.mark.parametrize('invalid', ['--database-url', '--maintenance-database-url', '--unknown', '--prefix'])
def test_rejected_restore_arguments_never_echo_credentials(command, entrypoint, invalid, capsys):
    args = restore_arguments(command) + [invalid, APP_URL]
    if invalid == '--prefix':
        args += ['--prefix']  # missing value after a previously valid URL-shaped value
    main = cli.main if entrypoint == 'cli' else cold_restore.main if command == 'restore' else checkpoint_restore.main
    with pytest.raises(SystemExit) as error:
        main(([command] if entrypoint == 'cli' else []) + args)
    assert error.value.code == 2
    output = capsys.readouterr()
    assert 'invalid restore arguments' in output.err
    assert APP_URL not in output.out + output.err
    assert APP_SECRET not in output.out + output.err


def test_other_cli_commands_retain_actionable_argument_errors(capsys):
    with pytest.raises(SystemExit):
        cli.main(['cache', 'not-an-action'])
    assert 'not-an-action' in capsys.readouterr().err


@pytest.mark.parametrize('command,missing', [
    ('restore', 'DA_RESTORE_DATABASE_URL'),
    ('restore-checkpoint', 'DA_RESTORE_DATABASE_URL'),
    ('restore-checkpoint', 'DA_RESTORE_MAINTENANCE_DATABASE_URL'),
])
@pytest.mark.parametrize('value', [None, '', '   '])
def test_missing_environment_refuses_before_restore_or_authority_access(command, missing, value, monkeypatch, capsys):
    monkeypatch.setenv('DA_RESTORE_DATABASE_URL', APP_URL)
    monkeypatch.setenv('DA_RESTORE_MAINTENANCE_DATABASE_URL', OPERATOR_URL)
    if value is None:
        monkeypatch.delenv(missing)
    else:
        monkeypatch.setenv(missing, value)
    monkeypatch.setattr(cold_restore, 'bootstrap', lambda *_: pytest.fail('restore called without credentials'))
    monkeypatch.setattr(checkpoint_restore, 'restore_checkpoint', lambda *a, **k: pytest.fail('restore called without credentials'))
    assert cli.main([command, *restore_arguments(command)]) == 1
    output = capsys.readouterr()
    assert missing + ' is required; restore refused' in output.out
    assert APP_URL not in output.out + output.err and OPERATOR_URL not in output.out + output.err


@pytest.mark.parametrize('command', ['restore', 'restore-checkpoint'])
def test_cli_reads_environment_without_forwarding_urls_in_argument_lists(command, monkeypatch, capsys):
    monkeypatch.setenv('DA_RESTORE_DATABASE_URL', APP_URL)
    monkeypatch.setenv('DA_RESTORE_MAINTENANCE_DATABASE_URL', OPERATOR_URL)
    forwarded = []
    module = cold_restore if command == 'restore' else checkpoint_restore
    original_main = module.main
    def main(arguments):
        forwarded.extend(arguments)
        return original_main(arguments)
    monkeypatch.setattr(module, 'main', main)
    if command == 'restore':
        monkeypatch.delenv('DA_RESTORE_MAINTENANCE_DATABASE_URL')
        def bootstrap(paths, dump, files, auth, url, prefix):
            assert url == APP_URL
            return {'warnings': []}
        monkeypatch.setattr(cold_restore, 'bootstrap', bootstrap)
    else:
        def restore(paths, directory, auth, url, prefix, **kwargs):
            assert url == APP_URL
            assert kwargs['maintenance_database_url'] == OPERATOR_URL
        monkeypatch.setattr(checkpoint_restore, 'restore_checkpoint', restore)
    assert cli.main([command, *restore_arguments(command)]) == 0
    output = capsys.readouterr()
    for secret in (APP_URL, OPERATOR_URL, APP_SECRET, OPERATOR_SECRET):
        assert secret not in repr(forwarded)
        assert secret not in output.out + output.err


@pytest.mark.parametrize('exception', [cold_restore.ColdRestoreError, ManagedBackupError, RuntimeError])
@pytest.mark.parametrize('command', ['restore', 'restore-checkpoint'])
def test_upstream_exceptions_never_reach_restore_output_or_logs(command, exception, monkeypatch, capsys, caplog):
    monkeypatch.setenv('DA_RESTORE_DATABASE_URL', APP_URL)
    monkeypatch.setenv('DA_RESTORE_MAINTENANCE_DATABASE_URL', OPERATOR_URL)
    def fail(*args, **kwargs):
        raise exception(APP_URL + ' ' + OPERATOR_URL + ' SSE-C-SENTINEL')
    monkeypatch.setattr(cold_restore, 'bootstrap', fail)
    monkeypatch.setattr(checkpoint_restore, 'restore_checkpoint', fail)
    assert cli.main([command, *restore_arguments(command)]) == 1
    output = capsys.readouterr()
    for secret in (APP_URL, OPERATOR_URL, 'SSE-C-SENTINEL'):
        assert secret not in output.out + output.err + caplog.text


@pytest.mark.parametrize('failure', ['success', 'exit', 'timeout'])
def test_redis_password_is_decoded_into_environment_and_never_argv_or_output(monkeypatch, capsys, failure):
    secret = 'REDIS-SENTINEL@:/with space'
    broker = 'redis://fixture%2Duser:' + quote(secret, safe='') + '@example.invalid:6381/15'
    monkeypatch.setenv('REDISCLI_AUTH', 'WRONG-AMBIENT-SECRET')
    calls = []
    def run(arguments, **kwargs):
        calls.append(arguments)
        assert arguments == ['redis-cli', '--no-auth-warning', '-h', 'example.invalid', '-p', '6381',
                             '-n', '15', '--user', 'fixture-user', '--raw', 'PING']
        assert kwargs['env']['REDISCLI_AUTH'] == secret
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(arguments, 30, output=secret, stderr=broker)
        return SimpleNamespace(returncode=0 if failure == 'success' else 1,
                               stdout='PONG\n' if failure == 'success' else secret, stderr=broker)
    monkeypatch.setattr(checkpoint_restore.subprocess, 'run', run)
    if failure == 'success':
        assert checkpoint_restore._broker_command(broker, 'PING') == 'PONG'
    else:
        try:
            checkpoint_restore._broker_command(broker, 'PING')
        except cold_restore.ColdRestoreError:
            assert secret not in traceback.format_exc() and broker not in traceback.format_exc()
        else:
            pytest.fail('broker failure ignored')
    output = capsys.readouterr()
    assert secret not in repr(calls) + output.out + output.err
    assert broker not in repr(calls) + output.out + output.err


def test_redis_without_password_does_not_inherit_ambient_authentication(monkeypatch):
    monkeypatch.setenv('REDISCLI_AUTH', 'AMBIENT-SECRET')
    def run(arguments, **kwargs):
        assert 'REDISCLI_AUTH' not in kwargs['env']
        assert arguments == ['redis-cli', '--no-auth-warning', '-h', 'localhost', '-p', '6379', '-n', '0', '--raw', 'PING']
        return SimpleNamespace(returncode=0, stdout='PONG')
    monkeypatch.setattr(checkpoint_restore.subprocess, 'run', run)
    assert checkpoint_restore._broker_command('redis://localhost', 'PING') == 'PONG'


def test_postgres_environment_preserves_decoded_password_and_connection_options():
    url = make_url(APP_URL + '?sslmode=verify-full&options=-csearch_path%3Drecorded')
    env = checkpoint_restore._database_env(url)
    assert env['PGPASSWORD'] == APP_SECRET
    assert env['PGUSER'] == 'recorded_app' and env['PGDATABASE'] == 'application'
    assert env['PGHOST'] == '127.0.0.1' and env['PGPORT'] == '55432'
    assert env['PGSSLMODE'] == 'verify-full' and env['PGOPTIONS'] == '-csearch_path=recorded'


@pytest.mark.parametrize('failure', ['success', 'exit', 'timeout', 'invalid-options'])
def test_sse_customer_key_uses_child_environment_not_argv_or_diagnostics(tmp_path, monkeypatch, capsys, failure):
    secret = 'SSE-C-SENTINEL /+= with space'
    options = ['--endpoint-url=https://fixture.invalid', '--API-SSECustomerAlgorithm=AES256', '--API-SSECustomerKey=' + secret]
    inputs = {'S3ENABLE': 'true', 'S3BUCKET': 'fixture', 'S3ENDPOINTURL': 'https://fixture.invalid',
              'S3_SSE_CUSTOMER_ALGORITHM': 'AES256', 'S3_SSE_CUSTOMER_KEY': secret,
              'S4CMD_OPTS': shlex.join(options)}
    if failure == 'invalid-options':
        inputs['S4CMD_OPTS'] += ' --unverified=' + secret
        with pytest.raises(ManagedBackupError) as error:
            CloudCheckpoint(inputs)
        assert secret not in str(error.value)
        return
    calls = []
    def run(arguments, **kwargs):
        calls.append(arguments)
        assert shlex.split(kwargs['env']['S4CMD_OPTS']) == options
        assert secret not in repr(arguments)
        assert kwargs['stderr'] == subprocess.DEVNULL
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(arguments, 600, output=secret, stderr=secret)
        return SimpleNamespace(returncode=0 if failure == 'success' else 1, stdout='')
    monkeypatch.setattr(subprocess, 'run', run)
    cloud = CloudCheckpoint(inputs)
    if failure == 'success':
        assert cloud.listing() == {}
        cloud.put('files/object', tmp_path / 'source')
    else:
        try:
            cloud.listing()
        except ManagedBackupError:
            assert secret not in traceback.format_exc()
        else:
            pytest.fail('provider failure ignored')
    output = capsys.readouterr()
    assert calls and secret not in repr(calls) + output.out + output.err


def test_real_restore_cli_process_rejects_url_flags_without_credential_output():
    command = [sys.executable, '-m', 'docassemble_executor.cli', 'restore-checkpoint',
               *restore_arguments('restore-checkpoint'), '--database-url', APP_URL]
    env = dict(os.environ)
    root = Path(__file__).resolve().parents[3]
    env['PYTHONPATH'] = os.pathsep.join([str(root / 'docassemble_base'),
                                      str(root / 'uv-experiment/executor/src'), env.get('PYTHONPATH', '')])
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert 'invalid restore arguments' in result.stderr
    assert APP_URL not in result.stdout + result.stderr


def test_proof_crash_code_has_no_interpolated_database_credentials():
    source = (Path(__file__).resolve().parents[2] / 'preparation/checkpoint_restore_proof.py').read_text()
    crash = source.split('        crash = ', 1)[1].split('        for secret in ', 1)[0]
    assert 'render_as_string' not in crash and 'repr(operator)' not in crash
    assert 'env=restore_env' in crash and 'r.main(' in crash
    assert '--database-url' not in source and '--maintenance-database-url' not in source


@pytest.mark.parametrize('error_type,message', list(checkpoint_restore.RESTORE_FAILURE_MESSAGES.items()))
def test_owned_failure_types_surface_only_fixed_literals(error_type, message, monkeypatch, capsys, caplog):
    monkeypatch.setenv('DA_RESTORE_DATABASE_URL', APP_URL)
    monkeypatch.setenv('DA_RESTORE_MAINTENANCE_DATABASE_URL', OPERATOR_URL)
    def fail(*args, **kwargs):
        raise error_type(APP_URL + ' SSE-C-SENTINEL')
    monkeypatch.setattr(checkpoint_restore, 'restore_checkpoint', fail)
    assert checkpoint_restore.main(restore_arguments('restore-checkpoint')) == 1
    output = capsys.readouterr()
    assert message in output.out
    assert APP_URL not in output.out + output.err + caplog.text
    assert 'SSE-C-SENTINEL' not in output.out + output.err + caplog.text


@pytest.mark.parametrize('error_type', [cold_restore.ColdRestoreError, ManagedBackupError])
def test_unmapped_restore_and_provider_errors_always_use_generic_literal(error_type, monkeypatch, capsys):
    monkeypatch.setenv('DA_RESTORE_DATABASE_URL', APP_URL)
    monkeypatch.setenv('DA_RESTORE_MAINTENANCE_DATABASE_URL', OPERATOR_URL)
    def fail(*args, **kwargs):
        raise error_type(APP_URL + ' SSE-C-SENTINEL')
    monkeypatch.setattr(checkpoint_restore, 'restore_checkpoint', fail)
    assert checkpoint_restore.main(restore_arguments('restore-checkpoint')) == 1
    output = capsys.readouterr()
    assert output.out == checkpoint_restore.WARNING + '\n' + checkpoint_restore.RESTORE_FAILURE + '\n'
    assert output.err == ''
