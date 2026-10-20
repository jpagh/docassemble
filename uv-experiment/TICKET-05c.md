# 05c: Durable polling and frozen stopped materialization (bounded 05c1)

Date: 2026-10-15 UTC. Branch: `uv-implementation`. Baseline:
`4d54c9a1afa15eabe701c19ad090c90af598a073`.

**PUBLIC APPLY-PRODUCING APPROVE AND RETRY REMAIN FENCED.** This uses the task's
explicit smallest-coherent-end-to-end fallback. It is implemented stopped
materialization, NOT full 05c, production writer-safety or parent-05 acceptance.
Independent review required. Parent Status/checklist unchanged; 06 blocked.

## Objective and bounded refinement

Connect actual 05a independent preparation and 05b immutable publication to an
independent DB installation poller, with retained-byte frozen sync, host/journal
binding and terminal slot reporting/replay. Do not falsely call installed bytes
normal service readiness: full schema/probe/all-role and recovery-to-DB integration
is the next operational slice. No browser/API approval/retry enablement and no
remaining writer-composite or credential-source implementation.

## Implementation

- `da-executor poll-installation --store PATH --baseline PATH [--prefix PREFIX]
  [--once]` polls PostgreSQL directly. URL comes from restricted
  `DA_PREPARATION_DATABASE_URL`, not a printed CLI argument. It imports only the
  executor and shared dependency-light schema/protocol, not Flask, application
  config, runtime docassemble, Celery or Redis. Existing preparation polling and
  builder remain independently usable; no broker dispatch authorizes installation.
- Environment-first claim accepts ONLY a pending internal approval attempt of the
  current desired published registry-add revision. It rechecks candidate/revision
  readiness identity, complete result digest and exact current target/core/tool.
  It records attempt/fence/host, immutable bundle/target and runtime generation.
  Running/unsupported/retry work is never lease-reclaimed, even after process loss.
- Root/operator-owned baseline JSON, config and retained store are trusted deployment
  inputs, not web-provided paths. Current feature flag, config/env update-enable,
  bounded Python-3.12/uv-0.12.21 policy, actual uv version, full retained manifest,
  project/lock/inventory/tool identities are checked before sync. This adds no
  broader target/private source/build-policy qualification or adoption authority.
- The host MutationLock covers claim, durable journal, drain, sync, verification
  and reporting. The journal binds DB attempt/revision/fence/host/bundle/target/
  runtime-generation before external lifecycle effects. Existing admission/drain,
  RuntimeLock, subreaper child-lifetime guardians and dedicated non-root frozen
  `--no-build` sync are reused. Apply uses offline/no-config environment settings;
  it never invokes solve/build. Exact distribution inventory and RECORD hashes
  are checked afterward without importing installed application code.
- After materialization this bounded command **stops in maintenance**. It does
  not run schema work, qualify web/roles, write normal verified/applied state,
  open admission or claim successful installation. Mutation outcomes are
  `materialized-stopped`, `failed-stopped` and `blocked`, with
  `normal_verified=false`; these receipts require exclusive RuntimeLock child
  exclusion. A confirmed drain abort instead records `aborted-before-sync`,
  `mutation_started=false`, `child_safe=false`, preserving the controller's
  admission/restoration state without excluding resumed application consumers.
  Both receipt paths report only the matching attempt/fence. Missing/corrupt
  journal, live installer descendants or an uncertain mutation retain the
  slot/fence rather than authorizing takeover.
- DB reporting can be replayed from the bound journal without retained bytes,
  configuration revalidation, reinstallation or recovery. Lost acknowledgements
  return the same terminal evidence. Late replay cannot release a different
  attempt; different terminal evidence/fence/host/bundle rejects. A completed
  receipt remains local authority: subsequent polling replays it, never silently
  begins another install. New lifecycle attempts/clearance remain fenced.
- Claim invalidates current verified-runtime claims under the same singleton lock
  as publication and increments runtime generation; historical normally verified
  state is retained separately. Terminal reporting never changes desired intent,
  and never overwrites a newer runtime observation. Materialization remains
  `unknown`, with a separate materialized revision reference. Authorized status
  adds separately allowlisted verified-running/history/recovery/materialized and
  terminal fields, never turns stopped bytes into installation success. Complete
  normal/recovery observation writers and HostEnvironment projection remain next.

## Fixture qualification corrections and supervisor approval

Fresh current-image lifecycle regression initially failed correctly before normal
readiness. The proof-only advanced migration still branched from the old pre-05
head, yielding two heads. It now follows `05b400000001`; production migrations are
unchanged. Next, serialized startup `fix_api_keys` correctly rejected the historical
lifecycle fixture's missing managed DB singleton. The supervisor explicitly
approved **fixture-only baseline seeding after create_tables and before web
startup**, retaining absent-baseline failure behavior.

`seed_lifecycle_sources.py` modifies only disposable staged wheel sources in
`image-build.sh`; repository production `create_tables.py` is untouched. The
fixture-only helper seeds named test intent/runtime-unknown using the image
manifest, not installed-package inference or a registry-compatible/adopted
production baseline. It does not overwrite existing state. No production path
ships/imports this helper; production managed-init/adoption remains later work.
Real PostgreSQL tests retain missing-baseline rejection and explicitly exercise
startup's actual `fix_api_keys` helper with the singleton absent, no Redis change;
fixture seed idempotency/unknown state and untouched production source are checked.
The initial current-image boot failure is not represented as accepted evidence.

The service-session proof also raced an unleased runtime-importing snapshot helper
against sync after workers released their leases. It now waits for apply to return,
then checks both durable completions/results and nested receipts. No lifecycle
assertion/exclusion was removed. This is a host proof-driver correction, not a
runtime bypass or relaxed child gate.

## Verification and evidence

Qualified evidence: `05c/evidence/accepted/`. Directory naming is the requested
layout, **not independent acceptance**. Exact commands/output are retained there;
Python subprocess wrappers captured output without shell redirection/heredocs.
Act output has trailing whitespace normalized. Earlier failures/superseded proof
bytes remain untracked under `05c/evidence/development/`.

Final image: `da-uv-05c:local`,
`sha256:b4f13c2f36c9c64009512d537e9a79f535ec987fe0347dc316453f9aa83e0e83`.
Build command (`build.log`):

```sh
docker build --progress=plain -f uv-experiment/fixture/Dockerfile -t da-uv-05c:local --build-arg DA_REPO_COMMIT=4d54c9a1afa15eabe701c19ad090c90af598a073+05c-working-tree .
# exit 0
```

This is a working-tree build, not a claim to be the eventual evidence/doc commit.
`source-image-verification.json`: 605 literal image-COPY source inputs match current
bytes; `source-sha256.json`: 647 host/build inputs hashed. Reproduce verification:

```sh
python3 uv-experiment/05c/evidence/accepted/verify-source.py
# PASS 605 current source inputs match literal fixture-image COPY paths
# PASS 647 host/build inputs hashed; exit 0

PROOF_IMAGE=da-uv-05c:local python3 uv-experiment/run-installation-proof.py
# seven PASS groups; exit 0 (installation/container.log)
```

The latter proves independent non-root prepare -> internal atomic approval ->
frozen live TEST-runtime materialization, retained exact digest/inventory, no
Flask/runtime imports or Redis/Celery in that execution container, durable terminal
before lost DB report, live child exclusion, independent CLI replay with the store
absent/no uv invocation, config/env update disable, target mismatch/corruption,
and crash before journal/after mutation marker remaining fenced. These crash
boundaries are explicit fault-state injection, not a claim to have killed a real
uv child in this new poller; actual guardian/uv/controller death is requalified by
the original lifecycle groups. Lost-ack-after-commit and stale-observation/late-
receipt/new-slot races additionally execute against real PostgreSQL.

Approved bundle: `c42f8b8eacd370e303824127febe1db49a19e1b53d3fe9062e135b9671c5192e`.
Retained inventory: packaging 25.0, six 1.17.0; actual installed inventory and
non-root uid 999 frozen invocation are in `installation/`. No normally ready
application or browser/API apply success is claimed by this small fixture.

```sh
PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
# 393 passed in 98.20s; exit 0 (postgres.log)

PROOF_IMAGE=da-uv-05c:local bash uv-experiment/run-preparation-builder-proof.sh
# four PASS groups; exit 0 (builder.log)
PROOF_IMAGE=da-uv-05c:local bash uv-experiment/run-preparation-poller-proof.sh
# two PASS groups; exit 0 (poller.log)
bash uv-experiment/run-preparation-container-cleanup-proof.sh
# four PASS groups; exit 0 (cleanup.log)
```

Builder/poller use the final current image. The unchanged cleanup script explicitly
uses retained old image `49a26f90...` as its private-PID worker substrate while
mounting current builder/cleanup sources; its own image receipt is retained and is
NOT current full-image lifecycle evidence. Each copied attested bundle contains
only its current digest, not stale historical bundles. Historical tracked proof
outputs were restored after copying this run's evidence. PostgreSQL 16.6 and
Redis 7.4.2 use the prior disposable test containers; Redis is only needed for
writer regressions, not installation dispatch/execution authority.

All original lifecycle groups ran on the final current image, no old-image
substitution and no changed expected scenario counts:

```sh
python3 uv-experiment/scenarios/full_maintenance.py --image da-uv-05c:local --evidence-dir uv-experiment/05c/evidence/accepted/lifecycle-full_maintenance
# 15 PASS; exit 0
python3 uv-experiment/scenarios/maintenance.py --image da-uv-05c:local --evidence-dir uv-experiment/05c/evidence/accepted/lifecycle-maintenance
# 7 PASS; exit 0
python3 uv-experiment/scenarios/consumers.py --image da-uv-05c:local --evidence-dir uv-experiment/05c/evidence/accepted/lifecycle-consumers
# 5 PASS; exit 0
python3 uv-experiment/scenarios/workers.py --image da-uv-05c:local --evidence-dir uv-experiment/05c/evidence/accepted/lifecycle-workers
# 10 PASS; exit 0
python3 uv-experiment/scenarios/service_sessions.py --image da-uv-05c:local --evidence-dir uv-experiment/05c/evidence/accepted/lifecycle-service_sessions
# 11 PASS; exit 0
python3 uv-experiment/scenarios/helpers_mail.py --image da-uv-05c:local --evidence-dir uv-experiment/05c/evidence/accepted/lifecycle-helpers_mail
# 10 PASS; exit 0
python3 uv-experiment/scenarios/bypasses.py --image da-uv-05c:local --evidence-dir uv-experiment/05c/evidence/accepted/lifecycle-bypasses
# 5 PASS; exit 0
python3 uv-experiment/scenarios/boot.py --image da-uv-05c:local --evidence-dir uv-experiment/05c/evidence/accepted/lifecycle-boot
# 25 PASS; exit 0
python3 uv-experiment/scenarios/proof.py --image da-uv-05c:local --evidence uv-experiment/05c/evidence/accepted/lifecycle-recovery
# 8 PASS; exit 0
```

The captured commands use absolute evidence paths rooted in this checkout; those
exact spellings appear in `lifecycle-*.log`. Total **96 real-container scenarios**.
These requalify the existing fixture lifecycle, not the missing new-poller
schema/probe/service/recovery-to-DB bridge.

```sh
act workflow_dispatch -W .act/checks.yml
# 294 passed, 1 skipped in 28.54s; no issues in 52 source files; Job succeeded; exit 0
```

Full native suite additionally ran in a final-image disposable container with the
repository read-only, Docker socket and image-owned Linux Docker CLI copied from
`docker:28.5.1-cli`, `BACKUP_TEST_IMAGE=da-uv-05c:local`, no global managed-mode
override, and PYTHONPATH selecting current executor source plus runtime packages:

```sh
docker exec da-05c-native-suite /usr/share/docassemble/executor-venv/bin/python -m pytest uv-experiment/executor/tests tests -q -p no:cacheprovider
# 331 passed in 58.32s, no skips; exit 0 (native-container.log)
```

`lint.log` records the exact changed-source Ruff E9/F command: all checks passed.
`git diff --cached --check -- . ':(exclude)uv-experiment/05c/evidence/accepted'`:
no output, exit 0 (`diff-check.log`). Raw generated evidence is preserved verbatim
and excluded from this source/documentation whitespace check. Intentional count changes: preparation
365 -> 393 (+24 installation protocol cases, +2 fixture-seed cases, +2 absent-
singleton startup-migration cases); act native 292 -> 294 (+2 journal/CLI cases),
mypy 51 -> 52 (+installation module). No count assertions or exclusions changed;
the one act Supervisor skip is unchanged and passes in the full native container.
Initial lint fixture-registration noqa errors were corrected, not suppressed by
removing fixtures. Docker cache pressure was resolved by deleting build cache and
only task-created superseded images/containers; prior accepted images and volumes
were preserved.

## Review fixes — 2026-10-15 UTC

Fixed P1 and advisory P2 on top of `d88249cb89ae4fae24f9bd1dc06a5be0fb279079`.
Preparation/drain now has a distinct durable `installation_preparing` phase;
`mutation_started` is written only after successful drain, before frozen sync.
A confirmed false return from `enter_maintenance` records a bound durable
`aborted-before-sync` receipt, reports the attempt as `aborted` and releases its
DB slot. Initial reporting and replay retain MutationLock but do not take
exclusive RuntimeLock or change admission. This applies even if the controller
could not restore service: the receipt claims no mutation, not normal readiness.
Normal consumers may survive; stopped/mutated receipts still require child
exclusion. An abort reporting outage replays without re-draining or sync. The
completed local receipt still intentionally fences subsequent installations;
subsequent-attempt rollover remains 05c2, not an uncertain running DB attempt.
DB runtime observation remains `unknown`; no fresh normal verification is claimed.

Terminal failures now retain only allowlisted `failure.stage`/`failure.reason`
pairs, including distinct update-disabled, missing/invalid baseline, target
mismatch and bundle corruption categories. No raw exception text/credentials
enter the receipt. Journal round trips and PostgreSQL report/replay retain these
pairs; unallowlisted reasons reject without DB mutation. Legacy terminal receipts
without this optional field remain replayable.

New serving regression drives **InstallationPoller.run_once**, real HTTP ingress,
a normally serving backend, actual admission drain timeout, a surviving shared
runtime consumer lease and actual controller abort/restoration (role readiness
checks are fixture stubs, not a new production all-role qualification). It tests
initial reporting and lost-report replay, no sync/byte changes, completed active
request, normal readiness/admission restored and no partial-mutation marker.
PostgreSQL abort replay independently proves DB slot release with normal consumers
still leased. Four actual gate-failure poller cases and eight journal reason
round trips cover P2.

Exact commands and full outputs: untracked `ticket-05c/fix-evidence/`. Final rerun:

```sh
uv run --project uv-experiment/executor --frozen pytest -q -p no:cacheprovider uv-experiment/executor/tests/test_installation.py
# 16 passed; exit 0 (installation-tests.log)
PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
# 413 passed; exit 0 (postgres.log)
PROOF_IMAGE=da-uv-05c:local python3 uv-experiment/run-installation-proof.py
# seven PASS groups; exit 0 (installation-proof.log)
act workflow_dispatch -W .act/checks.yml
# 308 passed, 1 skipped; mypy no issues in 52 source files; Job succeeded; exit 0 (act.log)
```

Counts intentionally increase: PostgreSQL/preparation 393 -> 413 (+18 reason/abort
report-replay cases and +2 unallowlisted-reason cases, both initialization paths);
act native 294 -> 308 (+8 journal round trips, +4 actual gate failure receipts,
+2 serving drain-timeout/lost-report cases). The one existing Supervisor skip and
52 mypy source files are unchanged; no count assertions or exclusions changed.
The proof uses retained image `sha256:b4f13c2f36c9c64009512d537e9a79f535ec987fe0347dc316453f9aa83e0e83`
for pinned tooling with the **current executor source mounted read-only via
PYTHONPATH**, not a rebuilt image/current full-image provenance claim. New proof
bytes are in `ticket-05c/fix-evidence/installation/`; prior tracked accepted
installation evidence is preserved unchanged. Earlier 96 lifecycle/full-image
runs above are historical evidence for the bounded base, not rerun for these
narrow poller changes. Independent re-review required; parent Status/checklist,
public approve/retry and all remainder fences below remain unchanged.

## Precisely fenced remainder

**05c2 operational remainder (not implemented):**

- Full published revision schema/setup/probe/all-role qualification, normal
  admission/applied advancement, DB-bound one-attempt core recovery and durable
  failed-attempt/recovery terminal reporting. The bounded stopped command does
  NOT invoke those steps. Boot/restart must reconcile published DB identity rather
  than blindly treat historical local fixture desired.json as DB desired intent.
- Complete monotonic HostEnvironment/verified-running/history/core-only/override
  observation writers, local/DB unified status, stale normal-readiness invalidation,
  health recheck on lost success reporting and status/browser success UX.
- Continuous boot/service-supervised preparation+installation polling, subsequent
  attempt/journal rollover and explicit child-safe uncertain-work retry clearance.
  Running-without-terminal stays fenced; no heartbeat reclaim or automatic retry.
- Current dependency/policy observation refresh before work/imports; complete
  operational target/installed-material/source/immutable-store qualification beyond
  the bounded retained registry builder contract. Production managed-init/adoption
  is NOT invented by fixture seeding and remains later initialization/adoption work.
- Final authenticated browser/API preview/approve -> normal apply qualification and
  public approve/retry enablement ONLY after operational AND writer prerequisites,
  aggregate independent review and parent closure. No parent approval fence lifted.

**Remaining writer prerequisites (unchanged 05b2b6 inventory):**

- users/views `edit_user_profile_page`, `user_profile_page`, `user_add`,
  `custom_register`, `invite`, `add_privilege`, `manage_account`; deletion/MFA/email
  and multi-commit composites, not merely already-qualified helper bodies.
- users/helpers `login_or_register`; users/signals `on_user_login`,
  `on_register_hook`, `on_password_change`; users/forms `MySignInForm.validate`
  LDAP/default-role creation; auth/views `oauth_callback`; phonelogin/views
  `phone_login_verify`; create_tables `get_role`, `get_user`, `populate_tables`
  account/default-role/fix-role passes. Their enclosing fix_secret/substitute_secret,
  cookie/session/auth flows and external hook/plugin effects remain unqualified.
- admin/views `config_page`; startup.initialize/read_config; base/read_config
  dependency/index policy; Docker/initialize.sh PYTHONPACKAGES/index observation;
  executor preparation/controller/CLI observation refresh. Config.allowed,
  ALLOW_UPDATES, DEVELOPER_CAN_INSTALL and name/source/dependency policy writer
  serialization/complete closure audit and actual-writer PostgreSQL races.
- utils/helpers install_zip/git/pip/uninstall; packages/views.update_package and
  packages/api.api_package; develop/views.playground_packages ZIP caller;
  update.clear_invalid_package/clear_invalid_package_auth/check_for_updates/
  add_dependencies/fix_names/install_package/uninstall_package/update_versions;
  tasks.update_packages/reset_server and direct update CLI; create_tables package
  rewrites/add_dependencies remain fenced/skipped, not qualified by rejection.
- Full direct PackageAuth/grant and remaining caller closure audit/races. Already
  qualified helper slices retain their qualification; surrounding composites do
  not inherit it merely by calling a helper.

**05a2b4 and other scope fences unchanged:** real credential-source decision and
injection, private indexes/bindings, broader source/target/build policies; update,
remove, config import, coherent upgrade, rollback, adoption/backup/image migration,
other-source installs and general managed API breadth. No historical grants or
intent are restored. No 05a2b4, full 05b/05c or parent-05 acceptance claimed.

## Resolution and limits

Bounded 05c1 is implemented with real end-to-end stopped-materialization evidence
and original regression gates requalified. Independent review remains required;
`accepted/` is not a review verdict. Trusted restricted filesystem/daemon/baseline
configuration is operator authority, not a malicious-build sandbox. Requested
`ticket-05c/diff.patch` and `change-summary.md` remain untracked. Parent tracker
Status/checklist unchanged; no new-system edits or branch switch.

Next: **05c2 — full published lifecycle/recovery reporting and subsequent-attempt
qualification**, with remaining writer prerequisites and public approval fenced.
