# 05b2b9: Final managed built-in SQL writer convergence

Date: 2026-10-15 UTC. Branch: `uv-implementation`. Baseline:
`283c16ff1df6c79f971d530bc69807c8ffc130a3`.

**PARENT 05 READY FOR ACCEPTANCE under the supervisor-approved EXTENDED readiness
rule below; NOT yet accepted.** Independent review is required. Parent Status and
checklist are unchanged. **06+ remain blocked until supervisor acceptance.** No
branch switch, new-system edits, later-ticket implementation, managed ingress
relaxation or legacy rejection removal.

## Approved boundary and readiness basis

The supervisor explicitly approved mandatory built-in SQL even when a plugin
would override a legacy firstresult hook: managed SQL authority cannot be
replaced by a plugin. Exact built-in implementations run precommit with
flush-only ownership. Plugin-only dispatch uses `subset_hook_caller` excluding
the exact built-in module and runs postcommit, with its captured thread context.
Plugins remain callable and additive; no arbitrary plugin/external effects are
claimed transactional. Flag-off retains legacy firstresult substitution and
independent commits.

The original readiness rule allowed only (a) by-design legacy fences,
(b) contract-permitted plugin/external non-DB effects and (c) ticket-08 config.
The supervisor explicitly EXTENDED it with **(d) by-design restricted-experiment
fences mapped to later tickets**, not implemented here. The complete maps below
are also appended to `TICKET-05-SPLIT.md`. This is an explicit approved boundary
change, not a claim that 05c5b or 05a2b5 were implemented.

All DB-affecting built-in managed-path writers in the exact 05b2b8 remaining list
are now integrated. There is no remaining built-in SQL composite from that list.
The remaining fences are exactly categories (a)-(d), with the later operations
listed below. This readiness claim is for the restricted experimental parent-05
registry path, NOT general production/source/recovery parity or acceptance itself.

## Implementation and explicit commit owners

- `users/views.edit_user_profile_page:delete_account` and
  `:delete_account_complete`: acquire and recheck administrative authority before
  loading target/capturing interview metadata. `delete_managed_account` owns the
  complete SQL interview/shared cleanup, account revocation, PackageAuth/identity
  reassignment and pending cleanup record commit. SQL is never moved after
  revocation. Captured checkout/plugin/file/Redis effects then run; a second,
  environment-locked commit records complete or fixed redacted failure.
- `users/views.manage_account`, authenticated and anonymous: lock/expire before
  account checks. The same enclosing owner handles authenticated deletion before
  logout. Anonymous deletion commits interview, upload-auth, global, chat and
  email SQL once before session/store cleanup. Both anonymous shared-delete
  settings are preserved (including other users' shared rows when false).
  Anonymous cleanup has no new durable identity/schema or recovery protocol.
- `users/helpers.login_or_register`: explicit caller-owned effects list for
  `sub_temp_user_dict_key`, `save_user_dict_key`, `fix_secret`, and `sub_temp_other`.
  Standalone invocation owns one environment-locked commit; enclosing callers
  pass their effects list and retain commit ownership. Legacy cookie-session
  inputs are captured without precommit session migration. User ID/language,
  newsecret, key-logged and tempuser/session effects are postcommit.
- `users/signals.on_user_login` and `on_register_hook`: own timestamp/default-role
  plus full built-in ownership/secret SQL in one transaction. The timestamp-only
  `update_last_login` helper stays non-reentrant for `api_verify` as before.
- `users/views.custom_register`: account/auth/invitation-role plus built-in login
  migration commit together before email or signal dispatch. The built-in
  registration receiver skips the already-committed migration; a request-local
  migrated-user marker prevents a second auto-login signal transaction. Tests
  execute registration signal AND auto-login, not an account-section stub.
- Managed registration mail-failure compensation intentionally changes from
  deleting only the new account row to serialized interview/account cleanup via
  `delete_managed_account` / `delete_managed_user_data`. This retains a disabled
  identity instead of orphaning migrated ownership, removes migrated built-in
  rows, and durably records pending/complete/redacted failed cleanup. Duplicate
  cleanup rejects safely without replacing prior evidence; no retry/resume or
  crash-recovery expansion. Flag-off still deletes the account as before.
- `users/signals.on_password_change` delegates to the now owning `fix_secret`.
  Direct `fix_secret` / `substitute_secret` own an environment-locked commit;
  supplied effects lists mean caller-owned flushes. Session newsecret is not
  published before that commit. Flag-off `fix_secret` still returns None.
- `auth/views.oauth_callback` and
  `phonelogin/views.phone_login_verify`: external credential validation remains
  outside SQL; account, timestamp, interview/upload/global/chat ownership and
  secret migration share one commit before authentication/session/cookie effects.
  They use the same explicit built-in migration helper; flag-off bodies retain
  their original ordering (including legacy phone's absent other-object migration).
- `users/helpers.set_user_info:old_password`: password/active/role plus all
  built-in secret SQL remain one transaction; plugin effects now run afterward.
- Built-in hooks: `daglobal/hooks.manage_global_objects` modes 0/1/2/3/6/7,
  `monitor/hooks.manage_chat_logs` modes 0/1/2/3/6,
  `tts/hooks.manage_tts_objects` modes 1/4 and
  `emailserver/helpers.manage_email_server_objects` modes 0/1/2 support the
  explicit flush-only ownership. Email attachment file deletion is queued after
  commit; managed mode 2 uses the actual filename (the flag-off spelling is
  unchanged). No arbitrary whole-hook dispatch runs inside the SQL transaction.

`users/managed_effects.py` is the narrow named hook/effect split, not a universal
transaction wrapper. No separate SQL connection, schema, source/permission policy,
production adoption, automatic retry, or generic plugin transaction protocol added.

## Required P2 invitation regression

`test_publication_invitation_batch_postgres.py` uses the actual `invite` body,
SQLAlchemy Sessions and PostgreSQL. Two distinct invitation tokens generated by the test signer
are visible through a separate connection before the first send. The first
recipient succeeds and the second delivery fails; only the failed invitation is
revoked. A separate flush-then-precommit failure sees both pending tokens locally,
neither independently, sends nothing and leaves neither invitation persisted.
Both fresh and upgrade initialization paths execute these tests.

## Qualification and exact commands

Qualified evidence: `05b2b9/evidence/accepted/`. Every `*-command.json` retains
exact inner argv and exit, with complete normalized merged `*.log`. The wrapper
itself records no secrets from exception text. Commands from repository root:

```sh
python3 uv-experiment/05b2b9/evidence/accepted/capture.py focused env PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -x -p no:cacheprovider uv-experiment/preparation/tests/test_publication_convergence_postgres.py uv-experiment/preparation/tests/test_publication_invitation_batch_postgres.py
python3 uv-experiment/05b2b9/evidence/accepted/capture.py postgres env PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
python3 uv-experiment/05b2b9/evidence/accepted/capture.py private-index env PROOF_EVIDENCE_DIR=uv-experiment/05b2b9/evidence/accepted/private-index PROOF_CONTAINER_NAME=da-05b2b9-private-index python3 uv-experiment/run-private-index-proof.py
python3 uv-experiment/05b2b9/evidence/accepted/capture.py recovery env PROOF_IMAGE=da-uv-05c5-fix:local PROOF_CONTAINER_NAME=da-05b2b9-recovery PROOF_EVIDENCE_DIR=uv-experiment/05b2b9/evidence/accepted/recovery PROOF_FLOW_ACTION=recovery python3 uv-experiment/run-image-bridge-proof.py
python3 uv-experiment/05b2b9/evidence/accepted/capture.py continuous env PROOF_IMAGE=da-uv-05c5-fix:local PROOF_CONTAINER_NAME=da-05b2b9-continuous PROOF_EVIDENCE_DIR=uv-experiment/05b2b9/evidence/accepted/continuous python3 uv-experiment/run-continuous-work-proof.py
python3 uv-experiment/05b2b9/evidence/accepted/capture.py image-bridge env PROOF_IMAGE=da-uv-05c5-fix:local PROOF_CONTAINER_NAME=da-05b2b9-image-bridge PROOF_EVIDENCE_DIR=uv-experiment/05b2b9/evidence/accepted/image-bridge python3 uv-experiment/run-image-bridge-proof.py
python3 uv-experiment/05b2b9/evidence/accepted/capture.py act-final act workflow_dispatch -W .act/checks.yml
python3 uv-experiment/05b2b9/evidence/accepted/capture.py source env PYTHONPATH=docassemble_base uv run --project uv-experiment/executor --frozen python uv-experiment/05b2b9/evidence/accepted/verify-evidence.py
python3 uv-experiment/05b2b9/evidence/accepted/capture.py lint uvx ruff check --select E9,F docassemble_webapp/docassemble/webapp/auth/views.py docassemble_webapp/docassemble/webapp/phonelogin/views.py docassemble_webapp/docassemble/webapp/users/helpers.py docassemble_webapp/docassemble/webapp/users/views.py docassemble_webapp/docassemble/webapp/users/signals.py docassemble_webapp/docassemble/webapp/users/managed_effects.py docassemble_webapp/docassemble/webapp/interview/helpers.py docassemble_webapp/docassemble/webapp/daglobal/hooks.py docassemble_webapp/docassemble/webapp/tts/hooks.py docassemble_webapp/docassemble/webapp/monitor/hooks.py docassemble_webapp/docassemble/webapp/emailserver/helpers.py uv-experiment/preparation/tests/test_publication_convergence_postgres.py uv-experiment/preparation/tests/test_publication_invitation_batch_postgres.py uv-experiment/preparation/tests/test_publication_registration_postgres.py uv-experiment/preparation/tests/test_publication_secret_writers_postgres.py uv-experiment/preparation/tests/test_publication_account_writers_postgres.py
python3 uv-experiment/05b2b9/evidence/accepted/capture.py diff-check git diff --cached --check
```

Final outputs (all exit 0):

- Focused **196 passed in 77.72s**; full **963 passed in 232.23s**. Intentional
  **767 -> 963 (+196)**, no exclusions/skips/count assertions changed. Native
  expectations unchanged. Four new P2 cases plus 192 enclosing-writer cases.
- Fourteen enclosing variants, both lock directions actually observed with
  `pg_blocking_pids`, independent unchanged uncommitted SQL/protocol/intent
  snapshots, flush-then-commit-failure rollback, missing baseline, flag-off and
  explicit commit counts. Actual built-in SQL executes, not a migration stub.
  Additional cases cover firstresult override semantics, postcommit plugin
  failure, durable redacted deletion failure/duplicate safety, registration
  ownership compensation, legacy session capture and both anonymous shared modes.
- Private index **nine PASS groups**. Recovery **nine PASS groups including
  completion**. Continuous **six PASS groups including completion**, plus its
  authenticated normal fixture. Image bridge **eight PASS groups including
  completion**. Raw complete proof output is retained, not only counts.
- Final act **372 passed, 1 skipped in 32.98s**; mypy **57 files/no issues**.
  The existing bare-act Supervisor skip and expected counts are unchanged.
- Ruff E9/F **All checks passed!** Source verifier **46 hashes verified**, all
  private retained bundles byte-verified, raw/base64 disposable private secrets
  absent. `image-source-command.json` retains exact Python argv and seven
  image-installed executor module hashes matching current unchanged source.

Tests compile exact production function bodies and hook dispatcher with real
PostgreSQL Sessions/Publication/PreviewAuthorizer/PackageProjection and reflected
account/hook schemas. Flask/decorators/forms/credential verification/crypto/store
objects are supplied. This is NOT full authenticated Flask, cryptography,
arbitrary-plugin SQL or external-store qualification.

Operational proofs reuse historical `da-uv-05c5-fix:local`, image
`sha256:3ba622c2fbf1478eac35c35eabafb87f260473247812d41efa97640eed415e45`,
Python 3.12.14/uv 0.12.21. Changed SQL writers are NOT installed in that image;
their qualification is actual-source PostgreSQL, not a rebuilt-image claim.
Private proof mounts current source. Final shared-anonymous/return-value fixes
were followed by the full 963/focused196/act gates; unchanged image-installed
operational modules are separately hash-qualified. No current-source image
rebuild is claimed.

Development failures/intermediate passing suites are isolated in untracked
`05b2b9/evidence/development/`, not acceptance evidence. Early failures were
fixture expectations/stubs; final self-review also preserved flag-off fix_secret
return behavior and anonymous shared cleanup semantics with explicit regressions.
After successful recovery/continuous proofs, Docker was 96% full / 4.0G available.
Supervisor approved removing ONLY this run's two evidence-copied containers;
`cleanup-inspect`, `image-source`, `cleanup-own-containers` and
`cleanup-build-cache` commands/logs record identities/actions (unused cache: 0B).
Historical containers/images, all volumes/evidence and current proof image were
preserved. Remaining image bridge/act passed. No Colima expansion, heredocs or
shell redirection used. Initial staged whitespace check found trailing spaces in
Docker WebSocket/Celery logs; normalized only text log whitespace (never retained
bundle bytes), preserved the failed check in development evidence, and reran
the staged check successfully.

## Remaining fences and mapped later tickets

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

## Resolution

The exact built-in DB remainder from 05b2b8 is closed, including the required P2
batch regression. **PARENT 05 READY FOR ACCEPTANCE under approved categories
(a)-(d)**, subject to independent review of both implementation and the explicit
fence mapping. This is not acceptance or a production rollout decision. Parent
Status/checklist unchanged; **06+ stay blocked** until supervisor acceptance.
The requested baseline-to-HEAD diff and change summary remain UNTRACKED in
`uv-experiment/ticket-05b2b9/`. Development evidence is also untracked.

Next: independent parent-05 acceptance review; no ticket-06 implementation.
