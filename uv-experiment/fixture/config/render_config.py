#!/usr/bin/env python3
"""Render the docassemble configuration template for the disposable fixture.

Only stdlib plus PyYAML (a docassemble dependency) are used.  The script fails
if any template placeholder is left unresolved so the fixture cannot silently
ship a broken configuration.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

VALUES = {
    "AZUREACCOUNTKEY": "null",
    "AZUREACCOUNTNAME": "null",
    "AZURECONTAINER": "null",
    "AZUREENABLE": "false",
    "BEHINDHTTPSLOADBALANCER": "false",
    "COLLECTSTATISTICS": "false",
    "CONFIGFROM": "null",
    "DAALLOWCONFIGURATIONEDITING": "true",
    "DAALLOWLOGVIEWING": "true",
    "DAALLOWUPDATES": "true",
    "DABACKUPDAYS": "14",
    "DACELERYWORKERS": "0",
    "DADEBUG": "false",
    "DAENABLEPLAYGROUND": "true",
    "DAEXPOSEWEBSOCKETS": "false",
    "DAHOSTNAME": "none",
    "DAMAXCELERYWORKERS": "1",
    "DAROOTOWNED": "false",
    "DAREADONLYFILESYSTEM": "false",
    "DASECRETKEY": "fixture-secret-key-not-for-production",
    "DASQLPING": "false",
    "DASTABLEVERSION": "false",
    "DASUPERVISORPASSWORD": "null",
    "DASUPERVISORUSERNAME": "null",
    "DAUPDATEONSTART": "false",
    "DAWEBSERVER": "none",
    "DAWEBSOCKETSIP": "null",
    "DAWEBSOCKETSPORT": "null",
    "DBBACKUP": "false",
    "DBHOST": "localhost",
    "DBNAME": "docassemble",
    "DBPASSWORD": "fixturepass",
    "DBPORT": "5432",
    "DBPREFIX": "postgresql+psycopg2://",
    "DBSSLCERT": "null",
    "DBSSLKEY": "null",
    "DBSSLMODE": "null",
    "DBSSLROOTCERT": "null",
    "DBTABLEPREFIX": "null",
    "DBUSER": "docassemble",
    "EC2": "false",
    "ENABLEUNOCONV": "false",
    "GOTENBERGENABLE": "false",
    "GOTENBERGPASSWORD": "null",
    "GOTENBERGURL": "null",
    "GOTENBERGUSERNAME": "null",
    "KUBERNETES": "false",
    "LETSENCRYPTEMAIL": "null",
    "LOCALE": "en_US.UTF-8",
    "LOGSERVER": "null",
    "POSTURLROOT": "/",
    "RABBITMQ": "redis://localhost:6379/1",
    "REDIS": "redis://localhost:6379/0",
    "S3_SSE_ALGORITHM": "null",
    "S3_SSE_CUSTOMER_ALGORITHM": "null",
    "S3_SSE_CUSTOMER_KEY": "null",
    "S3_SSE_KMS_KEY_ID": "null",
    "S3ACCESSKEY": "null",
    "S3BUCKET": "null",
    "S3ENABLE": "false",
    "S3ENDPOINTURL": "null",
    "S3REGION": "null",
    "S3SECRETACCESSKEY": "null",
    "SERVERADMIN": "admin@example.com",
    "TIMEZONE": "America/New_York",
    "URLROOT": "http://localhost",
    "USECLOUDURLS": "false",
    "USEHTTPS": "false",
    "USELETSENCRYPT": "false",
    "USEMINIO": "false",
    "USENGINXTOSERVEFILES": "false",
    "XSENDFILE": "false",
}


def render(template_text: str) -> str:
    for key, value in VALUES.items():
        template_text = template_text.replace("{{" + key + "}}", value)
    remaining = sorted(set(re.findall(r"\{\{[A-Z_0-9]+\}\}", template_text)))
    if remaining:
        raise SystemExit("unresolved configuration placeholders: " + ", ".join(remaining))
    return template_text


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: render_config.py TEMPLATE EXTRAS OUTPUT", file=sys.stderr)
        return 2
    template_path, extras_path, output_path = (Path(item) for item in argv[1:])
    config = yaml.safe_load(render(template_path.read_text(encoding="utf-8")))
    extras = yaml.safe_load(extras_path.read_text(encoding="utf-8")) or {}
    config.update(extras)
    output_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
