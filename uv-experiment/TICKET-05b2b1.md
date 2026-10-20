# 05b2b1: Email normalization and cooperating privilege deletion helper

Date: 2026-10-14. Branch: `uv-implementation`. Baseline:
`5b0053b9b1189116fd2bd7009eb3b1308dbcfd7f`.

**PUBLIC APPLY-PRODUCING APPROVAL REMAINS FENCED UNTIL 05c.**
Task-authorized smallest coherent fallback, not complete 05b2b/05b/parent
acceptance. No 05c, 05a2b4, branch switch or new-system changes. Independent
review required; parent Status/checklist unchanged; 06 blocked.

## Objective and implementation

Required 05b2a P2 fixed: make_user_inactive validates string email and strips
surrounding whitespace before managed/legacy branching when user_id is None.
Both branches preserve ID precedence and the existing missing-input error.

Actual remove_privilege helper now calls users.managed.delete_privilege in
managed mode. The SQL writer locks EnvironmentState before reading the role,
removes current memberships and the role in the SAME connection/transaction,
and does not commit independently. The helper commits once or rolls back a
protocol error. Built-in guard and flag-off ORM behavior are unchanged.
No default membership is added: the existing helper (unlike the separate view)
deletes role memberships without assigning a replacement. This does not change
users/views.delete_privilege's separate behavior or qualify that writer.

## Real writer qualification

New tests compile the exact named helper source function bodies (decorators only
removed) with actual SQLAlchemy Sessions and real PostgreSQL. This avoids loading
the entire application's circular imports/dependencies. Permission context and
managed flag are supplied, not full authenticated Flask requests. Neither helper
body, SQL writer, SQL connection nor commit is mocked/replaced. Account table is
a minimal authority fixture; Package tables remain actual ORM mappings.
External policy is the existing fixture callback, NOT config/Redis writer proof.

Race pauses the actual helper's real Session immediately before commit. Independent
pg_blocking_pids observation proves approval waits on the writer's environment
lock; uncommitted deletion is invisible, publication snapshots/Package intent
remain unchanged, and committed deletion denies approval. Lost-response replay
is denied after role deletion without altering the published revision/attempt.
Rollback and built-in guards are covered. Email tests execute the actual helper
through both modes with whitespace and non-string inputs. Existing suite
requalifies immutable revisions, one active attempt, heartbeat non-release and
atomic projection. No new runtime/solve/dispatch or slot-release interface.

## Verification and evidence

Exact commands (single-line Python subprocess capture, no shell redirection):

```sh
PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login pytest -q -p no:cacheprovider uv-experiment/preparation/tests/test_publication_helper_writers_postgres.py uv-experiment/preparation/tests/test_publication_writers_postgres.py uv-experiment/preparation/tests/test_publication_postgres.py
# 80 passed in 8.64s; exit 0

PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
# 193 passed in 25.75s; exit 0

act workflow_dispatch -W .act/checks.yml
# 292 passed, 1 skipped in 31.30s; Success: no issues found in 51 source files; Job succeeded; exit 0

uv tool run ruff check --select E9,F docassemble_webapp/docassemble/webapp/users/managed.py docassemble_webapp/docassemble/webapp/users/helpers.py uv-experiment/preparation/tests/test_publication_helper_writers_postgres.py
# All checks passed!; exit 0
```

Logs: `05b2b/evidence/accepted/{publication,postgres,act}.log`. Requested layout
is not an independent acceptance decision. Initial missing fixture-import run
(66 passed, 7 errors) retained in publication-development-failed.log, NOT passing
evidence. Initial lint fixture-name F811 findings fixed with the established
fixture suppression pattern. 14 intentional new PostgreSQL cases (7 scenarios x
both initialization paths): aggregate 179 -> 193, publication 66 -> 80. No count
assertions, exclusions or native/typecheck expectations changed. One documented
bare-act Supervisor skip remains.

## Fenced remainder / inventory

05b1/05b2a inventories remain authoritative except remove_privilege helper now
integrated. Still unqualified: interview/helpers.delete_user_data multi-store
account deletion and grant reassignment; users/views.delete_privilege and profile
edits; set_user_info composite active/role edits, registration/default-role and
other account writers; Redis API-key permission/constraint add/update/delete/fix;
admin config save/observation and source/dependency/name policy; remaining
PackageAuth/grant writers. Fresh config/Redis reads alone are not serialization.
No full production writer-safety claim. Retry current desired revision and
admin/still-authorized-original-approver semantics, explicit previewed ownerless
admin assignment, public confirmation/attempt-status adapters remain fenced.
Public approve/retry remain rejected; NO installation-success UX. Installation
polling/live frozen apply/terminal release is 05c; credential-source 05a2b4 and
private/broader source policy unchanged. Parent Status/checklist unchanged.

## Resolution and limits

Bounded 05b2b1 implemented and locally checked; independent review required.
Do not infer full helper/route or cross-store writer safety from these tests.

Next: 05b2b2 — remaining actual account/grant/composite/config/Redis writers,
retry, ownerless assignment and confirmation/status; public approval fenced until 05c.
