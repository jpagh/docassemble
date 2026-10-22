#!/bin/bash
# Build-time materialization of retained bundles and the executor tooling venv.
# This script runs only during image build; it never runs in recovery.
set -euo pipefail

BUNDLE_ROOT=/usr/share/docassemble/managed/bundles
EXECUTOR_VENV=/usr/share/docassemble/executor-venv
SOURCE_ROOT=/opt/da-sources
FIXTURE=/opt/docassemble-fixture

export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-/usr/share/docassemble/uv-python}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/var/cache/docassemble/uv}"

mkdir -p "$UV_PYTHON_INSTALL_DIR" "$UV_CACHE_DIR" "$BUNDLE_ROOT"

echo "image-build: installing pinned CPython"
MANAGED_PYTHON_VERSION=3.14.4
uv python install "$MANAGED_PYTHON_VERSION"
uv --version
UV_PYTHON=$(uv python find --managed-python "$MANAGED_PYTHON_VERSION")
export UV_PYTHON
export UV_PYTHON_DOWNLOADS=never
"$UV_PYTHON" - "$UV_PYTHON_INSTALL_DIR" "$MANAGED_PYTHON_VERSION" <<'PYTHON'
from pathlib import Path
import sys
assert Path(sys.executable).resolve().is_relative_to(Path(sys.argv[1]).resolve())
assert '.'.join(map(str, sys.version_info[:3])) == sys.argv[2]
PYTHON

# Bootstrap uses only stdlib modules from the protected image build inputs.
check_python_target() {
    "$UV_PYTHON" - "$1" <<'PYTHON'
from pathlib import Path
import sys
sys.path[:0] = ['/opt/docassemble-executor/src', '/docassemble_base']
from docassemble_executor.bundles import verify_python_target
bundle = Path(sys.argv[1])
assert verify_python_target(bundle / 'pyproject.toml', bundle / 'uv.lock'), 'retained project and lock Python target mismatch'
PYTHON
}

check_python_target /opt/docassemble-executor

echo "image-build: materializing bundle sources"
for name in core-r1 runtime-r1 runtime-r2; do
    mkdir -p "$BUNDLE_ROOT/$name/sources"
    for project in docassemble_base docassemble_demo docassemble_webapp; do
        cp -a "$SOURCE_ROOT/$project" "$BUNDLE_ROOT/$name/sources/"
    done
done
cp "$FIXTURE/bundles/core/pyproject.toml" "$BUNDLE_ROOT/core-r1/pyproject.toml"
cp "$FIXTURE/bundles/runtime/pyproject.toml" "$BUNDLE_ROOT/runtime-r1/pyproject.toml"
cp "$FIXTURE/bundles/runtime-r2/pyproject.toml" "$BUNDLE_ROOT/runtime-r2/pyproject.toml"
for name in runtime-r1 runtime-r2; do
    cp "$FIXTURE/fixture_advanced_revision.py" "$BUNDLE_ROOT/$name/sources/docassemble_webapp/docassemble/webapp/alembic/versions/"
    cp -a "$FIXTURE/sentinel-user" "$BUNDLE_ROOT/$name/sources/"
    cp -a "$FIXTURE/sentinel-preloaded" "$BUNDLE_ROOT/$name/sources/"
done
cp -a "$FIXTURE/slowfixture" "$BUNDLE_ROOT/runtime-r2/sources/"

cp -a "$BUNDLE_ROOT/runtime-r2" "$BUNDLE_ROOT/runtime-fault"

echo "image-build: syncing the independent executor venv"
check_python_target /opt/docassemble-executor
UV_PROJECT_ENVIRONMENT="$EXECUTOR_VENV" uv sync --frozen --project /opt/docassemble-executor
DA_RUNTIME_VENV=$("$EXECUTOR_VENV/bin/python" -c 'from docassemble_executor.paths import ExecutorPaths; print(ExecutorPaths.from_env().runtime_venv)')
export DA_RUNTIME_VENV
RUNTIME_VENV="$DA_RUNTIME_VENV"
"$EXECUTOR_VENV/bin/python" - "$FIXTURE" "$EXECUTOR_VENV" "$UV_PYTHON" <<'PYTHON'
from pathlib import Path
import sys
import os
import tomllib
from docassemble.base.managed_python import python_requires
from docassemble_executor.paths import ExecutorPaths
paths = ExecutorPaths.from_env()
assert Path(sys.executable).resolve() == Path(sys.argv[3]).resolve()
runtime = paths.runtime_venv.resolve()
protected = [paths.da_root / 'local3.14', Path(sys.argv[2]), Path(sys.argv[3]).resolve().parent.parent,
             Path(os.environ['UV_PYTHON_INSTALL_DIR']), paths.bundles_dir, paths.cache_dir]
for root in protected:
    root = root.resolve()
    assert not runtime.is_relative_to(root) and not root.is_relative_to(runtime), 'managed runtime overlaps protected material'
for template in [Path('/opt/docassemble-executor/pyproject.toml'), *Path(sys.argv[1]).glob('bundles/*/pyproject.toml')]:
    assert tomllib.loads(template.read_text())['project']['requires-python'] == python_requires()
PYTHON

# Explicit test-only DB intent is seeded after create_tables, before web startup.
# Production sources are untouched; only these disposable staged wheels contain it.
"$EXECUTOR_VENV/bin/python" "$FIXTURE/seed_lifecycle_sources.py" "$BUNDLE_ROOT"

echo "image-build: retaining fixture wheels outside disposable cache"
for name in core-r1 runtime-r1 runtime-r2; do
    "$EXECUTOR_VENV/bin/python" "$FIXTURE/materialize_wheels.py" "$BUNDLE_ROOT/$name"
done

echo "image-build: locking bundles"
for name in core-r1 runtime-r1 runtime-r2 runtime-fault; do
    (cd "$BUNDLE_ROOT/$name" && uv lock)
done

echo "image-build: preparing registry sdist wheels once, at image construction"
check_python_target "$BUNDLE_ROOT/core-r1"
REGISTRY_BUILDS=$(mktemp -d)
"$EXECUTOR_VENV/bin/python" "$FIXTURE/materialize_wheels.py" "$BUNDLE_ROOT/core-r1" --build-registry-sdists "$REGISTRY_BUILDS/wheels"
for name in core-r1 runtime-r1 runtime-r2; do
    "$EXECUTOR_VENV/bin/python" "$FIXTURE/materialize_wheels.py" "$BUNDLE_ROOT/$name" --retain-built "$REGISTRY_BUILDS/wheels"
    (cd "$BUNDLE_ROOT/$name" && uv lock)
done
rm -rf "$REGISTRY_BUILDS"
check_python_target "$BUNDLE_ROOT/core-r1"
UV_PROJECT_ENVIRONMENT="$RUNTIME_VENV" uv sync --frozen --project "$BUNDLE_ROOT/core-r1"

echo "image-build: recording actual inventories in the single runtime venv"
for name in core-r1 runtime-r2 runtime-r1; do
    check_python_target "$BUNDLE_ROOT/$name"
    UV_PROJECT_ENVIRONMENT="$RUNTIME_VENV" uv sync --frozen --no-build --project "$BUNDLE_ROOT/$name"
    "$EXECUTOR_VENV/bin/python" -c 'import json,sys; from pathlib import Path; from docassemble_executor.inventory import inspect_environment, inspect_material; p=Path(sys.argv[1]); print(json.dumps({"distributions": inspect_environment(p), "files": inspect_material(p)}, sort_keys=True))' "$RUNTIME_VENV" > "$BUNDLE_ROOT/$name/inventory.json"
done
cp "$BUNDLE_ROOT/runtime-r2/inventory.json" "$BUNDLE_ROOT/runtime-fault/inventory.json"

"$EXECUTOR_VENV/bin/python" - "$RUNTIME_VENV/bin/python" <<'PYTHON'
from pathlib import Path
import sys
assert Path(sys.argv[1]).resolve() == Path(sys.executable).resolve()
PYTHON

echo "image-build: rendering fixture configuration"
"$RUNTIME_VENV/bin/python" "$FIXTURE/config/render_config.py" \
    "$FIXTURE/config/config.yml.dist" \
    "$FIXTURE/config/config-extras.yml" \
    /usr/share/docassemble/config/config.yml

CORE_VERSIONS=$("$RUNTIME_VENV/bin/python" -c "import importlib.metadata as m, json; print(json.dumps({d: m.version(d) for d in ['docassemble.base','docassemble.demo','docassemble.webapp']}))")
PY_VERSION=$("$RUNTIME_VENV/bin/python" -c "import sys; print('.'.join(str(p) for p in sys.version_info[:3]))")
UV_VERSION=$(uv --version | awk '{print $2}')

echo "image-build: writing retained-bundle manifest"
"$EXECUTOR_VENV/bin/python" "$FIXTURE/generate_manifest.py" \
    --output "$BUNDLE_ROOT/manifest.json" \
    --base-image "${DOCASSEMBLE_OS_IMAGE:-unknown}" \
    --repo-commit "${DA_REPO_COMMIT:-unknown}" \
    --uv-version "$UV_VERSION" \
    --python-version "$PY_VERSION" \
    --core-versions "$CORE_VERSIONS" \
    --bundles-dir "$BUNDLE_ROOT"

echo "image-build: finalizing permissions"
mkdir -p /var/lib/docassemble/managed /var/run/docassemble
printf '%s\n' '{"revision":"r1","core_baseline":"r1","packages":["docassemble-sentinel-fixture","sentinel-preloaded-fixture"]}' > /var/lib/docassemble/managed/desired.json
chmod 755 /var/lib/docassemble/managed
for directory in /usr/share/docassemble/config /usr/share/docassemble/log /usr/share/docassemble/files /usr/share/docassemble/backup; do
    if [ -d "$directory" ]; then
        chown -R www-data:www-data "$directory"
    fi
done
useradd --system --home-dir /var/lib/docassemble-build --create-home --shell /usr/sbin/nologin da-environment
chown -R da-environment:da-environment "$RUNTIME_VENV" "$UV_CACHE_DIR"
chown da-environment:da-environment /var/log/docassemble-uv-invocations.log
chown -R root:root "$BUNDLE_ROOT" "$EXECUTOR_VENV" "$UV_PYTHON_INSTALL_DIR" /opt/docassemble-executor /opt/docassemble-fixture /usr/local/bin/uv /usr/local/libexec/uv-pinned
chmod -R go-w "$BUNDLE_ROOT" "$EXECUTOR_VENV" "$UV_PYTHON_INSTALL_DIR" /opt/docassemble-executor /opt/docassemble-fixture
chmod -R a+rX "$RUNTIME_VENV" "$BUNDLE_ROOT" "$EXECUTOR_VENV" "$UV_PYTHON_INSTALL_DIR" /opt/docassemble-executor /opt/docassemble-fixture
rm -f /usr/share/docassemble/config/config.yml.tmp

echo "image-build: done"
cat "$BUNDLE_ROOT/manifest.json"
