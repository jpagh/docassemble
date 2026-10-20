# 05: Registry install — inspection and proposed vertical split

Date: 2026-10-14 (UTC). Branch: `uv-implementation`.
Inspected baseline: `2e26f5ad7` (`Untrack local ticket handoffs`).
Initial `git status --short` was empty; there was no interrupted implementation
work to preserve or resume. No functional implementation changes were made.

**Decision status: proposed, awaiting user approval.** This document is not
acceptance of parent 05 or authorization to start the child slices. The
supervisor directed this run to stop with an inspection/split handoff only.
Parent 05 remains incomplete; 06 remains blocked regardless of its tracker label.

## Implementation inspection

Read the ticket, implementation spec, publication/source contracts, lifecycle
seams, and 03/04b3b handoffs before inspecting the executor and web seams.
Existing lifecycle authority is reusable, but it is not a publication service:

| Seam | Existing implementation | Missing for 05 |
| --- | --- | --- |
| `uv-experiment/executor/src/docassemble_executor/state.py` | Fixture `DesiredState`/`AppliedState` JSON and atomic local writes | DB singleton, publication/runtime generations, candidate and revision identities, global attempt slot and deduplication |
| `docassemble_webapp/docassemble/webapp/packages/models.py` and `alembic/versions/` | Legacy Package, PackageAuth, Install and legacy migrations | EnvironmentState, ManagedPackageIdentity, RequirementContribution, Candidate, Revision, InstallationAttempt, HostEnvironment/runtime observation, ObservedConfig and dedup records/migration |
| `executor/src/docassemble_executor/bundles.py` | Hash and verify retained bundles against an image manifest | Dynamic registry candidate materialization, immutable readiness digest, durable promotion/retention and cancellation/expiry cleanup |
| `executor/src/docassemble_executor/controller.py` (`load_context`, `apply_runtime`, `ServiceManager.run`) and `cli.py` | Fixture revision apply and service monitoring | Independent DB preparation/attempt polling, claims/fences, published-bundle loading, journal-to-DB terminal reporting/replay |
| `executor/src/docassemble_executor/uvsync.py` | Non-root frozen sync; ordinary sync adds `--no-build` | Preview solve and build materialization outside live runtime, exact core validation, portable candidate inventory/lock and supported transitive-source checks |
| `executor/src/docassemble_executor/status.py` | Local desired/applied/maintenance/recovery projection | Authorized candidate/revision/attempt DB status and monotonic observed-runtime freshness |
| `docassemble_base/docassemble/base/managed.py` | Fail-closed legacy mutation fence | Semantic prepare/approve/cancel/retry/status module |
| `docassemble_webapp/docassemble/webapp/packages/views.py` (`update_package`) and `packages/api.py` (`api_package`) | Managed mutations explicitly rejected | Registry preview and confirmation adapters without early Package commits or Celery authority |
| `utils/helpers.py` (`install_pip_package`, source helpers, uninstall) | Legacy projection/grant mutation, fenced in managed mode | All supported managed changes routed through the semantic module; other sources/operations kept fenced |
| `users/views.py` (`delete_privilege`, `edit_user_profile_page`), `users/api.py` (`api_user_by_id_privileges`), `interview/helpers.py` (`delete_user_data`) | Role/account/grant writes and independent commits | Common serialization with publication and fresh DB authorization, including account deletion and ownership-effect invalidation |
| `admin/views.py` (`config_page`), user/API permission administration | Existing policy/settings writers | Inventoried policy/permission writers cooperating with publication; no cached-current-user-only authority |

Executor paths in shortened table entries are relative to `uv-experiment/`;
webapp paths in shortened entries are relative to
`docassemble_webapp/docassemble/webapp/`. This is the starting writer inventory,
not a claim that all permission/policy writers have already been audited.

## Why a further split is necessary

Parent 05 crosses four new persistence/authority boundaries at once: legacy
SQLAlchemy projections and ownership, PostgreSQL publication/authorization
serialization, independent dynamic uv preparation/artifact retention, and the
root-owned journal/host-lock lifecycle. None of the first three is implemented
at this baseline. The fourth only accepts qualified fixture bundles.

A single bounded implementation would therefore have to introduce the schema,
module, source builder, UI/API, cooperating security writers, executor polling
and recovery reporting, then qualify each independently and together. Merely
adding endpoints or calling fixture `apply_runtime` would omit atomic approval,
build/source policy, or truthful durable outcomes. In-memory tests, heartbeat
slot release, and early Package commits are not acceptable substitutes. Splitting
preserves every parent checklist item rather than silently weakening it.

## Proposed dependency order and boundaries

`04 accepted -> 05a -> 05b -> 05c -> parent 05 acceptance -> 06`.
All slices retain flag-off/unadopted legacy behavior and managed fail-closed
fences. No slice implements update/remove, config import, coherent upgrade,
rollback, adoption, backups, other-source installation, or general managed API
breadth. No fresh browser-selected downgrade or automatic data restore.

### 05a — Durable registry preparation and preview

- Add the additive logical schema/migration and shared protocol needed by 05,
  including runtime observation and dedup; do not infer/adopt legacy intent.
  Test against explicitly seeded managed baseline fixtures only.
- Establish one semantic module for prepare/cancel/status. Approve/retry remain
  explicit unsupported operations until subsequent slices qualify them.
- Parse one registry requirement structurally, preserving exact declaration,
  extras/specifiers/markers and normalized identity; reject unsupported syntax
  without sanitization. Preserve all committed contributions and exact core.
- Capture parent/input/target/runtime generations in a short transaction; perform
  solve/build/materialization outside transactions and the live runtime using
  independent tooling. Claim/reclaim preparation with fencing.
- Publish immutable ready results using DB time, five-minute expiry from readiness,
  complete durable bundle/diff/validation and ownership effects. Cancelled/stale
  workers cannot publish late results. Implement candidate cleanup without
  deleting durable/shared artifacts or reusable uv cache.
- Add minimal registry web/API preview and cancellation UI, with desired-relative
  and verified-runtime-relative diff/warnings. No approval success or install
  success UI; all unsupported managed operations stay rejected.

Acceptance evidence: real PostgreSQL prepare dedup/body mismatch, cancellation,
late-worker fencing, expiry, immutable readiness and stale-preview tests; registry
requirement corpus including conflicts/no intent or runtime mutation; pinned-uv
container solve/build with unchanged core and cold-cache frozen/no-build sync
into a disposable test venv (never live apply); retained source/artifact/inventory
and redaction/cleanup evidence; flag-off and unsupported-path regressions; act
native/typecheck gate. This proves preview only, not parent acceptance.

### 05b — Atomic authorized publication and retry

- Complete module approve/retry semantics using documented common lock order or
  equivalent proven DB isolation, uniqueness and fencing constraints.
- Inventory and integrate all relevant account, role/API-permission, PackageAuth,
  package-name/source policy and config-policy writers with that serialization.
  Preserve account deletion behavior, ownership and explicit admin assignment
  for ownerless identities; recheck current name/source/operation authority.
- Atomically commit contribution/Package projection/current grants, immutable
  complete revision, desired pointer/generation, candidate publication, dedup
  result and one installation attempt. Expensive artifact IO remains outside.
- Recheck readiness digest/requester/DB-time expiry and parent/input/target/runtime
  generations. Busy leaves a valid candidate ready until expiry. Duplicate lost
  responses return the same recorded authorized result, even after advancement;
  key/body mismatch conflicts. Retry only current desired revision by authorized
  admin or still-authorized original approver, without solve.
- Add confirmation/status adapters showing publication/pending, never installation
  success. Keep public apply-producing approval fenced until 05c operational
  integration is ready; exercise full publication through module test fixtures.

Acceptance evidence: real PostgreSQL concurrent same-parent/candidate approvals,
busy slot, expiry, stale runtime/input generations, role/grant/policy revocation
and account deletion races through actual cooperating writers, ownerless/ownership
checks, alias/source bypass attempts, lost-response approval/retry and body
mismatch. Assert atomic DB snapshots/no partial Package intent, immutable revision,
exact approved bundle and one active attempt. Confirm unsupported paths and
flag-off behavior. Run act native/typecheck gate. No slot release on heartbeat.

### 05c — Durable polling, frozen apply and complete parent qualification

- Independent executor polls DB preparation/attempt work without importing
  Flask/docassemble runtime or depending on Celery/Redis dispatch authority.
  Connect 05a's builder and 05b's revisions to production experimental polling.
- Verify retained published bundles/target and current operational safety gates;
  apply approved bytes frozen using existing host-lock, drain/admission,
  child-process exclusion, non-root sync and recovery authority. No re-solve.
- Bind local journal and DB attempt/fencing identities. Release the slot only
  after durable terminal child-safe evidence; replay lost DB reporting without
  reinstallation, recovery loop or release of another attempt.
- Serialize monotonic runtime observations with publication; expose desired,
  verified running, historical normally verified, recovery, pending/failed and
  override/unknown states separately. Recovery neither succeeds the failed
  attempt nor changes desired intent. Unsupported/uncertain retry remains fenced.
- Enable the final registry preview/approve browser/API path only once this end-
  to-end operational contract is qualified. Re-run all child and lifecycle
  regression gates and obtain independent review before parent closure.

Acceptance evidence: pinned-uv real-container browser/API prepare -> preview ->
approve -> frozen apply with retained inventory and exact bundle/digest; conflict
leaves DB/runtime unchanged; failed apply preserves desired and truthful recovery;
Redis/Celery absence, crash/journal-boundary faults, child exclusion, lost DB ack
and replay, stale runtime observation and update-enable/target safety gates;
PostgreSQL full race suite; complete applicable maintenance regression proof;
act native/typecheck gate and source/evidence provenance. Parent 05 checklist
closes only after this aggregate review. 06 remains blocked until then.

## Verification and review of this inspection-only run

Exact command from repository root:

```text
act workflow_dispatch -W .act/checks.yml
```

Exit 0. Output: `249 passed, 1 skipped in 31.04s`;
`Success: no issues found in 47 source files`; `Job succeeded`.
The skip is the documented bare-act-image Supervisor test. No expected counts
were changed. This is a baseline native regression gate, not pinned-uv source or
PostgreSQL concurrency proof for ticket 05. Docker server reports `29.5.2`.
Full output (trailing whitespace normalized): `05/evidence/inspection/act.log`
(not accepted evidence).

No PostgreSQL tests were added/run, no new pinned-uv source/apply proof was run,
and no functional 05 acceptance requirement has been implemented or verified.
All parent acceptance items remain unmet in this run. Independent review of this
proposal is still required; existing 04 acceptance is not acceptance of 05.

## Resolution and limits

This run produces only a committed inspection/split proposal and a local tracker
comment. The original tracker Status and checklist are unchanged. No
`TICKET-05.md` acceptance handoff or `evidence/accepted/` claim is produced.
The proposed slices require explicit user approval before implementation.

Next: user approval of 05a/05b/05c; 06 remains blocked.

Proposed split: durable preview (05a), atomic publication (05b), durable frozen
execution and complete acceptance (05c) — awaiting user approval.

## Approval

- 2026-10-14: The user approved the vertical split (05a/05b/05c) and authorized
  starting slice 05a. Independent proposal review (`openai-codex/gpt-6-astra:low`)
  confirmed the baseline claims and judged the split sound and justified, with no
  issues found, plus four boundary clarifications incorporated into the 05a brief:
  (1) land preparation polling in 05a and reserve installation polling for 05c;
  (2) 05a cleanup acceptance must cover immediate candidate-credential invalidation,
  delayed deletion until worker exit, orphan cleanup and preservation of
  committed/shared credentials; (3) complete the 05b authorization-writer inventory
  before estimating 05b; (4) record this approval before child work begins. The
  original proposal above is preserved as historical evidence. Parent 05 remains
  incomplete; 06 remains blocked until parent 05 closes.

## 05a bounded implementation refinement

- 2026-10-14: After inspecting the missing persistence/builder/adapter seams,
  the implementation worker raised the explicit bounded-slice stop condition.
  The supervisor authorized implementing **05a1 only**, not another
  inspection-only handoff. This is a partial implementation of 05a, not 05a or
  parent-05 acceptance. The existing approved history above is unchanged.
- **05a1 (this run):** additive schema/migration/shared records, one internal
  PostgreSQL prepare/cancel/status protocol, structural registry parser,
  generation capture, fenced claim/reclaim/renew/failure/completion, DB-time
  immutable readiness and expiry, material invalidation and deletion-eligibility
  rules. Qualify with real PostgreSQL and the native gate. Worker results are
  trusted attestations, not builder/materialization proof. Production authorizer
  wiring and public adapters remain absent.
- **05a2 (next):** independent uv solve/build/materialization and retained bytes,
  preparation polling/dispatch (NOT installation polling), credential injection,
  physical delayed deletion and orphan reconciliation. Qualify pinned-uv
  containers, unchanged core, conflicts and disposable cold-cache frozen/no-build
  sync, retained source/artifact/inventory and redaction/cleanup evidence.
- **05a3:** authenticated registry preview/cancel web/API adapters, fresh policy
  authorizer wiring, full desired/runtime warnings and aggregate 05a acceptance.
  No approval or installation success UX.
- 05a2/05a3, 05b approve/retry/publication and 05c installation polling/frozen apply
  remain fenced. Existing managed mutation adapters continue rejecting requests.
  Parent 05 stays incomplete; 06 remains blocked.

## 05a2 bounded implementation refinement

- 2026-10-14: Exercised the user-authorized fallback rather than a plan-only
  handoff. **05a2 implements only the independent public-registry builder,
  retained verified source/wheel/project/lock/inventory bytes and complete 05a1
  worker attestation**, qualified with real pinned-uv containers. This is a
  bounded partial slice, not 05a or parent acceptance.
- **05a2b (next):** preparation polling/dispatch, claim renewal/reclaim integration,
  scoped credential injection, delayed physical deletion after ALL worker exits
  and trusted OS orphan reconciliation. No installation polling. Broader target,
  private index/binding and build-policy qualification remain fenced here too.
- 05a3 authenticated preview/cancel adapters, 05b publication/security writers and
  05c installation polling/live apply stay fenced. Managed routes still reject.


## 05a2b bounded implementation refinement

- 2026-10-14: Used the explicitly authorized fallback. **05a2b implements only
  preparation polling/dispatch and renewal/reclaim integration**, plus the 05a2
  P2 concurrent identical-bundle retention fix. Real independent pinned-uv +
  PostgreSQL dispatch and lease/fence tests qualify this bounded partial slice,
  not 05a or parent acceptance.
- **05a2b2 (next):** scoped credential injection, delayed physical deletion after
  ALL associated workers are proven exited, and trusted OS orphan reconciliation.
  These are NOT implemented or qualified. Worker rows remain unexited because
  builder return/lease timeout cannot prove descendant-safe exit. Public-only
  unauthenticated PyPI remains the only supported index; broader target,
  private index/binding and build-policy qualification remain fenced.
- 05a3 authenticated preview/cancel adapters, 05b publication/security writers and
  05c installation polling/live apply stay fenced. Parent Status/checklist and
  existing managed mutation fences remain unchanged; 06 remains blocked.

## 05a2b2 bounded implementation refinement

- 2026-10-14: Used the task's authorized end-to-end fallback. **05a2b2 implements
  opt-in private-PID preparation container exit authority, delayed physical
  candidate-material deletion gated on ALL worker exits, and trusted daemon
  orphan reconciliation.** Real Docker/pinned-uv/PostgreSQL evidence qualifies
  those items, not 05a or parent acceptance. In-process/historical worker rows
  remain conservatively unexited; missing receipts or uncertain OS authority
  never grant cleanup.
- **05a2b3 (next):** scoped credential injection and candidate-secret staging
  lifecycle qualification remain fenced. Public-only unauthenticated PyPI still
  has no supported credential-bearing source; private index/binding, broader
  target/build policy and unsupported source qualification remain fenced. This
  refinement is not authorization to broaden source policy implicitly.
- 05a3 authenticated preview/cancel adapters, 05b publication/security writers,
  05c installation polling/live apply and aggregate 05a/parent acceptance remain
  fenced. Parent Status/checklist unchanged; 06 blocked. Handoff:
  `TICKET-05a2b2.md`; independent review required.

## 05a2b3 bounded implementation refinement

- 2026-10-14: Source-contract inspection confirmed no credential-bearing source
  is currently unfenced. The supervisor approved implementing restricted
  candidate-secret staging and claim-scoped consumption with disposable fixtures
  only; production source credential injection unconditionally rejects. **05a2b3
  qualifies that mechanism's non-exposure, invalidation and delayed cleanup
  lifecycle, not actual authenticated uv injection or 05a/parent acceptance.**
  Real PostgreSQL and trusted-daemon orphan evidence covers userinfo/token/query
  forms and deletion only after ALL associated workers are proven exited.
- **05a2b4 (proposed next, not authorized source expansion):** explicit supported
  credential-source decision followed by scoped authenticated uv injection proof.
  Actual source authentication, private index/binding, broader target/build policy
  and unsupported sources remain fenced. No new source/index enabled in 05a2b3.
- 05a3 authenticated preview/cancel adapters, 05b publication/security writers,
  05c installation/live apply and aggregate 05a/parent acceptance remain fenced.
  Parent Status/checklist unchanged; 06 blocked. Handoff `TICKET-05a2b3.md`;
  independent review required.

## 05a3 preview qualification boundary

- 2026-10-14: Implemented authenticated prepare/cancel/status browser/API adapters,
  canonical shared semantic authority and fresh SQL account/role/PackageAuth plus
  current config policy reads. API-key permissions/constraints live in Redis, not
  SQL: they are reread per semantic authorization, fail closed on outage, and are
  not cached current_user authority. The supervisor approved this wiring; full
  cooperating security/policy-writer serialization remains 05b.
- Aggregate evidence in `TICKET-05a3.md` requalifies 05a1/05a2/05a2b/05a2b2/05a2b3
  plus authenticated preview, **bounded to public-PyPI preview mechanisms only**.
  Actual credential-source/injection qualification (proposed 05a2b4) remains
  fenced; no new source/index is enabled and no full source-contract acceptance
  is claimed. Approval/retry/publication and live apply are still rejected.
- Next implementation slice is 05b, then 05c. Independent 05a3 review required;
  no 05b/05c/parent-05 acceptance. Parent Status/checklist unchanged; 06 blocked.

## 05b bounded implementation refinement

- 2026-10-14: Exercised the user's explicit bounded fallback; supervisor confirmed
  the 05b1 handoff scope during implementation. **05b1** adds internal atomic
  approval publication, additive published/dedup schema and common environment-lock
  authority-writer transaction seam, qualified by 36 real PostgreSQL scenarios
  using fixture DB authorizer/projection callbacks. These are NOT races through
  actual production writers; NO actual production writer is integrated and NO
  production writer-safety claim is made. Complete preparation suite: 149 passed;
  native act: 292 passed, 1 documented skip; mypy: 51 source files.
- **05b2 (next):** ALL actual account/role/API-permission/PackageAuth/package-name
  and source/config-policy writers, complete audit and actual-writer race suite,
  real Package/current-grant projection, administrator ownerless assignment,
  retry semantics/lost-response tests and confirmation/status adapters. Detailed
  publication-contract section 8 remainder inventory is in `TICKET-05b1.md`.
  Internal callbacks must be replaced with qualified production integrations,
  using the same connection/transaction and common lock order.
- Public approve/retry still reject; PUBLIC apply-producing approval remains
  fenced until 05c. 05c installation polling/frozen apply/terminal slot release,
  credential-source 05a2b4 and parent acceptance remain fenced. Parent 05
  Status/checklist unchanged; 06 blocked. Independent 05b1 review required.

## 05b2 bounded implementation refinement

- 2026-10-14: Used the task-authorized coherent fallback as **05b2a**: actual SQL Package/current-owner projection, fresh internal approval authorizer sharing current config.allowed/Redis api_verify reads, and environment-first managed privilege-add/remove and account-deactivation helper writers. Real PostgreSQL tests execute the actual shared SQL writer functions and actual Package mappings, NOT full Flask helper/route or external policy/Redis writer races. Fixed 05b1 P2 with independent pg_blocking_pids observation before writer release (AUTOCOMMIT prevents cached observer snapshots).
- Qualified bounded cases: 30 new actual SQL projection/writer cases, 66 publication combined, 179 preparation aggregate; act 292 passed, 1 documented skip, mypy 51 files unchanged. Handoff `TICKET-05b2a.md`; independent review required. This is NOT full 05b2 or production writer-safety acceptance.
- **05b2b (next):** account deletion preserving multi-store behavior, users/views and remaining helper/account/role writers, config observation and policy writers, Redis API-key writers, remaining PackageAuth/name/source-policy writers and complete actual-writer audit/races; retry, explicit previewed administrator ownerless assignment and confirmation/status adapters remain fenced. Current external config/Redis reads are not sufficient serialization.
- PUBLIC apply-producing approval stays fenced until 05c; no installation polling/apply/slot-release, 05a2b4 source expansion or parent acceptance. Parent 05 Status/checklist unchanged; 06 blocked.

## 05b2b bounded implementation refinement

- 2026-10-14: Used task-authorized fallback as **05b2b1**: mandatory 05b2a P2 email string validation/whitespace normalization before managed/legacy branching, plus actual remove_privilege helper environment-first role/membership deletion in one transaction. Real PostgreSQL tests execute exact helper source bodies with real Sessions (not full Flask requests); 14 new cases across both initialization paths. Publication 80 passed; aggregate 193 passed; act 292 passed/1 documented skip, mypy 51 unchanged. Handoff `TICKET-05b2b1.md`; independent review required.
- **05b2b2 (next):** multi-store account deletion, views/composite/registration and remaining grant/security writers, Redis API-permission/constraint and config-policy observation coordination, retry, explicit previewed ownerless admin assignment and confirmation/status adapters remain explicitly fenced/unqualified. 05b1/05b2a inventories otherwise unchanged.
- PUBLIC apply-producing approval remains fenced until 05c; 05a2b4, installation polling/live apply/terminal release and parent acceptance unchanged. Parent Status/checklist unchanged; 06 blocked.

## 05b2b2 bounded implementation refinement

- 2026-10-14: Used task-authorized fallback as **05b2b2a**: actual users/views.delete_privilege composite membership removal/default-role fallback/role deletion now uses one environment-first SQL transaction and one commit. Exact view source body with real PostgreSQL Sessions qualified (not decorators/full authenticated Flask requests). 20 new cases across both initialization paths; publication 100 passed; aggregate 213 passed; act 292 passed/1 documented skip, mypy 51 unchanged. Handoff `TICKET-05b2b2a.md`; independent review required.
- **05b2b2b (next):** account deletion with multi-store behavior, profile/helper/composite/registration and remaining grant/security writers, Redis API-permission/constraint and config-policy observation coordination, retry, explicit previewed ownerless admin assignment and confirmation/status remain fenced/unqualified. 05b2b1 inventory unchanged except privilege deletion view now integrated.
- PUBLIC apply-producing approval remains fenced until 05c; no 05a2b4, live apply/installation polling/terminal release or parent acceptance. Parent Status/checklist unchanged; 06 blocked.

## Checkpoint: paused 2026-10-14

- The user paused the pipeline after 05b2b6. Branch `uv-implementation`, HEAD
  `fcea4ae64`.
- Completed and independently reviewed (all bounded): 05a1, 05a2, 05a2b, 05a2b2,
  05a2b3, 05a3, 05b1, 05b2a, 05b2b1, 05b2b2a, 05b2b3, 05b2b4, 05b2b5, 05b2b6.
  Handoffs: `TICKET-05a*.md` and `TICKET-05b*.md`.
- **Next on resume: 05c** — public apply-producing approval enablement,
  installation polling, live frozen apply, durable child-safe terminal
  reporting/slot release.
- Still fenced: remaining writer composites (profile/registration/default-role
  view composites; config save/observation + startup/executor dependency-input
  refresh; remaining PackageAuth/grant/name/source/dependency-policy writers)
  and **05a2b4** (real credential-source injection, private index/binding).
- Parent 05 acceptance is outstanding; 06 remains blocked.

## 05c bounded implementation refinement

- 2026-10-15: Exercised the task-authorized smallest-coherent-end-to-end fallback.
  **05c1** implements independent PostgreSQL approval-attempt polling, exact
  retained published-bundle frozen/no-build/offline materialization using existing
  host/drain/admission/child/non-root authority, journal attempt/fence/host/bundle/
  target binding, child-safe stopped terminal receipts and idempotent DB replay.
  It stops in maintenance and never claims normally verified application success.
  Running/uncertain work is not lease-reclaimed; subsequent attempt rollover and
  retry remain fenced. Public approval/retry stay false. See TICKET-05c.md.
- The supervisor approved explicit **lifecycle-fixture-only** DB baseline seeding
  after create_tables and before web startup when fresh current-image regression
  correctly exposed absent-singleton rejection in startup fix_api_keys. Only staged
  disposable wheel sources contain the wrapper/helper; production create_tables
  remains untouched. New PostgreSQL regression retains startup-migration rejection
  when the singleton is absent. No production managed-init/adoption authority is
  added; that remains later work. Proof-only advanced migration now follows current
  additive head, avoiding a fixture-only multiple-head branch. A host service proof
  snapshot now waits for apply before importing runtime; all assertions retained.
- Evidence: real pinned-uv internal prepare -> approve -> stopped frozen apply;
  exact retained bundle/inventory; no Redis/Celery dispatch/runtime imports; lost
  reporting replay without bytes/reinstall/recovery, live child exclusion, stale
  observations and target/update/corruption/crash-boundary fences. PostgreSQL 393
  passed; all 96 original current-image lifecycle scenarios passed; full native
  container 331 passed/no skips; act 294 passed/1 unchanged skip, mypy 52 files.
  Independent review required, not a parent or complete-05c acceptance claim.
- **05c2 (next):** full published schema/probe/all-role normal apply and DB-bound
  recovery/terminal reporting; complete verified running/history/recovery/override
  observations and unified status; boot/service polling and journal rollover;
  uncertain-work retry clearance; final browser/API enablement only after all
  operational and named writer prerequisites. Remaining 05b2b6 account/profile/
  registration/default-role/secret callers, config/dependency observation and
  PackageAuth/grant/name/source/dependency-policy closure/races remain fenced.
  Credential-source 05a2b4 and broader source/target/build policy remain fenced.
  Parent Status/checklist unchanged; 06 blocked.


## 05c2 qualified image-bridge refinement — 2026-10-15

- Supervisor approved the narrow explicitly opt-in image-core preparation bridge: every baseline distribution uses exact retained image-lock wheel bytes, with no core version/source change, rebuilding or requirement relaxation. Only public-PyPI registry additions on that operator-configured all-role baseline can be publicly approved. No production adoption/core-upgrade authority was added.
- To preserve the remaining writer fences, the supervisor approved experimental ingress restriction ONLY when DA_QUALIFIED_IMAGE_BASELINE is explicitly configured: health/readiness/status, existing-user login/logout, static assets and preview endpoints only; every other path rejects. Public approval/retry requires the existing administrator requester, unchanged SQL account authority/roles/current grants, and no ownerless assignment/owner transfer. Internal operator/plugin/helper writers remain unsupported and are NOT claimed safe. Default mode remains preview-only.
- Implemented real authenticated browser preview -> API approval/publication -> durable claim -> non-root exact frozen/no-build apply -> actual schema/core/web/all-12-role qualification -> durable normal receipt -> reported/applied/verified-running/readiness advancement. Success replay rechecks current bytes/role health, never repeats sync/schema. Safe explicit retry is ONLY the latest confirmed pre-mutation abort; uncertain/mutated/recovered/non-qualified retry remains rejected. Known reported normal/abort journals alone can roll over; stopped/uncertain work cannot.
- Evidence in TICKET-05c2.md and 05c2/evidence/accepted/: final image-installed pinned-uv full flow (including failure-visible abort/corruption, lost success replay), 447 PostgreSQL/preparation tests, original seven stopped-installation proof groups, 313 native act tests/one unchanged skip, mypy54, and all original 15 full-maintenance scenarios. Independent review required; accepted directory naming is not a verdict. Parent 05 Status/checklist unchanged; 06 blocked.
- Precisely fenced next: 05c3 DB-bound recovery/terminal reporting; boot/restart DB reconciliation and boot/service-supervised continuous polling (bridge-provisioned restart explicitly refuses, even before a local attempt journal); uncertain retry clearance; complete HostEnvironment/normal/history/core-only/override/config/dependency observation writers; remaining account/profile/registration/default-role/secret/auth/external-plugin composites, config/policy refresh and PackageAuth/name/source/dependency closure/races enumerated in TICKET-05c2.md; 05a2b4 credentials/private binding and all broader source/target/build/API/adoption/upgrade/backup work. No production writer-safety, full 05b/05c or parent-05 acceptance.

Next: 05c3 — DB-bound lifecycle recovery/terminal reporting; all other named fences remain.


## 05c3 bounded boot-observation refinement — 2026-10-15

- Used the task-authorized smallest coherent end-to-end fallback as **05c3a**, not a plan-only handoff: published DB cold-boot reconciliation under host/environment locks, durable monotonic HostEnvironment boot observation, stale current verification invalidation with normal/core-only history retained, and shared authorized/local runtime-status fields. Published boot remains independent maintenance: no solve/apply/schema/recovery/terminal replay/slot release. Qualified descriptor, initialized configured bridge even with descriptor missing, installation-host/published-journal or durable boot marker prevents fixture desired fallback; marker/host evidence refuses managed-disabled boot.
- Real image-installed proof covers authenticated publication before journal with descriptor missing, failed desired/history divergence, five successful cold boot observations with host generations, unavailable-table fail-closed maintenance and unchanged intent/attempt/material/journal/uv/schema-log digests. Final image bdeb98c07c9a8c81cab7ff56f8cf8bb7d80da2b7e897ea52915f1803e7964cba; full prior image-bridge eight PASS and stopped installation seven PASS. PostgreSQL473 (+26 intentional), act322 (+9)/one existing skip, mypy55 (+1); source611/host659 hashes; Ruff/whitespace pass. Handoff TICKET-05c3.md; independent review required.
- **05c3b (next, still fenced):** complete normal/history/core-only/override observation writers and unified live status/continuous health invalidation beyond boot unknown, qualified boot service/normal-receipt resumption, boot/service-supervised continuous preparation+attempt polling, broader subsequent-attempt/same-revision journal-rollover qualification, and config/dependency/policy observation refresh. Existing 05c2 health replay and known normal/confirmed-abort rollover remain unchanged bounded capabilities, not new completion claims.
- User explicitly keeps DB-bound independent core recovery/failed-recovered reporting, uncertain-work retry clearance, all named remaining 05b2b6 writer composites/callers/closure races, 05a2b4 credential/private-source scope, broader source/target/build/API/adoption/upgrade/backup and later tickets fenced. Parent 05 Status/checklist unchanged; no complete-05c3/05c/parent acceptance; 06 blocked.

Next: 05c3b — operational observation, qualified boot resumption, continuous polling and rollover qualification; all named exclusions remain fenced.


## 05c4 bounded continuous-operation refinement — 2026-10-15

- Used the task-authorized implemented fallback as **05c4a**: boot starts Supervisor-owned continuous non-root preparation/root lifecycle polling only after DB reconciliation; no Flask/Celery/Redis dispatch authority. Exact already DB-reported, no-pending desired normal receipts can cold-resume after fresh material/schema-usability/core/web/all-role qualification without sync/schema/setup/journal replay. Fresh fixture provisioning remains explicit; no production adoption added.
- Continuous turns retain only reported, same qualified live incarnations with fresh retained-byte/installed-material checks, avoiding periodic admission closure. Changed incarnations/lost-success reporting use full closed-admission health rechecks. Shared runtime fields and monotonic HostEnvironment live observations keep pending/divergence/failure/history distinct. Old known terminal is archived before subsequent permitted work; late reports never release another attempt slot. No uncertain journal clearance.
- Final pinned image 8de38ee79bcde59bade650faddf2e3a7336c1f9395b4827221f332a34281ce93: continuous end-to-end six PASS groups; original image-bridge eight PASS; published boot five reconciliations plus unavailable-table case; stopped installation seven PASS; original fixture boot25 PASS. PostgreSQL491 (+18 intentional); act327 (+5)/one existing skip, mypy56 (+1); source613/host663 inputs verified. Handoff TICKET-05c4.md; independent review required, not full05c4/05c/parent acceptance.
- **05c4b next, still fenced:** universal live status/health/HostEnvironment and normal/history/core-only/override writers outside this explicit supervised published-normal path, unchanged-process periodic protocol-health guarantee and broad success UX; cold unreported/lost-success receipt and restricted-service resumption; broader subsequent/same-revision/crash rollover qualification; complete config/dependency/policy observation refresh.
- DB-bound independent core recovery/failed-recovered reporting, automatic data restore, uncertain retry/clearance/schema replay, all named remaining 05b2b6 writer composites/callers/closure races, 05a2b4 real credentials/private-source binding, broader source/target/build/API/adoption/upgrade/backup and later tickets remain excluded. Exact writer inventory is repeated in TICKET-05c4.md. Parent05 Status/checklist unchanged; 06 blocked.

Next: 05c4b — universal live observation/status and remaining resumption/rollover qualification; all named exclusions remain fenced.


## 05c5 bounded implementation refinement

- 2026-10-15: Used the task-authorized implemented fallback **05c5a**, not full 05c5: immediate qualified failed frozen apply restores the exact retained image core once in stopped mode, with a durable attempt-bound recovery marker/identity and child-safe failed terminal report. Environment-first reporting serializes core-only/unknown runtime generation with publication; desired intent and historical normal remain separate. No restricted administration/readiness is claimed. Real pinned-uv failure -> one core recovery -> lost DB report/replay, unchanged intent/schema, and actual administrator retry rejection pass. PostgreSQL505 (+14 intentional); act333 (+6)/one unchanged skip/mypy56; original boot/continuous/image-bridge/installation proofs rerun on full final image. See TICKET-05c5.md and 05c5/evidence/accepted/. Independent review required.
- **05c5b (next)** remains fenced: universal live observation/status and all-role readiness/lost-success rechecks beyond the explicit bridge, cold unreported/uncertain receipt recovery, tables-unavailable boot restoration, restricted core-only service resumption, broader failed/recovered/crash rollover, explicit authorized uncertain-work clearance with schema/setup review, and config/dependency/policy observation refresh. Immediate stopped restoration is not those capabilities.
- ALL named remaining 05b2b6 writer composites/callers/actual-writer races, 05a2b4 credential-source decision/injection/private binding, broader source/target/build policy and later operations remain fenced exactly as listed in TICKET-05c5.md. No production writer-safety, complete05c5/05c/parent05 acceptance; parent Status/checklist unchanged; 06 blocked.

Next: 05c5b

## 05a2b4 bounded private-wheel snapshot refinement — 2026-10-15

- Task explicitly authorized a smallest coherent end-to-end fallback. Supervisor approved **HTTPS private-index wheel snapshots**, not an authenticated uv proxy: exact configured index paths, TLS verification, no redirects, owner-only independently source-scoped operator references; credentials never enter uv/build environments or retained project/lock/bundles. Only explicitly bound direct/transitive names enter a local offline snapshot. Pinned uv 0.12.21 then solves original declarations and cold-cache frozen-syncs retained wheels with no public fallback. New private source bindings appear in immutable redacted readiness warnings.
- Implemented and qualified bounded **05a2b4a**, not complete 05a2b4/source parity. Real authenticated HTTPS install, non-empty exact core, two private indexes sharing one host with distinct credentials, changed-source reference denial before requests, direct/transitive binding, public namesake denial, 401/403, redirect isolation, userinfo/token/query redaction, TLS and relocated retained frozen sync pass (nine groups). Existing real Docker all-worker-exit candidate-secret cleanup passes (four groups). PostgreSQL509 (+2 real retained preview cases); act372 (+39)/one unchanged skip, mypy57 (+1); focused44, source hashes/byte verification/redaction scan and lint pass. Handoff: TICKET-05a2b4.md; evidence: 05a2b4/evidence/accepted/. Independent review required.
- **05a2b5 (source remainder)**: HTTPS Git refs/subdirectories/SHA identity and scoped credential injection; private sdists/source builds/build-dependency auth; candidate-secret claim consumption by this builder and atomic candidate-to-committed promotion/rotation/revocation; authorized updater reuse/public source-policy adapters and production private dispatch; mixed public/private solving/general index ordering; direct URLs/uploaded ZIPs and broader source/target/build policy. Existing public/container gates remain public-only; operator references are not candidate promotion.
- **05c5b**, ALL precisely named 05b2b6 writer composites/callers/closure races in TICKET-05a2b4.md, production initialization/adoption, broader API/update/remove/config import/coherent upgrade/rollback/backup/image migration and tickets06+ remain fenced. No parent05/complete05a2b4/05a/05b/05c acceptance. Parent Status/checklist unchanged.

Next: 05a2b5 — remaining credential lifecycle/source support; 05c5b and named writer fences unchanged.


## 05b2b9 final convergence and approved readiness refinement — 2026-10-15

All DB-affecting built-in managed-path writers from the exact 05b2b8 remainder
are integrated with explicit environment-locked SQL ownership; plugin-only and
non-DB effects stay postcommit. See TICKET-05b2b9.md and its accepted evidence:
196 new PostgreSQL cases; complete suite963; private-index9/recovery9/continuous6/
image-bridge8 PASS; act372/one existing skip/mypy57. No later capability below
is implemented by this slice.

Supervisor explicitly EXTENDED the readiness rule: (a) legacy operation fences,
(b) contract-permitted plugin/external non-DB effects, (c) ticket08 config, and
(d) by-design restricted-experiment fences mapped to later tickets do not block
restricted parent05 acceptance. This supersedes historical remainder-as-blocker
wording above ONLY for readiness, not implementation or acceptance claims.
05c5b maps to tickets13/20 and operational breadth beyond the parent checklist;
05a2b5 maps to tickets10/11/12 by source, with ZIP breadth in09.

**PARENT 05 READY FOR ACCEPTANCE on that explicitly extended basis, NOT yet
accepted.** Parent Status/checklist unchanged; supervisor accepts only after
independent review of the integrated writers AND this mapping. **06+ remain
blocked until that acceptance.** No branch/new-system/later-ticket work.

### Complete retained-fence map


Paths are relative to `docassemble_webapp/docassemble/webapp` unless explicit.
No capability in this section is newly implemented or accepted by this slice.

### (a) By-design legacy operation fences

- `utils/helpers.install_zip_package` -> **09** (ZIP/playground);
  `install_git_package` -> **10** (authenticated Git);
  `install_pip_package` / `uninstall_package` -> **06** (registry update/remove),
  **12** (direct URLs). Their legacy Package/PackageAuth/source writers bypass
  immutable publication and remain rejected; existing supported registry installs
  use the canonical publication path, never these legacy writers.
- `packages/views.update_package`, `packages/api.api_package` -> **06/07**
  (update/remove/general API); `develop/views.playground_packages` install branch
  -> **09**. Existing unsupported/prepare/ZIP rejections remain first.
- `update.clear_invalid_package`, `clear_invalid_package_auth`,
  `check_for_updates`, `add_dependencies`, `fix_names`, `install_package`,
  `uninstall_package`, `update_versions` -> **06/14/16** (update/remove,
  coherent upgrade, legacy adoption). Legacy cleanup/discovery/name/source/
  installed-version/pip reconciliation stays superseded and rejected.
- `tasks/tasks.update_packages`, `reset_server`, direct `update` CLI -> **06/14/19**
  (update/remove, core upgrade, replacement-image migration). Direct
  `create_tables` schema CLI -> **13/14/16/19** (repair/upgrade/adoption/migration);
  no managed schema-entrypoint rejection is removed.
- `create_tables.populate_tables` package rewrite / `add_dependencies` pass ->
  **06/16**. Managed skip remains; only its built-in account/default-role pass
  was integrated previously.

### (b) Exact contract-permitted plugin/external effect boundary

These effects stay OUTSIDE the built-in SQL transaction. No transactional or
arbitrary custom SQL guarantee is inferred from their callable names.

- Plugin implementations dispatched by `users/managed_effects.sql_hook` through
  `manage_global_objects`, `manage_chat_logs`, `manage_tts_objects`,
  `manage_email_server_objects`, EXCLUDING exact built-ins
  `docassemble.webapp.daglobal.hooks`, `docassemble.webapp.monitor.hooks`,
  `docassemble.webapp.tts.hooks`, `docassemble.webapp.emailserver.helpers`.
  Only those exact built-in SQL implementations are mandatory/precommit.
- Arbitrary receivers of `docassemble_flask_user.signals.user_registered.send`,
  `user_logged_in` and `user_changed_password` remain external plugin dispatch;
  qualification covers only `users/signals.on_register_hook`, `on_user_login`
  and `on_password_change` built-ins. Custom receivers may commit or perform
  external effects independently and are not qualified here.
- `emails.send_invite_email`, `docassemble_flask_user.emails.send_invite_email`,
  `docassemble_flask_user.views._send_registered_email`, `_do_login_user`,
  `login_user`, `logout_user`, `set_language`, `get_session`, `update_session`,
  `delete_session_info`, Flask session publication/clear and response cookie
  mutation are postcommit effects, not SQL-atomic delivery/authentication.
- `utils/helpers.manual_checkout`, `files/savedfile.SavedFile.delete`,
  `get_server_redis` / `get_server_redis_user` store deletion and `r` / `r_user`
  cleanup are postcommit. Deletion's existing pending/complete/failed record is
  not a resumable external cleanup protocol. No plugin/external retry added.

### (c) Exact configuration observation/import fences -> ticket 08

- `admin/views.config_page` cloud/local save and fail-closed observation -> **08**.
- `startup.initialize` / `read_config`,
  `docassemble_base/docassemble/base/read_config.py`, `Docker/initialize.sh`
  dependency/index/PYTHONPACKAGES observation -> **08**.
- Executor `docassemble_executor.preparation`, `controller`, `cli`
  dependency/policy refresh before import/work -> **08**.
- `config.allowed`, `ALLOW_UPDATES`, `DEVELOPER_CAN_INSTALL` live-policy
  observation/reload -> **08**.

No shared cross-process snapshot/dirty-state authority protocol is invented.
Observed file/operator changes are not claimed serialized with SQL publication;
restricted ingress and frozen approved-input/drift boundaries remain.

### (d) Supervisor-approved restricted-experiment fences -> later tickets

**05c5b operational remainder** (NOT implemented):

- Universal live/status/HostEnvironment/normal/history/core-only/override writer
  closure, all-role/lost-success rechecks outside the explicit supervised
  published-normal bridge, unchanged-process periodic protocol-health guarantee
  and broader success UX -> **13/20** (repair/cache-management operational
  breadth beyond parent 05's restricted checklist).
- Cold unreported/lost-success/uncertain receipt recovery, interrupted-apply
  recovery and management-tables-unavailable boot restoration -> **13**
  (failed-installation repair).
- Restricted core-only service resumption -> **13**; immediate stopped-core
  restoration is not restricted administration/availability.
- Broader failed/recovered/crash/subsequent/same-revision journal rollover ->
  **13/20** (repair and retained-material/cache lifecycle qualification).
- Explicit authorized uncertain-work clearance, schema/setup review/replay and
  automatic data restore -> **13**; no inferred safe retry or schema clearance.
- Config/dependency/policy observation refresh in that inventory -> **08**, as (c).

**05a2b5 source/credential remainder** (NOT implemented):

- HTTPS Git refs/subdirectories/SHA identity and scoped authentication/injection
  -> **10** (authenticated HTTPS Git).
- Private sdists/source builds/build-dependency authentication -> **11**
  (private indexes); private wheel snapshot proof does not qualify builds.
- Candidate-secret claim consumption by this builder, atomic candidate-to-
  committed promotion/rotation/revocation and authorized-updater credential reuse
  -> **10/11/12**, according to source. Operator references are not promotion.
- Production private source-policy/public adapters and dispatch -> **11**;
  existing public/container gates remain public-only.
- Mixed public/private solving, general index ordering, explicit private/public
  binding breadth -> **11**; direct URLs -> **12**; uploaded ZIPs -> **09**.
- Broader source/target/build-policy qualification -> **10/11/12**, scoped to
  those later sources, not inferred from retained private-wheel evidence.

**Other unchanged later-operation fences** (NOT implemented): production managed
initialization/automatic legacy adoption -> **16**; general managed registry
update/remove -> **06**; general API -> **07**; config import -> **08**; coherent
core upgrade -> **14**; historical rollback -> **15**; complete managed backups
-> **17**; same-target cold restore -> **18**; replacement-image migration ->
**19**; safe cache management -> **20**; local/cloud/full pre-change checkpoints
-> **21/22/23**; broad experimental-container qualification -> **24**. These are
not silently enabled by parent-05 readiness.

Next: independent parent-05 acceptance review; 06 remains blocked.


## Parent 05 acceptance

- 2026-10-15: PARENT 05 ACCEPTED (bounded experimental scope). Final slice 05b2b9 at
  `b8a28695a`; independent review OK with notes and explicit PARENT 05 READY verdict.
  Accepted remainders (supervisor ruling): (a) by-design legacy package/update/remove
  fences; (b) contract-permitted plugin/external non-DB effects; (c) ticket-08 config
  import/observation; (d) restricted-experiment fences mapped to later tickets
  (05c5b -> 13/20 + operational breadth; 05a2b5 -> 10/11/12). Next: ticket 06.
