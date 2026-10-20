# 05a2b: Preparation-only polling and claim lifecycle

**Bounded partial slice ONLY: not 05a or parent-05 acceptance.** Uses the task's
explicit fallback: credential injection, physical delayed deletion and trusted OS
orphan reconciliation remain fenced as **05a2b2**. No installation polling,
public adapter, publication or live application. Managed routes still reject.
Branch `uv-implementation`; base `c0013fd8cdb80d070be71aa260c18ceefd39d9e6`.

## Implementation

Independent `python -m docassemble_executor.preparation_poller` consumes durable
queued or DB-time expired preparation work without Flask/Celery/Redis. Atomic
`claim_next` uses the existing environment -> candidate serialization, increments
the fence and retains separate worker rows across reclaim. Build executes outside
transactions; a renewal thread extends the lease every lease/3. Denied/uncertain
renewal fences completion; DB claim/publication checks independently reject late
results. Build failures retain a redacted terminal state, never raw exceptions.
Default loop polls every second with a 30-second lease; `--once` permits bounded
operator dispatch/proofs. Restricted DB configuration is supplied in environment.
The worker authorizer denies actor operations; this is internal tooling only.

Public unauthenticated PyPI / Python 3.12 / uv 0.12.21 remains the **only** supported
source policy. There is no scoped credential injection. Private indexes/bindings,
direct sources and unqualified policies fail closed. Builder environment isolation
remains unchanged. No malicious-build sandbox or broader build-policy acceptance.

Worker records deliberately stay **unexited**: builder return, polling-process exit
and lease timeout are not proof all build descendants exited. No physical cleanup
consumer or orphan reconciler is enabled. Shared/committed material and reusable
cache are not deleted. This conservative partial worker is not cleanup-ready
production execution; trusted descendant-safe exit evidence is still required.

Also fixes accepted 05a2 review P2: a destination-exists/nonempty atomic rename
collision verifies the winning retained bundle against the expected digest,
reuses it and discards local staging. Other IO failures propagate. A deterministic
barrier-controlled two-publisher regression exercises the real rename collision;
additional tests reject corrupted destinations and preserve permission errors.

## Verification and review

Qualified local evidence: `05a2b/evidence/accepted/`; independent review required.
Contains full logs, pinned-image identity, final retained source/wheel/project/lock/
inventory bytes and attestations for builder and independent polling proofs.
`source-hashes.json` identifies proof/implementation sources. Historical 05a2
proof outputs overwritten by the required rerun were restored after copying this
slice's new qualified output; older retained bytes are unchanged.

Exact commands from repository root; final executions all exit 0:

```sh
bash uv-experiment/run-preparation-builder-proof.sh
# PASS pinned uv 0.12.21: solve, source build, exact core, retained sources/wheels/inventory,
# cold-cache offline frozen --no-build disposable sync
# PASS conflict: redacted failure, no input/runtime mutation
# PASS unsupported credential-bearing direct source rejected, diagnostic redacted;
# credential injection/cleanup NOT qualified
# PASS retained-byte corruption rejected; reusable cache not relied upon

bash uv-experiment/run-preparation-poller-proof.sh
# PASS independent PostgreSQL preparation dispatch: pinned uv solve/build/retain ->
# fenced ready; no intent, runtime or installation mutation
# PASS independent poller idle after readiness; worker exit not inferred from builder return

uv run --project uv-experiment/executor --frozen pytest -q -p no:cacheprovider uv-experiment/executor/tests/test_registry_builder.py
# 5 passed in 0.08s

PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
# 58 passed in 7.73s

act workflow_dispatch -W .act/checks.yml
# 277 passed, 1 skipped in 29.84s
# Success: no issues found in 50 source files; Job succeeded

git diff --check
# no output
```

The existing disposable `da-05a2-postgres` container was reused (PostgreSQL 16.6,
port 55432, fixture password `preparation-test`). Poller proof joins that container's
network and creates/drops its own `poller_proof` schema; never use a production DB.
Actual migration/fresh initialization paths both exercise concurrent discovery,
renewal past the initial lease, lost renewal/reclaim, cancellation, redacted failure
and independent process consumption. Existing real-retained-attestation completion
now flows through the poller. No installation/intent/runtime mutation occurs.

Counts intentionally increase by 2 native retention tests, 10 PostgreSQL cases
(5 scenarios x 2 initialization paths), and 1 mypy source module. No expected-count
assertions or exclusions changed. Sole native skip is documented bare-act
Supervisor case. The pinned container additionally verifies end-to-end real builder
polling; lease edge cases use deterministic/test builders with real PostgreSQL.

## Resolution and limits

This delivers functional independent polling/dispatch plus renewal/reclaim and the
P2 fix, not a plan-only handoff. Credential injection, ALL-worker-exit physical
cleanup and trusted OS orphan reconciliation are **not implemented or qualified**.
No cleanup/credential acceptance is inferred from the existing eligibility tests
or public build proof. Broader target/private-index/binding/build-policy work,
05a3 authenticated adapters, 05b publication/security writers and 05c installation
polling/live apply remain fenced. Parent Status/checklist unchanged; 06 blocked.

## 2026-10-14 P1 review fix: drain renewal before terminal writes

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

Next: 05a2b2
