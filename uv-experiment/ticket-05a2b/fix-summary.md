# 2026-10-14 05a2b P1 fix

Commit: `de474ccd3f2de0c0bf351df11a1327ad3229b261`, directly atop
`c471bcaa9d7aa66340f8372bbbe149a532597ffc` on `uv-implementation`.
Combined diff base: `c0013fd8cdb80d070be71aa260c18ceefd39d9e6`.

Changed tracked files: preparation_poller.py, test_preparation_postgres.py,
TICKET-05a2b.md and parent issue 05-registry-install.md (Comments only).

## P1 review fix: drain renewal before terminal writes

Review found that an in-flight renewal could become uncertain after the poller
checked `lost` but before `publish_ready`, allowing readiness under a still-valid
DB lease. The same ordering gap affected redacted terminal failure. The poller
now stops and joins renewal immediately after building (including exceptional
return), then checks uncertainty before either terminal write. Audited both
`run_once` terminal paths; DB ownership/fence/expiry checks in `publish_ready`
and `fail` remain unchanged and authoritative if the lease expires during join.

Event-controlled real PostgreSQL regression holds renewal in flight, finishes
building, observes the join with the original lease still valid and no terminal
write, then releases renewal with an exception. Both successful and failed builds
stay fenced with no ready preview; both migration and fresh initialization run.
Intentional PostgreSQL count increase: 58 -> 62 (2 outcomes x 2 init paths).
Native counts, expected-count assertions and exclusions are unchanged.

Exact reruns from repository root, all exit 0 (full logs and copied proof bytes
in untracked `ticket-05a2b/fix-evidence/`; historical evidence restored unchanged):

```sh
PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
# 62 passed in 12.31s
bash uv-experiment/run-preparation-poller-proof.sh
# PASS independent PostgreSQL preparation dispatch: pinned uv solve/build/retain -> fenced ready; no intent, runtime or installation mutation
# PASS independent poller idle after readiness; worker exit not inferred from builder return
bash uv-experiment/run-preparation-builder-proof.sh
# PASS pinned uv 0.12.21: solve, source build, exact core, retained sources/wheels/inventory, cold-cache offline frozen --no-build disposable sync
# PASS conflict: redacted failure, no input/runtime mutation
# PASS unsupported credential-bearing direct source rejected, diagnostic redacted; credential injection/cleanup NOT qualified
# PASS retained-byte corruption rejected; reusable cache not relied upon
uv run --project uv-experiment/executor --frozen pytest -q -p no:cacheprovider uv-experiment/executor/tests/test_registry_builder.py
# 5 passed in 0.10s
act workflow_dispatch -W .act/checks.yml
# 277 passed, 1 skipped in 30.78s
# Success: no issues found in 50 source files; Job succeeded
git diff --check
# no output
```

No scope expansion: 05a2b2 credentials/physical cleanup/orphan reconciliation,
05a3/05b/05c, broader build policy and parent acceptance remain fenced. Parent
05 Status/checklist unchanged. Independent re-review required.


Additional validation: `git diff HEAD^ HEAD --check` passed without output.
`git diff --cached --name-only` produced no output. No staged files remain.
Initial explicit `git add` reported the ignored .scratch directory; adding the
already-tracked paths with `git add -u -- ...` succeeded. No files were force-added.
Independent review is still required. Untracked handoffs and fix-evidence remain
outside the commit; historical tracked proof evidence restored unchanged.
