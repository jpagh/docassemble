# 05a1: internal durable preparation protocol

The sections below describe historical qualification slices. Current bounded
private-index runtime **and PEP 517 build** support is documented in
[`TICKET-11c.md`](../TICKET-11c.md): scoped operator-reference fetching, exact
build-name bindings, backend-hook source validation, and frozen retained-wheel
application. This does not qualify private container credentials or hostile-build
isolation; 11c review/acceptance remains pending.

This is protocol qualification only. The public-registry builder and internal preparation-only polling are now
qualified as bounded slices below. Credential injection/deletion and authenticated
web/API adapters are not enabled.
Existing managed web/API routes still reject mutations. Approve/retry always
raise an explicit unsupported error. No InstallationAttempt is executed or
created by preparation. No legacy Package/Install rows are adopted.

`docassemble.base.managed_records` is the version-05a1 shared schema (SQLAlchemy
only, no application imports). Both web model mappings and the additive alembic
migration consume it. The independent executor wheel includes only this schema
file from base, not docassemble runtime code. Treat this migration's definitions
as frozen; future schema changes require a new migration, not edits to its factory.
The minimal revision/attempt/config/host records retain complete JSON snapshots;
publication and live observation writers are deliberately absent.

## Durable work consumption (05a2 integration contract)

A separately running worker will discover `managed_candidate.state = queued`
or `preparing` with a DB-time expired claim. `Preparation.claim` serializes the
claim and increments its fence; concurrent claimants cannot share authority.
`renew` extends only a current unexpired claim. Build/solve/materialization must
run **after the transaction exits**, outside the application runtime. Build bytes
must be retained and verified before `publish_ready` receives an attestation.
05a1 validates the complete result shape/core inventory/diffs/ownership effects,
but does **not** independently verify the attested filesystem bytes or solve.
No loop, wakeup, Celery task or production worker is started in this slice.

The future worker must poll/reconcile work independently of Redis/Celery. It must
call `fail` on preparation failure and report `worker_exited` only after all
associated build descendants are gone. Reclaiming a lease is not exit evidence.
On crash, trusted OS reconciliation must prove the old worker gone before marking
it exited. Old worker records remain separate after reclaim.

Lock order is environment singleton -> candidate -> candidate material. All
semantic transactions are short. Authorizers must query fresh trusted DB policy
inside that transaction; the fixture authorizer is **not production policy**.
No public caller may choose the authorizer, claim or result. Grant/policy writer
serialization and production actor wiring remain later qualification seams.

## Material/credential authority

`register_material` records candidate-only restricted staging. `material_authorized`
must be checked before new use; cancellation/failure/reclaim immediately deny use.
Cancellation/failure/expiry invalidate uncommitted, unshared references. A ready
candidate expires five minutes after the DB-time ready timestamp. Status and
cleanup apply expiry without changing the immutable result or extending the clock.
After all workers are proven exited, `cleanup_plan` returns only unusable candidate
material for deletion. This also covers terminal orphan records. Committed/shared
references are never invalidated or returned. No cache paths are registered.

This proves **durable invalidation/deletion eligibility**, not physical secret-file
deletion or forensic erasure. Actual credential/file lifecycle and orphan polling
are 05a2. All credential consumers must use the protocol rather than trust a
cached `usable` flag. Raw SQL/table access is trusted tooling, not an API boundary.

## Qualification

Real PostgreSQL tests live outside the native suite and require
`DA_PREPARATION_TEST_URL`; they never skip or substitute SQLite. They execute the
actual migration upgrade/downgrade with a test prefix in an isolated schema.
Only synthetic explicit managed baselines are seeded. The database URL must point
to a **disposable test database**, since tests replace `preparation_test` schema.

```sh
PYTHONPATH=docassemble_base \
DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres \
uv run --project uv-experiment/executor --frozen pytest -q -p no:cacheprovider \
uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
act workflow_dispatch -W .act/checks.yml
```

The contradictory requirement fixture preserves both declarations and verifies a
failed worker attestation changes neither intent nor runtime observations. Actual
uv conflict detection, artifact/inventory provenance and cold-cache disposable
frozen sync qualification remain 05a2. No preview is approvable in 05a1.

## Bounded 05a2 builder (not production dispatch)

`docassemble_executor.registry_builder.RegistryBuilder.build(claim.inputs)` runs
outside protocol transactions and never calls lifecycle/runtime sync. It returns
exactly the internal 05a1 attestation shape after independent byte verification.
The caller must still renew its claim and call fenced `publish_ready`; this slice
provides no polling/credential consumer and enables no public route.

Qualified inputs are explicit `target={"python":"3.12"}`, `tool={"uv":"0.12.21"}`,
`config={"index_url":"https://pypi.org/simple"}`. Other policies fail closed.
Core source digests here identify retained registry sdists (or pure Python wheels
where no sdist exists), not arbitrary old fixture source identities. Unsupported
multi-version locks and target-specific wheel-only packages fail closed. Universal
locks containing non-target packages may fail inventory checks rather than relax
requirements. This is not yet a general target adapter.

The original solve project/lock are retained separately from the materialization
project/lock, whose sources are only retained wheels. No installed runtime is used
as input; cold-cache offline frozen/no-build sync verifies a disposable venv.
Source builds use public build dependencies, may be dynamic/non-reproducible and
are not a malicious-code sandbox; wheel-byte reproducibility is not claimed.
Retained source and wheel hashes, not the reusable cache, establish byte identity.
The store is trusted restricted executor storage; backup/GC/publication retention
integration remains later work. Build as a dedicated non-root user.

Preparation polling, credential injection, candidate secret-file physical cleanup
and trusted OS orphan reconciliation remain 05a2b; the 05a1 material authority
rules remain unchanged. Redacted unsupported-source rejection is NOT evidence
of credential lifecycle qualification.


## Bounded 05a2b preparation dispatch (partial, not 05a acceptance)

A separately running restricted worker can consume queued and DB-time expired
preparation claims without Flask, Redis or Celery:

```sh
# Restricted executor identity; DB URL is trusted secret environment configuration.
DA_PREPARATION_DATABASE_URL=postgresql+psycopg2://... \
python -m docassemble_executor.preparation_poller --store /restricted/bundles
```

`--once` consumes at most one candidate; `--prefix` selects the trusted schema
prefix. Default loop interval is one second and claim lease is 30 seconds.
Discovery and claim are atomic under the existing environment/candidate lock
order. Build runs outside transactions; a separate thread renews every lease/3.
Renewal denial or DB uncertainty fences completion; ordinary build errors record
only `failed`. DB failures stop the process with a redacted operator message;
restart can reclaim expired work. The DB fence independently checks late completion.
This is preparation only, never installation polling or a public authorization
adapter. The worker's authorizer denies all actor operations.

Support remains **public PyPI without credentials only**, Python 3.12 / uv 0.12.21.
There is **no scoped credential injection**. Private indexes, source bindings,
direct sources and alternate config/targets fail closed as in 05a2. Ambient
UV/PIP credentials/configuration are not inherited by build commands. Hostile
build isolation and broader build-policy qualification are not claimed.

**05a2b2 remains fenced:** credential injection, physical delayed deletion and
trusted OS orphan reconciliation. Polling never calls `worker_exited`: a returned
builder, expired lease or vanished polling process is NOT proof that all build
descendants are gone. Consequently worker records remain conservatively unexited,
and cleanup eligibility remains blocked without external trusted exit evidence.
No file cleanup consumer is enabled; no committed/shared material or cache is
registered or deleted. This is intentionally not a production cleanup-ready worker.

Real PostgreSQL tests cover competing discovery, renewal over an initial lease,
reclaim/late fencing, cancellation, redacted failure and independent process
consumption. `bash uv-experiment/run-preparation-poller-proof.sh` additionally
runs independent dispatch through the actual pinned non-root builder to retained
ready bytes. It requires the disposable `da-05a2-postgres` container, drops only
its dedicated `poller_proof` schema, and must never point at a production database.

## Bounded 05a2b2 exit/cleanup authority (partial, not 05a acceptance)

The preceding 05a2b2 fence describes the historical 05a2b baseline. The optional
container path now proves worker exit by destroying a dedicated private PID
namespace, not by builder return or lease expiry. Run the **host** preparation
poller with access to a trusted local Docker daemon and restricted local paths:

```sh
DA_PREPARATION_DATABASE_URL=postgresql+psycopg2://... \
python -m docassemble_executor.preparation_poller --store /restricted/bundles \
  --worker-image da-uv-full-maintenance:local \
  --worker-repository /trusted/source/docassemble \
  --worker-receipts /restricted/worker-receipts \
  --material-root /restricted/candidate-material
```

The source checkout is mounted read-only into the pinned experimental image;
this is experimental operator tooling, not a deployed production adapter. The
image must provide `/usr/local/libexec/uv-pinned`, Python 3.12 and the non-root
1000:1000 identity. Daemon access is privileged operator authority. The receipt
and material roots must be restricted, trusted stores; no untrusted writer may
alter them. The material root must exist and contain only registered UUID-named
regular files; do not register bundles, cache paths or directories. In-process
builders still cannot prove exit. Historical unexited workers keep cleanup
blocked, even if a later container worker finishes.

Reconciliation runs before claims, after terminal writes and while idle. Durable
receipts bind full container and daemon IDs before start. Live creator locks,
live/uncertain containers, missing receipts, daemon identity changes and daemon
errors never authorize exit. Once a namespace is stopped, validated and removed,
exit reporting is retryable after lost DB acknowledgment. Daemon inventory must
remain authoritative; manual daemon metadata destruction/restoration is outside
qualification. Missing receipts after a creation crash require trusted operator
investigation, not timeout-based inference.

Physical unlink/fsync and DB tombstone follow immediate material invalidation
only when ALL workers are exited. Shared/committed material and reusable host
cache are preserved. Restricted fixture secrets prove gating/redaction/deletion;
no supported source accepts credentials. **Scoped credential injection and
candidate-secret staging qualification remain 05a2b3**, with private/binding,
broader target/build policy, 05a3/05b/05c still fenced. No forensic erasure or
malicious-build sandbox is claimed. See `TICKET-05a2b2.md` and run
`bash uv-experiment/run-preparation-container-cleanup-proof.sh` for real daemon,
pinned-uv and PostgreSQL evidence.

## Bounded 05a2b3 candidate-secret mechanism (no source enabled)

The preceding credential staging fence is historical. Trusted internal
`Preparation.stage_candidate_secret` / `read_candidate_secret` now provide
restricted candidate-only storage and claim-scoped consumption, qualified with
DISPOSABLE fixtures only. Staging requires an operator-owned 0700 root; files are
exclusive 0600 UUID entries, bounded to 64 KiB. Cleanup references are committed
before writing, then writing is serialized with claim denial/cancellation/cleanup.
Reads validate the current claim and candidate association; no cached flag grants
future use. Do not log returned bytes. No promotion or builder integration exists.
`inject_source_credentials` rejects every production request with a constant,
actionable error. Public unauthenticated PyPI remains the only supported source.

Cancellation/failure/expiry deny new use; physical deletion remains gated on ALL
worker exits under the existing trusted daemon authority. Registered missing files
are retryable after crashes; shared/committed files and reusable cache remain
untouched. This is neither forensic erasure nor protection from hostile builds
under the same OS identity. See `TICKET-05a2b3.md` for exact PostgreSQL, Docker and
redaction evidence. Actual authenticated uv injection, private index/binding,
broader target/build policy, 05a3/05b/05c remain fenced. No aggregate acceptance.

## Bounded 05a2b4 private wheel snapshot builder (operator-only)

The preceding source fence is historical. An explicit internal
`RegistryBuilder(..., private_snapshot=PrivateIndexSnapshot(references))` now
supports Python 3.12/uv 0.12.21 HTTPS private-index **wheel snapshots**. Captured
config contains only `private_indexes` (label -> credential-free HTTPS URL ending
in `/`) and `private_bindings` (normalized direct/transitive name -> index label).
Operator `references` separately maps labels to paths in trusted 0700 directories;
0600 same-owner regular files contain `index_url`, `username`, `password`. The
reference's independent exact URL scope must match captured policy. No candidate
staging paths may be passed to this consumer: candidate claim integration and
promotion remain fenced. Operator references persist and can be reused unattended,
without returning credentials in previews/results.

Downloader verifies TLS (optional explicit operator `ca_file`), rejects ALL
redirects and cross-path links, accepts only bounded same-package SHA256 wheel
links ending `py3-none-any.whl`, and rejects metadata identity mismatches or
unsupported/unbound transitive sources before uv. All declared dependencies,
even inactive-marker declarations, must be bound or exact core. All page links
are validated, not only the eventually selected version. Private sdists, shared
external artifact locations, query credentials and mixed public/private solving
are deliberately unsupported. Only declared names enter a local default snapshot;
uv always receives `--offline` and its explicit local default index, no secrets.
Source provenance and original declarations remain retained; frozen apply uses
relative retained wheels. Readiness warnings preview every private name/index
label without credential references.

Public adapters and container/poller policy remain public-only; no production
source authorization, private dispatch, updater credential API, HTTPS Git,
private source-build/build-dependency auth or candidate promotion is enabled.
Run `python3 uv-experiment/run-private-index-proof.py` with the pinned fixture image
available (default `da-uv-05c5-fix:local`; set `PROOF_IMAGE` to override). The proof
uses the fixed disposable name `da-05a2b4-private-index`; preserve/rename a prior
container before rerunning. See `TICKET-05a2b4.md` for evidence, exact limitations,
remaining 05a2b5/source and 05c5b/writer fences; no aggregate acceptance.
