"""Revision-bound Package projection, not historical account/grant authority."""
import re

from sqlalchemy import MetaData, Table, inspect, select, update


_URL = re.compile(r'[A-Za-z][A-Za-z0-9+.\-]*://[^\s]+')


def _redact(value):
    """Strip embedded credentials; credentials are current authority, never projected.

    Package rows can carry a bare URL (``giturl``) or a PEP 508 direct reference
    (``name @ https://...`` in ``limitation``), so every URL-shaped run is
    redacted for userinfo and query values, not just whole-string URLs.
    """
    if isinstance(value, str):
        if '://' not in value:
            return value
        from docassemble.base.managed_direct import redact_direct_url
        return _URL.sub(lambda match: redact_direct_url(match.group(0)), value)
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def package_projection(connection, prefix):
    name = prefix + 'package'
    if not inspect(connection).has_table(name):
        return None
    packages = Table(name, MetaData(), autoload_with=connection)
    return [{key: _redact(value) for key, value in dict(row).items()}
            for row in connection.execute(select(packages).order_by(packages.c.id)).mappings()]


def restore_package_projection(connection, prefix, snapshot):
    """Keep capture-point accounts/grants; restore only recorded Package values."""
    name = prefix + 'package'
    if not inspect(connection).has_table(name):
        return
    rows = snapshot.get('package_projection')
    if rows is None:
        raise ValueError('checkpoint applied revision lacks required Package projection; recapture required')
    packages = Table(name, MetaData(), autoload_with=connection)
    current = set(connection.scalars(select(packages.c.id)))
    if any(row['id'] not in current or set(row) != set(packages.c.keys()) for row in rows):
        raise ValueError('checkpoint Package projection unavailable or incompatible')
    connection.execute(update(packages).values(active=False))
    for row in rows:
        connection.execute(update(packages).where(packages.c.id == row['id']).values(**row))
