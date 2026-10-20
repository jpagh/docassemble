# Intentional full checkpoint restore (23)

This is **operator/executor tooling only**, not a browser action, package-only
rollback, or installation-failure fallback. Package-edit/admin approval does not
authorize it. Browser checkpoint status/identity selection conveys no execution
authority. Full restore loses subsequent DB/files changes, including interview
progress and accounts/grants, and **discards pending background work**. Sent mail
and other external effects cannot be undone.

## Preconditions

- Stop/exclude the old all-role deployment, its executors/preparers and queue
  producers. As in ticket 18, the explicit operator acknowledgement is the
  exclusion authority; a heartbeat, PID or expired lease is not. Run the
  independent executor as root, with SQL/Redis infrastructure available, not the
  application interpreter. Local admission/drain and mutation/runtime leases
  additionally exclude cooperating survivors; the tool does not silently kill
  arbitrary deployments or take over a surviving worker.
- Use the recorded compatible image/target, original absolute managed-store and
  config/files paths, and recorded database identity/configuration. Different
  infrastructure/config migration is not implicit restore authority.
- Select a complete local checkpoint directory, or retrieve a cloud checkpoint
  using `CloudCheckpoint.retrieve(prefix, destination, table_prefix)` and the
  existing trusted backup environment first. The same verified manifest/content
  contract is consumed for both. Cloud restore additionally requires
  `--environment /etc/docassemble/managed-backup.json`, bound to captured config
  and provider/container. No new secret-protection subsystem is provided.
- The qualified broker is Redis (`rabbitmq: redis://...` in config), with the
  existing `celery`/`single` queues/default Kombu priority layout. Unknown broker,
  custom queues, missing credentials/content or incompatible target/schema fail
  before replacing live data. Only those priority queue and unacked transport
  keys are discarded; never Redis `FLUSHDB` or unrelated API/application keys.
- Allow space for a private restored validation database plus a fresh temporary
  venv/cache. Preflight consumes retained frozen wheels using pinned uv 0.12.21,
  no old venv/cache, no solve/build or network substitution, and checks the
  restored schema without setup. Upstream expiry is not repaired by re-solving.

## Explicit authorization and command

Create a root-owned `0700` authorization directory outside restored paths and a
root-owned `0600` JSON file with these fields (compute SHA-256 from actual bytes):

```json
{
  "format": 1,
  "action": "full-checkpoint-restore",
  "checkpoint_id": "SELECTED-CHECKPOINT-UUID",
  "checkpoint_sha256": "SHA256-OF-checkpoint.json",
  "dump_sha256": "SHA256-OF-database",
  "incarnation": "FRESH-UUID",
  "old_deployment_excluded": true,
  "acknowledge_subsequent_data_loss": true
}
```

```sh
da-executor restore-checkpoint \
  --checkpoint /absolute/verified/checkpoint-directory \
  --authorization /root/restore-authorization/restore.json \
  --database-url 'postgresql+psycopg2://USER:PASSWORD@HOST:PORT/RECORDED_DATABASE' \
  --prefix 'RECORDED_TABLE_PREFIX' \
  --maintenance-database-url 'postgresql+psycopg2://OPERATOR:PASSWORD@SAME_HOST:SAME_PORT/postgres'
```

Do not treat this example as an authorization template to install automatically.
The actor must deliberately select that identity and acknowledge the warning.
The target cannot be `postgres` or a template database. Operator PostgreSQL
lifecycle credentials need create/drop/rename-database privileges; failure is
fail-closed. `--maintenance-database-url` is optional only when the target
credentials already have those privileges; do not grant them to the application
role. The independent operator URL is restricted to the same resolved host/port
and PostgreSQL server identity and the `postgres` maintenance DB, and is used
only for CREATE/DROP/RENAME. Validation restores use the captured app identity;
the private DB OWNER is that recorded application user. No credential appears in
fixed failure diagnostics.

## What is restored

Preflight validates checkpoint completeness, hashes, all managed references,
original target and retained packages; restores the actual dump into a private
validation DB; verifies schema evidence; reconstructs applied intent, config
observation, contributions and the revision-bound Package projection; invalidates
unpublished candidates/material and supersedes every copied pending/running
installation attempt. Capture-point DB accounts/grants/data are kept, not old
publication-time authority. Older applied revisions without required Package
projection evidence are refused with a recapture/repair diagnostic, never guessed.
Recovery/unverified captures retain their labels; restoration is not proof they
were known-good.

Only after successful preflight does it replace the selected DB and corresponding
files/config (including removing files created later), restore managed material,
discard stale broker work and establish a fresh incarnation. Copied health,
initialization, schema-clearance and pending-upgrade receipts are not authority.
The desired pointer selects the **recorded pre-change applied revision**, not the
already-advanced desired pointer in the dump.

Successful input restoration still leaves consumers closed. Start the independent
executor normally for exact frozen reconstruction, nonmutating schema
compatibility, core/web and all-role health qualification. Historical setup is
not rerun, including when the recorded revision's mode was `upgrade`; an
`unchanged` mode remains unchanged. Disabled convergence never admits copied
health as a substitute.

## Interruption / unavailable material

Inspect root-owned `STATE/checkpoint-restore.json`: selected identity, fresh
incarnation, target DB, private `staging_database`, phase, failure action and
completion state are outside the database being replaced. Boot, explicit
runtime/recovery commands and both durable workers refuse incomplete restore
states, even if the target DB is missing or contains only a partially restored
combination. No automatic data restore, retry, normal startup or queue replay.

Keep the old deployment excluded. Repair unavailable material/access, inspect the
recorded phase/target/staging DB, then deliberately rerun full restore with a
**new** restricted authorization/incarnation. Historical external journals are
archived. A preflight-only staging DB is cleaned up; destructive/interrupted
staging DBs remain named evidence for operator inspection. Clean up only the
specific recorded orphan DB after recovery; do not run wildcard DB deletion.

## Evidence boundary

`run-checkpoint-restore-proof.py` uses disposable old/replacement containers and
pinned uv, with real DB/files/grant rewind, schema/no-setup behavior and actual
empty-cache frozen reconstruction. PostgreSQL tests inject interruptions at
validation, DB drop/rename, local files, broker discard and authority reset.
Native tests exercise both S3/Azure content restore using external CLI fakes,
including post-checkpoint deletion, immutable backup preservation and failures.
Live-provider/end-to-end cloud deployment qualification remains ticket 24; fake
CLI content transport is not evidence of a live S3/Azure service guarantee.
