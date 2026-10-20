"""Sentinel imported only while locating the configured preloaded interview.

The nested questions module makes find_spec import this parent package when
startup locates its YAML. No interview execution or module preload is needed.
"""

from pathlib import Path

sentinels = Path("/tmp/da-sentinels")
sentinels.mkdir(exist_ok=True)
(sentinels / "sentinel-interview").write_text("preloaded interview resource lookup executed\n")
