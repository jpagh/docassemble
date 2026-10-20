"""Bundle identity and verification helpers.

A retained bundle is an immutable uv project directory (``pyproject.toml`` plus
``uv.lock`` plus any local sources).  The image build records a manifest with
the SHA-256 of each bundle file so runtime restoration can prove that the
material it uses is the material that was pinned.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_project(bundle_dir: Path) -> str:
    """Hash definitions and local fixture sources, excluding generated caches.

    Local sources stand in for the unpublished feature build in this proof.
    Their contents, not only the relative paths in the lock, pin that build.
    """
    digest = hashlib.sha256()
    files = [bundle_dir / name for name in ("pyproject.toml", "uv.lock", "inventory.json", "source-artifacts.json")]
    files.extend(sorted((bundle_dir / 'sources').rglob('*')))
    files.extend(sorted((bundle_dir / 'wheels').rglob('*')))
    for path in files:
        if not path.is_file() or any(part in ('__pycache__', '.venv', 'build', '.pytest_cache') or part.endswith('.egg-info') for part in path.parts):
            continue
        name = path.relative_to(bundle_dir).as_posix()
        digest.update(name.encode('utf-8') + b'\0')
        digest.update(sha256_file(path).encode('ascii'))
    return digest.hexdigest()


@dataclass(frozen=True)
class BundleIdentity:
    name: str
    directory: str
    project_hash: str
    revision: str | None = None
    core_baseline: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "directory": self.directory,
            "project_hash": self.project_hash,
            "revision": self.revision,
            "core_baseline": self.core_baseline,
        }


def load_manifest(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError("bundle manifest not found: " + str(path))
    return json.loads(path.read_text(encoding="utf-8"))


def verify_bundle(bundle_dir: Path, manifest: dict, name: str) -> tuple[bool, str]:
    """Verify a bundle directory against the manifest entry ``name``."""
    entries = manifest.get("bundles", {})
    expected = entries.get(name)
    if expected is None:
        return False, "bundle " + name + " is not recorded in the image manifest"
    if not bundle_dir.is_dir():
        return False, "bundle directory is missing: " + str(bundle_dir)
    missing = [f for f in ("pyproject.toml", "uv.lock") if not (bundle_dir / f).is_file()]
    if missing:
        return False, "bundle " + name + " is missing " + ", ".join(missing)
    actual = hash_project(bundle_dir)
    if actual != expected:
        return False, "bundle " + name + " hash mismatch: expected " + str(expected) + ", found " + actual
    return True, "bundle " + name + " matches the retained manifest"
