#!/bin/bash
# Repeatable command for the independent browser-recovery proof.
#
#   ./run-proof.sh            # build the pinned image, then run all scenarios
#   SKIP_BUILD=1 ./run-proof.sh
#
# Raw evidence is written under evidence/run-<UTC timestamp>/.
set -euo pipefail

EXPERIMENT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$EXPERIMENT_DIR/.." && pwd)"
IMAGE="${PROOF_IMAGE:-da-uv-proof:local}"
BASE_IMAGE="${DOCASSEMBLE_OS_IMAGE:-jhpyle/docassemble-os@sha256:9ada5376be7f4fbd1a276406c2bb7304dfa6f315d63cb0094640e2fbf0ecf9eb}"

if [ "${SKIP_BUILD:-0}" != "1" ]; then
    echo "run-proof: building $IMAGE from $BASE_IMAGE"
    DOCKER_BUILDKIT=1 docker build \
        -f "$EXPERIMENT_DIR/fixture/Dockerfile" \
        -t "$IMAGE" \
        --build-arg "DOCASSEMBLE_OS_IMAGE=$BASE_IMAGE" \
        --build-arg "DA_REPO_COMMIT=$(git -C "$REPO_ROOT" rev-parse HEAD)" \
        "$REPO_ROOT"
fi

exec python3 "$EXPERIMENT_DIR/scenarios/proof.py" --image "$IMAGE" "$@"
