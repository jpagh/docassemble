"""Real checkpoint restore keeps operator/app authority separate from child argv."""
# ruff: noqa: F811
import os

from sqlalchemy import create_engine, text

from test_checkpoint_restore_postgres import checkpoint  # noqa: F401
from test_preparation_postgres import protocol  # noqa: F401
from test_managed_backup_postgres import objects  # noqa: F401
from docassemble_executor import checkpoint_restore as restore


def test_checkpoint_cli_environment_retains_database_owner_and_keeps_urls_out_of_children(
        checkpoint, monkeypatch, capsys):
    paths, directory, auth, url, _ = checkpoint
    database_url = url.render_as_string(hide_password=False)
    operator_url = url.set(database='postgres').render_as_string(hide_password=False)
    monkeypatch.setenv('DA_RESTORE_DATABASE_URL', database_url)
    monkeypatch.setenv('DA_RESTORE_MAINTENANCE_DATABASE_URL', operator_url)
    statements, commands = [], []
    admin = restore._admin
    run = restore.subprocess.run
    def capture_admin(operator, statement):
        assert operator.database == 'postgres'
        statements.append(statement)
        return admin(operator, statement)
    def capture_run(arguments, **kwargs):
        commands.append(arguments)
        if arguments[0] == 'pg_restore' and '--exit-on-error' in arguments:
            assert kwargs['env']['PGUSER'] == url.username
            assert kwargs['env']['PGPASSWORD'] == url.password
            assert kwargs['env']['PGHOST'] == url.host
            assert kwargs['env']['PGPORT'] == str(url.port)
            assert database_url not in repr(arguments) and operator_url not in repr(arguments)
            if url.password:
                assert url.password not in repr(arguments)
        return run(arguments, **kwargs)
    monkeypatch.setattr(restore, '_admin', capture_admin)
    monkeypatch.setattr(restore.subprocess, 'run', capture_run)
    monkeypatch.setattr(restore.ExecutorPaths, 'from_env', lambda: paths)
    try:
        assert restore.main(['--checkpoint', str(directory), '--authorization', str(auth), '--prefix', 'test_']) == 0
    finally:
        monkeypatch.setattr(restore, '_admin', admin)
        monkeypatch.setattr(restore.subprocess, 'run', run)
    assert any(' OWNER ' + restore._quoted(url.username) + ' TEMPLATE template0' in statement for statement in statements)
    assert not any('ALTER ROLE' in statement or 'CREATEDB' in statement for statement in statements)
    assert any(arguments[0] == 'pg_restore' for arguments in commands)
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text('SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=:name'),
                                     {'name': url.database}) == url.username
    finally:
        engine.dispose()
    output = capsys.readouterr()
    for secret in (database_url, operator_url, url.password):
        if secret:
            assert secret not in output.out + output.err
    assert os.environ['DA_RESTORE_DATABASE_URL'] == database_url


def test_mismatched_maintenance_environment_refuses_before_database_lifecycle(checkpoint, monkeypatch, capsys):
    paths, directory, auth, url, _ = checkpoint
    monkeypatch.setenv('DA_RESTORE_DATABASE_URL', url.render_as_string(hide_password=False))
    monkeypatch.setenv('DA_RESTORE_MAINTENANCE_DATABASE_URL', 'sqlite:///OPERATOR-SENTINEL/postgres')
    statements = []
    original_admin = restore._admin
    monkeypatch.setattr(restore, '_admin', lambda *args: statements.append(args))
    monkeypatch.setattr(restore.ExecutorPaths, 'from_env', lambda: paths)
    try:
        assert restore.main(['--checkpoint', str(directory), '--authorization', str(auth), '--prefix', 'test_']) == 1
    finally:
        monkeypatch.setattr(restore, '_admin', original_admin)
    assert statements == []
    assert not (paths.state_dir / 'checkpoint-restore.json').exists()
    output = capsys.readouterr()
    assert restore.RESTORE_FAILURE_MESSAGES[restore.MaintenanceRestoreError] in output.out
    assert 'OPERATOR-SENTINEL' not in output.out + output.err
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text('SELECT value FROM public.post_checkpoint')) == 'subsequent data'
    finally:
        engine.dispose()
