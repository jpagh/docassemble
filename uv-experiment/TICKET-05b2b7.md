# 05b2b7: Environment-first SQL profile/account/role view writers

Date: 2026-10-15 UTC. Branch: `uv-implementation`. Baseline:
`3a774a5f6784461ba9b25a168aa29674382e7434`.

**Supervisor-approved single bounded sub-split, NOT final writer convergence or
parent-05 acceptance.** Independent review required. Parent Status/checklist
unchanged; tickets 06+ remain blocked. The requested accepted/ layout is an
evidence destination, not an acceptance verdict. No branch switch/new-system edits.

## Objective and approved refinement

Inspection found independently committing invite/registration/login-signal and
fix_secret/session/cookie/external-hook composites. Holding the SQL environment
lock through their first commit would not serialize their later writes; changing
non-SQL effects/commit ownership requires further approval and qualification.
The supervisor approved the one permitted sub-split: implement SQL profile/account/
role views and helper add_privilege with actual-writer PostgreSQL races, preserve
and fence every other composite by name. No further sub-split was taken. This is
implemented writer integration, not a plan-only handoff; it is intentionally NOT
completion of the originally requested whole writer inventory.

## Implementation

- users/views.edit_user_profile_page SQL profile/active/role replacement and
  MFA-disable/email-confirmation-reset branches acquire EnvironmentState on the
  SAME session connection before reading target/current SQL authority. Deletion
  branches remain unqualified multi-store composites, not covered by this claim.
- users/views.user_profile_page POST, user_add POST, add_privilege POST and
  users/helpers.add_privilege take the same environment-first lock. Expiring ORM
  state after acquisition prevents preloaded actor/target memberships from being
  reused across a blocking lock acquisition. Administrative views recheck their
  role-or-permission guard under the lock, rather than relying on decorators that
  ran before it. Existing commit ownership is unchanged: each included SQL branch
  commits its complete mutation once; failures roll back at the caller/request
  session boundary. No separate connection or generic decorator was introduced.
- Flag-off branches retain their legacy bodies and acquire no managed lock.
  Missing/unmanaged baseline rejects before included SQL writes. Read-only GET
  profile/form routes do not acquire new locks. Existing restricted image ingress,
  unsupported mutation/source fences and public publication restrictions are NOT
  weakened or broadened by these internal writer integrations.
- Required 05a2b4 P2 documentation correction: retained pages are **generated
  snapshot index pages**, not raw upstream HTML. Raw pages remain memory-only;
  rejected upstream pages can contain credentials and are never retained.
- Required P2 HTTPS fixture correction: artifact URL is now
  `filename?token=...#sha256=...`, with a valid SHA256 fragment. It asserts the
  URL-secret rejection message, exactly one index-page request and NO artifact
  request, no secret in error/request paths, and raw/base64 absence across final
  retained evidence. No downloader security policy changed. Proof driver accepts
  evidence/container environment overrides so historical evidence is preserved.

## Actual-writer qualification and limits

98 new PostgreSQL cases: 49 scenarios on EACH fresh and upgrade initialization.
Nine actual source-body writer variants: active revocation, role replacement,
profile edit, MFA disable, email reset, self profile, user_add, view add_privilege,
and helper add_privilege. Both real concurrent lock directions use independent
pg_blocking_pids observation before releasing the first transaction. Writer-first
active/role revocation rejects approval with no intent; publication-first commits
one immutable revision/attempt/grant and later changes do not undo it. Other SQL
edits serialize but do not spuriously invalidate ordinary authorization.

Actual SQL flush followed by injected commit failure proves rollback for all nine
branches. Flag-off and missing-managed-baseline checks cover all nine. Four
additional races preload actor roles, block behind a real role revocation, then
prove the included administrative view/helper rechecks reject after acquisition.
Uncommitted SQL and every shared protocol/Package/grant record are independently
observed while blocked. No counts/exclusions or existing assertions were relaxed.

Tests execute exact view/helper function bodies with real SQLAlchemy Sessions and
PostgreSQL, reflected minimal account schemas, and actual Publication,
PreviewAuthorizer and PackageProjection. Forms, current-user permission methods,
request/translation/render/redirect dependencies are supplied. This is NOT full
Flask route/decorator/form-validation/authentication qualification, external
config/Redis policy serialization, or full production writer-safety acceptance.
Existing restricted experimental ingress remains in place.

## Verification and evidence

Evidence: `05b2b7/evidence/accepted/`. Exact argv/exit codes are in
`*-command.json`, merged normalized output in corresponding `*.log`. Commands
below are single-line shell invocations from repository root, all exit 0:

```sh
python3 uv-experiment/05b2b7/evidence/accepted/capture.py focused env PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -x -p no:cacheprovider uv-experiment/preparation/tests/test_publication_profile_views_postgres.py
python3 uv-experiment/05b2b7/evidence/accepted/capture.py postgres env PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
python3 uv-experiment/05b2b7/evidence/accepted/capture.py private-index env PROOF_EVIDENCE_DIR=uv-experiment/05b2b7/evidence/accepted/private-index PROOF_CONTAINER_NAME=da-05b2b7-private-index python3 uv-experiment/run-private-index-proof.py
python3 uv-experiment/05b2b7/evidence/accepted/capture.py recovery env PROOF_IMAGE=da-uv-05c5-fix:local PROOF_CONTAINER_NAME=da-05b2b7-recovery PROOF_EVIDENCE_DIR=uv-experiment/05b2b7/evidence/accepted/recovery PROOF_FLOW_ACTION=recovery python3 uv-experiment/run-image-bridge-proof.py
python3 uv-experiment/05b2b7/evidence/accepted/capture.py continuous env PROOF_IMAGE=da-uv-05c5-fix:local PROOF_CONTAINER_NAME=da-05b2b7-continuous PROOF_EVIDENCE_DIR=uv-experiment/05b2b7/evidence/accepted/continuous python3 uv-experiment/run-continuous-work-proof.py
python3 uv-experiment/05b2b7/evidence/accepted/capture.py image-bridge env PROOF_IMAGE=da-uv-05c5-fix:local PROOF_CONTAINER_NAME=da-05b2b7-image-bridge PROOF_EVIDENCE_DIR=uv-experiment/05b2b7/evidence/accepted/image-bridge python3 uv-experiment/run-image-bridge-proof.py
python3 uv-experiment/05b2b7/evidence/accepted/capture.py act act workflow_dispatch -W .act/checks.yml
python3 uv-experiment/05b2b7/evidence/accepted/capture.py lint uvx ruff check --select E9,F docassemble_webapp/docassemble/webapp/users/views.py docassemble_webapp/docassemble/webapp/users/helpers.py uv-experiment/preparation/tests/test_publication_profile_views_postgres.py uv-experiment/preparation/private_index_proof.py uv-experiment/run-private-index-proof.py
python3 uv-experiment/05b2b7/evidence/accepted/capture.py source env PYTHONPATH=docassemble_base uv run --project uv-experiment/executor --frozen python uv-experiment/05b2b7/evidence/accepted/verify-evidence.py
python3 uv-experiment/05b2b7/evidence/accepted/capture.py diff-check git diff --cached --check
```

Results:

- Focused actual writers: **98 passed in 25.83s**. Initial 90-case run also passed;
  final run adds eight fresh/upgrade stale-actor regressions.
- Full publication/preparation PostgreSQL: **607 passed in 110.46s**.
  Intentional 509 -> 607 adds exactly those 98 cases, no new skips.
- HTTPS private index: **nine PASS groups**, including the corrected artifact
  query rejection after only an index request, distinct same-host credentials,
  TLS, offline cold-cache frozen sync, exact private core, retained-byte checks
  and raw/base64 secret absence. All referenced wheels/hidden files are retained.
- Recovery: **nine PASS groups including completion**; continuous: **six PASS
  groups including completion**, plus its unchanged authenticated normal fixture;
  image bridge: **eight PASS groups including completion**.
- Act: **372 passed, 1 skipped in 32.65s**; mypy **57 files, no issues**. Counts
  unchanged, existing bare-act Supervisor skip unchanged. No expectation edits.
- Ruff E9/F: **All checks passed!**; evidence verifier: all private retained
  bundles valid, private disposable secrets absent, **25 source hashes verified**.
- `image-source-command.json` records the exact additional Python subprocess
  command verifying seven image-installed executor modules against current source:
  installation, controller, durable_work, preparation, published, boot_observation,
  uvsync. All match; output includes the exact hashes. This does NOT claim the
  changed SQL views are installed in that historical image.

Image used throughout: existing `da-uv-05c5-fix:local`,
`sha256:3ba622c2fbf1478eac35c35eabafb87f260473247812d41efa97640eed415e45`,
Python 3.12.14, uv 0.12.21. Private-index proof runs current read-only mounted
source, non-root UID1000/cap-drop/no-new-privileges. Operational proofs use the
unchanged image-installed executor; mounted proof controls are not new SQL-view
integration evidence. No current-source image rebuild is claimed. New SQL writers
are qualified by the actual-source PostgreSQL tests, not those historical web
routes. Existing containers/images/volumes/evidence were preserved; this run's
four proof containers are left intact. Initial staged diff check found only
trailing whitespace in copied service logs; those log lines were normalized
without changing retained bundle bytes, then the check reran. The initial output
is preserved as diff-whitespace-before.log. No heredocs or shell redirection used.

## Precise remaining fenced list

Paths relative to docassemble_webapp/docassemble/webapp unless stated otherwise.
Prior separately qualified helpers stay qualified; enclosing composites do not
inherit qualification merely by calling them.

### Remaining writer composites/callers/races (not closed)

- users/views.edit_user_profile_page **delete_account/delete_account_complete**
  user_interviews + delete_user_data enclosing multi-store branches; users/views
  **custom_register, invite, manage_account**. SQL profile/MFA/email, user_add,
  user_profile_page and add_privilege branches above are the only new closures.
- users/helpers.**invite_user, login_or_register**; users/signals.**on_user_login,
  on_register_hook, on_password_change**; interview/helpers.**fix_secret** and
  remaining direct **substitute_secret** enclosing callers; cookie/session/auth
  and external manage_global_objects/manage_tts_objects/manage_chat_logs plugin
  effects/independent commits. Prior built-in transaction-owned SQL modes alone
  remain qualified; no arbitrary plugin atomicity claim.
- users/forms.**MySignInForm.validate** LDAP/default-role account creation;
  auth/views.**oauth_callback**; phonelogin/views.**phone_login_verify**;
  create_tables.**get_role, get_user, populate_tables** account/default-role and
  fix-user-roles passes, their registration/auth/secret/session/hook composites.
- admin/views.**config_page** cloud/local save + fail-closed observation;
  **startup.initialize/read_config** policy refresh; **base/read_config** and
  **Docker/initialize.sh** dependency/index/PYTHONPACKAGES observation; executor
  **preparation/controller/CLI** dependency/policy refresh before imports/work;
  **config.allowed, ALLOW_UPDATES, DEVELOPER_CAN_INSTALL** and name/source/
  dependency-policy writer serialization. Operator edits retain the explicit
  observed-snapshot/drift boundary, never a SQL/file atomicity claim. Config
  imports remain unsupported; no refresh capability is inferred from reads.
- utils/helpers.**install_zip_package, install_git_package, install_pip_package,
  uninstall_package**; packages/views.**update_package**;
  packages/api.**api_package**; develop/views.**playground_packages** ZIP caller.
  Existing managed reject/prepare fences stay intact; no unsupported grant/source
  or intent mutation is re-enabled.
- update.**clear_invalid_package, clear_invalid_package_auth, check_for_updates,
  add_dependencies, fix_names, install_package, uninstall_package, update_versions**;
  tasks/tasks.**update_packages, reset_server**; direct update CLI callers;
  create_tables.**populate_tables** package rewrites/**add_dependencies**. Existing
  managed reject/skip guards are NOT actual-writer race closure. Remaining
  PackageAuth/grant/name/source/dependency-policy complete caller audit and races
  remain required; prior PackageProjection/deletion proof is unchanged.

### Other unchanged parent-05/later fences

- **05c5b** universal live observation/status/all-role readiness/lost-success
  closure; cold uncertain/unreported recovery, management-table-outage boot
  restoration, restricted core-only service/admin resumption; broader failed/
  recovered/crash/same-revision rollover; explicit uncertain-work/schema/setup
  clearance; config/dependency/policy refresh. No automatic retry, heartbeat
  release, uncertain-journal deletion, schema replay or data restore is added.
- **05a2b5** HTTPS Git refs/subdirectories/SHA and scoped authentication, private
  sdists/build authentication, candidate claim consumption/promotion/rotation/
  revocation and authorized updater credential reuse, production private policy/
  source authorization/dispatch, mixed indexes/direct URLs/immutable ZIPs, broader
  source/target/build qualification. The bounded private-wheel snapshot is NOT
  full source parity or production private dispatch.
- Production managed initialization/adoption, broader installed-material/store
  qualification, update/remove/config import, coherent upgrade/rollback/adoption/
  backup/image migration/general managed API breadth and **tickets 06+** remain
  fenced. Existing restricted experimental approval does not become general
  production approval. NO parent-05/05b/05c/full writer-safety acceptance claim.

## Resolution and limits

Approved SQL-view chunk and both mandatory P2 fixes implemented and checked.
Independent review remains required. Parent 05 Status/checklist unchanged.
`ticket-05b2b7/diff.patch` spans `3a774a5f..HEAD`; it and change-summary.md remain
untracked. No second sub-split, new-system edit or branch switch.

Next: independent review of 05b2b7; named remaining writer, operational and source
fences must close before parent 05 acceptance; 06+ remain blocked.
