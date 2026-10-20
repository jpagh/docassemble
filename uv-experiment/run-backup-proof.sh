#!/bin/bash
# Ticket 02: real local backup/restore plus configured cloud CLI-boundary tests.
# Builds the ticket-01 fixture only if it is not available locally. Backup source
# is read-only mounted into fresh, network-isolated containers with no host ports.
set -euo pipefail

EXPERIMENT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$EXPERIMENT_DIR/.." && pwd)"
IMAGE="${BACKUP_TEST_IMAGE:-da-uv-proof:local}"
BASE_IMAGE="${DOCASSEMBLE_OS_IMAGE:-jhpyle/docassemble-os@sha256:9ada5376be7f4fbd1a276406c2bb7304dfa6f315d63cb0094640e2fbf0ecf9eb}"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    DOCKER_BUILDKIT=1 docker build -f "$EXPERIMENT_DIR/fixture/Dockerfile" -t "$IMAGE" \
        --build-arg "DOCASSEMBLE_OS_IMAGE=$BASE_IMAGE" \
        --build-arg "DA_REPO_COMMIT=$(git -C "$REPO_ROOT" rev-parse HEAD)" "$REPO_ROOT"
fi
IMAGE_ID=$(docker image inspect --format '{{.Id}}' "$IMAGE")
EVIDENCE="${BACKUP_EVIDENCE_DIR:-$EXPERIMENT_DIR/backup/evidence/run-$(date -u +%Y%m%d-%H%M%S)}"
mkdir -p "$EVIDENCE"
cd "$REPO_ROOT"
docker image inspect --format '{"id":{{json .Id}},"architecture":{{json .Architecture}},"os":{{json .Os}},"digests":{{json .RepoDigests}}}' "$IMAGE_ID" > "$EVIDENCE/image.json"
git rev-parse HEAD > "$EVIDENCE/parent-commit.txt"
docker run --rm --network none --entrypoint /bin/bash "$IMAGE_ID" -c \
    'uname -sm; bash --version | head -1; pg_dump --version; redis-cli --version; rsync --version | head -1; /usr/local/bin/uv --version' > "$EVIDENCE/tool-inventory.txt"
shasum -a 256 Docker/backup.sh Docker/cron/docassemble-cron-daily.sh tests/test_backup.py tests/test_backup_container.py uv-experiment/run-backup-proof.sh > "$EVIDENCE/source-sha256.txt"
BACKUP_EVIDENCE_DIR="$EVIDENCE" BACKUP_TEST_IMAGE="$IMAGE_ID" uv run --project uv-experiment/executor --frozen pytest -v \
    tests/test_backup.py tests/test_backup_container.py 2>&1 | tee "$EVIDENCE/tests.txt"
shasum -a 256 -c "$EVIDENCE/source-sha256.txt" > "$EVIDENCE/source-verification.txt"
echo "backup-proof: passed; evidence in $EVIDENCE"
