"""Independent configuration reading.

The executor must not import docassemble to discover database or recovery
settings, so it parses ``config.yml`` directly with PyYAML.  Only the handful of
keys needed for infrastructure and compatibility checks are exposed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import os
import re

import yaml


@dataclass(frozen=True)
class DatabaseSettings:
    host: str = "localhost"
    port: int | None = None
    user: str | None = None
    password: str | None = None
    name: str = "docassemble"

    @property
    def connect_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "host": self.host,
            "dbname": self.name,
        }
        if self.port:
            kwargs["port"] = self.port
        if self.user:
            kwargs["user"] = self.user
        if self.password:
            kwargs["password"] = self.password
        return kwargs


def load_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError("configuration file not found: " + str(path))
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("configuration file is not a mapping: " + str(path))
    # Match config.load's literal overlay before top-level key normalization.
    overlay = raw.get('config from')
    if overlay is not None:
        if not isinstance(overlay, dict):
            raise ValueError('unresolved configuration overlay')
        raw.update(overlay)
        raw.pop('config from', None)
    if any(not isinstance(key, str) for key in raw):
        raise ValueError('configuration keys must be strings')
    config = {key.replace("_", " "): value for key, value in raw.items()}
    # Infrastructure policy must see the same supported overrides as consumers.
    if os.environ.get('ENVIRONMENT_TAKES_PRECEDENCE', 'false').lower().strip() == 'true':
        for variable, key in (('DABACKUPDAYS', 'backup days'), ('RABBITMQ', 'rabbitmq'),
                              ('DAREADONLYFILESYSTEM', 'read only file system')):
            value = environment_value(variable)
            if value != '' and value is not None:
                config[key] = value
        if 'DBBACKUP' in os.environ:
            db = config.get('db') or {}
            if not isinstance(db, dict):
                raise ValueError("configuration 'db' section is not a mapping")
            config['db'] = dict(db, backup=environment_value('DBBACKUP'))
    def require_resolved(value):
        if isinstance(value, str) and ('arn:aws:secretsmanager:' in value or '@Microsoft.KeyVault(' in value):
            raise ValueError('unresolved infrastructure configuration')
        if isinstance(value, dict):
            for child in value.values():
                require_resolved(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                require_resolved(child)
    for key in ('backup days', 'backup file storage', 'read only file system', 'uploads', 'rabbitmq', 's3', 'azure'):
        require_resolved(config.get(key))
    require_resolved((config.get('db') or {}).get('backup') if isinstance(config.get('db') or {}, dict) else None)
    return config


def environment_value(variable: str):
    """The scalar translation used by the application's environment overrides."""
    value = os.environ.get(variable, '').strip()
    if value in ('true', 'True'):
        return True
    if value in ('false', 'False'):
        return False
    if value in ('null', 'None', 'Null'):
        return None
    if re.fullmatch(r'-?[0-9]+', value):
        return int(value)
    return value


def database_settings(config: dict[str, Any]) -> DatabaseSettings:
    db = config.get("db") or {}
    if not isinstance(db, dict):
        raise ValueError("configuration 'db' section is not a mapping")
    port = db.get("port")
    return DatabaseSettings(
        host=db.get("host") or "localhost",
        port=int(port) if port else None,
        user=db.get("user"),
        password=db.get("password"),
        name=db.get("name") or "docassemble",
    )
