# 05c4: Continuous durable work and healthy resumption (bounded 05c4a)

Date: 2026-10-15 UTC. Branch: `uv-implementation`. Baseline:
`821b6f48c4f2a33d5b8efe1e73c17a92aed57947`.

**Task-authorized implemented end-to-end refinement, NOT complete 05c4.**
Boot-supervised continuous preparation/installation, exact previously reported
normal cold resumption and sampled live status/material/incarnation invalidation
are implemented for the explicitly configured image bridge. Universal observation
writer/status closure remains **05c4b** below. Independent review required;
`accepted/` is an evidence destination, not an acceptance verdict. Parent 05
Status/checklist unchanged; 06 remains blocked.

## Objective and bounded refinement

05c3a established maintenance-only DB reconciliation. This slice connects that
boundary to real continuous operation rather than another one-shot command.
The task expressly permits the smallest coherent end-to-end implementation plus
an explicit remainder. No recovery, uncertain-work clearance, writer-composite
qualification, new source policy or adoption authority is included.

## Implementation

### Boot-owned supervision, independent durable discovery

Explicit `DA_DURABLE_WORK_STORE=/absolute/retained/store`, together with the
existing operator-provisioned `DA_QUALIFIED_IMAGE_BASELINE`, enables two independent
Supervisor programs: `durable-preparation` runs as `da-environment`;
`durable-installation` runs as root with the existing non-root frozen sync child.
Their fixed entrypoint is `da-executor run-durable-work --kind preparation|installation`.
Both are continuous loops, not repeated HTTP/Celery dispatch or one-shot proof
commands. They use independent config/SQLAlchemy/PostgreSQL tooling, never Flask
or installed application imports. Redis remains an application/protocol service,
not installation/preparation dispatch authority. URL/password is not an argv or
logged diagnostic.

Programs are registered with `autostart=false`, `autorestart=true` and explicitly
started **after that boot selected and completed DB reconciliation**. They do not
race bootstrap or launch merely because fresh fixture initialization created
managed evidence later in the same boot. The explicit fresh fixture may provision
its image descriptor after initial boot, then restart to enable durable operation;
this is not production baseline initialization/adoption. Default/unconfigured
behavior and the prior maintenance-only published-boot path remain unchanged.

The preparation loop reuses fenced claims, renewal and the image-wheel builder;
installation reuses MutationLock, drain/admission, descendant-safe RuntimeLock,
exact retained bundle checks, offline frozen/no-build sync, schema/probe/all-role
qualification and durable terminal reporting. Failures use fixed redacted
messages and leave uncertainty fenced. A restarted daemon never lease-reclaims a
running installation, deletes a journal, repeats uncertain schema/setup or restores
data. In-process preparation worker exit still does not prove descendant-safe
physical cleanup; no new cleanup or credential-source authority is claimed.

### Healthy no-pending cold resumption, without installation

Boot first records the same unknown/history observation as 05c3a. Resumption is
restricted to **no active attempt**, local applied exactly equal to DB desired,
verified local journal binding and an identical **already DB-reported succeeded
normal receipt** for the qualified image bridge. Pending, diverged, failed,
core-only, override, unreported/lost-success cold receipts and uncertain journals
cannot use this path. There is no slot release or terminal replay here.

Under host exclusion, resumption rechecks the exact image/bundle/retained bytes,
installed material, schema **usability** (not create_tables), isolated core probe,
web/login and all 12 fresh runtime/infrastructure role protocols behind preparing
admission. It CAS-checks the observed runtime generation, desired and empty active
slot under the publication lock, then records normal runtime/history and a new
HostEnvironment generation before opening normal readiness/traffic. No sync,
solve, build, schema/setup invocation or journal rewrite is performed. Healthy
retention is not an update: update-disable still rejects actual mutations, but
does not skip identity/material gates or prohibit read-only healthy resumption
(the latter distinction has a native gate regression).

### Continuous health/status and subsequent-attempt isolation

The installation daemon overlays shared `runtime_status()` fields on local
`/status.json`, and writes environment-first monotonic HostEnvironment observations:
desired/current normally verified/history/recovery/unknown identity, runtime/host
generations, active/latest attempt and phase, allowlisted failure/outcome,
divergence and normal readiness remain separate. Pending/divergence or a failed
current health observation removes ready and closes admission. DB outage yields
an explicit local unknown/non-ready projection, never cached verified readiness.
Authorized DB status continues using the same shared runtime fields.

Continuous turns **do not repeatedly close healthy traffic for serial-service
probes**. Only an already reported, still-current normal receipt with its matching
root-owned reported marker, unchanged qualified live role incarnations, open
admission and fresh retained-byte/installed-material checks can retain health.
Changed/missing incarnations or lost-success reporting use the full existing
closed-admission protocol recheck. Material failure invalidates matching current
DB normal, preserving desired and last-normal history, and does not auto-repair.
This retains the 04b3b same-incarnation qualification principle; it is not a new
claim of periodic protocol probing of an unchanged, occupied/hung process.

Before rollover, local terminal phase/binding and the previous exact durable DB
terminal must agree. A known finished receipt with a subsequent permitted pending
attempt is archived **before** any old normal health replay/admission opening.
Old normal replay additionally checks current desired, active attempt and runtime
observation. An idempotent late DB report never clears another attempt's slot.
Confirmed pre-sync abort retry remains the existing narrowly authorized capability;
failed/mutated/blocked/uncertain journals gain no rollover/retry authority.

## Verification and evidence

Qualified evidence: `05c4/evidence/accepted/`. Each `*-command.json` records exact
argv/exit; corresponding `*.log` retains output, with trailing whitespace normalized
by capture.py. BuildKit itself clips its large manifest output. No shell heredocs
or redirection were used for commands/capture.

Final image: `da-uv-05c4:local`,
`sha256:8de38ee79bcde59bade650faddf2e3a7336c1f9395b4827221f332a34281ce93`.
This is a current working-tree build, not the eventual documentation commit.
613 image-COPY source inputs match; 663 host/build inputs are hashed. Continuous,
image-bridge and published-boot proofs use image-installed executor code; the
read-only repository mount supplies proof drivers, not runtime-source injection.
As before, stopped installation uses pinned image tooling plus current read-only
mounted source. Full image-core wheels are retained in disposable proof stores,
not duplicated as hundreds of MB in Git evidence.

Exact final commands from repository root (all exit 0):

```sh
python3 uv-experiment/05c4/evidence/accepted/capture.py build docker build --progress=plain -f uv-experiment/fixture/Dockerfile -t da-uv-05c4:local --build-arg DA_REPO_COMMIT=821b6f48c4f2a33d5b8efe1e73c17a92aed57947+05c4-working-tree .
python3 uv-experiment/05c4/evidence/accepted/capture.py continuous python3 uv-experiment/run-continuous-work-proof.py
python3 uv-experiment/05c4/evidence/accepted/capture.py image-bridge env PROOF_IMAGE=da-uv-05c4:local PROOF_CONTAINER_NAME=da-05c4-image-bridge PROOF_EVIDENCE_DIR=uv-experiment/05c4/evidence/accepted/image-bridge python3 uv-experiment/run-image-bridge-proof.py
python3 uv-experiment/05c4/evidence/accepted/capture.py boot env PROOF_IMAGE=da-uv-05c4:local PROOF_EVIDENCE_DIR=uv-experiment/05c4/evidence/accepted/boot PROOF_PENDING_CONTAINER=da-05c4-boot-pending PROOF_FAILED_CONTAINER=da-05c4-image-bridge python3 uv-experiment/run-published-boot-proof.py
python3 uv-experiment/05c4/evidence/accepted/capture.py installation env PROOF_IMAGE=da-uv-05c4:local PROOF_CONTAINER_NAME=da-05c4-stopped-proof PROOF_EVIDENCE_DIR=uv-experiment/05c4/evidence/accepted/installation-final python3 uv-experiment/run-installation-proof.py
python3 uv-experiment/05c4/evidence/accepted/capture.py fixture-boot python3 uv-experiment/scenarios/boot.py --image da-uv-05c4:local --evidence-dir uv-experiment/05c4/evidence/accepted/fixture-boot
python3 uv-experiment/05c4/evidence/accepted/capture.py postgres env PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
python3 uv-experiment/05c4/evidence/accepted/capture.py act act workflow_dispatch -W .act/checks.yml
python3 uv-experiment/05c4/evidence/accepted/capture.py source python3 uv-experiment/05c4/evidence/accepted/verify-source.py
```

Results:

- New continuous proof: **all six PASS groups** including completion. Its fixture
  first requalifies authenticated publication, confirmed abort/safe retry, actual
  frozen apply and lost-success replay; then cold no-pending boot admits real
  login traffic without sync/schema/journal mutation. Both daemons are live;
  Supervisor replaces a killed preparation daemon. A new authenticated preparation
  is built/ready by continuous non-root polling, then public approval is discovered
  and installed by continuous installation polling (no one-shot worker commands).
  It records one exact non-root frozen/no-build live sync, archives the old receipt,
  and proves a late old success cannot release the new occupied slot. Corrupting
  the current retained bundle closes readiness and invalidates current DB normal,
  with desired/history retained and no sync/schema/journal/recovery/retry replay.
- Original image-bridge: **eight PASS groups**, original assertions retained.
- Published boot: **five successful reconciliations plus unavailable-table
  fail-closed case**, pending/failed divergence, descriptor removal and unchanged
  intent/attempt/material/invocation digests. This is the default maintenance-only
  path; it does not claim recovery or auto-application of uncertain work.
- Original stopped installation: **seven PASS groups**, including independent
  PostgreSQL work with Redis/Celery absent, child exclusion and crash boundaries.
- Original fixture boot: **25 PASS scenarios**, including local fixture safety
  recovery regressions. These are NOT DB-bound recovery implementation/evidence.
- PostgreSQL/preparation: **491 passed in 77.31s**, intentional 473 -> 491:
  nine new resumption/status/late-report scenarios on both fresh/upgrade schemas.
- Act: **327 passed, 1 skipped in 32.55s**; mypy **56 files, no issues**.
  Intentional 322 -> 327 (+5 source-named durable-work regressions); 55 -> 56
  (+durable_work). Existing bare-act Supervisor skip unchanged. No exclusions or
  old count assertions relaxed. Native focused **35 passed**; Ruff E9/F and
  changed-source/documentation whitespace checks pass (exact commands in evidence).

Development exposed and corrected premature polling after fresh initialization
and repeated healthy replay's transient admission closure. Parallel heavy fixture
startup also hit resource pressure/pg_isready timeout; final proofs ran sequentially.
Docker disk exhaustion caused one PostgreSQL run to fail; it was not excluded or
counted as qualification. After approved space cleanup, the complete suite reran
successfully without replacing its DB volume. Intermediate/failure bytes remain
untracked in `05c4/evidence/development/`; only final qualified outputs are above.

Supervisor authorized unused build-cache pruning and recorded one-at-a-time removal
of five unused completed-ticket image tags (bypasses/helpers/services/workers/
consumers); exact tags/sha256 are in `image-removals.txt`. Current proof image,
`da-uv-full-maintenance:local`, historical containers, named volumes and historical
evidence were preserved. Only this run's disposable fixtures were stopped/recreated
by its proof drivers. `disk-before.log` / `disk-final.log` retain space reports.

## Precisely fenced remainder

### 05c4b — NOT implemented/accepted here

- Universal live fields/status and all monotonic normal/history/core-only/override
  observation writers outside this explicitly supervised published-normal path;
  complete HostEnvironment role-health/override/recovery freshness, all normal
  HTTP health/UX surfaces and broad success UX. No universal continuously probed
  protocol-health guarantee for unchanged/hung role processes is claimed.
- Cold unreported/lost-success normal-receipt resumption/reporting, restricted
  core-only service resumption, broader subsequent-attempt/same-revision/crash
  rollover qualification beyond exact reported-normal and confirmed-abort paths.
  Active/uncertain cold receipts stay occupied/non-normal, never schema/setup replay.
- Complete config/dependency/policy observation refresh before imports/work;
  managed config import and arbitrary operator edits remain unsupported/unqualified.

### Explicit exclusions retained from 05c3a / task

- **DB-bound independent one-attempt core recovery and failed/recovered terminal
  reporting**, including tables-unavailable restoration. No automatic data restore,
  recovery loop, DB-attempt recovery integration or desired/grant restoration.
- **Uncertain-work retry/clearance**, historical/non-qualified/mutated/failed-stopped/
  blocked/recovered retry, schema/setup replay after uncertainty, heartbeat slot
  release or deletion of uncertainty evidence. Existing safe explicit pre-sync
  abort retry is not broader retry clearance.
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
  serialization. Reading current update gates is not qualification of these writers.
  Opt-in ingress rejection does not qualify internal/helper/operator writers.
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
  breadth and later tickets. No full 05b/05c/parent-05 acceptance; 06 remains blocked.

## Resolution and limits

Implemented and checked **bounded 05c4a** using the task's authorized refinement,
not a plan-only handoff. Independent review remains required. No branch switch
or new-system changes. Parent tracker Status/checklist unchanged. Requested
`ticket-05c4/diff.patch` (821b6f48..HEAD) and `change-summary.md` are left untracked.

Next: **05c4b — remaining universal live observation/status and resumption/rollover
qualification**; DB-bound recovery, uncertain retry clearance, named writer
composites, 05a2b4 and later tickets remain explicitly fenced.
