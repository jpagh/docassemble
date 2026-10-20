# 2026-10-14: 05a2b2 P1 fix

Commit: `55ffdc972e73171268d6d6e10c43ab34558e04a1` on `uv-implementation`, atop `58f67f0259bc2f7a0ec352952ce8b2eb3451c056`.


Review found that successful `build_claim` return released the receipt lock before
renewal drain and readiness publication. Concurrent reconciliation could remove
the stopped container and report exit first, making `_owns_claim` reject the
otherwise valid result. `claim_lock` now belongs to the poller dispatch scope:
creation, build/result retention, renewal stop/join and ready/failed DB writes
all occur before its release. The final reconciliation runs after release.
Automatic OS-lock release on process death preserves orphan recovery. Unsupported
policy/lock setup failures still take the redacted failed/fenced path.

The event-controlled PostgreSQL regression attempts concurrent reconciliation
both after build return during renewal drain and after drain before readiness.
Both passes exclude daemon/exit operations; readiness succeeds, then removal and
exit reporting follow it. Exercised on migration and fresh initialization (+2
intentional PostgreSQL cases: 66 -> 68). The existing native held-creator-lock
regression still passes. Native/mypy expected counts and exclusions unchanged.

Final exact verification commands from repository root (all exit 0):

```sh
PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen pytest -q -p no:cacheprovider uv-experiment/preparation/tests/test_preparation_postgres.py -k receipt_lock
# 2 passed, 44 deselected in 0.35s

PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
# 68 passed in 12.82s

uv run --project uv-experiment/executor --frozen pytest -q -p no:cacheprovider uv-experiment/executor/tests/test_registry_builder.py uv-experiment/executor/tests/test_preparation_containers.py
# 17 passed in 0.07s (includes held-creator-lock exclusion)

bash uv-experiment/run-preparation-container-cleanup-proof.sh
# PASS real pinned-uv container dispatch: retained ready bytes; namespace destroyed before worker exit
# PASS live orphan with build descendant blocks deletion after lease reclaim and newer worker exit; status redacted
# PASS trusted daemon orphan reconciliation destroys private PID namespace; ALL-worker exit gates physical deletion; retry idempotent

bash uv-experiment/run-preparation-poller-proof.sh
# PASS independent PostgreSQL preparation dispatch: pinned uv solve/build/retain -> fenced ready; no intent, runtime or installation mutation
# PASS independent poller idle after readiness; worker exit not inferred from builder return

bash uv-experiment/run-preparation-builder-proof.sh
# PASS pinned uv 0.12.21: solve, source build, exact core, retained sources/wheels/inventory, cold-cache offline frozen --no-build disposable sync
# PASS conflict: redacted failure, no input/runtime mutation
# PASS unsupported credential-bearing direct source rejected, diagnostic redacted; credential injection/cleanup NOT qualified
# PASS retained-byte corruption rejected; reusable cache not relied upon

act workflow_dispatch -W .act/checks.yml
# 289 passed, 1 skipped in 30.37s
# Success: no issues found in 51 source files; Job succeeded

git diff --check
# no output
```

Full rerun logs and copied proof bytes are intentionally untracked under
`ticket-05a2b2/fix-evidence/`; historical accepted evidence was restored after
copying. Independent re-review required. Parent 05 Status/checklist unchanged;
05a2b3/05a3/05b/05c remain fenced, no parent acceptance, 06 remains blocked.

Changed files:
- `.scratch/uv-environment-implementation/issues/05-registry-install.md`
- `uv-experiment/TICKET-05a2b2.md`
- `uv-experiment/executor/src/docassemble_executor/preparation_containers.py`
- `uv-experiment/executor/src/docassemble_executor/preparation_poller.py`
- `uv-experiment/preparation/tests/test_preparation_postgres.py`

Combined diff: `git diff de474ccd3f2de0c0bf351df11a1327ad3229b261..HEAD > uv-experiment/ticket-05a2b2/diff.patch`.
Summary, diff and fix evidence intentionally UNTRACKED. No staged files.
