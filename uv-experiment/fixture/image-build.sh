#!/bin/bash
# Build-time materialization of retained bundles and the executor tooling venv.
# This script runs only during image build; it never runs in recovery.
set -euo pipefail

BUNDLE_ROOT=/usr/share/docassemble/managed/bundles
RUNTIME_VENV=/usr/share/docassemble/local3.12
EXECUTOR_VENV=/usr/share/docassemble/executor-venv
SOURCE_ROOT=/opt/da-sources
FIXTURE=/opt/docassemble-fixture

export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-/usr/share/docassemble/uv-python}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/var/cache/docassemble/uv}"

mkdir -p "$UV_PYTHON_INSTALL_DIR" "$UV_CACHE_DIR" "$BUNDLE_ROOT"

echo "image-build: installing pinned CPython"
uv python install 3.12.14
uv --version
uv python find 3.12

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
UV_PROJECT_ENVIRONMENT="$EXECUTOR_VENV" uv sync --frozen --project /opt/docassemble-executor

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
UV_PROJECT_ENVIRONMENT="$RUNTIME_VENV" uv sync --frozen --project "$BUNDLE_ROOT/core-r1"
for name in core-r1 runtime-r1 runtime-r2; do
    "$EXECUTOR_VENV/bin/python" "$FIXTURE/materialize_wheels.py" "$BUNDLE_ROOT/$name" --retain-built
    (cd "$BUNDLE_ROOT/$name" && uv lock)
done

echo "image-build: recording actual inventories in the single runtime venv"
for name in core-r1 runtime-r2 runtime-r1; do
    UV_PROJECT_ENVIRONMENT="$RUNTIME_VENV" uv sync --frozen --no-build --project "$BUNDLE_ROOT/$name"
    "$EXECUTOR_VENV/bin/python" -c 'import json,sys; from pathlib import Path; from docassemble_executor.inventory import inspect_environment, inspect_material; p=Path(sys.argv[1]); print(json.dumps({"distributions": inspect_environment(p), "files": inspect_material(p)}, sort_keys=True))' "$RUNTIME_VENV" > "$BUNDLE_ROOT/$name/inventory.json"
done
cp "$BUNDLE_ROOT/runtime-r2/inventory.json" "$BUNDLE_ROOT/runtime-fault/inventory.json"

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
