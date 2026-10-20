# 05a2b2: Container exit authority and gated material deletion

**Bounded partial slice ONLY — not 05a or parent-05 acceptance.** Uses the
explicitly authorized implementation fallback. Scoped credential injection is
NOT implemented or qualified and remains **05a2b3**. Public unauthenticated PyPI
is still the only supported source. No installation polling, public adapters,
publication or live apply. Parent Status/checklist unchanged; 06 remains blocked.
Branch `uv-implementation`; base `de474ccd3f2de0c0bf351df11a1327ad3229b261`.

## Implementation

Opt-in preparation container workers run the existing pinned builder in a
non-root, capability-dropped, no-new-privileges Docker container with its own PID
namespace, no daemon socket, no DB/credential environment and only a read-only
source-repository mount. Inputs are checked against the existing exact public
policy **before staging or creating a container**. Shared policy validation now
serves both builder and container dispatcher; unsupported credential-bearing
index/source policies fail closed with redacted errors. This is not credential
injection evidence or a hostile-build sandbox.

A restricted local receipt durably records the full container ID, daemon ID,
worker and candidate **before start**. An exclusive OS file lock spans creation,
start, wait and result retention; reconciliation takes that lock nonblocking.
Builder return/`docker wait` do not mark exit. After terminal DB writes, the trusted
daemon proves the private namespace stopped, the reconciler validates its
configuration, removes the container without force, and only then reports worker
exit. All descendants are subject to PID-namespace destruction, including detached
children. Live, paused, dead, restarting or untrusted containers remain unproven.
Daemon errors and daemon identity changes fail closed. A successful inventory from
the same daemon can replay an already removed container after lost DB reporting.

The independent reconciliation pass runs before claims, after dispatch and while
idle, handling exited orphan containers without relying on leases or builder
return. Existing in-process polling remains available but **cannot prove exit**;
its workers, including historical workers, still block deletion. A crash between
DB claim/container creation and durable receipt can leave an unproven worker:
missing receipts never authorize exit and need trusted operator investigation.
This intentionally prefers leaked restricted storage over unsafe deletion.

Physical cleanup holds environment/candidate/material serialization through
eligibility, unlink/fsync and the DB deletion tombstone. It applies DB-time expiry
and requires a terminal candidate, immediate unusability, and **ALL** associated
worker rows exited, including prior reclaimed workers. Only flat UUID-named
regular files registered in the explicitly configured restricted material root
are eligible; outside paths, directories and symlinks are rejected. Missing files
are retryable after the same gate. Committed/shared files, retained bundles and
reusable cache paths are never cleanup targets. Disposable per-container build
caches are not reusable host cache entries. This is not forensic erasure.

## Verification and review

Qualified evidence: `05a2b2/evidence/accepted/` (full logs, image identity, source
hashes and final attested retained source/wheel/project/lock/inventory bytes).
Independent review is required; no parent acceptance is claimed. Exact commands
from repository root, final executions exit 0:

```sh
bash uv-experiment/run-preparation-container-cleanup-proof.sh
# PASS real pinned-uv container dispatch: retained ready bytes; namespace destroyed before worker exit
# PASS live orphan with build descendant blocks deletion after lease reclaim and newer worker exit; status redacted
# PASS trusted daemon orphan reconciliation destroys private PID namespace; ALL-worker exit gates physical deletion; retry idempotent

PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
# 66 passed in 12.75s

bash uv-experiment/run-preparation-poller-proof.sh
# PASS independent PostgreSQL preparation dispatch: pinned uv solve/build/retain -> fenced ready; no intent, runtime or installation mutation
# PASS independent poller idle after readiness; worker exit not inferred from builder return

bash uv-experiment/run-preparation-builder-proof.sh
# PASS pinned uv 0.12.21: solve, source build, exact core, retained sources/wheels/inventory, cold-cache offline frozen --no-build disposable sync
# PASS conflict: redacted failure, no input/runtime mutation
# PASS unsupported credential-bearing direct source rejected, diagnostic redacted; credential injection/cleanup NOT qualified
# PASS retained-byte corruption rejected; reusable cache not relied upon

uv run --project uv-experiment/executor --frozen pytest -q -p no:cacheprovider uv-experiment/executor/tests/test_registry_builder.py uv-experiment/executor/tests/test_preparation_containers.py
# 17 passed in 0.09s (5 registry_builder + 12 container authority/policy tests)

act workflow_dispatch -W .act/checks.yml
# 289 passed, 1 skipped in 30.56s
# Success: no issues found in 51 source files; Job succeeded

git diff --check
# no output
```

The container proof uses the existing disposable PostgreSQL 16.6 container
`da-05a2-postgres` (55432), drops only `container_cleanup_proof`, performs a real
pinned-uv build and retained-byte verification, and observes a live orphan with a
`sleep` descendant through the trusted daemon. After lease reclaim, a newer
fixture worker is explicitly exited (it never launches OS work); the live older
worker still prevents physical deletion. Namespace termination/removal then allows
idempotent deletion. Preparation creates no installation/intent/runtime mutation.

PostgreSQL tests exercise both migration and fresh initialization. They prove
immediate denial, all-worker gating, actual unlink, committed/shared/cache
preservation, idempotence, redacted status and path/symlink rejection. Native
regressions cover creator lock exclusion, missing receipt, daemon outage/switch,
untrusted PID/privilege configuration, live/uncertain states, lost DB-ack replay,
and userinfo/token-only/query credentials rejected before staging with no secret
in errors. Counts intentionally increase by 4 PostgreSQL cases, 12 native tests
and 1 mypy module. No expected-count assertions or exclusions changed. The sole
native skip remains the documented bare-act Supervisor test.

Exact additional scan command (exit 0; `redaction.log` records its PASS):

```sh
python - <<'PYSCAN'
from pathlib import Path
root = Path('uv-experiment/05a2b2/evidence/accepted')
assert all(b'DISPOSABLE_SECRET_TOKEN' not in p.read_bytes()
           for p in root.rglob('*') if p.is_file())
print('PASS disposable secret absent from every qualified evidence file')
PYSCAN
```

A byte scan of **every** qualified evidence file checks absence of the disposable
fixture secret, including retained project/lock and all logs. This is evidence of
non-injection/redaction in the implemented public-only scope, **not** successful
credential injection. Older builder/poller evidence was restored after copying
this run's final proof bytes/logs; historical accepted evidence is unchanged.

## Resolution and limits

Trusted daemon access is privileged operator authority, not a web/API feature.
Receipt storage, material storage and the source repository must be restricted,
operator-owned local configuration; do not share them with untrusted writers.
The daemon identity and its inventory must remain authoritative: manual daemon
metadata destruction/restoration is outside this proof. No container worker is
automatically enabled without the explicit CLI options documented in README.

**05a2b3 remains fenced:** scoped credential injection for explicitly supported
credential sources and qualification of candidate-secret staging lifecycle. No
private index/bindings, broader target/build policy or credential-source scope is
silently enabled. Current public-only policy has no supported credential-bearing
source. 05a3 authenticated adapters, 05b publication/security writers and 05c
installation polling/live apply remain fenced. Parent 05 Status/checklist remains
unchanged. Independent review of this bounded sub-slice is still required.


## 2026-10-14 P1 review fix: receipt lock through readiness

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

Next: 05a2b3
