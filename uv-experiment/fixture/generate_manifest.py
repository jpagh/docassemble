#!/usr/bin/env python3
"""Write the retained-bundle manifest recorded at image build time."""

from __future__ import annotations

import argparse
import json
import platform
import sysconfig
from datetime import datetime, timezone
from pathlib import Path

from docassemble_executor.bundles import hash_project, sha256_file  # type: ignore[import-not-found]
from docassemble_executor.inventory import inspect_environment, inspect_material  # type: ignore[import-not-found]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--base-image", required=True)
    parser.add_argument("--repo-commit", required=True)
    parser.add_argument("--uv-version", required=True)
    parser.add_argument("--python-version", required=True)
    parser.add_argument("--core-versions", required=True, help="JSON object of core distribution versions")
    parser.add_argument("--bundles-dir", required=True)
    parser.add_argument("--core-baseline", default="r1")
    parser.add_argument("--runtime-revision", default="r1")
    args = parser.parse_args()

    bundles_dir = Path(args.bundles_dir)
    names = ["core-" + args.core_baseline, "runtime-" + args.runtime_revision, "runtime-r2", "runtime-fault"]
    bundles: dict[str, str] = {}
    for name in names:
        directory = bundles_dir / name
        if directory.is_dir():
            bundles[name] = hash_project(directory)

    manifest = {
        "format": 2,
        "inventories": {name: json.loads((bundles_dir / name / 'inventory.json').read_text()) for name in names},
        "executor_inventory": {
            'distributions': inspect_environment(Path('/usr/share/docassemble/executor-venv')),
            'files': inspect_material(Path('/usr/share/docassemble/executor-venv')),
        },
        "uv_sha256": sha256_file(Path('/usr/local/bin/uv')),
        "uv_binary_sha256": sha256_file(Path('/usr/local/libexec/uv-pinned')),
        "identity": {
            "base_image": args.base_image,
            "repo_commit": args.repo_commit,
            "uv_version": args.uv_version,
            "python_version": args.python_version,
            "python_implementation": platform.python_implementation(),
            "platform": sysconfig.get_platform(),
            "machine": platform.machine(),
            "libc": list(platform.libc_ver()),
            "os_release": platform.freedesktop_os_release(),
            "core_baseline": args.core_baseline,
            "runtime_revision": args.runtime_revision,
            "core_versions": json.loads(args.core_versions),
        },
        "bundles": bundles,
        # This exact fixture-only migration adds an unrelated table. Unknown
        # production revisions remain incompatible; this is not a generic
        # claim that newer schemas are usable by older core releases.
        "compatible_schema_revisions": ["fixture_advanced_revision"],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    Path(args.output).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
