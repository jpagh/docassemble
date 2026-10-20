# 05b1: Internal atomic approval publication (bounded fixture scope)

Date: 2026-10-14. Branch: `uv-implementation`. Baseline:
`82957c3f18a3c63f5dde45f0bdeac3618bb23330`.

**PUBLIC APPLY-PRODUCING APPROVAL REMAINS FENCED UNTIL 05c.**
**NO actual production writer is integrated; NO production writer-safety claim
is made.** This is the user-authorized bounded fallback, confirmed by the
supervisor during implementation, not completion of 05b or parent 05.
All actual account/role/API-permission/PackageAuth/name/source-policy/config-policy
writers, retry, ownerless administrator assignment and public approve/confirmation
adapters remain explicitly fenced as **05b2**. Credential-source 05a2b4 and 05c
are unchanged. No new-system changes or branch switch.

## Objective and implementation

`docassemble.base.managed_publication.Publication` extends the shared semantic
protocol for internal fixture publication only. Public adapters still construct
`Preparation`, whose approve/retry methods reject. No production route constructs
Publication; no installation polling, apply or slot-release interface is added.

The additive `05b100000001` migration adds publication-result deduplication and
published candidate state. Unique actor/key, candidate, revision and attempt
constraints plus foreign keys back application checks. Fresh ORM initialization
uses the same 12-table schema/guards. The older migration now explicitly freezes
its original 11-table/state/guard domain rather than silently absorbing new
publication schema from the shared factory. Published readiness bytes and revisions
remain immutable. Downgrade explicitly rejects: published history needs a separate
archival protocol, not silent preview-only reinterpretation.

Approval checks dedup first; exact successful repeats require current status
authority, return the recorded pending revision/attempt even after generation
advancement, and mismatching key/body conflicts. New requests lock environment,
then candidate; recheck fresh trusted DB authorization, requester, digest,
DB-time expiry, parent and all input/target/runtime/publication generations,
active slot, contributions and ownership effects. Ownerless assignment remains
unsupported. Busy does not consume readiness or extend expiry. A trusted SQL-only
projection callback runs on that same connection; exceptions roll back its writes.
One short transaction commits contribution/identity/projection, complete immutable
revision snapshot and exact approved bundle attestation, desired pointer/generation,
one pending installation attempt, published candidate and dedup result.
No expensive artifact IO, solve, apply, credential promotion or heartbeat release
occurs. The source builder's durable-materialization attestation remains trusted;
these tests do not independently requalify uv/retained filesystem bytes.

Return values say `published` / `pending`, never installed. Shared candidate status
marks installation_requested only for published candidates, continues to report
approval_supported false and never shows installation success. Cancellation of a
published candidate explicitly rejects, rather than surfacing a raw trigger error.
Public confirmation/attempt status UX remains 05b2/05c, not this small projection.

## Common serialization seam for 05b2

`Publication.authority_write()` opens a transaction, obtains EnvironmentState's
row lock BEFORE the writer reads or mutates authority, then yields the exact SQL
connection used for all mutations. Commit/rollback occurs once at context exit.
Lock order: environment -> candidate (publication only) -> authority/projection
rows. Authorizer signature remains `(connection, actor, operation, candidate)`;
projection is `(connection, actor, candidate) -> {legacy_package_id, owner_id}`.
Both are trusted integration interfaces, not browser-supplied callbacks. Projection
must recheck actual normalized name/source/current grants and preserve ownership;
callbacks may perform only short SQL work, no independent commit or external IO.

Fixtures mirror that call order: writer obtains environment lock, then mutates
current authority SQL on the yielded connection; publication acquires environment
and candidate locks, rereads current authority, then invokes SQL projection on its
publication connection. 05b2 can substitute actual writers/Package projections
without changing transaction ownership. Fixture SQL authority/projection tables
are deliberately **NOT** real User/Role/PackageAuth/Package writers. No claim that
legacy multi-commit deletion or Redis/config writers already obey this seam.

## Fenced remainder / writer inventory

Publication-contract section 8 inventory and inspected authority expansions:

| Writer/caller | Current treatment / 05b2 remaining work |
| --- | --- |
| packages/views.py direct limitations/install/uninstall | Existing managed mutation fence; public approve/confirmation and real Package projection not integrated. |
| packages/api.py install/update/delete/restart=false/status | Existing fence/preview; public approve/retry and durable attempt status not integrated. |
| utils/helpers.py install_zip/git/pip_package/uninstall | Existing managed fence; no source replacement/ownership bypass; actual grant projection needs integration. |
| develop/views.py Playground ZIP install | Existing managed fence; no ZIP/source scope expansion. |
| tasks/tasks.py update_packages/reset_server; update.py CLI/helpers | Existing legacy managed fences; installation polling remains 05c. |
| create_tables.py discovery/add_dependencies/core rewrites | Existing managed skip; schema upgrade supplied here, no intent adoption. |
| base/read_config.py + Docker/initialize.sh dependency/index inputs | Existing CLI managed fence; observation/policy serialization not integrated. |
| admin/views.py config_page | Cloud/file writes and restart are outside SQL; policy/config serialization and observed generation fail-closed contract not integrated. |
| interview/helpers.py delete_user_data | Multiple commits, PackageAuth reassignment to user 1, role removal and account disabling must be preserved and integrated; no actual deletion race evidence here. |
| users/views.py deletion/profile/role administration; users/api.py privilege/account routes | Actual SQL role/account writers not integrated. |
| users/helpers.py add_user_privilege/remove_user_privilege and hooks/default.py forwarding | Actual helper authority mutations not integrated. |
| api/helpers.py add/update/delete/fix_api_keys; UI/API callers | Redis-backed credential permissions/constraints need cross-store serialization; no actual Redis writer race evidence here. |
| packages/managed.py PreviewAuthorizer policy/config readers | Preview uses fresh SQL/current config/Redis callbacks only; not approved publication authority. Actual-name aliases, source protection, current ownership and revocation need publication recheck qualification. |
| Install/Package version readers and package lists/status | Desired/pending/verified-running/recovery distinctions and confirmation adapters remain; never use legacy installed flags as proof. |

05b2 also owes administrator ownerless assignment displayed in preparation,
preservation of existing ownership, retry ONLY current desired revision by admin
or still-authorized original approver, lost-response retry/key-body evidence,
actual account/role/grant/policy revocation races, alias/source bypass cases and
complete writer audit. Relevant external policy mutations cannot be made safe
merely by this SQL environment lock: their coordination needs explicit integration.
05c owns live frozen apply, runtime observations and durable terminal child-safe
slot release. No automatic heartbeat release exists here.

## Verification (qualified internal fixtures only)

Exact commands from repository root (the first three logs were captured before
supervisor steering to single-line bash/no shell redirection; subsequent commands
use simple calls):

```sh
PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login pytest -q -p no:cacheprovider uv-experiment/preparation/tests/test_publication_postgres.py
# 36 passed in 3.66s; exit 0

PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
# 149 passed in 21.16s; exit 0

act workflow_dispatch -W .act/checks.yml
# 292 passed, 1 skipped in 30.39s
# Success: no issues found in 51 source files; Job succeeded; exit 0

uv tool run ruff check --select E9,F docassemble_base/docassemble/base/managed_publication.py docassemble_base/docassemble/base/managed_records.py docassemble_base/docassemble/base/managed_preparation.py docassemble_webapp/docassemble/webapp/alembic/versions/05a100000001_managed_preparation.py docassemble_webapp/docassemble/webapp/alembic/versions/05b100000001_managed_publication.py uv-experiment/preparation/tests/test_publication_postgres.py
# All checks passed!; exit 0
```

Real PostgreSQL 16.6 disposable database, both migration and fresh initialization.
**36 cases are fixture-authorizer/projection scenarios, NOT actual cooperating
production-writer race evidence.** They cover same-parent/candidate competition,
same-key duplicate concurrency, busy/expired heartbeat slot preservation, DB-time
expiry, stale input/runtime/target, digest/requester/ownership/ownerless rejection,
contribution drift, lost-response authorization and body mismatch, immutable revision,
exact approved attestation and preservation of existing contributions. Controlled
threads prove cooperating fixture revocation/deletion blocks approval until commit,
and revoked duplicates cannot view the result. A paused uncommitted projection is
invisible to independent readers; projection failure rolls back all intent.

Evidence: `uv-experiment/05b/evidence/accepted/{publication,postgres,act}.log`.
"accepted" is the requested qualified-evidence layout, not independent acceptance.
No actual writer evidence is inferred from this directory name. Intentional
PostgreSQL count: 113 -> 149 (+36 scenarios). Packaging schema-count assertion
11 -> 12 is intentional; native test/typecheck counts unchanged. One native skip
is still the bare-act Supervisor test. Initial lint found unused import/fixture
name-shadow lint; fixed, final lint passed. No test exclusions added.

## Resolution and limits

Implemented/locally checked bounded 05b1; independent review required. Full 05b
and parent 05 remain incomplete; parent Status/checklist unchanged. No actual
production writer integration/safety claim, public approval, retry, installation
success UX, 05c, 05a2b4 or parent acceptance. 06 remains blocked. Commands after
steering follow simple single-line bash/no pipes, heredocs or shell redirection.

Next: 05b2 — actual cooperating writers, Package/current-grant projection,
ownerless assignment, retry and confirmation/status adapters, still public-fenced
until 05c operational integration.
