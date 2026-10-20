# 05b2b2a: Cooperating privilege-deletion view

Date: 2026-10-14. Branch: `uv-implementation`. Baseline:
`be3f3a7750cfaea5b5465ec452084111c08b67d1`.

**PUBLIC APPLY-PRODUCING APPROVAL REMAINS FENCED UNTIL 05c.**
Task-authorized smallest coherent fallback, NOT complete 05b2b2/05b or parent
acceptance. No 05c, 05a2b4, branch switch or new-system changes. Independent
review required. Parent Status/checklist unchanged; 06 blocked.

## Objective and implementation

Integrate actual users/views.delete_privilege's composite writer. Managed mode
calls delete_privilege_for_view, locking EnvironmentState BEFORE authority reads
on the Session's connection. The SAME transaction removes the deleted role's
memberships, assigns the default user role only to affected existing users left
without any roles, and deletes the role. The view commits once; protocol failure
rolls back all effects and displays the existing error. No intermediate membership
commit. The view's built-in guard includes trainer, unlike the separate helper.
Other memberships are retained, including administrator memberships. Flag-off
ORM branch is unchanged. Missing role/default role fails closed.

This narrow slice does not integrate the account-deletion multi-store workflow:
its independent SQL commits and file/Redis cleanup require separate qualification.
It does not extend the helper deletion semantics to assign default memberships.

## Real writer qualification

Tests compile the exact named view source body (decorators only removed) and
execute its managed branch using actual SQLAlchemy Sessions and PostgreSQL.
The view, SQL writer, connection and commit are NOT mocked or replaced. Translation,
flash/redirect and the managed flag are supplied; authentication decorators and
full Flask requests are NOT qualified. Account/role tables are minimal authority
fixtures; Package tables use actual mappings. External policy remains the fixture
callback, NOT config/Redis writer proof.

An actual Session before_commit event pauses the writer after all SQL mutations.
Independent pg_blocking_pids observation proves approval waits on its environment
lock. Independent snapshots show neither uncommitted default-role assignment nor
partial Package intent. Committed deletion denies approval. Post-publication
revocation denies lost-response replay without altering immutable revision,
desired state, Package/grants or the single occupied attempt slot. Other-role
retention, built-in/missing-ID guards and failed-commit rollback are covered.
Existing publication suite requalifies immutable revision, one active attempt,
heartbeat non-release and atomic projection. No release/retry/runtime interface
is added.

## Verification and evidence

Exact commands (captured with single-line Python subprocess, no shell redirection):

```sh
PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login pytest -q -p no:cacheprovider uv-experiment/preparation/tests/test_publication_view_writers_postgres.py uv-experiment/preparation/tests/test_publication_helper_writers_postgres.py uv-experiment/preparation/tests/test_publication_writers_postgres.py uv-experiment/preparation/tests/test_publication_postgres.py
# 100 passed in 11.27s; exit 0

PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
# 213 passed in 29.66s; exit 0

act workflow_dispatch -W .act/checks.yml
# 292 passed, 1 skipped in 30.40s; Success: no issues found in 51 source files; Job succeeded; exit 0

uv tool run ruff check --select E9,F docassemble_webapp/docassemble/webapp/users/managed.py docassemble_webapp/docassemble/webapp/users/views.py uv-experiment/preparation/tests/test_publication_view_writers_postgres.py
# All checks passed!; exit 0
```

Logs: `05b2b2/evidence/accepted/{publication,postgres,act,lint}.log`.
Trailing whitespace in act output normalized. The requested accepted layout is
not an independent acceptance decision. Initial
lint run reported two F811 fixture parameter warnings; fixed using established
fixture suppression pattern. 20 intentional new PostgreSQL cases (10 scenarios
x both initialization paths): aggregate 193 -> 213, publication 80 -> 100. No
expected count assertions, exclusions, native/typecheck expectations changed.
One documented bare-act Supervisor skip remains. Final source change after test
runs is only two lint suppression comments.

## Fenced remainder / inventory

05b2b1 inventory remains authoritative except users/views.delete_privilege now
integrated. Still unqualified: interview/helpers.delete_user_data multi-store
account deletion and grant reassignment; profile edits; set_user_info composite
active/role edits, registration/default-role and other account writers; Redis
API-key permission/constraint add/update/delete/fix; admin config save/observation
and source/dependency/name policy; remaining PackageAuth/grant writers. Fresh
config/Redis reads alone are NOT serialization. No full production writer-safety
claim. Retry current desired revision by admin/still-authorized original approver,
explicit previewed ownerless admin assignment, public confirmation/attempt-status
adapters remain fenced. Public approve/retry still reject; NO installation-success
UX. Installation polling/live frozen apply/terminal release remains 05c;
credential-source 05a2b4 and broader private/source policy unchanged.

## Resolution and limits

Bounded 05b2b2a implemented and locally checked; independent review required.
Do not infer full route authentication or cross-store writer safety from these tests.
Untracked diff/change-summary requested under `ticket-05b2b2/`; no prior untracked
handoff artifacts were modified. Parent Status/checklist unchanged.

Next: 05b2b2b — remaining actual account/grant/composite/config/Redis writers,
retry, explicit ownerless assignment and confirmation/status; public approval
fenced until 05c.
