#!/bin/bash
# 04a: maintenance foundation for the current experimental web slice.
set -euo pipefail
EXPERIMENT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$EXPERIMENT_DIR/.." && pwd)"
IMAGE="${PROOF_IMAGE:-da-uv-maintenance:local}"
if [ "${SKIP_BUILD:-0}" != "1" ]; then
    docker build -f "$EXPERIMENT_DIR/fixture/Dockerfile" -t "$IMAGE" \
        --build-arg "DA_REPO_COMMIT=$(git -C "$REPO_ROOT" rev-parse HEAD)" "$REPO_ROOT"
fi
exec python3 "$EXPERIMENT_DIR/scenarios/maintenance.py" --image "$IMAGE" "$@"
