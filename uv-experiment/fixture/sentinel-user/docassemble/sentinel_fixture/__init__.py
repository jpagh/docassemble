"""Fixture user package imported by the core's user-module scan.

Importing this module is the observable "user import" event.  It is installed
as ``docassemble.sentinel_fixture`` so the ordinary docassemble user-package
discovery path imports it during normal startup.
"""

import os

SENTINEL_DIR = os.environ.get("DA_SENTINEL_DIR", "/tmp/da-sentinels")


def _write_sentinel(name: str, message: str) -> None:
    try:
        os.makedirs(SENTINEL_DIR, exist_ok=True)
        with open(os.path.join(SENTINEL_DIR, name, ), "w", encoding="utf-8") as handle:
            handle.write(message + "\n")
    except OSError:
        # A sentinel must never break the application under test.
        pass


_write_sentinel("sentinel-user-import", "docassemble.sentinel_fixture imported")
