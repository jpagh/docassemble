# Experimental-container qualification and recovery walkthrough

This is a **single-container, all-role experiment**, not a production rollout.
Ticket 24's run is not acceptance; the supervisor/reviewer decides acceptance.
Use the final run's `summary.json`, validation exits and logs, not this prose, as
the result. A missing/failed gate means **not qualified**. Never reuse fixture
credentials, attach these proofs to a production database, or publish their ports.

## Repeatable run

Prerequisites: Docker on Linux with the pinned base's platform support, Python
3.10+ on the host, `uv`, `uvx`, and `act`. Reserve loopback 18080/18081 and the
proofs' `da-*` names. The host services caddy/vaultwarden/ja/int/backup are never
stopped, replaced or reconfigured. The host PostgreSQL/Redis test suites use only
da-postgres (55432) and da-redis (56379, database 15). Each source phase creates
its own run-owned disposable PostgreSQL container and volume, with no published
ports. Its source container shares the database's network namespace and connects
on loopback; an authenticated `SELECT 1` readiness check precedes the proof. Source
proofs run as `da-environment`; container/volume cleanup is verified after each
phase, including failures. The scenario containers have their own PostgreSQL/Redis,
files and credential stores; no production files, broker or database is mounted.
Do not run competing qualification jobs; historical harnesses use fixed disposable names.

From the repository root on `uv-implementation`, with tickets 05–23 preserved:

```sh
mkdir -p uv-experiment/24/evidence/validation
# Image construction is intentional preparation (solves/builds allowed here).
docker build --progress=plain -f uv-experiment/fixture/Dockerfile \
  --build-arg DA_REPO_COMMIT="$(git rev-parse HEAD)+ticket-24-worktree" \
  -t da-uv-24:local .
for pair in old:1.10.12 new:1.10.16; do
  docker build --progress=plain -f uv-experiment/fixture/Dockerfile.replacement-proof \
    --build-arg PROOF_BASE_IMAGE=da-uv-24:local \
    --build-arg RELEASE="${pair#*:}" -t "da-uv-24-${pair%:*}:local" .
done

PYTHONPATH=docassemble_base:uv-experiment/executor/src \
DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/15 \
DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres \
uv run --project uv-experiment/executor --frozen --with Flask --with flask-login \
  --with redis==5.2.1 --with tomli-w==1.2.0 pytest -q -p no:cacheprovider \
  uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
act workflow_dispatch -W .act/checks.yml
# Use a NEW evidence directory; failed evidence must not be overwritten.
python3 uv-experiment/run-qualification.py --evidence uv-experiment/24/evidence/run-YOUR-UTC-STAMP
# The changed Python list and exact lint argv are recorded with the run evidence.
uvx ruff check --select E9,F <ticket-24-changed-python-files>
git diff --check
git diff --cached --name-only  # must be empty; never stage or commit here
```

`--only PHASE ...` is for diagnostics. Even a successful subset exits nonzero and
reports missing phases; it is **not** complete qualification. To select a different
migration pair, set `PROOF_OLD_IMAGE` and `PROOF_NEW_IMAGE` explicitly. An interrupted
run keeps logs/results. Check Docker for surviving **proof-owned** objects and
remove those by exact name only. Never run a system-wide prune. The top-level
runner removes newly created da-* containers/volumes; inner drivers also clean up
in `finally`. Pre-existing da-* proof containers cause refusal, not destruction.

If the PostgreSQL suite hangs, stop its pytest process first, retain partial
output, then reset **only the disposable test schema**, before a fresh run:

```sh
docker exec da-postgres psql -U postgres -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='postgres' AND pid <> pg_backend_pid();"
docker exec da-postgres psql -U postgres -c 'DROP SCHEMA IF EXISTS preparation_test CASCADE;'
```

Do not run this reset while another test/proof is using that database.

## Evidence and what it means

The runner records exact argv/exits/durations, immutable image inspection, pinned
base/bundle manifest, Python/platform/libc/uv and Docker identities, source hashes
(including the uncommitted implementation), protected-service IDs/start times
before/after, and per-phase output. `source_qualification_proof.py` instruments
accepted builders without changing their assertions: it retains only bundle
hashes/inventories and completed/failed builder-call counts, never source secrets.
Actual resolver/build/frozen-sync counts come from the image uv audit wrapper;
**builder attempts are not resolver invocations**. Counts include deliberately
failed preparation attempts. Image-build audit prefixes are not runtime solves.

| Run phase | Executed boundary / additional evidence |
| --- | --- |
| proof | Normal sentinels; real admin login/config/logs; broken and killed frozen apply; one independent core recovery; compatible/unknown DB; missing/offline recovery material. |
| boot | Exact inventories, frozen boot, ownership, true/initial/false/update-disabled flags, flag-off adopted-installation fence. Offline safety recovery retains the image-prepared core cache (including platform-specific registry wheels); the separate empty-cache restart proves already-converged retention, not cold recovery. |
| maintenance, consumers, workers, service_sessions, helpers_mail | Request/child leases and controller interruption; Supervisor lifetimes; real Redis Celery prefork and queue state; websocket/Pattern/UNO sessions; scheduled/manual helpers, backup split, real Exim retained delivery. |
| bypasses, full_maintenance | Legacy CLI/API/installer/stale-task fences; intentional non-root development override reported as **not locked convergence**; reload and all-role health; infrastructure shutdown. |
| browser-api, registry | Real authenticated session/CSRF preview and API-key approve; prepare-only shim, permissions/restart false/idempotency; targeted update/removal and frozen normal application. HTTP browser forms, not visual/JavaScript certification. |
| config_publication | Isolated PostgreSQL observation/import/approval; unchanged interactive state. Actual Flask adapter HTTP and policy/race cases additionally run in the full PostgreSQL suite. |
| zip_install, playground_install | Real wheel/sdist ZIP metadata/retention, Playground generation/prepare/approve/cold apply. Not a claim of arbitrary Playground code safety. |
| git_install, private_git | Public HTTPS subdirectory/commit identity plus disposable authenticated HTTPS Git, token scoping/rotation/revocation, cold build/frozen apply. |
| private_index_credentials | TLS-on authenticated local index, public namesake, exact transitive/static/dynamic build bindings, private sdist/PEP 517, credential rotation/revocation, host-unavailable frozen apply. |
| direct_url | Both wheel and `.zip` sdist HTTPS forms, changed-byte refusal, explicit Update, expiry/unavailability and sensitive declaration exception without bundle leakage. |
| core-upgrade | Fresh coherent solves; one frozen apply; schema/health/promotion and lost-report replay; failed setup uncertainty, one prior-core recovery, no restart retry/setup loop. Final run also exercises actual recovery login/CSRF → repair → ready → approve after operator schema clearance, with supervised runtime-owned preparation and application; the proof certifies the bound terminal outcome. |
| historical-rollback | Exact retained historical solution, **no solve/data rewind**, current grants/owners, unchanged schema, missing/known-incompatible material refusal; fresh browser downgrade remains refused in PostgreSQL tests. |
| legacy-adoption | Synthetic conservative legacy/config classification → system publication → uv/schema/web/all-role readiness; no invented ownership. |
| managed-backup, cold-restore | Real dump/objects/credentials and no old venv/cache; fresh incarnation, old deployment stopped, exact web-selected core, stale work invalidated, damaged history distinguished from mandatory material. |
| image-migration | Newer and older shipped-core images around a web-selected release, conflict and both restarts; high-water prevents older setup. Deliberate downtime, not a rolling-upgrade availability promise. |
| cache | Disjoint-path validation, usage/clear/prune, mutation exclusion, retained-material preservation and cold/offline behavior. |
| checkpoint, cloud-checkpoint | Requested failure-before-mutation, local copy interruption, competing writer exclusion, unique same-day identities, remote retrieval/integrity and nightly rotation. |
| restore-local, restore-s3, restore-azure | Each runs the full applied-1.10.14 / pending-1.10.15 fixture. The cloud cases restore **local files AND cloud objects AND real DB AND credential bytes**, after deleting local checkpoint staging. Changed/deleted/new objects and later DB/grants rewind; reserved backup/checkpoint namespaces survive. SIGKILL after DB drop remains gated; explicit fresh authorization finishes; exact pre-change frozen core boots, no reverted upgrade replay or setup/solve/build. |

The full PostgreSQL suite is also the current-authority/concurrency matrix:
`test_publication_writers_postgres.py`, `test_managed_api_postgres.py`, the
`test_publication_*_postgres.py` built-in writer series, `test_installation_postgres.py`,
`test_core_upgrade_postgres.py`, and `test_checkpoint_restore_postgres.py`. These
exercise real PostgreSQL/process interleavings (with some transport/source bodies
loaded into bounded Flask fixtures), not a claim that every account UI route is
exposed by experimental ingress. Native tests inject crash/report/health/schema
boundaries; use the source-named tests and raw logs to distinguish injected faults
from actual SIGKILL/container-restart cases.

Checkpoint `result.json` records allocated `du -sx --block-size=1` bytes before
maintenance, after capture (temporary checkpoint staging), after transport,
before/after restore and at verified steady state. These are measured snapshots,
**not an asserted process-wide peak, quota or production sizing guarantee**.
Capture maintenance duration includes intentional failed capture cases; successful
capture time is separately recorded. Restore time includes fault injection,
preflight and explicit retry; outer driver duration includes full boot/health.
Retained wheels/backups dominate the fixture's extra storage and must not be
mistaken for reclaimable cache. Production time/disk depend on packages, database,
files and provider latency. No automatic checkpoint deletion is promised.

## Walkthrough: ordinary operation and recovery

1. Start the pinned fixture with the image's managed entrypoint and explicit
   qualified-baseline/store configuration used by the drivers. Provision an image
   baseline deliberately (see `image_bridge_proof.py setup`); do not fabricate an
   applied/healthy receipt or copy a venv. Keep the immutable image ID and manifest.
2. Log in as a currently authorized administrator. Prepare a registry/source or
   explicit config import. Preview desired-vs-runtime changes, provenance,
   ownership and warnings. A prepared candidate does not change desired intent or
   runtime. Approve its current ready digest. Authorization is re-read; revoked,
   stale, busy or expired work must be refused. API callers need current key scope
   and `restart` permission; legacy add/update/remove is prepare-only.
3. For a coherent upgrade choose a newer exact/latest-stable/prerelease release as
   appropriate. Fresh browser downgrade is rejected. Optionally request a complete
   checkpoint **before approval**; it extends maintenance and a requested failure
   must stop before package/schema mutation. Publication can advance desired before
   apply, so desired is never proof of currently running packages.
4. Observe independent `da-executor status` / `/recovery/status.json` and readiness.
   Desired, last normally verified, actually running, actual recovery and failure
   are separate. During drain/new health checks keep users/jobs gated. Never
   release a mutation slot because a heartbeat/lease expired or a parent died.
5. On failure retain the journal, selected/recovery baselines, schema start/success,
   terminal receipt, uv log and all retained bundles. Exactly one attempt-bound
   independent core recovery may occur. It must not erase desired/failure evidence
   or retry the failed upgrade. If restricted recovery is available, use normal
   admin login for config/logs and explicit repair/retry. `repair` prepares a NEW
   solution of committed desired intent; retry is the exact published revision and
   is offered only when allowed. A ready repair needs explicit approval.
6. An uncertain schema is not permission to run older setup, stamp, downgrade or
   automatically replay setup. Keep workloads stopped. Inspect the real DB revision
   and operation identity; only matching restricted-root operator clearance can
   acknowledge uncertainty. The core-upgrade proof shows wrong/missing clearances
   refused; clearance itself queues nothing. A new explicit repair is separate.
7. Missing/unavailable mandatory artifacts or incompatible schema means maintenance,
   not fictitious recovery readiness. Preserve failed evidence and repair source/
   storage/permissions explicitly. `DAUPDATEONSTART=false` and disabling the
   experiment cannot bypass an adopted installation's safety gates. Development
   installation is an intentional override, never locked convergence.

## Cache and package-only rollback

Run independent root tooling inside the excluded/qualified deployment:

```sh
da-executor cache usage
da-executor cache prune   # conservative uv cache policy, not retained-object GC
da-executor cache clear   # all reusable cache, after dispatch/mutation exclusion
```

Do not `rm -rf` arbitrary configured roots. The command rejects overlap/symlink
escapes and races with preparation/apply. It never removes retained bundles,
mandatory artifacts, ZIPs, source authentication or failure/history evidence.
Frozen retained-wheel application can work from an empty cache; a lock alone
cannot replace unavailable network artifacts or missing retained material.

Exact historical **package rollback** uses eligible recorded target/material and
requires acknowledging that database/files remain unchanged. Review the retained
schema policy: known incompatibility blocks; an unproven newer schema is disclosed
risk, not certified data compatibility. Restoring interview data is a different,
intentional full restore below.

## Same-target cold replacement (not a data rewind)

Stop/exclude the old deployment first. A root acknowledgement is the bounded
experiment's exclusion authority; timeout/lease expiry is not. Deploy the recorded
image, original absolute store paths/accounts and replacement infrastructure config.
Use an empty replacement database and the existing trusted full backup files tar
plus snapshot-bound dump/material. Create a root-owned 0700 authorization directory
and 0600 JSON containing:

```json
{"format":1,"incarnation":"NEW-UUID","dump_sha256":"DUMP-SHA256","old_deployment_excluded":true}
```

```sh
# Load this variable from restricted operator input; do not enable shell tracing.
export DA_RESTORE_DATABASE_URL="$REPLACEMENT_DB_URL"
da-executor restore --dump /trusted/database --files /trusted/files.tar \
  --authorization /root/restore/authorization.json --prefix "$TABLE_PREFIX"
unset DA_RESTORE_DATABASE_URL
# Then start the same image's normal managed entrypoint, without an old venv/cache.
```

`DA_RESTORE_DATABASE_URL` is required; URL argv flags are not supported. Keep
environment/history/logs restricted: database URLs are credentials. The proof's
copy of the same image-owned qualified-baseline directory is an explicitly
recorded harness equivalence, not permission to skip provisioning in a deployment.
Only new frozen reconstruction plus real schema/web/all-role health establishes
readiness. Copied applied/initialized flags and old Redis tasks establish nothing.
Failed/uncertain backups remain maintenance plus explicit recovery, without an
automatic retry/recovery loop. Unrelated historical damage may warn while verified
current service succeeds; mandatory current/recovery damage blocks.

## Intentional FULL checkpoint restore (irreversible rewind)

**All subsequent DB/files/config changes, user progress and grants are lost.**
Pending broker work is discarded. External effects (e.g. sent mail) cannot be
undone. This is never automatic installation recovery or package-only rollback.

1. Stop/exclude old deployment and every cooperating consumer/writer. Select the
   exact complete checkpoint, recorded same image/target, schema and pre-change
   applied revision. A pending desired upgrade in the dump must not be replayed.
2. For cloud checkpoints, retrieve through
   `CloudCheckpoint(trusted_inputs).retrieve(prefix, NEW_ABSOLUTE_DIRECTORY, table_prefix)`
   under trusted independent operator Python; it verifies immutable identity,
   manifest/dump/material and captured content. `prefix` is the exact advertised
   `checkpoints/<attempt>-<checkpoint>/`. Do not construct a checkpoint from a live
   bucket reference or mixed nightly files. Use the existing provider credentials,
   endpoints/encryption options and access controls. Both providers are qualified
   here only through external CLI fakes; live CLI/service compatibility must be
   separately qualified before deployment.
3. Verify dump/checkpoint SHA-256 and create a NEW restricted-root authorization:

```json
{"format":1,"action":"full-checkpoint-restore","incarnation":"NEW-UUID","checkpoint_id":"CHECKPOINT-UUID","checkpoint_sha256":"CHECKPOINT-JSON-SHA256","dump_sha256":"DUMP-SHA256","old_deployment_excluded":true,"acknowledge_subsequent_data_loss":true}
```

4. Keep provider input JSON root-owned/non-writable by others. It contains the
   same resolved string mapping as backup (`S3ENABLE/S3BUCKET/...` or
   `AZUREENABLE/AZURECONTAINER/AZURE_STORAGE_ACCOUNT/...`, credentials and CLI PATH).
   An unauthorized override is refused. A separate operator PostgreSQL URL must
   address the **same cluster**, database `postgres`; do not grant CREATEDB to the
   application user just for this operation.

```sh
# Both are required, loaded from restricted operator input without shell tracing.
export DA_RESTORE_DATABASE_URL="$CAPTURED_APP_DB_URL"
export DA_RESTORE_MAINTENANCE_DATABASE_URL="$SAME_CLUSTER_OPERATOR_POSTGRES_URL"
da-executor restore-checkpoint --checkpoint /trusted/checkpoint \
  --authorization /root/restore/authorization.json --prefix "$TABLE_PREFIX" \
  --environment /root/restore/cloud-inputs.json
unset DA_RESTORE_DATABASE_URL DA_RESTORE_MAINTENANCE_DATABASE_URL
# Omit --environment only for a local-only checkpoint; never use URL argv flags.
```

5. Inspect the external `checkpoint-restore.json`. Preflight must validate a
   disposable restored DB and frozen environment before replacing live DB/files.
   A destructive failure remains gated. Keep the deployment excluded; inspect its
   recorded `phase` and `staging_database`, repair the cause, and explicitly restore
   again with a fresh UUID/authorization. No automatic startup retry is allowed.
   Inspect/drop only the named orphan staging DB after confirming it is no longer
   in use; never clean it by a wildcard. Keep the external journal/history.
6. `inputs-restored` is not normal readiness. Start managed boot to perform exactly
   the recorded pre-change frozen reconstruction and schema compatibility/web/all-
   role checks. Confirm selected/applied package inventory, DB revision, files,
   credentials, fresh incarnation, cancelled candidates and suppressed upgrade.
   The qualified default Redis celery/single priority/unacked layout is purged;
   unrelated application state is preserved. Unknown transports/routes fail closed.

## Security and limitations that are not relaxed

After image migration, historical image bundle digests are retained as provenance,
not obsolete image bytes. Historical revisions restore from their exact retained
publication bundle (including core) and any selected core bundle; missing or
mismatched retained material prevents a complete backup. The managed manifest's
`revision_image_material` records each revision's image digest, whether image
bytes were archived, and its restore bundle digests. Current image full/core and
qualified baseline wheel material remain mandatory. Full checkpoint restore
qualifies Redis with AOF disabled and a synchronous RDB `SAVE` purge barrier;
unsupported persistence modes refuse rather than claim durable task discard.
The credential-free endpoint fingerprint also fences worker startup after restore.

Existing backup destinations/access/security remain unchanged. **Backup readers
can read saved source credentials**, including the accepted sensitive direct-URL
credential material. These trusted backups are not encrypted/redacted by a new
credential-protection product. Keep dumps, auth/provider JSON and raw backup
objects out of repository evidence; disposable fixture credentials are not real
secrets. Never treat the qualification's non-secret inventory export as a backup.

Build hooks/authorized source code are not a malicious-code sandbox. Private
build-source interposition controls normal PEP 517 prerequisite resolution, not
arbitrary backend I/O. Source/build output can be dynamic/non-reproducible.
External registry/Git/private host availability, live S3/Azure behavior, arbitrary
production schema/data compatibility, split roles, RabbitMQ, remote conversion,
rolling availability and arbitrary extension/plugin effects are **not** certified.
Synthetic releases use current image code with changed coherent metadata; they
prove lifecycle direction/identity, not new upstream application compatibility.
If a critical gate fails or a supported scenario cannot run, retain evidence,
record the blocker, and reopen the affected policy/ticket with the supervisor;
do not relabel an unsupported path or declare completion.
