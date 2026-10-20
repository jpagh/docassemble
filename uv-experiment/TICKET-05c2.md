# 05c2: Qualified image-bridge approval through normally verified application

Date: 2026-10-15 UTC. Branch: `uv-implementation`. Baseline:
`ed1fa4cedd469c5450b3aaf41239a1001a68584b`.

**Experimental, explicitly opt-in all-role image bridge ONLY.** No production
writer-safety, full 05c or parent-05 acceptance. Independent review required.
Parent 05 Status/checklist unchanged; 06 remains blocked.

## Objective and approved qualification boundary

Connect authenticated preview/approval, immutable publication, durable executor
claim, exact frozen application and normal all-role readiness. The existing
public-registry builder could not reproduce this lifecycle fixture's patched
local core wheels from PyPI. The supervisor approved an explicit image-core
bridge instead of silently changing core identity or enabling generic approval:

- An operator provisions an exact retained image-wheel baseline and explicitly
  sets `DA_QUALIFIED_IMAGE_BASELINE=/operator/path/baseline.json` in the controller
  and web service. There is NO default enablement. Only this all-role baseline
  can produce public approval/retry. Other targets/core/source policies reject.
- Every distribution in the selected image runtime is protected as exact core.
  Local wheels are copied from the hash-verified image bundle; registry wheels
  are fetched at exact image-lock URLs/hashes, without building/re-solving core.
  Missing exact wheels reject. Loading the descriptor rechecks full image target,
  retained bundle hash, complete inventory, versions and original locked wheel
  hashes. No requirement relaxation, core upgrade or production adoption API.
- The supervisor approved restricting independent ingress in this mode to health,
  readiness/status, existing-user login/logout, static assets and the two preview
  endpoints. Every other route rejects with 403, for every HTTP method, before
  forwarding. Default ingress is unchanged. Tests enumerate source route declarations
  and exercise GET/POST rejection of every non-allowlisted route (>100).
- Public mutations require an **existing administrator requester**. Capture/bind
  SQL account authority, roles/memberships and unrelated current grants; reread
  them under the existing environment-first publication lock. Target owner grants
  must remain exactly the requester, and PackageProjection preserves existing
  ownership. No ownerless assignment, other-owner transfer, account/profile/
  registration/configuration or legacy package writer route is enabled. New
  Package/owner projection remains the approved 05b atomic publication operation.
- Internal operator/plugin/helper writer invocation is **unsupported**, not made
  safe by ingress restriction. Config files/descriptor/store are trusted operator
  inputs; this is not complete cooperating-writer qualification or a hostile-code
  sandbox. The named remaining writer composites below are still unqualified.

This is the approved bounded qualification refinement, not a plan-only handoff.
The exact all-role runtime fixture is deliberately used; no new production
initialization/adoption or other source/target breadth is invented.

## Implementation

### Preparation and public adapters

`RegistryBuilder(..., image_baseline=PATH)` and preparation-poller
`--image-baseline PATH` require explicit operator provisioning for image-core
inputs. The ordinary public-only builder remains available. All image core
requirements bind exact retained wheel paths; only the new public-PyPI add and
its new transitive dependencies resolve/build outside the live runtime, non-root.
Universal-lock non-target marker branches are not falsely counted as installed
Linux distributions. The apply project includes exact pins and wheel bindings
for **every selected transitive**, not only original top-level requirements;
this fixes the real peppercorn offline miss exposed by sampleproject's dependency.
Original declarations/constraints remain in the project and immutable inputs.

The retained manifest includes immutable wheel-payload installed-material hashes,
including only hash-qualified namespace overlap variants, never hashes supplied
by mutable runtime RECORDs. Interpreter-generated entrypoints/provenance are not
wheel payloads. Cold-cache offline frozen/no-build disposable sync verifies this
mapping before readiness. The live apply and success replay verify the same map.

`QualifiedPublication` uses the existing Publication/PreviewAuthorizer/
PackageProjection, fresh API-key credential/constraint/permission rereads and
current operational policy. Managed setup still globally disables **legacy**
ALLOW_UPDATES/install/restart paths; only the explicit bridge semantic factory
reads the configured operational update setting. It does not re-enable legacy
installers. Browser POST retains CSRF; API retains real api_verify authentication.
Public approval returns 202 `published` / `pending`, never installation success.
The browser offers exact-preview approval and status refresh only when supported.
Flag-off/default adapters remain preview-only and unsupported actions reject.

### Frozen lifecycle and durable status

The independent poller retains the existing MutationLock, drain/admission,
RuntimeLock/guardian child exclusion and dedicated non-root frozen sync authority.
It binds attempt/revision/fence/host/target/runtime generation/bundle before effects.
The default 05c1 path still stops in maintenance. Only the configured image bridge
continues through actual create_tables/schema usability, isolated core probe,
web/login startup and **all 12 actual runtime/infrastructure role protocols**
behind preparing admission. No automatic recovery or re-solve occurs.

After successful qualification it records local applied state, verified journal
and immutable `applied-normal` receipt. Reporting under the matching DB claim
advances the normally verified runtime observation/history and releases the slot;
it never changes desired intent. A local reported-binding marker prevents the
old service monitor from opening admission during a lost DB-success handoff.
Normal readiness/admission opens only after reporting and fresh role checks.

Success reporting/replay rechecks retained-byte/material integrity and current
live role incarnations without repeating sync/schema. Ordinary application
consumers are not mistaken for installer descendants requiring exclusive lock.
Failed health replay closes readiness, invalidates a matching current normal DB
claim and leaves uncertain work fenced. A newer runtime generation cannot be
replaced by the old success. Stopped 05c1 receipt replay still needs exclusive
child exclusion and still works without retained bytes, as before.

Safe public **explicit** retry is limited to the latest confirmed pre-sync drain
abort of the current desired revision. It reauthorizes the original admin
requester, requires unchanged authority/current grants and reuses published bytes.
Running, mutated/failed-stopped, recovered, historical/non-qualified and uncertain
work cannot authorize retry. There is no automatic uncertain-work clearance.
Known reported normal receipts can roll over to a subsequent qualified publication;
known abort receipts can roll over only to their authorized same-revision retry.
Old bound journals are durably archived, not deleted as clearance of uncertainty.

Authorized DB status distinguishes desired, latest normally verified running,
unknown/current invalidation, historical verification, pending/running, aborted/
failed and terminal outcome/reason. Local independent status also exposes the
bound publication/failure while normal web is stopped. Preparation/publication/
materialization never imply application. Failures keep desired intent and do not
restore historical grants. Complete observation writers remain fenced below.

Boot is explicitly rejected once this bridge has been provisioned, including
publication/claim-before-journal boundaries, or if a published-apply journal exists.
It cannot silently fall back to old fixture desired.json or invent DB recovery.
This is deliberate absence of DB boot reconciliation, not a deployable boot daemon.

## Verification and evidence

Qualified evidence: `05c2/evidence/accepted/`. This requested directory name is
**not independent acceptance**. Exact commands/output are retained; single-line
Python subprocess wrappers captured logs without shell redirection/heredocs.
Act trailing whitespace is normalized; other generated output is retained.

Final image `da-uv-05c2:local`:
`sha256:05e3695542d0f3c6d945e2e77d61047dfa3ea7c2b1c8bd7562e974367232c50f`.
It is an explicit working-tree build, not a claim to the eventual documentation
commit. The qualified full bridge uses **image-installed code**, not source
injection: the repository mount supplies only its proof driver. Operator setup
explicitly seeds a coherent immutable qualified fixture baseline revision and
singleton from the disposable lifecycle fixture's unknown baseline; production
baseline initialization remains absent. Real PostgreSQL/Redis in the
all-role container provide application services, not installation dispatch.

```sh
docker build --progress=plain -f uv-experiment/fixture/Dockerfile -t da-uv-05c2:local --build-arg DA_REPO_COMMIT=ed1fa4cedd469c5450b3aaf41239a1001a68584b+05c2-working-tree .
# exit 0; build.log (BuildKit itself clips its large manifest output)
python3 uv-experiment/05c2/evidence/accepted/verify-source.py
# PASS 609 current image-COPY source inputs; PASS 654 host/build input hashes; exit 0
python3 uv-experiment/run-image-bridge-proof.py
# eight PASS groups including driver completion; exit 0; image-bridge.log
```

The full proof performs real session login/CSRF and real serialized fixture API-key
provision plus api_verify/permission checking. Browser-authenticated prepare and
preview -> API-authenticated approve -> publication/pending -> real drain abort
with surviving accepted consumer -> failure-visible status -> API safe retry ->
executor claim -> one exact non-root offline frozen/no-build live sync -> actual
schema/probe/web/all-role qualification -> injected lost DB-success reporting ->
independent CLI health-checked replay with no repeated sync/schema -> applied/DB
verified-running/normal readiness. A second authenticated publication with corrupt
retained bytes fails before sync; desired remains, prior normal is history only,
normal readiness is false and independent status shows `bundle-invalid`.

`image-bridge/` retains exact preview/attestation, full project/lock/hash manifest/
installed-material mapping, newly built candidate/transitive wheels and sources,
image baseline descriptor/lock, actual inventory, journal/status and logs. Hundreds
of MB of image-core wheels are **not duplicated in Git evidence**; the descriptor,
original image lock and proof's in-container full retained-byte verification witness
them. This is not a claim that the evidence subtree is itself a complete bundle.
The disposable proof container retains the complete content-addressed store.
No browser JavaScript automation or broad production HTTP writer proof is claimed.

```sh
PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
# 447 passed in 72.22s; exit 0; postgres.log
PROOF_IMAGE=da-uv-05c2:local PROOF_CONTAINER_NAME=da-05c2-stopped-proof PROOF_EVIDENCE_DIR=uv-experiment/05c2/evidence/accepted/installation python3 uv-experiment/run-installation-proof.py
# original seven PASS groups; exit 0; installation.log
act workflow_dispatch -W .act/checks.yml
# 313 passed, 1 skipped in 31.46s; mypy no issues in 54 source files; Job succeeded; exit 0
```

The stopped installation proof uses final pinned tooling plus current read-only
mounted source, as its existing driver does. Historical accepted 05c evidence is
untouched; optional destination/container arguments isolate this run's artifacts.
The existing PostgreSQL 16.6 / Redis 7.4.2 disposable fixtures serve the regression
suite. No new skip or count assertion/exclusion changed. Intentional counts:
PostgreSQL/preparation 413 -> 447 (+34: 17 image-bridge scenarios on fresh/upgrade);
act 308 -> 313 (+5 ingress/opt-in/replay/boot boundaries); mypy 52 -> 54 (+image
baseline/published lifecycle modules). Six existing status cases intentionally
expect truthful `failed`, rather than generic `not-verified`, for failed attempts.
Packaging regression now includes the canonical qualification module.

```sh
python3 uv-experiment/scenarios/full_maintenance.py --image da-uv-05c2:local --evidence-dir /Users/jack/Projects/docassemble/uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance
# all original 15 PASS; exit 0; lifecycle-full-maintenance.log
```

This requalifies the existing full-maintenance group on the final image with
bridge mode **off** (default behavior preserved). It is not a rerun/claim of all
96 historical lifecycle scenarios or full parent acceptance. Lint and changed
source/documentation whitespace checks also pass; exact commands in `lint.log`
and `diff-check.log`.

Initial development runs exposed Docker VM disk exhaustion, fixture-driver
password/prefix/key-return mistakes, universal-lock non-target inventory and a
real unretained transitive offline miss, and the success-replay admission probe
mode. They were corrected, not bypassed or excluded. Intermediate logs/failed
proofs remain untracked under `05c2/evidence/development/`; final evidence is the
repeated all-image run above. Supervisor approved deleting exactly four unused
dev/pre-audit image tags, none cited as accepted qualification images; named
volumes/current accepted images were preserved. Image storage dropped
93.94GB -> 83.07GB at cleanup; PostgreSQL restarted without data replacement.

After the same task resumed from its interrupted staged state, inspection found
that the saved whitespace check had failed on a trailing blank line in
`published.py`. Removed that blank line, rebuilt the image above and repeated
all required gates plus all 15 full-maintenance scenarios, source verification
and lint. No functional scope change or assertion relaxation. Task-owned bridge
and installation evidence directories were cleared before recapture, avoiding
nested `docker cp` output or mixed attempt receipts from repeated runs. Other
untracked development/review artifacts were preserved.

## Precisely fenced remainder

- **05c3:** DB-bound one-attempt core recovery and failed/recovered terminal
  reporting; recovery must remain independently available without DB tables.
  No automatic recovery in this poller, uncertain retry/clearance or schema/setup
  replay after uncertainty. Failed-stopped/blocked receipts cannot roll into new
  mutations; production repair/recovery integration is not supplied here.
- Boot/restart DB reconciliation and boot/service-supervised continuous preparation
  and installation polling. Bridge-provisioned restart explicitly refuses, even
  before a local attempt journal; no fallback to fixture desired authority.
- Complete monotonic HostEnvironment/normal/history/core-only/override observation
  writers, config/dependency-input refresh before imports/work and unified live
  health/status observation outside this explicit attempt/report/replay lifecycle.
  Current DB normal state is the latest qualified observation, not a new universal
  continuous health-monitor guarantee. Broad production success UX is not enabled.
- Full production managed initialization/adoption, broader target/installed-material
  and immutable-store/source/build-policy qualification. The explicit experimental
  image-core wheel bridge is not adoption, coherent upgrade or general core solve.
- All remaining **05b2b6 writer composites/caller closure and actual-writer races**:
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
  Ingress restriction is NOT qualification of these writers or arbitrary operator
  edits; no config import is enabled.
- Remaining PackageAuth/grant/name/source/dependency-policy closure: utils/helpers
  install_zip/git/pip/uninstall; packages/views.update_package and api.api_package;
  develop/views.playground_packages ZIP caller; update clear_invalid_package/
  clear_invalid_package_auth/check_for_updates/add_dependencies/fix_names/
  install_package/uninstall_package/update_versions; tasks.update_packages/
  reset_server/direct update CLI; create_tables package rewrites/add_dependencies.
  Existing reject/skip fences and previous qualified helper slices remain intact.
- **05a2b4** real credential-source scope/injection, private indexes/bindings, other
  sources/targets/build policies; update/remove/config import/coherent upgrade/
  rollback/adoption/backup/image migration/general managed API breadth. No
  historical grants/intent restoration. No complete 05b/05c/parent-05 acceptance.

## Resolution and limits

Implemented and locally checked the supervisor-approved restricted end-to-end
05c2 capability. Independent review is mandatory before acceptance; this does not
close parent 05. No branch switch/new-system changes. Requested diff/change-summary
artifacts remain untracked under `ticket-05c2/`: `diff.patch` is the requested
`ed1fa4c..HEAD` 05c2 slice; `combined-05c.patch` separately spans
`4d54c9a1a..HEAD` (05c1 plus 05c2) for context. Tracker Status/checklist unchanged.

Next: **05c3 — DB-bound lifecycle recovery/terminal reporting**, with boot/polling,
observation writers, remaining writer composites and 05a2b4 still fenced.
