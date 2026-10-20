#!/bin/bash
# Bounded 05a2 proof: disposable non-root container only, never live application.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EVIDENCE="$ROOT/uv-experiment/05a2/evidence/accepted"
IMAGE="${PROOF_IMAGE:-da-uv-full-maintenance:local}"
mkdir -p "$EVIDENCE"
docker image inspect "$IMAGE" --format '{{.Id}}' > "$EVIDENCE/image.txt"
docker rm da-05a2-proof >/dev/null 2>&1 || true
docker run --name da-05a2-proof --user 1000:1000 --entrypoint sh \
  -v "$ROOT:/repo:ro" \
  -e PYTHONPATH=/repo/uv-experiment/executor/src:/repo/docassemble_base \
  -e UV_PROJECT_ENVIRONMENT=/tmp/executor -e UV_CACHE_DIR=/tmp/tool-cache \
  "$IMAGE" -c 'mkdir -p /tmp/proof; /usr/local/libexec/uv-pinned run --project /repo/uv-experiment/executor --frozen python /repo/uv-experiment/preparation/builder_proof.py' \
  > "$EVIDENCE/container.log" 2>&1
cat "$EVIDENCE/container.log"
docker cp da-05a2-proof:/tmp/proof/. "$EVIDENCE/"
