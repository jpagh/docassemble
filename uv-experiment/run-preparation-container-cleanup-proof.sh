#!/bin/bash
# Host trusted Docker daemon plus disposable local PostgreSQL; never live apply.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EVIDENCE="$ROOT/uv-experiment/05a2b2/evidence/accepted"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$EVIDENCE"
cd "$ROOT"
docker image inspect da-uv-full-maintenance:local --format '{{.Id}}' > "$EVIDENCE/image.txt"
PROOF_ROOT="$WORK" PYTHONPATH=docassemble_base \
DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres \
uv run --project uv-experiment/executor --frozen python uv-experiment/preparation/container_cleanup_proof.py \
  > "$EVIDENCE/container-cleanup.log" 2>&1
cat "$EVIDENCE/container-cleanup.log"
rm -rf "$EVIDENCE/retained"
cp -R "$WORK/retained" "$EVIDENCE/"
cp "$WORK/attestation.json" "$EVIDENCE/"
