#!/bin/bash
# Source before activating any application Python. Unadopted flag-off is a no-op.
# Only the experimental entrypoint currently supplies a qualified executor.
DA_MANAGED_STATE_DIR="${DA_MANAGED_STATE_DIR:-/var/lib/docassemble/managed}"
da_managed_evidence=false
for da_record in desired.json applied.json lifecycle.json initialized.json admission.json cold-restore.json; do
    if [ -e "$DA_MANAGED_STATE_DIR/$da_record" ] || [ -L "$DA_MANAGED_STATE_DIR/$da_record" ]; then
        da_managed_evidence=true
    fi
done
if [ "${DA_UV_MANAGED:-false}" != "true" ]; then
    if [ "$da_managed_evidence" = "true" ]; then
        echo "managed startup: durable evidence exists but experiment is disabled; legacy boot refused" >&2
        exit 1
    fi
elif [ -x "${DA_EXECUTOR_ENTRYPOINT:-/usr/share/docassemble/executor-venv/bin/da-executor}" ]; then
    # No artifact-path default: the image/operator provisions an explicit private
    # API-writer store. Root execution verifies that exact owner before boot.
    da_executor="${DA_EXECUTOR_ENTRYPOINT:-/usr/share/docassemble/executor-venv/bin/da-executor}"
    if ! "$(dirname "$da_executor")/python" - <<'PY'
import os
from pathlib import Path
import pwd
from docassemble.base.managed_artifacts import artifact_root
root = os.environ.get('DA_MANAGED_ARTIFACT_ROOT')
uid = os.environ.get('DA_MANAGED_ARTIFACT_UID')
if not root or uid != str(pwd.getpwnam('www-data').pw_uid):
    raise SystemExit('managed startup: explicit API-writer artifact root/UID required')
artifact_root(Path(root), reader=True)
PY
    then
        echo "managed startup: artifact store validation failed; legacy boot refused" >&2
        exit 1
    fi
    exec "$da_executor" boot "$@"
else
    echo "managed startup: independent executor is unavailable; use the experimental image; legacy boot refused" >&2
    exit 1
fi
unset da_managed_evidence da_record
