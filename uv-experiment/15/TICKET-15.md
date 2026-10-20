# 15: Exact historical package rollback (review pending)

Implementation/evidence only; no ticket or parent acceptance is marked.

## Contract implemented

- Admin-only retained-revision selection. Matching image/target/tool, complete
  historical intent, and intact full/core bundles and artifact hashes are
  required. The initial image baseline is also a rollback target; its original
  recovery bundle and provisioned exact wheels must verify.
- Preparation cold-materializes the recorded project with pinned uv using only
  `sync --frozen --offline --no-build --no-config`, in a disposable venv. There
  is no lock/solve/build or re-selection of historical sources. The immutable
  preview binds historical requirements, core changes, current ownership effects
  and both desired/running diffs.
- Approval requires explicit `acknowledge_database_unchanged: true` (JSON) or
  `true` (form). Current accounts/grants remain current. Restored ownerless
  interactive intent requires an explicit list of normalized names to assign to
  the approving administrator. No transitive inventory creates owners. Managed
  dependency intent/config snapshots are restored, not YAML or unrelated settings.
- Rollback publishes a new immutable revision and uses the existing installation
  slot, maintenance/drain, frozen sync, one-attempt prior-core recovery and durable
  reporting. Desired intent and failure evidence are not rewound after failure.
  There is no fresh browser-selected downgrade operation.
- Durable `schema_mode: unchanged` skips older setup during rollback, later
  ordinary operations, and same-image boot. Boot resumption verifies the published
  unchanged-schema policy instead of demanding invented setup-success evidence.
  An explicit newer upgrade selects the schema/setup path again.

## Narrow schema policy and limitations

Known negative knowledge is authored by the retained release wheel's repeatable
`Docassemble-Incompatible-Schema-Revision` METADATA header. Wheel bytes are bound
by the immutable bundle manifest. This is a bounded negative list, not a general
schema-certification mechanism or an operator browser override.

Unreadable recorded database state or retained Alembic inventory fails closed.
Known incompatibilities block before mutation (and block recovery qualification).
An unknown/newer recorded revision is **unproven**, not automatically incompatible
or silently certified compatible. Nonmutating runtime/web/all-role checks remain
mandatory. Their success cannot prove complete native-library, application, data
or schema compatibility. Preview explicitly discloses this risk; the executor
rechecks actual database state before mutation. Durable schema-check evidence and
status retain the `unproven` distinction. No schema downgrade, stamp, automatic
backup or data restore is added.

## Container proof

Pending final evidence capture in `evidence/container/` and `evidence/proof.log`.
The accepted 14b fixture fallback is reused: 1.10.14 is synthetic coherent release
metadata over the current application code, **not** a published-release or changed
application-code compatibility claim. The rollback applies the exact retained
initial 1.10.13 bundle with real pinned uv. The proof adds real `cowsay==6.1`
intervening intent/material, explicitly provisions/deletes a disposable current
owner, and restores historical requirements with explicit assignment. An advanced
unknown marker over unchanged compatible fixture tables is classified unproven;
a separate fixture-authored known-incompatible marker is rejected. These are
bounded compatibility-policy tests, not universal data compatibility evidence.

## Validation

- Focused executor/PostgreSQL/HTTP/boot tests: **131 passed**.
- Native act suite: **735 passed, 1 skipped**; mypy **66 source files**, no issues.
- Ruff E9/F: **121 modified/untracked Python files**, clean.
- Full PostgreSQL/preparation suite and container proof: running; final counts
  will be recorded before handoff.

Superseded in-flight PostgreSQL runs were explicitly stopped after final boot and
idempotent-assignment replay fixes; their logs are retained. Other backends were
terminated and `preparation_test` dropped before rerunning. A stale proof image
was rejected by the driver’s source-hash check before setup; that negative run is
retained as `evidence/container-source-mismatch/`, not reported as green.

No staging/committing. Disposable proof containers are removed by the driver;
existing caddy/vaultwarden/ja/int/backup services are not modified.
