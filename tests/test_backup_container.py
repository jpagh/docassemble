"""Real backup/restore at the command boundary, in network-isolated containers.

Set BACKUP_TEST_IMAGE to the ticket-01 fixture image (or an equivalent image
with PostgreSQL, Redis and rsync). No host ports or user containers are touched.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from collections.abc import Iterator
import uuid

import pytest

REPO = Path(__file__).resolve().parents[1]
ROOT = "/srv/docassemble-backup-fixture"
IMAGE = os.environ.get("BACKUP_TEST_IMAGE")
pytestmark = pytest.mark.skipif(not IMAGE, reason="set BACKUP_TEST_IMAGE for disposable container evidence")


class Container:
    def __init__(self, name: str):
        self.name = name

    def shell(self, command: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["docker", "exec", self.name, "bash", "-c", command],
                              check=check, capture_output=True, text=True, timeout=120)

    def backup(self, extra: str = "") -> subprocess.CompletedProcess[str]:
        return self.shell(f"env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/root "
                          f"DA_ROOT={ROOT} DA_ACTIVATE=/missing-runtime/activate "
                          f"CONTAINERROLE=all DBTYPE=postgresql DABACKUPDAYS=14 {extra} "
                          f"bash {ROOT}/webapp/backup.sh", check=False)


def record_evidence(name: str, text: str) -> None:
    directory = os.environ.get("BACKUP_EVIDENCE_DIR")
    if directory:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)


@pytest.fixture
def container() -> Iterator[Container]:
    name = "da-backup-proof-" + uuid.uuid4().hex[:12]
    assert IMAGE is not None
    # This suite verifies legacy, unadopted scheduling even when its OS image
    # also contains the managed experiment. Install the scheduler's shell entrypoints.
    command = ["docker", "run", "--detach", "--network", "none", "--name", name,
               "-e", "DA_UV_MANAGED=false", "-e", f"DA_MANAGED_STATE_DIR={ROOT}/managed",
               "--entrypoint", "/bin/bash"]
    for source, target in ((REPO / "Docker" / "backup.sh", ROOT + "/webapp/backup.sh"),
                           (REPO / "Docker" / "managed-consumer.sh", ROOT + "/webapp/managed-consumer.sh"),
                           (REPO / "Docker" / "run-certbot.sh", ROOT + "/webapp/run-certbot.sh"),
                           (REPO / "Docker" / "cron" / "docassemble-cron-daily.sh", "/opt/daily.sh")):
        command += ["--mount", f"type=bind,source={source},target={target},readonly"]
    command += [IMAGE, "-c", "sleep infinity"]
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)
    instance = Container(name)
    try:
        instance.shell(f'''set -e
pg_ctlcluster "$(ls /etc/postgresql | sort -V | tail -1)" main start
su postgres -c 'cd /tmp; createdb backup_fixture'
su postgres -c "cd /tmp; psql backup_fixture -c \\\"CREATE TABLE evidence (id int, value text); INSERT INTO evidence VALUES (1, 'original database');\\\""
mkdir -p {ROOT}/{{config,files,log,backup}} /var/lib/redis /var/log/nginx
printf 'fixture: original configuration\\n' > {ROOT}/config/config.yml
chmod 640 {ROOT}/config/config.yml
printf 'original uploaded file\\n' > {ROOT}/files/upload.txt
printf 'original log\\n' > {ROOT}/log/docassemble.log
printf 'original access log\\n' > /var/log/nginx/fixture-access.log
redis-server --daemonize yes --bind 127.0.0.1 --dir /var/lib/redis --dbfilename dump.rdb
redis-cli SET fixture:key 'original redis'
redis-cli SAVE
''')
        yield instance
    finally:
        subprocess.run(["docker", "rm", "--force", name], check=True, capture_output=True, timeout=120)


def test_local_backup_round_trip_with_real_database_files_and_redis(container: Container):
    result = container.backup()
    assert result.returncode == 0, result.stderr
    result = container.shell(f'''set -e
DAY=$(date +%m-%d)
BACKUP={ROOT}/backup/$DAY
test -f "$BACKUP/redis.rdb"
test -f "{ROOT}/backup/nginxlogs/fixture-access.log"
printf 'changed file\\n' > {ROOT}/files/upload.txt
printf 'changed config\\n' > {ROOT}/config/config.yml
su postgres -c 'cd /tmp; dropdb backup_fixture; createdb backup_fixture'
su postgres -c "cd /tmp; pg_restore -d backup_fixture '$BACKUP/postgres/backup_fixture'"
cp "$BACKUP/files/upload.txt" {ROOT}/files/upload.txt
cp "$BACKUP/config/config.yml" {ROOT}/config/config.yml
redis-cli SHUTDOWN NOSAVE
cp "$BACKUP/redis.rdb" /var/lib/redis/dump.rdb
redis-server --daemonize yes --bind 127.0.0.1 --dir /var/lib/redis --dbfilename dump.rdb > /dev/null
su postgres -c 'cd /tmp; psql -At backup_fixture -c "SELECT value FROM evidence WHERE id = 1"'
cat {ROOT}/files/upload.txt {ROOT}/config/config.yml
redis-cli GET fixture:key
stat -c %a {ROOT}/config/config.yml
''')
    assert result.stdout.splitlines() == ["original database", "original uploaded file",
                                          "fixture: original configuration", "original redis", "640"]
    record_evidence("local-round-trip.txt", result.stdout)


def test_certificate_backup_does_not_renew_certificates(container: Container):
    container.shell('''set -e
mkdir -p /etc/letsencrypt/live/fixture.example /tmp/backup-guard
touch /etc/letsencrypt/da_using_lets_encrypt
printf 'fixture certificate\\n' > /etc/letsencrypt/live/fixture.example/fullchain.pem
printf '#!/bin/bash\\ntouch /tmp/renewed\\nexit 67\\n' > /tmp/backup-guard/certbot
chmod +x /tmp/backup-guard/certbot
''')
    result = container.backup("USEHTTPS=true USELETSENCRYPT=true PATH=/tmp/backup-guard:/usr/local/bin:/usr/bin:/bin")
    assert result.returncode == 0, result.stderr
    result = container.shell(f'''set -e
test ! -f /tmp/renewed
DAY=$(date +%m-%d)
test -f {ROOT}/backup/$DAY/letsencrypt.tar.gz
tar -xOf {ROOT}/backup/letsencrypt.tar.gz etc/letsencrypt/live/fixture.example/fullchain.pem
''')
    assert result.stdout == "fixture certificate\n"
    record_evidence("certificate-backup.txt", result.stdout)


def test_cron_role_backs_up_configured_remote_postgres(container: Container):
    container.shell("su postgres -c \"cd /tmp; psql -c \\\"ALTER ROLE postgres PASSWORD 'fixture-only';\\\"\"")
    result = container.backup("CONTAINERROLE=cron DBHOST=127.0.0.1 DBNAME=backup_fixture "
                              "DBUSER=postgres DBPASSWORD=fixture-only DBPORT=5432")
    assert result.returncode == 0, result.stderr
    result = container.shell(f'''set -e
DAY=$(date +%m-%d)
su postgres -c 'cd /tmp; createdb restored_remote'
# Remote dumps retain their existing root-owned private directory permissions.
PGPASSWORD=fixture-only pg_restore -h 127.0.0.1 -U postgres -d restored_remote "{ROOT}/backup/$DAY/postgres/backup_fixture"
su postgres -c 'cd /tmp; psql -At restored_remote -c "SELECT value FROM evidence WHERE id = 1"'
''')
    assert result.stdout == "original database\n"
    record_evidence("remote-postgres-round-trip.txt", result.stdout)


def test_daily_scheduler_reads_configuration_and_backs_up_after_jobs(container: Container):
    container.shell(f'''set -e
mkdir -p "{ROOT}/custom logs"
printf 'configured log\\n' > "{ROOT}/custom logs/configured.log"
cat > {ROOT}/config/config.yml <<'YAML'
secretkey: fixture-only-secret
backup days: 0
log: {ROOT}/custom logs
YAML
chown root:www-data {ROOT}/config/config.yml
chmod 640 {ROOT}/config/config.yml
cat > {ROOT}/webapp/run-cron.sh <<'SH'
#!/bin/bash
printf '%s' "$1" > "$DA_ROOT/cron-ran"
printf 'after daily interview jobs\\n' > "$DA_ROOT/files/upload.txt"
SH
chmod +x {ROOT}/webapp/run-cron.sh
''')
    result = container.shell(f"DA_ROOT={ROOT} DA_PYTHON=/usr/share/docassemble/local3.12 "
                             "CONTAINERROLE=cron DABACKUPDAYS=14 bash /opt/daily.sh", check=False)
    assert result.returncode == 0, result.stderr
    result = container.shell(f'''set -e
test ! -d "{ROOT}/backup/$(date +%m-%d)"
cat {ROOT}/cron-ran
cat {ROOT}/backup/files/upload.txt
cat "{ROOT}/backup/custom logs/configured.log"
stat -c %a {ROOT}/backup/config.yml
''')
    assert result.stdout.splitlines() == ["cron_dailyafter daily interview jobs", "configured log", "640"]
    record_evidence("scheduled-configuration.txt", result.stdout)


def test_scheduled_certificate_backup_keeps_renewal_and_job_order(container: Container):
    container.shell(f'''set -e
mkdir -p /etc/letsencrypt/live/fixture.example
touch /etc/letsencrypt/da_using_lets_encrypt
cat > {ROOT}/config/config.yml <<'YAML'
secretkey: fixture-only-secret
use https: true
use lets encrypt: true
external hostname: fixture.example
enable email server: false
YAML
chown root:www-data {ROOT}/config/config.yml
chmod 640 {ROOT}/config/config.yml
cat > /usr/share/docassemble/local3.12/bin/certbot <<'SH'
#!/bin/bash
printf 'renewed fixture certificate\\n' > /etc/letsencrypt/live/fixture.example/fullchain.pem
SH
chmod +x /usr/share/docassemble/local3.12/bin/certbot
cat > {ROOT}/webapp/run-cron.sh <<'SH'
#!/bin/bash
tar -xOf "$DA_ROOT/backup/letsencrypt.tar.gz" etc/letsencrypt/live/fixture.example/fullchain.pem > "$DA_ROOT/cert-before-jobs"
printf 'after jobs certificate\\n' > /etc/letsencrypt/live/fixture.example/fullchain.pem
SH
chmod +x {ROOT}/webapp/run-cron.sh
''')
    result = container.shell(f"DA_ROOT={ROOT} DA_PYTHON=/usr/share/docassemble/local3.12 "
                             "CONTAINERROLE=all bash /opt/daily.sh", check=False)
    assert result.returncode == 0, result.stderr
    result = container.shell(f'''set -e
cat {ROOT}/cert-before-jobs
tar -xOf "{ROOT}/backup/$(date +%m-%d)/letsencrypt.tar.gz" etc/letsencrypt/live/fixture.example/fullchain.pem
''')
    assert result.stdout.splitlines() == ["renewed fixture certificate", "after jobs certificate"]
    record_evidence("scheduled-order.txt", result.stdout)


@pytest.mark.parametrize("provider", ["s3", "azure"])
def test_cloud_branches_preserve_existing_component_destinations(container: Container, provider: str):
    # These executables fake only the external cloud CLI boundary, not backup logic.
    container.shell('''set -e
mkdir -p /tmp/cloud-cli /etc/letsencrypt/live/fixture.example /var/log/apache2
touch /etc/letsencrypt/da_using_lets_encrypt
printf 'fixture certificate\\n' > /etc/letsencrypt/live/fixture.example/fullchain.pem
printf 'fixture site\\n' > /etc/apache2/sites-available/fixture.conf
printf 'fixture apache log\\n' > /var/log/apache2/fixture-access.log
cat > /tmp/cloud-cli/cloud-fake <<'PY'
#!/usr/share/docassemble/executor-venv/bin/python
import json, os, pathlib, sys
args = sys.argv[1:]
if pathlib.Path(sys.argv[0]).name == 's4cmd':
    source = pathlib.Path(args[-2])
    prefix = args[-1].removeprefix('s3://fixture-bucket/')
    if source.is_dir():
        keys = [prefix + '/' + str(file.relative_to(source)) for file in source.rglob('*') if file.is_file()]
    else:
        keys = [prefix]
    auth_ok = os.environ.get('AWS_ACCESS_KEY_ID') == 'fixture-access' and os.environ.get('AWS_SECRET_ACCESS_KEY') == 'fixture-only'
else:
    keys = [args[args.index('-n') + 1]]
    auth_ok = os.environ.get('AZURE_STORAGE_ACCOUNT') == 'fixture-account' and os.environ.get('AZURE_STORAGE_KEY') == 'fixture-only'
with open('/tmp/cloud-cli/results.jsonl', 'a') as stream:
    stream.write(json.dumps({'keys': keys, 'auth_preserved': auth_ok}) + '\\n')
PY
chmod +x /tmp/cloud-cli/cloud-fake
ln -s cloud-fake /tmp/cloud-cli/s4cmd
ln -s cloud-fake /tmp/cloud-cli/az
''')
    flags = ("S3ENABLE=true S3BUCKET=fixture-bucket AWS_ACCESS_KEY_ID=fixture-access "
             "AWS_SECRET_ACCESS_KEY=fixture-only" if provider == "s3" else
             "AZUREENABLE=true AZURECONTAINER=fixture-store AZURE_STORAGE_ACCOUNT=fixture-account "
             "AZURE_STORAGE_KEY=fixture-only")
    result = container.backup(flags + " USEHTTPS=true USELETSENCRYPT=true DAWEBSERVER=apache "
                              "PATH=/tmp/cloud-cli:/usr/local/bin:/usr/bin:/bin")
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in container.shell("cat /tmp/cloud-cli/results.jsonl").stdout.splitlines()]
    keys = {key for call in calls for key in call["keys"]}
    day = container.shell("date +%m-%d").stdout.strip()
    assert {"letsencrypt.tar.gz", "apache/fixture.conf", "apachelogs/fixture-access.log",
            "log/docassemble.log", "postgres/backup_fixture", "redis.rdb",
            f"backup/{day}/config/config.yml", f"backup/{day}/postgres/backup_fixture",
            f"backup/{day}/redis.rdb", f"backup/{day}/letsencrypt.tar.gz"} <= keys
    assert not any("/files/" in key for key in keys)
    assert all(call["auth_preserved"] for call in calls)
    record_evidence(f"cloud-{provider}-fake.json", json.dumps(
        {"external_service": "CLI fake, not a live provider", "keys": sorted(keys), "auth_preserved": True}, indent=2
    ) + "\n")


def test_combined_redis_cron_role_does_not_fall_back_to_remote_when_local_dump_is_absent(container: Container):
    container.shell("rm /var/lib/redis/dump.rdb")
    result = container.backup("CONTAINERROLE=redis:cron REDIS=redis://remote-fixture REDISCLI=false")
    assert result.returncode == 0, result.stderr


def test_scheduled_exim_certificate_work_preserves_private_key_access(container: Container):
    container.shell(f'''set -e
mkdir -p /etc/letsencrypt/live/fixture.example /etc/ssl/docassemble
rm -f /etc/ssl/docassemble/exim.crt /etc/ssl/docassemble/exim.key
touch /etc/letsencrypt/da_using_lets_encrypt
printf 'fixture certificate\\n' > /etc/letsencrypt/live/fixture.example/fullchain.pem
printf 'fixture-only private key\\n' > /etc/letsencrypt/live/fixture.example/privkey.pem
chmod 600 /etc/letsencrypt/live/fixture.example/privkey.pem
cat > {ROOT}/config/config.yml <<'YAML'
secretkey: fixture-only-secret
use https: true
use lets encrypt: true
external hostname: fixture.example
enable email server: true
YAML
chown root:www-data {ROOT}/config/config.yml
chmod 640 {ROOT}/config/config.yml
printf '#!/bin/bash\\nexit 0\\n' > /usr/share/docassemble/local3.12/bin/certbot
printf '#!/bin/bash\\nexit 0\\n' > /usr/share/docassemble/local3.12/bin/supervisorctl
printf '#!/bin/bash\\nexit 0\\n' > {ROOT}/webapp/run-cron.sh
chmod +x /usr/share/docassemble/local3.12/bin/{{certbot,supervisorctl}} {ROOT}/webapp/run-cron.sh
''')
    result = container.shell(f"DA_ROOT={ROOT} DA_PYTHON=/usr/share/docassemble/local3.12 "
                             "CONTAINERROLE=all bash /opt/daily.sh", check=False)
    assert result.returncode == 0, result.stderr
    result = container.shell("stat -c '%G %a' /etc/exim4/exim.key")
    assert result.stdout == "Debian-exim 640\n"
    record_evidence("scheduled-exim-access.txt", result.stdout)
