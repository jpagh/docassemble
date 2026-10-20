# 05b2b5: Redis API-key authority writer serialization

Date: 2026-10-14. Branch: `uv-implementation`. Baseline:
`a84c88a3e6f7584547649640156df115edac053f`.

**PUBLIC APPLY-PRODUCING APPROVAL AND RETRY REMAIN FENCED UNTIL 05c.**
No production writer-safety, 05c, 05a2b4, full 05b2b or parent-05 acceptance.
Independent review required. Parent Status/checklist unchanged; 06 blocked.

## Objective and approved bounded refinement

The supervisor approved the task's single permitted sub-split after inspection:
land the coherent Redis API-key writer integration with real writer races and
full regression gates; explicitly fence the remaining account/config/policy
composites. No further sub-split was taken. This is implementation, not a
plan-only handoff, but is NOT the complete remaining-writer slice originally
requested. Complete production writer audit/qualification remains blocked on
the named remainder below.

## Implementation

`utils/managed_api_keys.py` owns a flag-gated environment-first lock context and
writer decorator. Managed callers take the singleton lock on an independent
SQL connection before Redis reads or mutations, hold it until the operation
returns or raises, and release on commit/rollback. An absent/unmanaged baseline
rejects before Redis access. The independent transaction avoids committing or
rolling back unrelated caller ORM work. Flag-off takes no DB connection and
executes the original writer body unchanged. No schema, generation or native
expected-count changes. The module lives in utils rather than api to avoid
api/__init__ importing views while utils/api_key is only partly initialized.

Integrated actual writers:

- api/helpers.add_api_key, update_api_key (permissions AND constraints),
  delete_api_key and fix_api_keys. fix_api_key is a pure key transformation;
  its Redis migration caller now holds the lock for the whole scan/move/delete.
- api/views.manage_api: existing direct new/edit/delete bodies are retained
  unchanged, now inside the same lock. This includes reads used to edit a key;
  the lock also spans rendering/list reads. login_required remains outside.
- utils/api_key.add_specific_api_key: collision/name reads and write serialized.

Existing api/api.do_api_user_api (DELETE/POST/PATCH), api_user_api and
api_user_userid_api callers continue invoking those decorated helpers; startup.initialize invokes decorated fix_api_keys;
create_tables.populate_tables invokes decorated add_specific_api_key after its
account transaction. No direct Redis authority writers were found outside these
functions by the API-key-prefix search. Arbitrary operator Redis writes are not
covered. This does not freshly authorize every administrator caller or change
legacy permission field formats (including the view's existing new-key limits
field). Those are not silently repaired in a serialization slice.

Redis is NOT SQL-atomic. A failed acknowledgement can leave a mutation applied;
rollback never restores earlier credentials/permissions. Publication rereads
current Redis authority under the environment lock and fails closed on outage.
These tests prove serialization of the writer boundary, not distributed atomic
commit, authenticated Flask request integration, or complete production safety.
There is no automatic retry, cleanup daemon or forensic erasure claim.

## Real writer qualification

42 new PostgreSQL cases: 21 scenarios each on upgrade/fresh schema initialization.
Actual source bodies and actual decorator/context/lock_authority execute against
real PostgreSQL and real disposable Redis 7.4.2. Actual PreviewAuthorizer and
PackageProjection execute; request credential/constraint evaluation, forms,
current_user and rendering are supplied fixtures, NOT complete api_verify or
Flask/plugin integration. Independent pg_blocking_pids observation proves both
lock directions before release, not merely timing or an event assertion.

Nine writer paths in both directions: helper add, specific add, permission edit,
constraint-only edit, delete, legacy migration, view new/edit/delete. Revocations
that lock first reject approval with unchanged protocol snapshots and no partial
intent. Additions preserve unrelated authority. Publication that locks first
commits once before later revocation; revocation never undoes desired intent.
Additional cases cover lost Redis acknowledgement/released SQL lock, flag-off
no DB locking and missing managed baseline/no Redis mutation.

The new tests explicitly skip without DA_API_WRITER_REDIS_URL, which must name
a disposable database (flushed per case). Qualified runs below provide it, run
all cases, and have no new skips. Redis client is pinned via --with redis==5.2.1;
no executor production dependency is added.

## Verification and evidence

Full commands/output are in `05b2b5/evidence/accepted/`. Directory naming follows
the requested layout, NOT independent acceptance. Single-line Python subprocess
wrappers captured output without shell redirection. Act trailing whitespace was
normalized. Static inventory is inspection evidence, not runtime qualification.
Initial fixture errors (missing read_packages fixture permission and incorrect
literal environment table name) and the 38-case pre-constraint smoke remain
untracked in evidence/development; no production failure hidden in accepted logs.

Disposable Redis setup command:

```sh
docker run -d --name da-05b2b5-redis -p 127.0.0.1:56379:6379 redis:7.4.2-alpine
# exit 0; image digest sha256:02419de7eddf55aa5bcf49efb74e88fa8d931b4d77c07eff8a6b2144472b6952
```

Exact qualification commands:

```sh
PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -p no:cacheprovider uv-experiment/preparation/tests/test_publication_api_key_writers_postgres.py
# 42 passed in 6.71s; exit 0

PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -p no:cacheprovider uv-experiment/preparation/tests/test_publication_api_key_writers_postgres.py uv-experiment/preparation/tests/test_publication_account_writers_postgres.py uv-experiment/preparation/tests/test_publication_retry_postgres.py uv-experiment/preparation/tests/test_publication_view_writers_postgres.py uv-experiment/preparation/tests/test_publication_helper_writers_postgres.py uv-experiment/preparation/tests/test_publication_writers_postgres.py uv-experiment/preparation/tests/test_publication_postgres.py
# 218 passed in 36.73s; exit 0

PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
# 331 passed in 53.95s; exit 0

act workflow_dispatch -W .act/checks.yml
# 292 passed, 1 skipped in 30.64s; Success: no issues found in 51 source files; Job succeeded; exit 0

uv tool run ruff check --select E9,F docassemble_base/docassemble/base/managed_publication.py docassemble_webapp/docassemble/webapp/api/helpers.py docassemble_webapp/docassemble/webapp/api/views.py docassemble_webapp/docassemble/webapp/utils/api_key.py docassemble_webapp/docassemble/webapp/utils/managed_api_keys.py uv-experiment/preparation/tests/test_publication_api_key_writers_postgres.py
# All checks passed!; exit 0
```

Publication 176 -> 218 and aggregate 289 -> 331 intentionally add 42 cases.
No expected-count assertions, native/typecheck counts or exclusions changed.
The one act skip is the existing bare-image Supervisor skip.

## Fenced remainder / precise inventory

All web paths below are relative to docassemble_webapp/docassemble/webapp.
The static AST inventory retains candidate writers/readers for inspection in
writer-inventory.log; entries are not all mutations. Existing qualified helpers
are not thereby unqualified again, but their surrounding composites are not
qualified merely by invoking them.

**Remaining account/profile/registration/default-role composites (next writer
slice, still unqualified):**

- users/views.edit_user_profile_page (profile/active/role replacement, MFA/email
  confirmation and both user_interviews + delete_user_data deletion composites),
  user_profile_page, user_add, custom_register, invite, add_privilege, manage_account.
- users/helpers.login_or_register and users/signals.on_user_login,
  on_register_hook (default/invite role replacement), on_password_change.
- users/forms.MySignInForm.validate LDAP account/default-role creation;
  auth/views.oauth_callback and phonelogin/views.phone_login_verify account/auth
  composites, their session/secret substitutions and registration signal effects.
- create_tables.get_role, get_user, populate_tables account/default-role creation
  and fix-user-roles pass. Package initialization already skips in managed mode;
  this is NOT qualification of the account portion.
- users/helpers.set_user_info with old_password and
  interview/helpers.substitute_secret/fix_secret, including the monitor/hooks
  secret-substitution callback. The SQL-only set_user_info path remains the
  prior qualified slice; non-SQL substitution/composite handling is not covered.

**Remaining config observation/dependency-input refresh (still unqualified):**

- admin/views.config_page cloud/local save and fail-closed observation after save.
- startup.initialize/read_config policy loading and refresh; base/read_config
  dependency/index inputs and Docker/initialize.sh PYTHONPACKAGES/index input
  observation; executor preparation/controller/CLI startup dependency-input
  observation refresh before accepting any import. No config-import enablement
  or dependency generation refresh was added here. Direct operator file edits
  remain the contract's observed-snapshot/drift boundary, never SQL-file atomicity.
- Current config.allowed, ALLOW_UPDATES, DEVELOPER_CAN_INSTALL and name/source/
  dependency policy refresh must join environment-first serialization. Fresh
  policy reads alone do not establish serialization.

**Remaining PackageAuth/grant/name/source/dependency-policy inventory:**

- utils/helpers.install_zip_package, install_git_package, install_pip_package,
  uninstall_package; packages/views.update_package, packages/api.api_package;
  develop/views.playground_packages ZIP install caller. Existing managed
  reject/prepare fences remain; legacy direct intent/grant/source/name mutation
  must not be re-enabled without qualification.
- update.clear_invalid_package, clear_invalid_package_auth, check_for_updates,
  add_dependencies, fix_names, install_package, uninstall_package, update_versions;
  tasks/tasks.update_packages/reset_server and direct update CLI callers. Their
  existing managed rejection is not real writer race qualification.
- create_tables.populate_tables package source rewrites and add_dependencies
  invocation remain skipped/fenced in managed mode; dependency/source/name policy
  refresh beyond that skip remains unqualified. Actual PackageProjection and
  deletion's grant reassignment retain their previous bounded qualification.
- Complete actual-writer/caller closure audit and PostgreSQL races for all of the
  above are still required. No complete production writer-safety claim.

Read-only hits (user_can_edit_package/get_package_info, download_zip_package,
UserModel.active readers and socketserver's local role-name list) are not newly
claimed as writers. Unsupported package sources stay rejected, not silently
routed through the registry preview mechanism.

05c retains installation polling, live frozen apply, durable child-safe terminal
evidence/slot release, operational retry clearance, verified running/recovery
identity and browser/API end-to-end enablement. Public apply-producing approval
and retry MUST remain fenced until that operational qualification AND the
remaining writer prerequisites are complete. 05a2b4 credential-source decision,
authenticated injection/private binding and broader source/target/build policy
remain unchanged and fenced. No parent acceptance claimed.

## Resolution and limits

Bounded supervisor-approved Redis chunk implemented and locally checked, subject
to independent review. Remaining writer inventory above is the next writer
slice prerequisite, NOT discarded work or permission to enable approval. Diff
and change summary under ticket-05b2b5 remain untracked; prior artifacts preserved.
No branch switch, no new-system changes; parent Status/checklist unchanged.

Next: 05c.
