"""Configuration-preloaded module sentinel.

Listed under ``preloaded modules`` in the fixture configuration; importing it is
the observable "preloaded module" event.
"""

import os
from docassemble.base.hooks import get_default_timezone

# Exercise the real hook registered by the preceding configured module.
get_default_timezone()

SENTINEL_DIR = os.environ.get("DA_SENTINEL_DIR", "/tmp/da-sentinels")

try:
    os.makedirs(SENTINEL_DIR, exist_ok=True)
    with open(os.path.join(SENTINEL_DIR, "sentinel-preloaded-module"), "w", encoding="utf-8") as handle:
        handle.write("sentinel_preloaded_fixture imported\n")
except OSError:
    pass
