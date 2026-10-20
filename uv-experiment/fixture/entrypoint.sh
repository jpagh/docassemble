#!/bin/bash
# Disposable-container entry point for the independent recovery proof.
set -euo pipefail

export PATH=/usr/share/docassemble/executor-venv/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin
export DA_CONFIG_FILE="${DA_CONFIG_FILE:-/usr/share/docassemble/config/config.yml}"
export DA_RUNTIME_VENV="${DA_RUNTIME_VENV:-/usr/share/docassemble/local3.12}"
export DA_BUNDLE_DIR="${DA_BUNDLE_DIR:-/usr/share/docassemble/managed/bundles}"
export DA_MANAGED_STATE_DIR="${DA_MANAGED_STATE_DIR:-/var/lib/docassemble/managed}"
export DA_RUN_DIR="${DA_RUN_DIR:-/var/run/docassemble}"
export DA_RECOVERY_RUN_DIR="${DA_RECOVERY_RUN_DIR:-/var/run/docassemble/recovery}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/var/cache/docassemble/uv}"
export UV_PYTHON_DOWNLOADS=never

# PIDs cannot survive a container restart (or the snapshot fixture).
rm -f "$DA_RUN_DIR/web-normal.pid" "$DA_RUN_DIR/web-recovery.pid" "$DA_RUN_DIR/ready"
echo "entrypoint: starting independent lifecycle executor"
source /opt/docassemble-fixture/managed-startup.sh
# Only an unadopted flag-off installation can reach the original bootstrap.
exec /opt/docassemble-fixture/initialize.sh "$@"
