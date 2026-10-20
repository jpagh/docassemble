# 05b2a: Real SQL Package projection and bounded cooperating helper writers

Date: 2026-10-14. Branch: `uv-implementation`. Baseline:
`9c1da913049d713162391450f905da1ffe1b33a9`.

**PUBLIC APPLY-PRODUCING APPROVAL REMAINS FENCED UNTIL 05c.**
This implements the task's explicitly authorized smallest coherent fallback,
not all 05b2, full writer-safety, 05b/05c/05a2b4 or parent-05 acceptance.
Parent Status/checklist unchanged; 06 remains blocked. No branch switch or
new-system changes. Independent review required.

## Objective and implementation

Real `PackageProjection` writes the actual Package/PackageAuth schema on the
publication connection. A new identity creates one active pip Package and its
owner grant; an existing explicitly managed identity preserves its Package ID
and current owner grant. It rejects ambiguous aliases, legacy implicit adoption,
core/non-pip sources, changed ownership and multi-owner grants. Exact declaration,
extras and markers remain in contributions and immutable revision inputs; legacy
Package.limitation is only the compatibility specifier projection, NOT executor
intent. No transitive inventory is turned into Package ownership.

PreviewAuthorizer now supports internal approval with fresh SQL user, role,
normalized Package name/source and owner checks. `internal_publication_service`
uses the SAME current config.allowed/app-policy and fail-closed Redis api_verify
and permission revalidation as preview_service; no fixture authority is wired
into that factory. **This factory is not production-approval-safe:** config and
Redis writers are not serialized yet. It is not called by any public adapter;
preview_service still creates Preparation, whose approve/retry reject.

`users/managed.py` contains production SQL mutations used by managed branches of
add_user_privilege, remove_user_privilege and make_user_inactive in users/helpers.
Those helper callers preserve permission/self-admin/cron checks and flag-off ORM
behavior. The SQL functions lock EnvironmentState before target authority reads,
then mutate on the caller's exact session connection; helpers commit once or
rollback protocol errors. There is no independent SQL-function transaction.
The users/api.py privilege POST/DELETE route already delegates to these helpers;
no route rewrite is required. Full Flask helper/route execution is NOT qualified
by the bounded tests: the actual shared SQL writer functions are exercised
against PostgreSQL without replacing their implementation with mocks.

Lock order remains environment -> candidate (approval only) -> authority and
projection. Concurrent revocation/deactivation holds the environment lock until
commit; approval then rereads current authority and rejects with no intent writes.
No heartbeat-based slot release, runtime mutation, solve or dispatch was added.

## Deterministic 05b1 P2 regression

The writer transaction is retained while an INDEPENDENT PostgreSQL connection
observes a backend waiting on its exact writer PID via pg_blocking_pids, Lock
wait_event_type and the environment SELECT FOR UPDATE query. Only then is the
writer released; approval must reject. Both original fixture deletion/revocation
and new actual SQL writer races use this assertion, not a scheduling/done check
as proof. Observer uses AUTOCOMMIT because pg_stat_activity snapshots cache inside
a transaction. An initial full-suite run exposed this caching (1 failed, 178
passed); the observer fix passed both the full and targeted reruns. The failed
log is retained and explicitly NOT passing evidence.

## Verification and evidence

Real PostgreSQL disposable schema, both migration and fresh initialization.
Package and PackageAuth tables are created from actual ORM mappings; minimal
user/role/upload authority fixtures supply the referenced schema and actors.
External policy is a test callback, NOT real config/Redis writer-race proof.
No full application request or external writer-safety acceptance is claimed.

Exact executed test commands (captured via single-line Python subprocess calls,
without shell redirection/heredocs):

```sh
PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login pytest -q -p no:cacheprovider uv-experiment/preparation/tests/test_publication_writers_postgres.py uv-experiment/preparation/tests/test_publication_postgres.py
# 66 passed in 7.02s; exit 0

PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
# 179 passed in 24.18s; exit 0

act workflow_dispatch -W .act/checks.yml
# 292 passed, 1 skipped in 30.42s; Success: no issues found in 51 source files; Job succeeded; exit 0

uv tool run ruff check --select E9,F docassemble_base/docassemble/base/managed_publication.py docassemble_webapp/docassemble/webapp/users/managed.py docassemble_webapp/docassemble/webapp/users/helpers.py docassemble_webapp/docassemble/webapp/packages/managed.py uv-experiment/preparation/tests/test_publication_writers_postgres.py uv-experiment/preparation/tests/test_publication_postgres.py
# All checks passed!; exit 0 (initial unused test import removed)
```

Logs: `05b2/evidence/accepted/{publication,postgres,act}.log` (trailing
whitespace normalized only).
`postgres-before-observer-fix.log` is failed development evidence, not acceptance.
The directory label is requested layout, not an independent acceptance decision.
30 new PostgreSQL cases (15 scenarios x two schema paths); aggregate intentional
149 -> 179, publication 36 -> 66. Native counts/typecheck/exclusions unchanged.
One skip is the documented bare-act Supervisor test. No expected assertions were
weakened. Tests cover actual projection exact bundle and original declaration,
immutable revision, current owner preservation, rollback with no partial Package
or grant, same-parent/candidate competition, one slot, stale generations, busy
expired-heartbeat preservation, alias/source/adoption rejection, lost response,
key/body mismatch, actual role revocation/deactivation lock races, actual add/remove
SQL mutation and rollback. Existing suite requalifies expiry and fixture protocol
scenarios; these are not relabeled actual external-writer evidence.

## Fenced remainder / inventory

05b1's complete remainder inventory remains authoritative except the bounded
Package projection and three helper integrations above. Specifically:

- interview/helpers.delete_user_data remains multi-commit, grant reassignment to
  user 1, account disabling and file/Redis cleanup; no deletion integration/proof.
- users/views.delete_privilege/edit_user_profile_page, helper remove_privilege,
  set_user_info active/role composite edits and other account/registration/default
  role paths remain unqualified; the entire user/API writer family is NOT safe.
- admin/views.config_page cloud/file saves and config.allowed/dependency-policy
  observation coordination remain unimplemented. Arbitrary file edits retain the
  observed-snapshot contract, never SQL/file atomicity claims.
- Redis API-key add/update/delete/fix writers and actual api_verify revocation
  races remain unqualified. Current reads alone are not serialized publication.
- Other PackageAuth, package-name/source-policy mutation writers remain fenced
  from approval qualification; existing legacy managed mutation fences persist.
- Retry current desired revision/admin-or-still-authorized-original-approver,
  lost-response retry dedup, explicit ownerless admin assignment shown in preview,
  public confirmation/attempt-status adapters remain NOT implemented.
- 05c owns executor installation polling/frozen live apply and child-safe durable
  terminal slot release. No public approval until aggregate operational qualification.
- Credential-source 05a2b4, broader private index/target/build policy and parent
  acceptance remain fenced. No new source support or implicit adoption.

## Resolution and limits

Bounded 05b2a implemented and locally checked; independent review required.
Do not claim full 05b2 writer safety from SQL-function tests or from the current
config/Redis reader factory. Parent 05 Status/checklist unchanged.

Next: 05b2b — remaining actual authority/security writers and external-store
coordination, retry, explicit ownerless assignment and confirmation/status;
public apply-producing approval remains fenced until 05c.
