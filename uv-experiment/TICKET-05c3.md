# 05c3: Published boot observation and reconciliation (bounded 05c3a)

Date: 2026-10-15 UTC. Branch: `uv-implementation`. Baseline:
`d94b1d76c838e2435a754899588e76b86eb3c07b`.

**Task-authorized smallest coherent end-to-end fallback, NOT complete 05c3.**
Implemented a real DB-published cold-boot observation path and shared truthful
runtime projection. This is not normal-service restart, continuous dispatch,
complete observation-writer qualification, recovery or parent-05 acceptance.
Independent review required. Parent 05 Status/checklist unchanged; 06 blocked.

## Objective and bounded refinement

05c2 explicitly refused provisioned-bridge restart. Replace that blanket refusal
with independent environment-first DB reconciliation and durable HostEnvironment
observation while normal admission stays closed. A cold boot cannot reuse old
process health, silently fall back to fixture desired.json, replay schema/setup,
re-solve, auto-apply pending intent or clear uncertain work.

The full assigned 05c3 crosses additional normal/core-only/override writers,
boot resumption and continuous poller supervision. The task expressly permits a
bounded implemented fallback. This slice implements **05c3a only**; the exact
05c3b operational remainder is fenced below and appended to TICKET-05-SPLIT.md.
Existing 05c2 public qualification, retry scope and ingress restrictions are not
broadened. No writer composite or credential-source work is included.

## Implementation

### Independent published boot reconciliation

`boot_observation.py` is loaded before application interpreter/probe/schema/sync.
Boot holds MutationLock and proves RuntimeLock child exclusion before observing.
Routing selects this path for an existing qualified descriptor, a configured
bridge with durable initialization even if that descriptor disappeared, any
installation-host evidence, a published apply/materialization journal, or a prior
published-boot marker. Only the explicit fresh fixture bootstrap may initially
run with its configured descriptor not yet provisioned. The ordinary unconfigured
fixture boot path remains unchanged; arbitrary operator deletion of both config
and all durable evidence is not a supported adoption/restore protocol.

Before DB access, boot writes root-owned `published-boot.json`. Detection survives
DB outage and later descriptor removal, and managed-disabled startup refuses this
marker/installation-host evidence. Connection uses independent config parsing or
restricted DA_PREPARATION_DATABASE_URL; no Flask, application config imports,
Celery or Redis dispatch authority. SQL diagnostics/credentials are not exposed
on reconciliation failure. Missing management tables/intent remain independent
maintenance, never a pretend empty managed baseline.

Under the same EnvironmentState lock as publication, reconciliation:

- Reads DB desired revision and active/latest desired attempt, validates published
  revision linkage, and never derives DB intent from local installed inventory.
- Invalidates a former normal/core-only/override observation to current `unknown`.
  Last normal remains historical; prior runtime kind/core-only recovery evidence
  is retained separately. **historical_recovery_core exposes ONLY a previous
  core-only observation, never a prior normal/override runtime's core_baseline.**
- Increments runtime_generation only when changing that observation, invalidating
  prepared previews. Repeated already-unknown boot observations do not needlessly
  invalidate previews. HostEnvironment generation advances per observation under
  the environment-first lock, with stable local host identity and DB observation
  time, desired/target, active/latest attempt, local applied/journal/receipt binding,
  history and divergence. Host rows are now actually written, not storage only.
- Does not change publication generation, desired pointer, contributions, Package
  ownership, immutable revisions, attempt phase/fence/terminal, or active slot.
  A local normal receipt is evidence to inspect, NOT current health or authority
  to release a running slot. Recovery evidence is retained, not resumed/reported.

The independent ingress/status endpoint remains alive in maintenance. Normal
readiness, locked convergence and recovery availability are false, including
matching local applied/DB desired identities. DAUPDATEONSTART or forced recovery
does not authorize an application/recovery action in this bounded published path.
A DB error returns a fixed redacted maintenance diagnosis. No retry/recovery loop.

### Shared truthful status fields

Shared `runtime_status()` in the dependency-light semantic module projects desired,
current normally verified identity, historical normally verified identity,
core-only recovery, development override, materialized/unknown and monotonic runtime
generation. Authorized publication status and independent published-boot status
use the same fields; failure stage/reason uses one allowlisted projection.
Boot additionally distinguishes pending, uncertain active execution, failed and
not-verified terminal work, plus local journal/recovery evidence. Historical
applied bytes do not become verified-running or recovery availability on reboot.

This is a shared projection plus boot/unknown/history writer, **not completion of
all normal/history/core-only/override writers or universal live health status**.
05c2 lost-success material/role health recheck is preserved and requalified, not
replaced with a DB-only success claim. No general browser success UX is enabled.

## Verification and evidence

Qualified evidence: `05c3/evidence/accepted/`. This requested directory name is
**not an independent acceptance verdict**. Exact argv, exit codes and outputs are
retained as `*-command.json` / `*.log`; capture.py uses Python subprocesses, no
shell redirection/heredocs. Trailing whitespace in captured output is normalized.

Final current-source image: `da-uv-05c3:local`,
`sha256:bdeb98c07c9a8c81cab7ff56f8cf8bb7d80da2b7e897ea52915f1803e7964cba`.
This is a working-tree build, not a claim of the later documentation commit.

Exact commands (all exit 0), from repository root:

```sh
python3 uv-experiment/05c3/evidence/accepted/capture.py build docker build --progress=plain -f uv-experiment/fixture/Dockerfile -t da-uv-05c3:local --build-arg DA_REPO_COMMIT=d94b1d76c838e2435a754899588e76b86eb3c07b+05c3-working-tree .
python3 uv-experiment/05c3/evidence/accepted/capture.py image-bridge env PROOF_IMAGE=da-uv-05c3:local PROOF_CONTAINER_NAME=da-05c3-image-bridge PROOF_EVIDENCE_DIR=uv-experiment/05c3/evidence/accepted/image-bridge python3 uv-experiment/run-image-bridge-proof.py
python3 uv-experiment/05c3/evidence/accepted/capture.py boot env PROOF_IMAGE=da-uv-05c3:local python3 uv-experiment/run-published-boot-proof.py
python3 uv-experiment/05c3/evidence/accepted/capture.py installation env PROOF_IMAGE=da-uv-05c3:local PROOF_CONTAINER_NAME=da-05c3-stopped-proof PROOF_EVIDENCE_DIR=uv-experiment/05c3/evidence/accepted/installation python3 uv-experiment/run-installation-proof.py
python3 uv-experiment/05c3/evidence/accepted/capture.py postgres env PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
python3 uv-experiment/05c3/evidence/accepted/capture.py native uv run --project uv-experiment/executor --frozen pytest -q -p no:cacheprovider uv-experiment/executor/tests/test_boot_observation.py uv-experiment/executor/tests/test_image_bridge.py
python3 uv-experiment/05c3/evidence/accepted/capture.py source python3 uv-experiment/05c3/evidence/accepted/verify-source.py
python3 uv-experiment/05c3/evidence/accepted/capture.py act act workflow_dispatch -W .act/checks.yml
```

Results:

- Build succeeded; BuildKit clips its own large manifest output, as previously.
- Original full image-bridge proof: **all eight PASS groups**, no assertion/count
  reduction. Actual login/CSRF/API credential prepare/approve, abort/safe retry,
  exact non-root frozen/no-build sync, schema/probe/all-12-role readiness, lost
  success replay without repeated sync/schema and corrupt second publication.
- New boot proof: actual authenticated published-pending intent **before local
  attempt journal**, with descriptor removed before its first reconciliation;
  actual failed second desired revision with historical normal; both cold reboot
  into DB-derived maintenance status and durable host generations 1/2, preserving
  desired/slot/attempt/intent/journal/installed inventory/uv/schema-log digests.
  Repeated descriptor-removal detection passes. Temporarily renamed management
  table produces independent failure maintenance; restoration re-observes pending
  at host generation 3. Five successful real boot reconciliations and one
  unavailable-table failure, not stubbed startup or source-injected executor code.
- Original stopped installation proof: **seven PASS groups**, including no runtime
  imports or broker authority, child exclusion, lost-report replay, corrupt bytes,
  target/update gates and uncertain crash boundaries remaining fenced. As before,
  this small proof uses pinned image tooling and current read-only mounted source.
- PostgreSQL/preparation: **473 passed in 73.70s** (447 -> 473 intentionally: 13
  boot-observation cases on both fresh/upgrade schemas). Real environment-first
  preview invalidation, pending/uncertain/no-slot-release, normal/core-only/
  override/unknown histories, terminal failures and absent desired rollback.
- Native focused: **14 passed in 0.99s**. Act: **322 passed, 1 skipped in 32.68s**,
  mypy **no issues in 55 source files**. Intentional 313 -> 322 (+9 new native
  cases), 54 -> 55 (+boot_observation). Existing Supervisor skip unchanged;
  no exclusions/count assertions relaxed. Two existing direct fixture-boot
  guard tests now expect the explicit independent-reconciliation requirement.
- Provenance: **611 image-COPY source inputs**, **659 host/build inputs** hashed
  and matched. Boot and full bridge use image-installed code; repository mount
  supplies proof drivers only. No runtime-source injection for those proofs.
- Changed-source Ruff E9/F: all checks passed (`lint-command.json`, `lint.log`).
  Source/documentation whitespace check passes (`diff-check.log`).

Full image-core wheels are not duplicated into Git evidence, as in 05c2. The
full retained store remains in the disposable proof containers; descriptors,
lock/manifest/material/inventory/attestation and actual hash-qualified apply are
retained. These tests do not claim new production baseline adoption.

No new continuous-polling proof or broader same-revision rollover qualification
is claimed. Existing 05c2 confirmed-abort -> authorized retry already exercises
two attempts of one revision and its lost-success replay; it is regression
coverage, NOT completion of the requested subsequent-attempt operational work.
The original 96 lifecycle scenarios were not all rerun for this bounded branch;
act plus the real fresh all-role and cold-boot proofs are this run's evidence.

Initial development hit Docker VM disk exhaustion before preparation readiness.
Supervisor authorized unused build-cache pruning, then removal of ONLY this
run's intermediate da-05c3 containers/image/cache to rebuild after self-review
corrections (historical recovery label and missing-descriptor detection). All
intermediate/failure bytes remain untracked in `05c3/evidence/development/`.
No pre-existing images/containers/named volumes or historical accepted evidence
were removed. Exact Docker space/cleanup commands and before/after reports are
retained. All required proofs/gates above reran after the final installed build.

## Precisely fenced remainder

### 05c3b operational remainder — NOT implemented here

- Boot/service-supervised **continuous** preparation and installation polling.
  Existing manually invoked CLI loops are not boot daemon integration. No Flask,
  Celery or Redis dispatch authority may substitute for durable work discovery.
- Complete normal/history/core-only/override HostEnvironment and runtime writers,
  unified local/authorized DB live status beyond this boot/unknown observation,
  continuous readiness invalidation/health monitoring and broad success UX.
  05c2's explicit attempt/report/replay health checks remain its bounded guarantee.
- Qualified cold-boot normal/restricted-service resumption and healthy normal
  receipt requalification/reporting after process loss. This path deliberately
  **stays maintenance**; it does not run sync/schema/recovery or resume polling.
- Subsequent-attempt journal rollover qualification beyond the existing known
  reported-normal/new-publication and confirmed-abort/same-revision retry paths.
  No new attempt/receipt archival or uncertain journal clearance was added.
- Current config/dependency/policy observation refresh before imports/work;
  managed config/import and arbitrary operator edit serialization stay fenced.

### Explicit exclusions retained from 05c2 / task

- **DB-bound core recovery and failed/recovered terminal reporting**, including
  one-attempt independent restoration with DB tables unavailable. Existing local
  recovery remains independent, but is NOT bound to published DB attempts here.
- **Uncertain-work retry/clearance**, schema/setup replay after uncertainty,
  automatic recovery/retry or heartbeat release. Mutated/failed-stopped/blocked/
  recovered/non-qualified work cannot authorize public retry or a new mutation.
- Remaining **05b2b6 writer composites/caller closure and actual-writer races**:
  users/views edit_user_profile_page, user_profile_page, user_add, custom_register,
  invite, add_privilege, manage_account, deletion/MFA/email/multi-commit composites;
  users/helpers login_or_register; users/signals on_user_login/on_register_hook/
  on_password_change; MySignInForm.validate LDAP/default-role creation;
  auth/views.oauth_callback; phonelogin/views.phone_login_verify; create_tables
  get_role/get_user/populate_tables account/default-role/fix-role passes and their
  fix_secret/substitute_secret/cookie/session/auth/external-hook/plugin composites.
- admin/views.config_page; startup.initialize/read_config; base/read_config and
  Docker/initialize.sh dependency/index/PYTHONPACKAGES observation; executor
  preparation/controller/CLI observation refresh; config.allowed, ALLOW_UPDATES,
  DEVELOPER_CAN_INSTALL and name/source/dependency policy writer serialization.
  Opt-in ingress rejection is not qualification of internal/helper/operator writers.
- Remaining PackageAuth/grant/name/source/dependency-policy closure: utils/helpers
  install_zip/git/pip/uninstall; packages/views.update_package and api.api_package;
  develop/views.playground_packages ZIP caller; update clear_invalid_package/
  clear_invalid_package_auth/check_for_updates/add_dependencies/fix_names/
  install_package/uninstall_package/update_versions; tasks.update_packages/
  reset_server/direct update CLI; create_tables package rewrites/add_dependencies.
- **05a2b4** real credential-source decision/injection, private indexes/bindings,
  other sources/targets/build policies; complete production initialization/adoption,
  installed-material/store qualification beyond the explicit image bridge; update,
  remove, config import, coherent upgrade/rollback/adoption/backup/image migration,
  general managed API breadth and later tickets. No historical grant/intent restore.

## Resolution and limits

Implemented and locally checked the task-authorized **bounded 05c3a** fallback,
with real image-installed boot reconciliation evidence. No claim of complete
05c3/05c/05b/parent-05, recovery, uncertain retry, writer composite or 05a2b4
acceptance. Independent review remains required. No branch switch or new-system
edits. Parent tracker Status/checklist unchanged. Requested diff/change-summary
artifacts are untracked under `ticket-05c3/`.

Next: **05c3b — complete operational observation writers, qualified boot resumption,
continuous durable-work supervision and subsequent-attempt rollover qualification**;
DB-bound recovery, uncertain retry clearance, remaining writers and 05a2b4 remain fenced.
