# 05a2b3: Restricted candidate-secret staging and lifecycle

**Bounded partial slice ONLY — not 05a or parent-05 acceptance. No credential-bearing source is enabled.** Branch `uv-implementation`; base `55ffdc972e73171268d6d6e10c43ab34558e04a1`.

## Decision and implementation

Inspection of the confirmed source contract and the 05a2b2 fences found no
unfenced credential-bearing source: only unauthenticated public PyPI is qualified.
The supervisor explicitly approved the fallback: implement the staging/consumption
mechanism with disposable fixtures and reject production credential injection.
This is NOT actual authenticated uv injection evidence, private-source parity or
authorization to broaden policy.

`Preparation.stage_candidate_secret` writes at most 64 KiB of opaque bytes to an
operator-owned 0700 directory, with UUID filenames and exclusive 0600 files.
It first commits a candidate-only cleanup reference, then serializes claim
validation and writing/fsync with cancellation and cleanup. A crash before writing
leaves a registered missing file; a crash after writing leaves registered material.
Cancellation between registration and writing denies writing. No secret enters
DB inputs, previews, results, command arguments, receipts or retained bundles.
The DB stores only the restricted path and lifecycle flags. No promotion exists.

`read_candidate_secret` validates the current, unexpired claim under the same
serialization, checks candidate association and usable/unshared/uncommitted flags,
then reads only a bounded regular file with restricted owner/mode and no symlink
following. Different candidates and reclaimed workers cannot consume it. A newer
claim of the SAME preparation may use it. Returned bytes are trusted-consumer
material; callers must not log them or treat an earlier read as future authority.
This cannot revoke bytes already read by a live worker or protect them from hostile
build code under the same OS identity. No production builder calls these methods.
`inject_source_credentials` unconditionally fails before DB/tooling/staging with
an actionable constant error; all existing public-source fences remain intact.

Cancellation/failure deny new use immediately. Readiness itself grants no worker
use; DB-time expiry invalidates candidate-only references on reconciliation/status.
Physical deletion reuses 05a2b2's existing all-worker-exit gate, trusted daemon
namespace destruction, retryable unlink/fsync/tombstone and independent orphan
pass. Lease expiry, build return and newer-worker exit do not prove older exit.
Committed/shared material and reusable cache are preserved. No forensic erasure,
cache representation erasure, public API or new source policy is claimed.

## Verification and review

Evidence: `05a2b3/evidence/accepted/` contains full current logs, source hashes,
image identities and each final attested bundle (not earlier historical results).
Act log trailing whitespace is normalized. Retained wheels and their manifest-listed
`.gitignore` entries are force-tracked so clean-checkout evidence is complete.
Independent review is required; this directory is qualification evidence, not
aggregate acceptance. Exact commands from repository root (all exit 0):

```sh
PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
# 81 passed in 14.04s

bash uv-experiment/run-preparation-container-cleanup-proof.sh
# PASS real pinned-uv container dispatch: retained ready bytes; namespace destroyed before worker exit
# PASS restricted candidate-secret staging and claim-scoped consumption: userinfo/token/query fixtures only; no source enabled
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

uv run --project uv-experiment/executor --frozen pytest -q -p no:cacheprovider uv-experiment/executor/tests/test_registry_builder.py uv-experiment/executor/tests/test_preparation_containers.py
# 17 passed in 0.18s

act workflow_dispatch -W .act/checks.yml
# 292 passed, 1 skipped in 30.43s
# Success: no issues found in 51 source files; Job succeeded

git diff --check
# no output
```

The builder's historical PASS wording refers to source credential injection,
which remains unqualified, not the new fixture staging lifecycle. Proof scripts
write their historical evidence paths; current outputs were copied here, historical
tracked evidence restored and only this run's newly generated historical-path
untracked bundles removed. No previous handoff directories were changed.

Intentional counts: PostgreSQL/preparation suite 68 -> 81 (+10 real PostgreSQL
cases across migration/fresh initialization, +3 native rejection cases); act
289 -> 292 (+3 rejection forms), mypy remains 51 files. No expected-count
assertions or exclusions changed. The one act skip is still the documented
bare-image Supervisor test. Intermediate PostgreSQL run: 76 passed before the
cancel-before-write and native rejection tests were added; final run above is
superseding evidence.

Real PostgreSQL tests inject userinfo/token-only/query disposable forms into the
restricted staging primitive, consume them only from their own preparation,
check permissions, redacted errors/status/ready previews, denial after
cancel/failure/expiry/reclaim, retryable missing-file cleanup, symlink/mode
rejection, all-worker deletion gating and committed/shared/cache preservation.
Expiry uses a test-only trigger-disabled timestamp adjustment, not a production
clock/immutability change. Fixture workers launch no OS work; their explicit exit
attestations are NOT daemon evidence. The separate real Docker proof stages and
consumes all three forms, cancels, then shows an older live orphan with a sleep
build descendant prevents deletion even after the newer worker exits. Only trusted
namespace destruction/removal and exit reporting release deletion.

Exact additional evidence scan (exit 0):

```sh
python - <<'PYSCAN'
from pathlib import Path
root = Path('uv-experiment/05a2b3/evidence/accepted')
assert all(b'DISPOSABLE_SECRET_TOKEN' not in p.read_bytes()
           for p in root.rglob('*') if p.is_file())
print('PASS disposable secret absent from every qualified evidence file including project/lock and logs')
PYSCAN
```

Additional copied-bundle validation (exit 0):

```sh
PYTHONPATH=docassemble_base uv run --project uv-experiment/executor --frozen python - <<'PY'
from pathlib import Path
import json
from docassemble_executor.registry_builder import verify_retained
root=Path('uv-experiment/05a2b3/evidence/accepted')
for name in ('builder','poller','container'):
    proof=root/name
    digest=json.loads((proof/'attestation.json').read_text())['bundle']['digest']
    verify_retained(proof/'retained'/digest,digest)
assert all(b'DISPOSABLE_SECRET_TOKEN' not in p.read_bytes() for p in root.rglob('*') if p.is_file())
print('PASS all three copied attested bundles byte-verified; disposable secret absent from all qualified evidence')
PY
```

During evidence packaging, deleting wheel `.gitignore` markers initially caused
this validation to fail; restoring the manifest-listed markers fixed it. No
functional code changed. Initial cached diff check also flagged act log whitespace;
normalization fixed it. Final committed diff check passes.

This tests deliberate non-exposure, not arbitrary URL-secret detection. Disposable
secret values are intentionally present in test source, never qualified evidence.

## Resolution and limits

This fulfills the authorized mechanism/lifecycle fallback only. The restricted
root, DB, receipts and daemon are trusted operator configuration; arbitrary SQL
and local filesystem modification are not a security boundary. Lost-worker
receipts still fail closed and require operator investigation as in 05a2b2.

**Remaining fenced:** actual uv credential injection against a real supported
credential-bearing source, private index/binding, broader target/build policy and
unsupported source qualification. These require an explicit source-scope decision
and separate pinned-uv authenticated-source evidence. 05a3 authenticated preview
adapters, 05b publication/security writers, 05c installation/live apply and
aggregate 05a/parent acceptance remain fenced. Parent Status/checklist unchanged;
06 remains blocked. Independent review required.

Next: 05a2b4 (explicit source-scope decision and authenticated injection qualification)
