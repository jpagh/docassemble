#!/bin/bash
# 04b2a only: both real Celery workers, reversible drain and Redis broker proof.
set -euo pipefail
EXPERIMENT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$EXPERIMENT_DIR/.." && pwd)"
IMAGE="${PROOF_IMAGE:-da-uv-workers:local}"
if [ "${SKIP_BUILD:-0}" != 1 ]; then
    docker build -f "$EXPERIMENT_DIR/fixture/Dockerfile" -t "$IMAGE" \
        --build-arg "DA_REPO_COMMIT=$(git -C "$REPO_ROOT" rev-parse HEAD)" "$REPO_ROOT"
fi
exec python3 "$EXPERIMENT_DIR/scenarios/workers.py" --image "$IMAGE" "$@"
