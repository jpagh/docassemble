"""User-package favicon sentinel.

The fixture configuration sets ``favicon: sentinel_favicon_fixture:...``.  The
core's setup code imports the module before the colon when resolving the
favicon, so this import is the observable "user favicon setting" event.
"""

import os

SENTINEL_DIR = os.environ.get("DA_SENTINEL_DIR", "/tmp/da-sentinels")

__version__ = "1.0.0-fixture"

try:
    os.makedirs(SENTINEL_DIR, exist_ok=True)
    with open(os.path.join(SENTINEL_DIR, "sentinel-favicon"), "w", encoding="utf-8") as handle:
        handle.write("sentinel_favicon_fixture imported\n")
except OSError:
    pass
