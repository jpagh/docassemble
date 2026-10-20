#!/bin/bash
# Disposable proof database only; public-only preparation, never installation.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EVIDENCE="$ROOT/uv-experiment/05a2b/evidence/accepted/poller"
IMAGE="${PROOF_IMAGE:-da-uv-full-maintenance:local}"
mkdir -p "$EVIDENCE"
docker image inspect "$IMAGE" --format '{{.Id}}' > "$EVIDENCE/image.txt"
docker rm da-05a2b-poller-proof >/dev/null 2>&1 || true
docker run --name da-05a2b-poller-proof --user 1000:1000 --entrypoint sh \
  --network container:da-05a2-postgres -v "$ROOT:/repo:ro" \
  -e PYTHONPATH=/repo/uv-experiment/executor/src:/repo/docassemble_base \
  -e DA_PREPARATION_DATABASE_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:5432/postgres \
  -e UV_PROJECT_ENVIRONMENT=/tmp/executor -e UV_CACHE_DIR=/tmp/tool-cache \
  "$IMAGE" -c 'mkdir -p /tmp/proof; /usr/local/libexec/uv-pinned run --project /repo/uv-experiment/executor --frozen python /repo/uv-experiment/preparation/poller_proof.py' \
  > "$EVIDENCE/container.log" 2>&1
cat "$EVIDENCE/container.log"
docker cp da-05a2b-poller-proof:/tmp/proof/. "$EVIDENCE/"
