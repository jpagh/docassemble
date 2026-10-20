# 05c5: Attempt-bound stopped core recovery (bounded 05c5a)

Date: 2026-10-15 UTC. Branch: `uv-implementation`. Baseline:
`ac5499e0dd22539f6e048c5083462ccc73c9ef64`.

**Task-authorized smallest coherent end-to-end fallback, NOT complete 05c5.**
Failed qualified frozen apply now makes one immediate independent core-only
restoration attempt, records its identity and reports the original attempt as
failed. Recovery remains **stopped**, not available restricted administration.
Universal live observation and uncertain-work clearance remain **05c5b** below.
Independent review required; `accepted/` is the requested evidence destination,
not an acceptance verdict. Parent 05 Status/checklist unchanged; 06 blocked.

## Objective and bounded refinement

05c4a connected supervised durable polling and exact reported-normal resumption.
The assigned operational remainder also crosses universal observation writers,
uncertain schema/setup clearance and restricted recovery resumption. This run
uses the expressly authorized fallback to implement only **DB-bound immediate
stopped core recovery + truthful recovery reporting**, not those other seams.
No writer-composite, source-credential or desired-intent authority is added.

## Implementation

### One immediate core-only restoration, before terminal reporting

Only a qualified image-bridge attempt that reached the durable mutation marker
can take this path. Gate/context/drain failures and confirmed pre-sync aborts do
not trigger it. The poller retains MutationLock, drains any qualification roles,
proves descendant-safe RuntimeLock exclusion, and validates the selected image
and retained core baseline against the attempt's qualified image identity.
It reuses the existing image-manifest/hash-qualified core project/lock, not the
failed candidate, installed inventory or a new solve.

The existing controller restoration now supports a stopped mode. It persists
`recovery_attempt_started` BEFORE restoration, performs dedicated non-root
`uv 0.12.21 sync --frozen --no-build --reinstall` with offline/no-config settings,
checks exact installed core material, isolated core probe and database schema
**usability**, and records succeeded/failed recovery. It never calls
create_tables, user hooks, schema/setup replay or data restore. It does not start
restricted administration, open admission, create normal readiness or advance
the applied revision. The original controller's serving-recovery default is
unchanged and requalified by the original 25 fixture boot scenarios.

Restoration itself reads no management tables and requires no Flask/application
imports in the executor. An immediate DB reporting outage cannot prevent local
restoration. This does NOT implement recovery from a cold uncertain journal or
prove a management-table-outage boot restoration path: those remain fenced.
Missing target authority or child exclusion leaves the attempt uncertain/occupied.
An interrupted or already attempted recovery is not automatically attempted again.

### Durable attempt-bound failure and serialized recovery identity

After recovery completion AND fresh exclusive child exclusion, the root journal
stores the original attempt/revision/fence/host/bundle/target identity, original
allowlisted failure, `failed-stopped`, `normal_verified=false`, child-safe terminal
evidence and separate recovery identity `<attempt UUID>:core:1`, attempt count,
result and exact image-target/core-bundle digest/inventory. Replay checks journal
binding AND recovery phase/result/count and never repeats sync, recovery, setup
or schema. A crash before this complete receipt stays fenced; it is not permission
to reconstruct/clear uncertain work from a heartbeat or delete history.

Reporting locks EnvironmentState before the matching attempt, as publication
already does. It writes terminal **failed**, never succeeded, releases only its
matching occupied slot, and increments runtime_generation for a current recovery
observation. A succeeded restoration projects `core-only` with exact recovery
identity; a failed restoration projects `unknown`. Desired pointer, publication
generation, contributions, grants and immutable revisions remain untouched.
Last normal remains history, never verified-running after recovery. Newer runtime
observations are not overwritten, and idempotent late reporting cannot release a
later attempt's slot.

Shared authorized/local status now exposes `recovery_id`, `last_recovery_id` and
`last_recovery_result`, separately from desired/current normal/history and failure.
The existing supervised HostEnvironment projection uses these shared fields.
`historical_recovery_core` still exposes **only a previous core-only observation**,
never a normal/override runtime's core baseline. Because this slice is stopped,
`normal_ready=false` and `recovery_available=false` even after successful core
restoration. No continuous live-health or restricted-administration availability
claim is made for restored core bytes.

### Retry and journal boundaries are not widened

Public retry still requires the existing confirmed pre-sync abort. Child-safe
recovered failure is **not** review/clearance of uncertain schema/setup work.
The container proof invokes the actual runtime-side qualified retry module with
fresh real administrator SQL authorization and proves it rejects recovered work;
PostgreSQL adapter regressions do the same. No authorized uncertain retry
clearance, recovered/failed rollover, cold recovery resumption or historical retry
is implemented. No schema/setup uncertainty is silently treated as safe.

## Verification and evidence

Qualified evidence: `05c5/evidence/accepted/`. Exact argv and exit codes are in
`*-command.json`; merged output is in corresponding `*.log` (trailing whitespace
normalized by capture.py). No shell heredocs or redirection were used.

Final **full current-source fixture build**, not a derived image:
`da-uv-05c5:local`,
`sha256:693e4479f9f637d46da8b45a5cc62fdd7b6899baa8a9fe5117f2293ff563637f`.
This is a working-tree build, not a claim to identify the later documentation
commit. Source verification matches **614** image-COPY source inputs and hashes
**666** host/build inputs. Recovery/continuous/image-bridge/published-boot proofs
use image-installed executor code; read-only repository mounts supply proof
controls, not injected runtime implementation. The existing stopped-installation
proof uses pinned image tooling plus current read-only mounted executor source.
Full third-party image wheels are not duplicated into Git evidence; exact
manifests, locks, material maps, inventories, receipts and invocation logs are.
Task-owned disposable stores were removed only after evidence capture with
supervisor approval; they are not a new durable-backup claim.

Exact final qualification commands from repository root (all exit 0):

```sh
python3 uv-experiment/05c5/evidence/accepted/capture.py build docker build --progress=plain -f uv-experiment/fixture/Dockerfile -t da-uv-05c5:local --build-arg DA_REPO_COMMIT=ac5499e0dd22539f6e048c5083462ccc73c9ef64+05c5-working-tree .
python3 uv-experiment/05c5/evidence/accepted/capture.py recovery env PROOF_IMAGE=da-uv-05c5:local PROOF_CONTAINER_NAME=da-05c5-recovery-qualified PROOF_EVIDENCE_DIR=uv-experiment/05c5/evidence/accepted/recovery PROOF_FLOW_ACTION=recovery python3 uv-experiment/run-image-bridge-proof.py
python3 uv-experiment/05c5/evidence/accepted/capture.py continuous env PROOF_IMAGE=da-uv-05c5:local PROOF_CONTAINER_NAME=da-05c5-continuous PROOF_EVIDENCE_DIR=uv-experiment/05c5/evidence/accepted/continuous python3 uv-experiment/run-continuous-work-proof.py
python3 uv-experiment/05c5/evidence/accepted/capture.py image-bridge env PROOF_IMAGE=da-uv-05c5:local PROOF_CONTAINER_NAME=da-05c5-image-bridge PROOF_EVIDENCE_DIR=uv-experiment/05c5/evidence/accepted/image-bridge python3 uv-experiment/run-image-bridge-proof.py
python3 uv-experiment/05c5/evidence/accepted/capture.py boot env PROOF_IMAGE=da-uv-05c5:local PROOF_EVIDENCE_DIR=uv-experiment/05c5/evidence/accepted/boot PROOF_PENDING_CONTAINER=da-05c5-boot-pending PROOF_FAILED_CONTAINER=da-05c5-image-bridge python3 uv-experiment/run-published-boot-proof.py
python3 uv-experiment/05c5/evidence/accepted/capture.py installation env PROOF_IMAGE=da-uv-05c5:local PROOF_CONTAINER_NAME=da-05c5-stopped-proof PROOF_EVIDENCE_DIR=uv-experiment/05c5/evidence/accepted/installation python3 uv-experiment/run-installation-proof.py
python3 uv-experiment/05c5/evidence/accepted/capture.py fixture-boot python3 uv-experiment/scenarios/boot.py --image da-uv-05c5:local --evidence-dir uv-experiment/05c5/evidence/accepted/fixture-boot
python3 uv-experiment/05c5/evidence/accepted/capture.py act act workflow_dispatch -W .act/checks.yml
python3 uv-experiment/05c5/evidence/accepted/capture.py postgres env PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
python3 uv-experiment/05c5/evidence/accepted/capture.py native uv run --project uv-experiment/executor --frozen pytest -q -p no:cacheprovider uv-experiment/executor/tests/test_core_recovery.py uv-experiment/executor/tests/test_installation.py uv-experiment/executor/tests/test_boot_observation.py uv-experiment/executor/tests/test_durable_work.py
python3 uv-experiment/05c5/evidence/accepted/capture.py source python3 uv-experiment/05c5/evidence/accepted/verify-source.py
python3 uv-experiment/05c5/evidence/accepted/capture.py lint uvx ruff check --select E9,F docassemble_base/docassemble/base/managed_preparation.py uv-experiment/executor/src/docassemble_executor/controller.py uv-experiment/executor/src/docassemble_executor/installation.py uv-experiment/executor/tests/test_core_recovery.py uv-experiment/preparation/tests/test_core_recovery_postgres.py uv-experiment/preparation/image_bridge_proof.py uv-experiment/preparation/core_recovery_retry_proof.py uv-experiment/run-image-bridge-proof.py uv-experiment/run-continuous-work-proof.py
```

Results:

- Recovery: **all nine PASS groups including completion**. Real authenticated
  first publication/normal apply and lost-success recheck precede a second
  publication. A proof-only root-owned runtime `.lock` permission fault makes the
  real pinned non-root frozen uv invocation fail; permissions are restored before
  the real independent core sync. This is an actual frozen apply failure, **not**
  a new claim of interruption during partial package mutation. Both actual sync
  invocations and their UIDs/flags are retained. Exactly one core restoration;
  no solve/build. DB-report outage is injected after durable local recovery.
  Replay with candidate store hidden performs no further sync/recovery. Desired,
  applied revision, local intent and schema log stay unchanged; DB status shows
  failed desired, historical normal, separate exact core recovery and no active
  slot. Local maintenance is not normal/recovery-available. Real administrator
  module retry rejects recovered work. Existing safe abort/retry is requalified.
- Continuous: **six PASS groups including completion**, plus its unchanged
  authenticated normal fixture. Both daemons, Supervisor replacement, cold
  no-pending normal resumption, subsequent continuous prepare/apply, old late
  report isolation and retained-material invalidation all pass.
- Original image bridge: **eight PASS groups**. Original installation: **seven
  PASS groups**. Published boot: **five reconciliations + unavailable-table
  fail-closed case**. Original fixture boot: **25 PASS scenarios**, including
  all three startup-mode/update-disabled local safety-recovery regressions.
- PostgreSQL/preparation: **505 passed in 79.51s**. Intentional 491 -> 505:
  seven new recovery-report/authorization/fencing/late-observation scenarios,
  each on fresh and upgraded schemas.
- Act: **333 passed, 1 skipped in 32.20s**; mypy **56 files, no issues**.
  Intentional 327 -> 333: six new source-named native recovery/child/replay tests.
  Existing bare-act Supervisor skip unchanged; no assertions/count exclusions
  relaxed. Native focused: **36 passed**. Ruff E9/F and source/doc whitespace
  checks pass; exact commands/output retained.

Development evidence remains untracked under `05c5/evidence/development/`.
Two proof-driver faults were corrected: the initial interruption timer killed
before the uv audit wrapper, so it was replaced by a deterministic real uv
permission failure; reusing a closure variable caused a recursive lost-report
stub, corrected without changing the implementation. A later retry-helper import
was corrected to use the actual runtime-side qualified module, not an invented
shared-module export. **Real recovery succeeded in each of those failed proof
runs**; they are not counted as final qualification. A pending-boot preparation
run hit Docker disk pressure and is likewise preserved, not excluded/accepted.
Initial act found an optional-recovery type error; an explicit completed-result
guard fixed it. The full image and ALL final proofs were rerun afterward.

Supervisor approved one-at-a-time removal of four unused completed-ticket image
tags after verifying zero container references (tags/sha256 in
`image-removals.txt`), unused build-cache pruning, and this run's disposable
container removal only after evidence capture. Historical containers, all named
volumes and historical evidence were preserved. Colima was not expanded. Final
free space is 18G (`disk-final.log`). No derived-image fallback was needed.

## 2026-10-15 UTC — P1 superseded recovery-target validation fix

Review found that a superseded runtime observation bypassed recovery image-target
validation. `report_terminal` now validates recovery against the **attempt's
published revision** before writing its terminal result or releasing its slot,
independently of runtime generation. Only runtime projection updates remain
conditional on a matching generation. No recovery/retry authority is widened.

Extended the foreign-image regression with a newer development-override runtime
observation before reporting. Both schema initialization paths reject the foreign
receipt and preserve the complete DB snapshot, including the occupied attempt.
Before the fix: **2 failed, 8 passed, 6 deselected in 1.80s** (the new upgrade and
fresh cases failed to raise). After: **10 passed, 6 deselected in 1.67s**.
PostgreSQL/preparation: **507 passed in 92.36s**; 505 -> 507 is intentional,
adding exactly the combined regression on both initialization paths.
Act: **333 passed, 1 skipped in 32.08s**, mypy **56 files, no issues**;
counts and existing Supervisor skip unchanged, no exclusions relaxed.

Rebuilt the full current-source fixture as `da-uv-05c5-fix:local`,
`sha256:3ba622c2fbf1478eac35c35eabafb87f260473247812d41efa97640eed415e45`.
Recovery **nine PASS groups**, continuous **six PASS groups plus its normal
fixture**, original image bridge **eight PASS groups**. The image-installed
`installation.py` matches the fixed source SHA256
`416482ae1cd20f7b046f1bee6d60339cf59f433c0320cc29d5007c7f46820569`.
Exact single-line commands and complete output are captured in untracked
`ticket-05c5/fix-evidence/*-command.json` and `*.log`; see `fix-summary.md`
for the command list. Original accepted evidence above is preserved, not
represented as post-fix output. Supervisor approved removal of only this run's
three disposable containers after evidence capture; historical containers,
all named volumes and images were preserved. Independent re-review required.
Parent Status/checklist unchanged; every fenced item below remains fenced.

## Precisely fenced remainder

### 05c5b — NOT implemented/accepted here

- Universal live observation/status and all normal/history/core-only/override
  writers outside the supervised published-normal path; normal HTTP health/UX
  closure, all-role readiness invalidation and lost-success rechecks on every
  relevant path. No new continuously probed unchanged/hung-process guarantee.
- Cold unreported/lost-success receipt resumption/reporting, restricted core-only
  administration/service resumption, cold interrupted-apply/core recovery,
  tables-unavailable boot restoration, and receipt reconstruction after a crash
  before durable terminal evidence. Core-only is **stopped**, availability false.
- Explicit authorized uncertain-work retry/administrator clearance, especially
  schema/setup review and replay safety. Failed/recovered/mutated/blocked or
  non-qualified journals and broader same-revision/crash rollover stay fenced;
  child-safe evidence alone does not authorize those retries. No heartbeat slot
  release, deleting uncertainty evidence, automatic recovery loop or data restore.
- Complete config/dependency/policy refresh before imports/work; managed config
  import and arbitrary operator edits remain unsupported/unqualified.

### Explicit writer/source/later exclusions retained

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
  preparation/controller/CLI dependency/policy observation refresh; config.allowed,
  ALLOW_UPDATES, DEVELOPER_CAN_INSTALL and name/source/dependency-policy writer
  serialization. Reading current gates or rejecting ingress does not qualify
  internal/helper/operator/plugin writers.
- Remaining PackageAuth/grant/name/source/dependency-policy closure: utils/helpers
  install_zip/git/pip/uninstall; packages/views.update_package and api.api_package;
  develop/views.playground_packages ZIP caller; update clear_invalid_package/
  clear_invalid_package_auth/check_for_updates/add_dependencies/fix_names/
  install_package/uninstall_package/update_versions; tasks.update_packages/
  reset_server/direct update CLI; create_tables package rewrites/add_dependencies.
- **05a2b4** real credential-source decision/injection, private indexes/bindings,
  other sources/targets/build policies; production managed initialization/adoption,
  broader installed-material/store qualification; update/remove/config import,
  coherent upgrade/rollback/adoption/backup/image migration/general managed API
  breadth and later tickets. No full 05b/05c/05c5/parent-05 acceptance; 06 blocked.

## Resolution and limits

Implemented and checked **bounded 05c5a**, not a plan-only handoff. No branch
switch/new-system edits; parent tracker Status/checklist unchanged. Independent
review remains required. `ticket-05c5/diff.patch` (ac5499e0..HEAD) and
`change-summary.md` are intentionally left untracked.

Next: 05c5b — universal live observation/status, restricted/cold recovery and explicit uncertain-work clearance; named writer composites and 05a2b4 remain fenced.
