#! /bin/bash

set -euo pipefail

usage() {
    cat <<'USAGE'
Usage: install-live-package-container.sh PACKAGE_PATH CONTAINER_NAME

Install a local docassemble package into a running docassemble container as
a non-root account, replacing the package in the single runtime. Managed images
use the independent executor's exclusive development override; legacy images
retain the www-data installation and WSGI touch.

Examples:
  ./install-live-package-container.sh ~/docassemble/docassemble_base docassemble-dev
  ./install-live-package-container.sh ~/docassemble/docassemble_webapp docassemble-dev

Environment overrides:
  CONTAINER=container
  DA_ACTIVATE=/usr/share/docassemble/local3.14/bin/activate
  DA_WSGI=/usr/share/docassemble/webapp/docassemble.wsgi
  SETUPTOOLS_VERSION=83.0.0
  KEEP_STAGING=0
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

if [[ $# -ne 2 ]]; then
    usage >&2
    exit 2
fi

CONTAINER="${CONTAINER:-container}"
DA_ACTIVATE="${DA_ACTIVATE:-/usr/share/docassemble/local3.14/bin/activate}"
DA_WSGI="${DA_WSGI:-/usr/share/docassemble/webapp/docassemble.wsgi}"
SETUPTOOLS_VERSION="${SETUPTOOLS_VERSION:-83.0.0}"
KEEP_STAGING="${KEEP_STAGING:-0}"

package_path="$1"
container_name="$2"

if ! command -v "$CONTAINER" >/dev/null 2>&1; then
    echo "Could not find Apple Container command: $CONTAINER" >&2
    exit 1
fi

if [[ ! -e "$package_path" ]]; then
    echo "Package path does not exist: $package_path" >&2
    exit 1
fi

if ! container_info=$("$CONTAINER" inspect "$container_name" 2>/dev/null); then
    echo "Container does not exist: $container_name" >&2
    exit 1
fi

# Apple Container does not support Docker's inspect --format option.
running=$(printf '%s\n' "$container_info" | awk '/"state"[[:space:]]*:/ { sub(/^.*"state"[[:space:]]*:[[:space:]]*"/, ""); sub(/".*$/, ""); print; exit }')
if [[ "$running" != "running" ]]; then
    echo "Container is not running: $container_name" >&2
    exit 1
fi

package_path=$(realpath "$package_path")
staging_dir="/tmp/docassemble-live-install-$(date +%Y%m%d%H%M%S)-$$"
local_staging_dir=""

cleanup() {
    if [[ "$KEEP_STAGING" != "1" ]]; then
        "$CONTAINER" exec \
            -e STAGING_DIR="$staging_dir" \
            "$container_name" \
            bash -lc 'rm -rf "$STAGING_DIR"' >/dev/null 2>&1 || true
    else
        echo "Leaving staged package in container at $staging_dir"
    fi
    if [[ -n "$local_staging_dir" ]]; then
        rm -rf "$local_staging_dir"
    fi
}
trap cleanup EXIT

if [[ -d "$package_path" ]]; then
    if ! command -v rsync >/dev/null 2>&1; then
        echo "Could not find rsync; it is required when installing a package directory" >&2
        exit 1
    fi
    local_staging_dir=$(mktemp -d "${TMPDIR:-/tmp}/docassemble-live-package.XXXXXX")
    rsync -a --delete \
        --exclude='/build/' \
        --exclude='/dist/' \
        --exclude='/*.egg-info/' \
        --exclude='__pycache__/' \
        --exclude='*.py[cod]' \
        "$package_path/" "$local_staging_dir/package/"
    install_source="$local_staging_dir/package"
    install_target="$staging_dir/package"
else
    install_source="$package_path"
    install_target="$staging_dir/$(basename "$package_path")"
fi

echo "Preparing staging directory in container $container_name"
"$CONTAINER" exec \
    -e STAGING_DIR="$staging_dir" \
    "$container_name" \
    bash -lc 'rm -rf "$STAGING_DIR" && mkdir -p "$STAGING_DIR/package" "$STAGING_DIR/wheelhouse"'

echo "Copying $package_path into $container_name:$install_target"
if [[ -d "$package_path" ]]; then
    "$CONTAINER" cp "$install_source/." "$container_name:$install_target/"
else
    "$CONTAINER" cp "$install_source" "$container_name:$install_target"
fi

"$CONTAINER" exec \
    -e STAGING_DIR="$staging_dir" \
    "$container_name" \
    bash -lc 'chown -R www-data:www-data "$STAGING_DIR"'

# Route managed installations through independent exclusive authority before
# runtime activation. Exit 76 means only an unadopted legacy image may proceed.
managed_result=0
"$CONTAINER" exec \
    -e INSTALL_TARGET="$install_target" \
    "$container_name" \
    bash -c '
        gate=/usr/share/docassemble/webapp/managed-consumer.sh
        if [ -f "$gate" ]; then
            source "$gate" development-install "$INSTALL_TARGET"
        else
            state="${DA_MANAGED_STATE_DIR:-/var/lib/docassemble/managed}"
            if [ "${DA_UV_MANAGED:-false}" = true ]; then exit 75; fi
            parent="${state%/}"
            while [ -n "$parent" ]; do
                if { [ -e "$parent" ] || [ -L "$parent" ]; } &&
                   { [ ! -d "$parent" ] || [ ! -x "$parent" ]; }; then exit 75; fi
                case "$parent" in
                    /|.) break ;;
                    */*) parent="${parent%/*}"; parent="${parent:-/}" ;;
                    *) parent=. ;;
                esac
            done
            for record in desired.json applied.json lifecycle.json initialized.json admission.json; do
                if [ -e "$state/$record" ] || [ -L "$state/$record" ]; then exit 75; fi
            done
        fi
        exit 76
    ' || managed_result=$?
if [[ "$managed_result" == 0 ]]; then
    echo "Installed $package_path as a supervised managed development override"
    exit 0
elif [[ "$managed_result" != 76 ]]; then
    echo "Managed development installation refused or failed ($managed_result)" >&2
    exit "$managed_result"
fi

echo "Installing package in $container_name as www-data"
"$CONTAINER" exec \
    --user www-data \
    -e DA_ACTIVATE="$DA_ACTIVATE" \
    -e DA_WSGI="$DA_WSGI" \
    -e HOME=/var/www \
    -e INSTALL_TARGET="$install_target" \
    -e SETUPTOOLS_VERSION="$SETUPTOOLS_VERSION" \
    -e STAGING_DIR="$staging_dir" \
    "$container_name" \
    bash -lc '
        set -eo pipefail
        source /etc/profile
        source "$DA_ACTIVATE"
        export PIP_CACHE_DIR="$STAGING_DIR/pip-cache"
        cd "$STAGING_DIR"
        python -m pip download --dest "$STAGING_DIR/wheelhouse" "setuptools==$SETUPTOOLS_VERSION"
        python -m pip install --no-deps --force-reinstall --upgrade --no-index --find-links="$STAGING_DIR/wheelhouse" "$INSTALL_TARGET"
        touch "$DA_WSGI"
    '

echo "Installed $package_path into $container_name and touched $DA_WSGI"
