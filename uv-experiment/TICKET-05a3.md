# 05a3: Authenticated registry preparation preview adapters

**Preview ONLY, public unauthenticated PyPI ONLY. Not 05b, 05c or parent-05 acceptance.**
Branch `uv-implementation`; starting HEAD `37b58fb0888a70b04bfa2c458681baaaf10c85f1`.
Independent review required. Parent 05 Status/checklist unchanged; 06 remains blocked.

## Decision and implementation

The supervisor approved moving the canonical dependency-light semantic authority
into `docassemble.base.managed_preparation`, retaining the executor re-export and
shipping the canonical module in the executor wheel and fixture image. Base never
imports executor or Flask. Both adapters and independent tooling use the same
Preparation class; no adapter constructs uv commands, submits worker attestations,
writes Package/contributions/grants or advances intent/runtime pointers.

- Session-authenticated `/package_preview` serves a minimal prepare/refresh/cancel
  page. Mutation is POST with the existing CSRF mechanism (not exempt). Preview
  output is rendered as text, not HTML. No approval or installation-success button.
- API-key-authenticated `/api/package_preview` accepts GET status and POST prepare
  or cancel. JSON prepare: `{"requirement":"six==1.17.0","idempotency_key":"unique-key"}`;
  cancel: `{"action":"cancel","candidate_id":"<uuid>"}`. GET requires
  `candidate_id`. The key uses existing API header/bearer/cookie mechanisms.
  Actor identity comes only from authenticated current_user, never request fields.
  Prepare returns 202, status/cancel 200; forbidden 403, semantic rejection 409.
  Approval/retry, restart=false and unsupported operations explicitly reject.
- Fresh SQL authorizer reads active/non-disabled account, role memberships and
  PackageAuth in each semantic transaction. Current config.allowed supplies role
  permissions; ALLOW_UPDATES/DEVELOPER_CAN_INSTALL are current policy, not cached
  current_user role checks. Legacy names are normalized before comparison; other
  users' explicit requirements, ownerless identities, core/source replacement and
  unsupported sources stay fenced. Existing identity ownership must also match
  the requester. Admin cancellation/status can inspect another requester's work;
  opaque ID knowledge alone never authorizes access. Ownerless assignment is NOT
  added; it requires 05b publication/assignment qualification.
- API key permission/constraint records actually live in Redis, not SQL. Per
  semantic authorization the adapter revalidates the key/constraints and rereads
  key permissions, failing closed on Redis error. It never relies on cached
  current_user.limits. Existing limited-admin-key semantics are preserved; keys
  do not confer administrator identity. Current SQL roles remain authoritative.
  Full cooperating-writer serialization (including config/Redis writers) is 05b,
  NOT proven by these preview-only fresh reads.
- Status exposes current desired revision/runtime observation and generation,
  immutable desired-relative and normally verified-runtime-relative full diffs,
  ownership effects and validation. Unknown/core-only/development-override states
  have no exact runtime diff and carry explicit warnings. Stale/expired previews
  are suppressed, expiry is DB time and never extended on refresh. Approval and
  installation-request flags remain false. The current runtime observation is not
  replaced by historical legacy Install/Package versions.
- Legacy `/api/package` and `/updatepackage` managed mutation rejection fences
  remain unchanged. Flag-off/unadopted legacy behavior is not rerouted. The new
  preview endpoint returns 404 flag-off and rejects absent managed DB baselines.

## Verification and review

Evidence: `05a3/evidence/accepted/`. This is qualification evidence awaiting
independent review, not approval/publication/live-apply acceptance. All final
commands below ran from repository root and exited 0:

```sh
PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
# 113 passed in 16.57s

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
# 17 passed in 0.08s

uv run --project uv-experiment/executor --frozen pytest -q -s -p no:cacheprovider uv-experiment/executor/tests/test_executor_packaging.py
# isolated executor/schema import: 11 tables; 1 passed in 1.70s

act workflow_dispatch -W .act/checks.yml
# 292 passed, 1 skipped in 30.26s
# Success: no issues found in 51 source files; Job succeeded

uv tool run ruff check --select E9,F docassemble_base/docassemble/base/managed_preparation.py docassemble_webapp/docassemble/webapp/packages/managed.py uv-experiment/preparation/tests/test_preview_adapters_postgres.py uv-experiment/executor/src/docassemble_executor/preparation.py
# All checks passed!

git diff --check
# no output
```

PostgreSQL is the existing disposable 16.6 container on 55432. New HTTP component
tests use the actual adapter and policy module with Flask session identity and
real SQL, across both migration and fresh initialization. They prove allowed and
denied prepares, ownership/normalized aliases, revoked roles/account/config
permission/PackageAuth, ownerless handling, requester/admin status/cancel,
immutable full and distinct desired/runtime diffs, expiry/staleness and runtime
warnings. Assert no intent/revision/installation/observation mutation. Production
API permission callback is tested with controlled key-store helpers (including
revocation/outage), not a real Redis server. Existing API credential verification
and its constraints remain the production authentication seam. This is not a
full production Flask application boot or browser JavaScript automation proof.

Intentional count change: PostgreSQL/preparation 81 -> 113 (+30 real PostgreSQL
HTTP/policy cases, +2 shared-import checks). Native act/mypy counts remain 292/51;
no count assertions or exclusions changed. The lone native skip is still the
bare-act Supervisor test. Additional isolated import blocks executor imports and
successfully imports web/shared authority; executor re-export identity is asserted.
Fixture-COPY-layout wheel test covers the canonical semantic module's packaging.

Intermediate runs failed because prepare's new authorization context initially
omitted requester_id expected by fixture authorizers; fixed without weakening
fixture checks. A fixture-import lint cleanup temporarily lost pytest fixture
registration; restored explicit fixture import. Final complete run supersedes both.

Proof scripts write historical evidence locations. This run copied only each
current attested bundle/image identity, removed unrelated historical copied
outputs, restored historical tracked evidence and removed only newly generated
historical-path bundle directories. Wheels and manifest-listed `.gitignore` files
are retained. Source hashes and current full command logs are included; act trailing
whitespace normalized. Additional copied-bundle verification and secret scan:

```sh
PYTHONPATH=docassemble_base uv run --project uv-experiment/executor --frozen python - <<'PY'
from pathlib import Path
import json
from docassemble_executor.registry_builder import verify_retained
root=Path('uv-experiment/05a3/evidence/accepted')
for name in ('builder','poller','container'):
    proof=root/name
    digest=json.loads((proof/'attestation.json').read_text())['bundle']['digest']
    verify_retained(proof/'retained'/digest,digest)
assert all(b'DISPOSABLE_SECRET_TOKEN' not in p.read_bytes() for p in root.rglob('*') if p.is_file())
print('PASS all three current copied attested bundles byte-verified; disposable secret absent from all qualified evidence')
PY
```

Exit 0, PASS as printed. This is deliberate fixture-secret non-exposure, not
arbitrary secret discovery or successful production authenticated uv injection.

## Aggregate 05a acceptance evidence — bounded PREVIEW scope

| Slice | Requalified mechanism and evidence |
| --- | --- |
| 05a1 | Real PostgreSQL migration/fresh schema, parser, dedup/body mismatch, immutable readiness/expiry, cancellation/staleness/late-worker fences and unchanged intent/runtime: complete `postgres.log` + native act suite. |
| 05a2 | Current pinned-uv public registry solve/source build, exact core, retained byte validation, conflict, corruption, cold-cache offline frozen/no-build **disposable** sync: `builder.log`, `builder/`, `bundle-validation.log`. |
| 05a2b | Independent real PostgreSQL/pinned-uv preparation-only dispatch, renewal/reclaim/no-exit-inference: `poller.log`, `poller/`, PostgreSQL concurrency regressions. |
| 05a2b2 | Private-PID preparation container receipt/lock and trusted daemon orphan authority; ALL-worker delayed physical deletion: `container-cleanup.log`, `container/`, PostgreSQL + `registry-container.log`. |
| 05a2b3 | Restricted disposable candidate-secret staging/claim-scoped consumption/non-exposure/invalidation and delayed cleanup: complete PostgreSQL and current container orphan evidence; production injection still rejects. |
| 05a3 | Fresh authenticated policy/HTTP prepare/cancel/status and truthful full preview; single shared semantic authority and isolated packaging/import evidence: `postgres.log`, `packaging.log`, unchanged native fences in `act.log`. |

This aggregate qualifies public-PyPI **preview mechanisms only**, subject to
independent review. It does NOT close full source-contract/credential-source
qualification or parent 05. Real credential-source scope/injection remains the
proposed fenced 05a2b4: no source/index expansion is authorized. Publication,
approval, retry, security-writer serialization are 05b. Durable installation
polling/live frozen apply, operational UI enablement and parent acceptance are 05c.
No installation-success UX, early Package commits, grants or pointer advancement.

## Resolution and limits

05a3 implemented and locally checked, review required. Trusted baseline snapshot,
restricted storage, daemon and executor configuration remain operator authority;
no legacy adoption or malicious-build sandbox is claimed. No new-system edits or
branch switch. No 05b/05c/parent acceptance; parent checklist untouched and 06 blocked.

Next: 05b
