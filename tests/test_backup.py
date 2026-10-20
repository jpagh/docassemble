"""Behavioral tests at the standalone and scheduled backup command boundaries."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from datetime import datetime

import pytest

REPO = Path(__file__).resolve().parents[1]
BACKUP = REPO / "Docker" / "backup.sh"
DAILY = REPO / "Docker" / "cron" / "docassemble-cron-daily.sh"


@pytest.fixture
def backup_env(tmp_path: Path) -> dict[str, str]:
    root = tmp_path / "docassemble root"
    for name in ("config", "files", "log", "backup", "webapp"):
        (root / name).mkdir(parents=True)
    (root / "config" / "config.yml").write_text("fixture: original\n")
    (root / "config" / "config.yml").chmod(0o640)
    (root / "files" / "uploaded file.txt").write_text("original upload\n")
    (root / "log" / "docassemble.log").write_text("original log\n")
    # Calling backup must not activate Python or execute interview cron jobs.
    (root / "webapp" / "run-cron.sh").write_text("#!/bin/bash\nexit 91\n")
    (root / "webapp" / "run-cron.sh").chmod(0o755)
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "DA_ROOT": str(root),
        # These are unadopted legacy scheduler fixtures, not the image's
        # /var/lib/docassemble/managed installation.
        "DA_UV_MANAGED": "false",
        "DA_MANAGED_STATE_DIR": str(root / "managed"),
        "DA_CONFIG_FILE": str(root / "config" / "config.yml"),
        "DA_ACTIVATE": str(tmp_path / "no-runtime" / "activate"),
        "CONTAINERROLE": "cron",
        "DABACKUPDAYS": "14",
        "DBTYPE": "postgresql",
        "LANG": "C.UTF-8",
    }


def invoke(env: dict[str, str], script: Path = BACKUP) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=30)


def install_cli(env: dict[str, str], name: str, body: str) -> Path:
    bin_dir = Path(env["HOME"]) / "bin"
    bin_dir.mkdir(exist_ok=True)
    executable = bin_dir / name
    executable.write_text("#!/usr/bin/env python3\n" + body)
    executable.chmod(0o755)
    env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
    return executable


def test_standalone_backup_copies_local_files_config_and_logs_without_runtime(backup_env):
    result = invoke(backup_env)
    assert result.returncode == 0, result.stderr
    root = Path(backup_env["DA_ROOT"])
    rolling = root / "backup" / datetime.now().strftime("%m-%d")
    assert (root / "backup" / "config.yml").read_text() == "fixture: original\n"
    assert (root / "backup" / "files" / "uploaded file.txt").read_text() == "original upload\n"
    assert (rolling / "files" / "uploaded file.txt").read_text() == "original upload\n"
    assert (rolling / "config" / "config.yml").read_text() == "fixture: original\n"
    assert (rolling / "log" / "docassemble.log").read_text() == "original log\n"
    assert (rolling / "config" / "config.yml").stat().st_mode & 0o777 == 0o640


def test_failed_cloud_component_is_not_masked_by_successful_final_upload(backup_env):
    backup_env.update(S3ENABLE="true", S3BUCKET="fixture-bucket", CONTAINERROLE="log")
    trace = Path(backup_env["HOME"]) / "cloud.jsonl"
    backup_env["CLOUD_TRACE"] = str(trace)
    install_cli(backup_env, "hostname", 'print("fixture-host")\n')
    install_cli(backup_env, "s4cmd", '''import json, os, sys
with open(os.environ["CLOUD_TRACE"], "a") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
sys.exit(23 if sys.argv[-1] == "s3://fixture-bucket/log" else 0)
''')
    result = invoke(backup_env)
    assert result.returncode != 0
    assert "cloud-logs failed (exit 23)" in result.stderr
    calls = [json.loads(line) for line in trace.read_text().splitlines()]
    assert calls[-1][-1].startswith("s3://fixture-bucket/backup/")


def test_scheduled_backup_reports_copy_failure_after_interview_cron(backup_env):
    root = Path(backup_env["DA_ROOT"])
    runtime = root / "runtime" / "bin"
    runtime.mkdir(parents=True)
    (runtime / "activate").write_text(":\n")
    backup_env["DA_PYTHON"] = str(runtime.parent)
    backup_env["DA_CONFIG"] = str(root / "missing-config.yml")
    (root / "webapp" / "backup.sh").write_text(BACKUP.read_text())
    (root / "webapp" / "run-cron.sh").write_text(
        '#!/bin/bash\nprintf "%s" "$1" > "$DA_ROOT/cron-ran"\n'
    )
    # The OS/config-reader boundary supplies no additional exports in this test.
    install_cli(backup_env, "su", "pass\n")
    result = invoke(backup_env, DAILY)
    assert result.returncode != 0
    assert "local-config failed" in result.stderr
    assert (root / "cron-ran").read_text() == "cron_daily"


def test_azure_uses_existing_paths_and_reports_individual_upload_failure(backup_env):
    root = Path(backup_env["DA_ROOT"])
    custom_log = root / "custom logs" / "nested"
    custom_log.mkdir(parents=True)
    (custom_log / "audit file.log").write_text("audit\n")
    trace = Path(backup_env["HOME"]) / "azure.jsonl"
    backup_env.update(AZUREENABLE="true", AZURECONTAINER="fixture-store", CONTAINERROLE="log",
                      LOGDIRECTORY=str(custom_log.parent), CLOUD_TRACE=str(trace))
    install_cli(backup_env, "hostname", 'print("fixture-host")\n')
    install_cli(backup_env, "az", '''import json, os, sys
with open(os.environ["CLOUD_TRACE"], "a") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
name = sys.argv[sys.argv.index("-n") + 1]
sys.exit(41 if name == "log/nested/audit file.log" else 0)
''')
    result = invoke(backup_env)
    assert result.returncode != 0
    assert "azure-log failed (exit 41)" in result.stderr
    calls = [json.loads(line) for line in trace.read_text().splitlines()]
    names = {call[call.index("-n") + 1] for call in calls}
    date = datetime.now().strftime("%m-%d")
    assert names == {"log/nested/audit file.log", f"backup/fixture-host/{date}/config/config.yml",
                     f"backup/fixture-host/{date}/custom logs/nested/audit file.log"}
    assert not (root / "backup" / date / "files").exists()


def test_database_dump_failure_survives_successful_upload_and_cleanup(backup_env):
    backup_env.update(CONTAINERROLE="sql", S3ENABLE="true", S3BUCKET="fixture-bucket", DABACKUPDAYS="0")
    install_cli(backup_env, "chown", "pass\n")
    install_cli(backup_env, "s4cmd", "pass\n")
    install_cli(backup_env, "su", '''import sys
if "pg_dump" in sys.argv[-1]:
    sys.exit(17)
print("postgres\\ntemplate0\\nfixture_database")
''')
    result = invoke(backup_env)
    assert result.returncode != 0
    assert "postgres-dump failed (exit 17)" in result.stderr


@pytest.mark.parametrize("azure", [False, True])
def test_existing_dated_retention_removes_expired_directories(backup_env, azure):
    root = Path(backup_env["DA_ROOT"])
    expired = root / "backup" / "01-01"
    expired.mkdir()
    (expired / "config.yml").write_text("old fixture\n")
    os.utime(expired, (1, 1))
    if azure:
        trace = Path(backup_env["HOME"]) / "retention.jsonl"
        backup_env.update(AZUREENABLE="true", AZURECONTAINER="fixture-store", CLOUD_TRACE=str(trace))
        install_cli(backup_env, "hostname", 'print("fixture-host")\n')
        install_cli(backup_env, "az", '''import json, os, sys
with open(os.environ["CLOUD_TRACE"], "a") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
''')
    result = invoke(backup_env)
    assert result.returncode == 0, result.stderr
    assert not expired.exists()
    if azure:
        calls = [json.loads(line) for line in trace.read_text().splitlines()]
        assert any(call[:3] == ["storage", "blob", "delete"] and
                   call[call.index("-n") + 1] == "backup/fixture-host/01-01/config.yml" for call in calls)


def test_failed_date_does_not_erase_existing_backups_or_report_success(backup_env):
    previous = Path(backup_env["DA_ROOT"]) / "backup" / "prior-evidence.txt"
    previous.write_text("previous backup\n")
    install_cli(backup_env, "date", "import sys\nsys.exit(19)\n")
    result = invoke(backup_env)
    assert result.returncode != 0
    assert "rolling-date failed" in result.stderr
    assert previous.read_text() == "previous backup\n"


@pytest.mark.parametrize("settings,rolling,files", [
    ({"DABACKUPDAYS": "0"}, False, False),
    ({"BACKUPFILESTORAGE": "false"}, True, False),
    ({"DAREADONLYFILESYSTEM": "true"}, False, False),
])
def test_existing_backup_configuration_controls_are_preserved(backup_env, settings, rolling, files):
    backup_env.update(settings)
    result = invoke(backup_env)
    assert result.returncode == 0, result.stderr
    root = Path(backup_env["DA_ROOT"])
    dated = root / "backup" / datetime.now().strftime("%m-%d")
    assert dated.exists() is rolling
    assert (dated / "files").exists() is files
    if settings.get("DAREADONLYFILESYSTEM") == "true":
        assert not list((root / "backup").iterdir())
    else:
        # backup-file-storage historically controls the dated copy, not the live copy.
        assert (root / "backup" / "files" / "uploaded file.txt").read_text() == "original upload\n"


def test_repeated_same_day_backup_replaces_the_earlier_dated_copy(backup_env):
    assert invoke(backup_env).returncode == 0
    root = Path(backup_env["DA_ROOT"])
    dated = root / "backup" / datetime.now().strftime("%m-%d")
    (dated / "earlier-operation.txt").write_text("not retained\n")
    (root / "files" / "uploaded file.txt").write_text("second upload with a different length\n")
    result = invoke(backup_env)
    assert result.returncode == 0, result.stderr
    assert not (dated / "earlier-operation.txt").exists()
    assert (dated / "files" / "uploaded file.txt").read_text() == "second upload with a different length\n"


def test_sourced_backup_is_callable_without_changing_the_lifecycle_environment(backup_env):
    backup_env["BACKUP_SCRIPT"] = str(BACKUP)
    result = subprocess.run(["bash", "-euc", '''
source "$BACKUP_SCRIPT"
test ! -f "$DA_ROOT/backup/config.yml"
before_role="$CONTAINERROLE"
before_pwd="$PWD"
docassemble_backup
test "$CONTAINERROLE" = "$before_role"
test "$PWD" = "$before_pwd"
test -f "$DA_ROOT/backup/config.yml"
'''], env=backup_env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_scheduled_configuration_reader_failure_cannot_fall_back_to_defaults(backup_env):
    root = Path(backup_env["DA_ROOT"])
    runtime = root / "runtime" / "bin"
    runtime.mkdir(parents=True)
    (runtime / "activate").write_text(":\n")
    backup_env["DA_PYTHON"] = str(runtime.parent)
    (root / "webapp" / "backup.sh").write_text(BACKUP.read_text())
    install_cli(backup_env, "su", "import sys\nsys.exit(31)\n")
    result = invoke(backup_env, DAILY)
    assert result.returncode != 0
    assert "backup configuration load failed" in result.stderr
    assert not (root / "backup" / "config.yml").exists()


def test_failed_hostname_does_not_upload_into_another_cloud_namespace(backup_env):
    trace = Path(backup_env["HOME"]) / "namespace.jsonl"
    backup_env.update(S3ENABLE="true", S3BUCKET="fixture-bucket", CONTAINERROLE="log", CLOUD_TRACE=str(trace))
    install_cli(backup_env, "hostname", "import sys\nsys.exit(29)\n")
    install_cli(backup_env, "s4cmd", '''import json, os, sys
with open(os.environ["CLOUD_TRACE"], "a") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
''')
    result = invoke(backup_env)
    assert result.returncode != 0
    assert "cloud-hostname failed" in result.stderr
    calls = [json.loads(line) for line in trace.read_text().splitlines()]
    assert [call[-1] for call in calls] == ["s3://fixture-bucket/log"]


def test_remote_redis_failure_is_reported_without_unsuppressing_native_output(backup_env):
    backup_env.update(REDIS="redis://remote-fixture", REDISCLI="redis-cli --user fixture")
    install_cli(backup_env, "redis-cli", '''import sys
print("fixture-only-sensitive-native-output", file=sys.stderr)
sys.exit(47)
''')
    result = invoke(backup_env)
    assert result.returncode != 0
    assert "rolling-remote-redis failed (exit 47)" in result.stderr
    assert "fixture-only-sensitive-native-output" not in result.stderr


@pytest.mark.parametrize('remote', [False, True])
@pytest.mark.parametrize('failed', [False, True])
def test_managed_dump_and_manifest_follow_existing_postgres_destinations(backup_env, remote, failed):
    backup_env.update(DA_UV_MANAGED='true', DBNAME='fixture_database')
    if remote:
        backup_env.update(DBHOST='remote-fixture')
    else:
        backup_env.update(CONTAINERROLE='sql')
    install_cli(backup_env, 'chown', 'pass\n')
    install_cli(backup_env, 'su', 'print("fixture_database")\n')
    executable = install_cli(backup_env, 'python', '''import pathlib, sys
assert sys.argv[1:4] == ['-m', 'docassemble_executor.managed_backup', '--dump']
dump = pathlib.Path(sys.argv[4])
dump.write_bytes(b'fixture-dump')
directory = pathlib.Path(str(dump) + '.managed')
directory.mkdir()
''' + ('sys.exit(23)\n' if failed else '(directory / "manifest.json").write_text("complete")\n'))
    backup_env['DA_EXECUTOR_ENTRYPOINT'] = str(executable.parent / 'da-executor')
    result = invoke(backup_env)
    assert result.returncode == (1 if failed else 0), result.stderr
    root = Path(backup_env['DA_ROOT']) / 'backup'
    dated = root / datetime.now().strftime('%m-%d') / 'postgres'
    assert (dated / 'fixture_database').read_bytes() == b'fixture-dump'
    assert (dated / 'fixture_database.managed/manifest.json').exists() is not failed
    if not remote:
        assert (root / 'postgres/fixture_database.managed/manifest.json').exists() is not failed
    if failed:
        assert 'managed-postgres-dump failed (exit 23)' in result.stderr


def test_managed_full_backup_cannot_silently_succeed_without_database(backup_env):
    backup_env.update(DA_UV_MANAGED='true', DABACKUPDAYS='0')
    result = invoke(backup_env)
    assert result.returncode == 1
    assert 'managed objects incomplete (no application database dump selected)' in result.stderr


def test_managed_readonly_without_cloud_cannot_claim_durable_backup(backup_env):
    backup_env.update(DA_UV_MANAGED='true', CONTAINERROLE='sql', DBNAME='fixture_database',
                      DAREADONLYFILESYSTEM='true')
    install_cli(backup_env, 'chown', 'pass\n')
    install_cli(backup_env, 'su', 'print("fixture_database")\n')
    executable = install_cli(backup_env, 'python', 'pass\n')
    backup_env['DA_EXECUTOR_ENTRYPOINT'] = str(executable.parent / 'da-executor')
    result = invoke(backup_env)
    assert result.returncode == 1
    assert 'no durable backup destination selected' in result.stderr
