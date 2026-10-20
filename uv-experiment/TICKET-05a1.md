# 05a1: Durable registry preparation protocol

**This run implements ONLY the supervisor-authorized bounded partial slice 05a1.**
05a2/05a3, 05b and 05c remain fenced. This is not 05a or parent-05 acceptance.
Branch: `uv-implementation`; approved starting base:
`35a3a93fa774f4060ee9ca16dfe25b12b89889d6`.
Source: `.scratch/uv-environment-implementation/issues/05-registry-install.md`;
refinement history: `TICKET-05-SPLIT.md`.

## Implementation

- Additive logical tables and actual alembic upgrade/downgrade for environment,
  normalized package identity/contribution, candidate, minimal immutable revision,
  record-only installation attempt, host/config observations, dedup and separate
  preparation-worker/material records. The migration seeds nothing and does not
  infer intent from legacy Package/Install rows. Shared schema is shipped alone
  in the independent executor wheel; no Flask/runtime code is imported.
- One internal PostgreSQL semantic module owns prepare/cancel/status and worker
  claims. It preserves the exact PEP 508 declaration while extracting structured
  name/extras/specifiers/marker and normalized identity. Unsupported sources,
  syntax, operations and restart=false reject without sanitizing or legacy writes.
  All committed contributions and exact core/target/tool/config snapshots survive.
- Short environment -> candidate transactions capture parent and all four
  generations. Claims/reclaims/renewals use DB time and monotonic fencing. A
  reclaimed worker remains separately tracked until proven exited. No expensive
  solve/build or live runtime operation is performed by this module.
- Completion accepts only a complete internal worker attestation: retained bundle,
  project/lock/artifact digests, inventory, exact core, desired-relative and
  verified-runtime-relative diff, warning/validation and ownership effects.
  Result digest binds captured parent/generations/inputs/result. Changed snapshots,
  cancelled candidates, expired claims and late workers cannot publish.
- DB-time readiness starts a five-minute expiry. A PostgreSQL trigger prevents
  ready-result rewriting, terminal resurrection and candidate-history deletion;
  revision records reject updates/deletion. Status never refreshes expiry and
  hides stale/expired previews. Approval and installation are always unsupported.
- Candidate-only material registration/use requires the current claim. Cancellation,
  failure and expiry invalidate uncommitted/unshared references. Deletion eligibility
  waits for **all** associated workers' proven exit, including reclaimed/orphan
  workers. Shared/committed references are preserved. No cache paths are registered,
  no material is deleted by 05a1 and no usable reference is promoted to committed.

Mechanism/integration constraints: [`preparation/README.md`](preparation/README.md).
Durable work consumption is specified there; the preparation polling loop lands in
05a2, not installation polling. All existing managed browser/API mutation routes
remain explicit rejection fences, with legacy flag-off/unadopted behavior unchanged.

## Verification and review

Qualified test evidence: [`05a1/evidence/accepted/VERIFICATION.md`](05a1/evidence/accepted/VERIFICATION.md).
The directory holds qualified local checks, **not a claim of independent acceptance**.

Final real PostgreSQL 16.6 and parser command:

```sh
PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
```

Exit 0: **33 passed in 1.42s** (11 real PostgreSQL scenarios, 22 native corpus/rejection
cases). Covers actual additive migration upgrade/downgrade, simultaneous dedup,
body mismatch, cancellation, stale/late/claimed worker fencing, DB-time readiness,
five-minute expiry, readiness immutability, all four stale generations, failed
conflicting-declaration attestation/no intent or observation mutation, complete
core/diff validation, access denial, unknown-runtime warnings, immediate candidate
credential-reference invalidation, delayed eligibility until worker exit, orphan
eligibility and committed/shared credential preservation. Expiry uses a test-only
DB trigger to age readiness without waiting five minutes or modifying an immutable
result after publication. No SQLite/mock substitute is used.

```sh
act workflow_dispatch -W .act/checks.yml
```

Exit 0: **271 passed, 1 skipped in 31.99s**; mypy **48 source files**; `Job succeeded`.
Counts intentionally increase by 22 new native tests and one executor module;
no expected-count assertion or exclusion was changed. Existing flag-off and
unsupported-path regressions remain part of the full native gate. The sole skip
remains the documented bare-act-image Supervisor test.

Wheel build plus isolated wheel-only import: exit 0, `example`, `11` tables.
Ruff E9/F over all changed Python files and `git diff --check`: pass.
Schema DDL compilation for PostgreSQL/MySQL: all 11 tables compile; this is **not**
a real MySQL migration qualification. An initial migration-copy implementation
failed setup (11 errors); it was replaced by direct `CreateTable` compilation and
all real migration tests were rerun successfully.
Independent review is required and has not been performed by this writer.

## Resolution and limits

05a1 is implemented and locally checked, awaiting independent review. No public
caller can prepare/submit attestations through a production adapter. The injected
DB authorizer is a mandatory trusted seam; tests use a fixture authorizer, not
production security-writer integration. Legacy-package linkage remains a nullable
logical identifier; no projection/grants are written here.

The protocol trusts a worker's materialization/requirements-satisfied attestation;
it does not verify retained filesystem bytes or run uv. Credential cleanup proves
durable authority/eligibility only, not physical deletion or secret erasure. No
pending config import, legacy adoption, update/remove, retry, approval, attempt
execution or live apply is added. These limits are explicit fenced remaining work,
not silently passed acceptance items.

Pinned-uv registry/container conflict detection, retained source/artifact/inventory
provenance, actual worker polling, physical credential lifecycle/orphan cleanup and
cold-cache frozen/no-build disposable sync remain 05a2. Authenticated browser/API
preview/cancellation adapters and aggregate 05a qualification remain 05a3. Parent
05 is incomplete; 06 stays blocked.

Next: 05a2

## 2026-10-14 P1 review fixes

- Fixture packaging: preserve the executor's existing repository-relative schema
  include by copying the single canonical module to
  `/docassemble_base/docassemble/base/managed_records.py` in the image. No schema
  source is duplicated in the repository. The new packaging regression replays
  the actual relevant Docker COPY instructions in a disposable directory, builds
  a wheel, installs it in an isolated venv and imports executor/schema with `-I`.
  This exercises the failed build seam without requiring a full fixture build.
- Fresh initialization: shared table definitions now attach PostgreSQL-only
  `after_create` DDL for the same function/triggers used by the migration.
  Migration `CreateTable` execution does not dispatch table events, so its
  explicit installation remains single. Repeated metadata creation skips the
  existing tables and does not recreate guards. Real PostgreSQL tests run every
  protocol scenario through both upgrade and fresh ORM metadata creation, assert
  both triggers exist, and reject revision update/delete and readiness mutation.
- Exact verification (exit 0 for each; complete output retained in the untracked
  `ticket-05a/fix-evidence/` directory):

```sh
uv run --project uv-experiment/executor --frozen pytest -q -s -p no:cacheprovider uv-experiment/executor/tests/test_executor_packaging.py
# 1 passed in 1.96s; isolated executor/schema import: 11 tables
PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
# 46 passed in 2.33s
act workflow_dispatch -W .act/checks.yml
# 272 passed, 1 skipped in 30.20s; no issues found in 48 source files; Job succeeded
git diff --check
# no output
```

Counts intentionally increase: native +1 packaging test; PostgreSQL/parser
33 -> 46 from testing both initialization paths (24 PostgreSQL cases plus 22
parser cases). No gate assertion or exclusions changed. PostgreSQL uses the real
`postgres:16.6` image (digest
`sha256:557fea37a744d5f4c8faab304b0a90858b53ab119735a88c131fd19dab802f36`).
Full fixture-image build was not needed or run. Independent review remains
required; no 05a or parent-05 acceptance is claimed and all later slices stay fenced.
