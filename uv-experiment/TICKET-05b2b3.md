# 05b2b3: Internal retry and truthful public publication status

Date: 2026-10-14. Branch: `uv-implementation`. Baseline:
`0acbd652290df564f4d7e08d956f789dc1d54dfb`.

**PUBLIC APPLY-PRODUCING APPROVAL AND RETRY REMAIN FENCED UNTIL 05c.**
Supervisor-approved bounded convergence fallback: internal retry plus public
read-only confirmation/revision/attempt status, NOT complete remaining 05b2b,
05b or parent acceptance. Independent review required. No branch switch,
new-system, 05c or 05a2b4 changes. Parent Status/checklist unchanged; 06 blocked.

## Objective and implementation

Internal Publication.retry takes the common environment-first lock, checks fresh
retry authority, current desired revision, exact target/tool/core and the global
active slot. Only an authorized administrator or still-authorized original
approver can create an attempt. The actual PreviewAuthorizer rechecks active
account, SQL roles, current policy, API permission reader, normalized Package name,
source/core protection and current ownership for the non-admin original approver.
Another developer cannot retry merely by having package-management permission.

Retry atomically inserts a new pending attempt, occupies the existing slot and
records durable actor/key/body deduplication. It changes neither revision nor
Package/grants, contributions, candidate, bundle, desired pointer or generation;
there is no solve, dispatch or heartbeat release. Same-key replay returns the
original recorded response after fresh retry authorization; body mismatch rejects.
New retry of a historical/non-desired revision rejects. System-origin or non-add
revisions remain unsupported. Only a free slot is consumed: actual terminal
reporting, host safety, uncertain-work clearance and release remain 05c, NOT
implemented by this slice.

Additive retry_result schema/migration and ORM mapping support both real upgrade
and fresh initialization. The original 05a migration's factory iteration excludes
the later retry table, just as it excludes publication_result: old upgrade/downgrade
must not accidentally create/drop future records. Packaging expectation intentionally
changes 12 -> 13 tables and explicitly checks retry_result; native test count unchanged.

Public existing preview GET adapters accept candidate_id, revision_id or attempt_id.
Published candidate GET becomes a read-only publication confirmation. These use
Preparation's allowlisted publication_status, NOT the internal publisher factory.
Fresh status authorization is requester/admin only. Responses distinguish published
revision, desired identity, latest/requested attempt phase, occupied slot and runtime
kind/generation. Pending/in-progress is NOT installation success; terminal success
records show reported-terminal/not-verified, never installation success. Raw bundle,
request, executor error and arbitrary attempt fields are omitted. Runtime verification
and current/historical runtime revision identity are NOT qualified here; 05c remains
responsible for their complete projection. Public POST approve/retry still reject.
Flag-off endpoint behavior is unchanged. No browser apply confirmation action added.

## Real writer qualification

40 intentional new PostgreSQL cases (20 scenarios x both initialization paths).
Tests exercise actual Publication.retry, PackageProjection, PreviewAuthorizer,
authority SQL writers, and exact make_user_inactive helper source with real Sessions.
Independent pg_blocking_pids proves retry blocks on a retained environment lock
before committed account/role revocation denies it. Snapshots include EVERY shared
protocol table plus actual Package/grants. Paused retry insertion and injected failure
prove no partial attempt/slot/dedup or intent, and rollback. Competing retries and
retry-vs-approval consume exactly one slot; expired heartbeat never frees it.
Original revision and bundle remain unchanged. Existing suite requalifies immutable
revision triggers, atomic Package intent and helper/view deletion races.

Actual Flask request contexts execute the public adapter with the real read-only SQL
service. Authentication context and service factory dependencies are supplied, not
full route/decorator authentication qualification. Accounts/roles are minimal SQL
fixtures; actual Package ORM schema is used. External policy is still a callback:
this is NOT config/Redis writer coordination proof. Test-only failed-attempt/free-slot
fixtures simulate prior qualified terminal reporting; they are NOT an executor release
API or terminal-safety evidence. No production safety claim follows from them.

## Verification and evidence

Commands captured with single-line Python subprocess calls, no shell redirection:

```sh
PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login pytest -q -p no:cacheprovider uv-experiment/preparation/tests/test_publication_retry_postgres.py uv-experiment/preparation/tests/test_publication_view_writers_postgres.py uv-experiment/preparation/tests/test_publication_helper_writers_postgres.py uv-experiment/preparation/tests/test_publication_writers_postgres.py uv-experiment/preparation/tests/test_publication_postgres.py
# 140 passed in 16.42s; exit 0

PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
# 253 passed in 34.10s; exit 0

act workflow_dispatch -W .act/checks.yml
# 292 passed, 1 skipped in 30.18s; Success: no issues found in 51 source files; Job succeeded; exit 0

uv tool run ruff check --select E9,F docassemble_base/docassemble/base/managed_publication.py docassemble_base/docassemble/base/managed_preparation.py docassemble_base/docassemble/base/managed_records.py docassemble_webapp/docassemble/webapp/packages/managed.py docassemble_webapp/docassemble/webapp/packages/models.py docassemble_webapp/docassemble/webapp/alembic/versions/05a100000001_managed_preparation.py docassemble_webapp/docassemble/webapp/alembic/versions/05b300000001_managed_retry.py uv-experiment/preparation/tests/test_publication_retry_postgres.py uv-experiment/preparation/tests/test_preparation_postgres.py uv-experiment/executor/tests/test_executor_packaging.py
# All checks passed!; exit 0
```

Qualified logs: `05b2b3/evidence/accepted/{publication,postgres,act,lint}.log`.
Requested accepted layout is NOT independent acceptance. Act trailing whitespace
normalized. Aggregate 213 -> 253 and publication 100 -> 140 are intentional +40
new cases. No exclusions, native test counts or mypy expectations changed. The
single documented bare-act Supervisor skip remains. Initial development failures
were fixture migration overlap/teardown, incomplete snapshot coverage/helper
signature and a second-preview diff fixture; corrected without weakening protocol.
Initial act run: 291 passed, 1 failed, 1 skipped because schema packaging still
expected 12 tables; fixed intentionally to 13. Development logs remain untracked
under `05b2b3/evidence/development/`, not accepted evidence.

## Fenced remainder / inventory

Remaining 05b2b items, unchanged and explicitly unqualified:

- interview/helpers.delete_user_data multi-store account deletion and PackageAuth
  reassignment, including its current independent commits and file/Redis cleanup.
- Remaining grants/security/composite writers: users/helpers.set_user_info,
  create_user/login_or_register/default-role/account writers; users/views profile
  and account edits, user_add/custom_register and other registration/account
  writers; remaining direct PackageAuth/grant and package-name/source/dependency
  policy writers and composite callers not separately qualified by prior slices.
  Already-qualified privilege helpers and users/views.delete_privilege remain so.
- Redis API permission/constraint coordination through api/helpers.add_api_key,
  update_api_key, delete_api_key, fix_api_key/fix_api_keys and callers.
- admin/views.config_page config save/observation coordination, startup/executor
  dependency-input refresh, current source/name/dependency policy serialization.
  Fresh config/Redis reads alone are NOT serialization.
- Ownerless managed identities require explicit administrator assignment displayed
  and bound in preview; current ownerless path still rejects rather than inventing
  or transferring an owner.
- Complete real PostgreSQL races through ALL the above actual writers; no full
  production publication-writer safety or complete 05b2b qualification claimed.

Public apply-producing approve AND retry remain fenced until 05c. Independent
installation polling, live frozen apply, terminal child-safe evidence/slot release,
operational safety/uncertain-work retry clearance, complete verified running/recovery
identity/status and end-to-end browser/API confirmation remain 05c. Credential-source
05a2b4 and broader private/source/target/build policy remain unchanged. No parent-05
or 05b/05c acceptance, and no historical grant restoration.

## Next-slice account-deletion decision

Supervisor explicitly approved the following fail-closed boundary for follow-up:
environment lock -> DB authorization/ownership deletion mutations in the same
publication-serialized transaction -> commit -> non-DB files/cloud/Redis cleanup.
Surface non-DB failures as durable redacted errors; never resurrect intent or restore
historical grants. Non-DB stores are not transactional with SQL. Preserve flag-off
behavior; test the actual interview/helpers.delete_user_data writer. This decision
is recorded here, NOT implemented by this fallback.

## Resolution and limits

Bounded 05b2b3 implemented and locally checked, subject to independent review.
Untracked diff/change-summary under `ticket-05b2b3/`; prior untracked artifacts
unchanged. Parent Status/checklist unchanged. No additional recursive sub-split.

Next: 05b2b4 — remaining account/grant/composite/config/Redis writers and explicit
previewed ownerless assignment, with complete actual-writer PostgreSQL qualification;
public approval/retry remain fenced until 05c.
