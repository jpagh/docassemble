# 05b2b6: old-password SQL authority and built-in secret hooks

Date: 2026-10-14. Branch: `uv-implementation`. Baseline:
`ed83ba1595e81af3e1b8e30794559cef240511c2`.

**PUBLIC APPLY-PRODUCING APPROVAL AND RETRY REMAIN FENCED UNTIL 05c.**
No production writer-safety, complete 05b2b, 05c, 05a2b4 or parent-05 acceptance.
Independent review required; parent Status/checklist unchanged; 06 blocked.

## Objective and approved bounded refinement

The supervisor approved the single permitted sub-split: land old_password
set_user_info + substitute_secret SQL serialization and real writer races;
fence remaining account/config/policy composites by name. Inspection then found
that the built-in global/TTS/chat callbacks independently commit the same ORM
session. The supervisor explicitly approved including only their substitution
branches using the existing kwargs transaction-owned flag. No second sub-split,
generic hook decorator or external-plugin atomicity claim was taken.

This is an implemented SQL writer chunk, NOT completion of the entire remaining
05b2b5 inventory. No new-system edits, branch switch, schema or dependency change.

## Implementation

- users/helpers.set_user_info now takes the environment lock and expires cached
  SQL authority for old_password as well as the previously qualified SQL-only
  path. Managed privilege validation/replacement shares the password and active
  changes in one transaction; no intermediate password commit before roles.
- interview/helpers.substitute_secret takes the same session's environment lock
  before its reads/hooks. Managed SQL dictionary substitutions flush, never
  independently commit. Caller owns final commit/rollback; direct managed callers
  with no enclosing commit remain unqualified, not silently auto-committed.
- Built-in daglobal/hooks.manage_global_objects modes 6/7,
  tts/hooks.manage_tts_objects mode 4, and monitor/hooks.manage_chat_logs mode 6
  receive transaction_owned through existing kwargs and flush when owned. Their
  other modes are unchanged. Flag-off substitution and default hook invocations
  retain their existing independent commits.

EnvironmentState precedes secret/authority row locks on the SAME SQL connection.
No independent lock connection or distributed transaction was introduced.

External plugins/hook dispatch, crypto, session cookies and authentication are
NOT qualified here. External hooks may have non-SQL effects or commit the ORM
session; such a plugin breaks this bounded SQL transaction assumption. Failed
SQL work cannot roll back prior file/cloud/Redis/plugin effects. There is no
restoration, retry, cleanup or forensic-erasure claim. Existing decrypt-error
handling is unchanged (this is not a password/data-recovery policy change).
Only the exact built-in SQL substitution branches are included in the proof.

## Actual-source PostgreSQL qualification

34 cases: 17 scenarios on each fresh/upgrade schema initialization. Tests execute
actual set_user_info, substitute_secret, daglobal, TTS and monitor function bodies
with real SQLAlchemy Sessions and PostgreSQL. Built-in hook tables are reflected
minimal fixtures; encryption, request/session/current-user and hook dispatch are
supplied fixtures, not complete Flask/plugin integration.

Both lock directions cover password-only, active revocation and role replacement.
Independent pg_blocking_pids observation establishes blocking before release.
Writer-first revocations leave protocol/Package intent unchanged and reject
approval; publication-first commits once and later revocation never undoes intent.
Actual global/TTS/chat substitutions remain uncommitted while blocked and commit
with password/roles exactly once. Wrong password, missing role, external hook
failure and final SQL commit failure roll back SQL; the external-hook fixture's
recorded side effect is deliberately not rolled back. Missing managed baseline
rejects before any hook. Managed substitution leaves commit ownership to caller;
flag-off tests exercise both raw substitution and all actual built-in callbacks,
including the temporary-user branch and no environment-lock acquisition.

## Verification and evidence

Qualified command/output logs are in `05b2b6/evidence/accepted/`; the directory
name follows the task and is NOT independent acceptance. Commands were captured
with single-line Python subprocess wrappers (no shell redirection/heredocs).
Disposable PostgreSQL and Redis from prior slices were reused. No new skips.
The initial lint run found fixture noqa placement issues; corrected and rerun;
that failed output remains untracked in evidence/development. Early 30-case runs
were superseded by the final 34-case proof after adding actual flag-off hooks.

```sh
PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -p no:cacheprovider uv-experiment/preparation/tests/test_publication_secret_writers_postgres.py
# 34 passed in 8.98s
# exit 0
```

```sh
PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -p no:cacheprovider uv-experiment/preparation/tests/test_publication_secret_writers_postgres.py uv-experiment/preparation/tests/test_publication_api_key_writers_postgres.py uv-experiment/preparation/tests/test_publication_account_writers_postgres.py uv-experiment/preparation/tests/test_publication_retry_postgres.py uv-experiment/preparation/tests/test_publication_view_writers_postgres.py uv-experiment/preparation/tests/test_publication_helper_writers_postgres.py uv-experiment/preparation/tests/test_publication_writers_postgres.py uv-experiment/preparation/tests/test_publication_postgres.py
# 252 passed in 42.04s
# exit 0
```

```sh
PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
# 365 passed in 60.72s (0:01:00)
# exit 0
```

```sh
act workflow_dispatch -W .act/checks.yml
# [checks/native suite + typecheck]   | 292 passed, 1 skipped in 30.53s
# [checks/native suite + typecheck]   | Success: no issues found in 51 source files
# [checks/native suite + typecheck] 🏁  Job succeeded
# exit 0
```

```sh
uv tool run ruff check --select E9,F docassemble_webapp/docassemble/webapp/users/helpers.py docassemble_webapp/docassemble/webapp/interview/helpers.py docassemble_webapp/docassemble/webapp/daglobal/hooks.py docassemble_webapp/docassemble/webapp/monitor/hooks.py docassemble_webapp/docassemble/webapp/tts/hooks.py uv-experiment/preparation/tests/test_publication_secret_writers_postgres.py
# All checks passed!
# exit 0
```

Publication 218 -> 252 and preparation aggregate 331 -> 365 intentionally add
34 cases. No expected-count assertions, schema/native/typecheck counts or
exclusions changed. Act's one skip remains the existing bare-image Supervisor
skip; mypy checks 51 files. Static writer-inventory.log has 167 candidate hits;
it is inspection evidence, NOT complete actual-writer closure qualification.

## Fenced remainder / precise inventory

All web paths below are relative to docassemble_webapp/docassemble/webapp.
Prior qualified helpers are not unqualified again, but surrounding composites
are not qualified merely because they invoke a cooperating helper.

**Account/profile/registration/default-role composites:**

- users/views.edit_user_profile_page (profile/active/role replacement, MFA/email
  confirmation and user_interviews + delete_user_data deletion composites),
  user_profile_page, user_add, custom_register, invite, add_privilege, manage_account.
- users/helpers.login_or_register; users/signals.on_user_login, on_register_hook
  (default/invite role replacement), on_password_change. Their fix_secret/session
  substitutions and commit ownership remain unqualified.
- users/forms.MySignInForm.validate LDAP account/default-role creation;
  auth/views.oauth_callback and phonelogin/views.phone_login_verify account/auth,
  secret/session and registration-signal composites.
- create_tables.get_role, get_user, populate_tables account/default-role creation
  and fix-user-roles pass. Managed package initialization skip is not account proof.
- interview/helpers.fix_secret and other direct substitute_secret callers above:
  complete enclosing transaction qualification remains required. External plugin
  implementations of manage_global_objects, manage_tts_objects and manage_chat_logs
  remain unqualified; built-in SQL substitution modes alone are covered here.

**Config observation and dependency-input refresh:**

- admin/views.config_page cloud/local save and fail-closed observation after save.
- startup.initialize/read_config policy loading/refresh; base/read_config
  dependency/index inputs; Docker/initialize.sh PYTHONPACKAGES/index observation;
  executor preparation/controller/CLI startup observation refresh before imports.
- config.allowed, ALLOW_UPDATES, DEVELOPER_CAN_INSTALL and name/source/dependency
  policy refresh serialization. No config-import enablement added. Operator edits
  remain the observed-snapshot/drift boundary, never SQL-file atomicity.

**PackageAuth/grant/name/source/dependency-policy writers:**

- utils/helpers.install_zip_package, install_git_package, install_pip_package,
  uninstall_package; packages/views.update_package, packages/api.api_package;
  develop/views.playground_packages ZIP caller. Existing managed reject/prepare
  fences remain; unsupported source/intent/grant mutation is not re-enabled.
- update.clear_invalid_package, clear_invalid_package_auth, check_for_updates,
  add_dependencies, fix_names, install_package, uninstall_package, update_versions;
  tasks/tasks.update_packages/reset_server and direct update CLI callers. Existing
  managed rejection is not real writer race qualification.
- create_tables.populate_tables package source rewrites and add_dependencies
  remain skipped/fenced in managed mode; other dependency/source/name refresh
  remains unqualified. Prior PackageProjection/deletion grant proof is unchanged.
- Complete actual-writer/caller closure audit and real races for this named
  remainder are still required. No production writer-safety acceptance claim.

05c still owns installation polling, live frozen apply, durable child-safe
terminal evidence/slot release, operational retry clearance, verified running/
recovery identity and browser/API end-to-end enablement. Public approve/retry
MUST remain fenced until 05c operational proof AND writer prerequisites complete.
05a2b4 credential-source decision, authenticated injection/private binding and
broader source/target/build policy remain fenced. No parent acceptance claimed.

## Resolution and limits

Supervisor-approved bounded secret SQL chunk implemented and locally checked,
subject to independent review. Named remaining writers are prerequisites, not
discarded work. Diff/change summary stay untracked under ticket-05b2b6. Existing
untracked artifacts preserved; parent 05 Status/checklist unchanged.

Next: 05c.
