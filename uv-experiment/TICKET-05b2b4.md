# 05b2b4: Account deletion, SQL account helpers and explicit ownerless assignment

Date: 2026-10-14. Branch: `uv-implementation`. Baseline:
`ef9df96c113193c01450140e484247c591308abf`.

**PUBLIC APPLY-PRODUCING APPROVAL AND RETRY REMAIN FENCED UNTIL 05c.**
This is the already-approved 05b2b4 sub-split, not full 05b2b/05b or parent-05
acceptance. No production writer-safety acceptance claim. Independent review
required. Parent Status/checklist unchanged; 06 remains blocked.

## Objective and implementation

Resumed the interrupted working tree after reading git status/diff and all partial
source changes. Preserved that implementation rather than restarting. Completed
fresh-session account deletion reads and additional cleanup/composite tests.

Managed interview/helpers.delete_user_data now takes the environment lock before
SQL authorization/ownership mutation. One transaction removes session/upload
associations and roles, clears authentication/account details, deactivates the
account, and preserves legacy PackageAuth reassignment to user 1 while aligning
managed identity owners. It commits a durable pending account_cleanup record with
those changes. Only afterward do chat/global/email hooks, playground files and
Redis cleanup execute. Success records complete; exceptions record failed with a
fixed redacted error and surface account-deactivated/cleanup-failed, never restore
grants or desired intent. SQL failure rolls back and executes no cleanup. Fresh
reads after the lock discard cached ORM account state. Flag-off deletion body is
unchanged. Non-DB stores are not SQL-atomic; pending records survive interruption,
but this slice adds no cleanup daemon or automatic retry/forensic erasure claim.

Additive account_cleanup migration and ORM/shared-schema mapping cover upgraded
and fresh installations. Old 05a migration excludes this later table from its
factory iteration. Packaging intentionally changes schema count 13 -> 14; native
test count is unchanged. Downgrade fails closed pending explicit archival work.

SQL-only users/helpers.set_user_info edits (without old_password) lock before
fresh reads, validate role effects, and commit profile/active/password/role changes
in one transaction. Privilege mutation failures roll back the composite. create_user
takes the same environment lock before account/role writes. Existing caller
permission checks remain; this is publication serialization qualification, NOT
fresh authorization of every administrator endpoint/composite caller. Non-SQL
old_password secret substitution remains outside this qualification in 05b2b5.

Explicit assign_owner=true is bound into the prepare idempotency digest and the
immutable preview ownership effects. Only an administrator can prepare/approve
assignment of an existing ownerless managed identity. UI offers an explicit admin
checkbox; status displays the effects. Implicit assignment still rejects. Internal
publication rechecks ownerless identity and empty current owner grants, atomically
assigning identity and PackageAuth to the requester. No historical grant restore,
other-owner transfer or new legacy adoption. Public approve/retry still reject.

## Real writer qualification

32 intentional new PostgreSQL cases (16 scenarios x upgrade/fresh initialization).
Exact actual helper source bodies execute with real SQLAlchemy Sessions; hook,
file, Redis and current-user dependencies are supplied fixtures, NOT complete
Flask/plugin/cloud/Redis integration. Actual PackageProjection, PreviewAuthorizer
and Publication execute against SQL fixtures and actual Package mappings.

Independent pg_blocking_pids confirms approval blocks while the actual deletion
or SQL account edit holds the environment lock; after commit it rejects revoked
authority. Creation blocks behind publication. Assertions cover unchanged protocol
snapshots/no partial publication, legacy reassignment, committed pending before
cleanup, success/fixed-error outcomes for hook/file/Redis failure, SQL rollback,
flag-off deletion, one-commit profile/active/role effects, and retained published
intent after deletion. Ownerless tests cover explicit preview, nonadmin/implicit
rejection, idempotency mismatch/replay, admin revocation race and changed grants.

Existing publication/preparation regressions requalify dedup, retry, immutability,
slot competition, prior integrated writers and preview/status fences. They do not
qualify the remaining config/Redis writers or any live installation.

## Verification and evidence

Exact commands and complete outputs are in
`05b2b4/evidence/accepted/{publication,preparation,act,lint}.log`.
Commands captured using single-line Python subprocess calls, no shell redirection:

```sh
PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login pytest -q -p no:cacheprovider uv-experiment/preparation/tests/test_publication_account_writers_postgres.py uv-experiment/preparation/tests/test_publication_retry_postgres.py uv-experiment/preparation/tests/test_publication_view_writers_postgres.py uv-experiment/preparation/tests/test_publication_helper_writers_postgres.py uv-experiment/preparation/tests/test_publication_writers_postgres.py uv-experiment/preparation/tests/test_publication_postgres.py
# 172 passed in 24.34s; exit 0

PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
# 285 passed in 44.65s; exit 0

act workflow_dispatch -W .act/checks.yml
# 292 passed, 1 skipped in 31.38s; Success: no issues found in 51 source files; Job succeeded; exit 0

uv tool run ruff check --select E9,F docassemble_base/docassemble/base/managed_preparation.py docassemble_base/docassemble/base/managed_publication.py docassemble_base/docassemble/base/managed_records.py docassemble_webapp/docassemble/webapp/alembic/versions/05a100000001_managed_preparation.py docassemble_webapp/docassemble/webapp/alembic/versions/05b400000001_account_cleanup.py docassemble_webapp/docassemble/webapp/interview/helpers.py docassemble_webapp/docassemble/webapp/packages/managed.py docassemble_webapp/docassemble/webapp/packages/models.py docassemble_webapp/docassemble/webapp/users/helpers.py uv-experiment/executor/tests/test_executor_packaging.py uv-experiment/preparation/tests/test_preparation_postgres.py uv-experiment/preparation/tests/test_publication_account_writers_postgres.py
# All checks passed!; exit 0
```

Publication 140 -> 172 and aggregate 253 -> 285 intentionally add 32 cases;
no exclusions/native counts/typecheck expectations changed. Single documented
bare-act Supervisor skip remains. Act whitespace normalized. Initial interrupted
run logs and this run's initial 28-case smoke/lint fixture-import warnings remain
untracked development evidence; no functional failure hidden in accepted logs.
Accepted directory naming is the requested evidence layout, NOT independent acceptance.

## 2026-10-14 P1 duplicate-deletion fix

Fixed review finding against 05b2b4: while holding the environment lock,
managed deletion now rejects any existing account_cleanup record before account
mutation. It never replaces pending, failed or complete evidence. No automatic
retry/resumable cleanup was added; flag-off behavior is unchanged.

Added two source-body PostgreSQL regressions, each exercised on upgrade/fresh
initialization: fail before Redis cleanup then retry with a fresh Session (the
original email is already cleared), and reject a concurrent duplicate during
pending cleanup plus a duplicate after completion. Failure evidence remains
unchanged and duplicates run no cleanup hooks or Redis deletion.

Exact commands and complete outputs are retained untracked in
`ticket-05b2b4/fix-evidence/{account-writers,publication,preparation,act}.log`.
Single-line Python subprocess wrappers capture output without shell redirection.

```sh
PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login pytest -q -p no:cacheprovider uv-experiment/preparation/tests/test_publication_account_writers_postgres.py
# 36 passed in 7.54s; exit 0
PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login pytest -q -p no:cacheprovider uv-experiment/preparation/tests/test_publication_account_writers_postgres.py uv-experiment/preparation/tests/test_publication_retry_postgres.py uv-experiment/preparation/tests/test_publication_view_writers_postgres.py uv-experiment/preparation/tests/test_publication_helper_writers_postgres.py uv-experiment/preparation/tests/test_publication_writers_postgres.py uv-experiment/preparation/tests/test_publication_postgres.py
# 176 passed in 25.34s; exit 0
PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
# 289 passed in 45.71s; exit 0
act workflow_dispatch -W .act/checks.yml
# 292 passed, 1 skipped in 30.90s; Success: no issues found in 51 source files; Job succeeded; exit 0
uv tool run ruff check --select E9,F docassemble_webapp/docassemble/webapp/interview/helpers.py uv-experiment/preparation/tests/test_publication_account_writers_postgres.py
# All checks passed!; exit 0
git diff --check
# no output; exit 0
```

Counts intentionally increase account-writer 32 -> 36, publication 172 -> 176,
aggregate 285 -> 289 for four regression cases. No expected-count assertions,
native/typecheck expectations or exclusions changed. Independent re-review
required; parent 05 Status/checklist and all fenced remainder stay unchanged.

## Fenced remainder / inventory

**05b2b5 — remaining view/composite/config/Redis writers and their races**:

- users/views profile/account edits, user_add/custom_register, login_or_register,
  default-role/registration/account composites and other remaining security/grant
  writers/callers; users/helpers old_password secret-substitution composites.
- Remaining direct PackageAuth/grant, package-name/source/dependency policy writers
  and composite callers not qualified by the prior slices or this SQL-only slice.
- api/helpers.add_api_key, update_api_key, delete_api_key, fix_api_key/fix_api_keys
  and callers: Redis API permission/constraint writer coordination.
- admin/views.config_page save/observation coordination, startup/executor dependency
  input refresh, current source/name/dependency policy serialization. Fresh config
  and Redis reads alone are not serialization.
- Complete actual-writer audit and PostgreSQL races across that remaining inventory;
  no complete production publication-writer safety acceptance in this slice.

05c retains public apply-producing approval/retry enablement, installation polling,
live frozen apply, durable terminal child-safe evidence/slot release, operational
safety/uncertain-work retry clearance, complete verified running/recovery identity
and end-to-end browser/API apply confirmation. 05a2b4 credential-source decision and
authenticated injection, private index/binding, broader source/target/build policy
remain unchanged and fenced. No 05c, 05a2b4 or parent-05 acceptance claimed.

## Resolution and limits

Bounded 05b2b4 implemented and locally checked, subject to independent review.
Requested diff/change-summary artifacts under ticket-05b2b4 remain untracked;
prior untracked artifacts preserved. No branch switch or new-system changes.
Parent 05 Status/checklist unchanged, 06 blocked.

Next: **05b2b5 — remaining view/composite/config/Redis writers and their races**;
public approval/retry remain fenced until 05c.
