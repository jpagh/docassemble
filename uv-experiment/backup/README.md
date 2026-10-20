# Ticket 02: reusable existing backup execution

`Docker/backup.sh` extracts the backup work formerly embedded in
`Docker/cron/docassemble-cron-daily.sh`. This is a behavior-preserving prefactor,
not a new backup/checkpoint product or a production managed-lifecycle integration.
The main Dockerfile already installs `Docker/*.sh` into the webapp directory.

## Callable operation

A **trusted caller must supply the resolved configuration environment** normally
emitted by `docassemble.base.read_config`, plus the existing OS/cloud CLI tools.
The standalone operation does not activate Python, read YAML through the mutable
runtime, renew certificates, restart services or run interview cron jobs.
Setting `DA_CONFIG_FILE` alone does **not** load the other configuration settings.
Future independent lifecycle tooling must resolve these settings and provide
trusted tools without activating/importing the application or user packages.

```bash
# After the caller has supplied the resolved environment:
bash /usr/share/docassemble/webapp/backup.sh

# Or use the callable shell operation:
source /usr/share/docassemble/webapp/backup.sh
docassemble_backup
```

Important inputs retain their existing meanings: `DA_ROOT`, `DA_CONFIG_FILE`,
`CONTAINERROLE`, `LOGDIRECTORY`, `DAWEBSERVER`, `DAREADONLYFILESYSTEM`,
`DABACKUPDAYS`, `BACKUPFILESTORAGE`, `DBTYPE`, database/SSL settings, `REDIS` and
`REDISCLI`, S3/Azure enablement/destination/authentication variables, and the
HTTPS/Let's Encrypt flags. Both raw and colon-wrapped role strings work.
Defaults remain those of the existing routine (`all`, 14 days, local storage).
Run with the same privileges/umask as the existing root daily job.

The operation returns **0 when all selected commands succeed, 1 if any fail**.
Failures identify the component and, for command failures, its exit code on
stderr; command arguments/credential environment values are not printed by the
collector. Work remains best-effort: later components can run after a failure,
and partial output can remain. A successful later upload/cleanup cannot erase
an earlier failure. Remote Redis native output stays suppressed as before, but
its failure is now reported. Phases isolate their working directory/environment
changes from their caller.

## Scheduled execution

The daily job still activates its normal runtime and uses the existing
configuration reader, then performs the original sequence:

1. Renew certificates when configured.
2. Delegate current certificate/Apache-site backup to
   `docassemble_backup_certificates`.
3. Perform existing Exim certificate work and daily interview jobs.
4. Delegate the remaining backup work to `docassemble_backup_data`.

The standalone `docassemble_backup` calls both backup phases without unrelated
maintenance. Daily execution accumulates backup failures across both phases.
Activation/configuration-reader failures now stop the scheduled job rather than
silently choosing backup destinations from incomplete/default configuration.
Other certificate-renewal/interview-job error handling is unchanged.

## Preserved coverage/destinations

- Local current copies: configured logs; config/uploaded files for all/cron;
  selected web-server logs for all; local PostgreSQL dumps for all/sql; existing
  Redis RDB for all/redis; configured certificates.
- Dated `backup/MM-DD`: selected config/logs/files, Redis, PostgreSQL and
  certificates with the existing role, read-only and backup-days gates.
  Cron-only remote PostgreSQL/Redis paths remain supported.
- S3/Azure: existing current object prefixes (`log`, `apachelogs`/`nginxlogs`,
  `postgres`, `redis.rdb`, `letsencrypt.tar.gz`, `apache`) and rolling `backup`
  prefix (hostname-scoped outside all-role). S3 still wins if both are enabled.
  Retention and overwrite/delete flags remain unchanged.
- Existing file permissions/ownership/copy semantics and cloud authentication
  are retained. Configuration, including credentials/embedded URLs, is copied
  unchanged; backup readers remain trusted. No new encryption or access policy.

Error-path repairs prevent a failed date lookup from deleting the backup root
and a failed/empty hostname lookup from writing to another cloud namespace.
PostgreSQL enumeration/dump failures are observed directly instead of through
an `awk | bash` pipeline whose last successful command masked earlier failures.

## Verification

```bash
# Builds the pinned ticket-01 fixture only if its local image is absent.
# Runs fresh containers with no network and no published ports; removes them.
./uv-experiment/run-backup-proof.sh

# Choose a compatible ticket-01 fixture image by tag or immutable ID:
BACKUP_TEST_IMAGE=da-uv-proof:local ./uv-experiment/run-backup-proof.sh

# Fast command-boundary tests:
uv run --project uv-experiment/executor --frozen pytest -q tests/test_backup.py
```

The proof records the actual image ID/tool inventory and read-only mounted
source hashes. Real PostgreSQL/file/config/Redis restoration, scheduled YAML
configuration and ordering are exercised. Cloud commands are **external CLI
fakes**, exercising destinations, coverage, authentication pass-through and
failure propagation—not live S3/Azure or certificate-authority evidence.
Raw runs live in `backup/evidence/run-*/`; reviewed evidence goes in
[`evidence/accepted/`](evidence/accepted/). No personal containers or port 80
are used. The fixture requires the ticket-01 core 3.12/executor Python layout.

## Existing limitations — not a historical full-system checkpoint

- `MM-DD` directories are removed/recreated: same-day calls overwrite earlier
  dated backups; there is no unique immutable checkpoint identity.
- No writer drain, cross-component consistency, atomic publication, concurrency
  lock, crash-boundary certification or full restorable-inventory validation.
  An exit code of 0 is not a declaration of a complete historical checkpoint.
- With S3/Azure enabled, uploaded file storage is not copied into dated backups.
  This routine does not snapshot/version corresponding live cloud-file objects.
- The legacy S3 current-log branch uses `DA_ROOT/log`; Azure/local use
  `LOGDIRECTORY`. This extraction does not silently change that distinction.
- Local Redis copies the existing disk RDB, without forcing a fresh save.
  Remote cron Redis uses the existing `redis-cli --rdb` path.
- `backup file storage: false` suppresses the dated local file copy, not the
  separate current local copy. `backup days: 0` suppresses dated backups, not
  current copies. Read-only gates and the legacy certificate-copy exception
  are preserved; a configured no-op may succeed without producing a checkpoint.
- Local PostgreSQL enumeration retains legacy `postgres`/`template` substring
  exclusions and does not add cluster-global role backups. No new database
  engine or arbitrary-filename support is claimed.
- Unadopted backups do not add managed bundles/image identity/package history.
  The ticket-17 managed full-backup extension below adds that coverage only for
  an explicitly managed application dump. Hourly/shutdown/initialization backup
  paths are unchanged.
- Real provider SDK/CLI/service behavior, immutable cloud-file coverage and
  pre-change checkpoint policy/restore workflows remain later qualification.

## Ticket 17: managed full-backup extension

With `DA_UV_MANAGED=true`, full `backup.sh` replaces the application database's
`pg_dump` with the independent executor's snapshot-bound operation. The trusted
caller supplies the same resolved backup settings as above, the independent
executor Python (`DA_EXECUTOR_ENTRYPOINT`'s sibling `python`), config/DB access,
`DA_DURABLE_WORK_STORE`, `DA_QUALIFIED_IMAGE_BASELINE`, and the applicable
`DA_MANAGED_ARTIFACT_ROOT`, `DA_EXECUTOR_ARTIFACT_ROOT`, `DA_MANAGED_SECRET_ROOT`
and `DA_INDEX_CREDENTIAL_ROOT`. The scheduled independent executor passes these
image/operator inputs through its existing trusted mapping. No runtime activation
or application imports are needed. A managed full backup with no application DB
dump or no durable destination selected fails rather than claiming completeness.
The existing read-only/local-copy gates are preserved, not bypassed.

Each ordinary PostgreSQL dump has an adjacent `<database>.managed/` directory:

- `records.json`: archive-derived retained revision/intent/config, baseline,
  operation/schema evidence and persistent material records (candidate-only
  material records excluded from this object inventory).
- `objects/`: mandatory retained full/core and image recovery bundles, image
  baseline artifacts, required ZIP/direct artifacts, persistent source auth and
  local lifecycle/selected-baseline evidence. Disposable caches, venvs, candidate
  staging and live admission/PID authority are excluded.
- `manifest.json`: final verified completion marker, dump hash, archive-derived
  records digest/reference set, copied file hashes and original file/directory
  ownership/modes. Failed/interrupted assembly has **no** completion marker.

Enumeration, the exported DB snapshot, copying and verification hold the same
environment-row lock used by publication and candidate-material cleanup. This
can delay publication for the backup duration. Objects published after an earlier
interview-file copy but before this snapshot are enumerated and copied; later
publication cannot alter this dump's certification. Verification parses the actual
`pg_restore` COPY output, never a later live DB. All retained historical mandatory
references are included; a missing unrelated historical object still fails
backup creation. No destructive history collection is introduced.

The dump and adjacent directory use the existing PostgreSQL local/current/dated
and S3/Azure paths and existing access model. Backup readers can extract usable
PAT Git and credential-bearing direct URLs, as accepted; no credential-specific
protection or new external secret service is added. Successful provider CLI
uploads, not remote-provider end-to-end readback, remain the transport boundary.

Verify a downloaded/copied dump **and** its adjacent directory before using it:

```sh
# Independent executor environment/config identifies the trusted table prefix.
python -m docassemble_executor.managed_backup --verify --dump /backup/postgres/docassemble
python3 uv-experiment/run-managed-backup-proof.py
```

The manifest's `managed_objects_complete: true` applies only after dump binding,
all reference/file checks, and verification succeed. Never trust a boolean or an
old marker alone: interrupted destination updates may leave old files, which
must fail dump-hash/reference verification. Restore original object paths with
recorded directory/file ownership/modes; restore the corresponding ordinary DB
dump. Copied candidates/host observations/journals are evidence, **not startup or
installation authority**; full restore admission/fencing is ticket 18. Do not
point a running deployment at restored authority.

`interview_checkpoint_complete` is always **false**. No interview writer drain,
cloud interview-object versioning, automatic database rewind or cross-component
checkpoint is claimed here; those remain tickets 21–23. Existing MM-DD overwrite,
retention and credential expiry/upstream availability limitations still apply.

## Tickets 21–22: opt-in content-backed pre-change checkpoints

The executor's existing per-operation checkpoint option remains **off by default**,
prepare-authorized and bound to the approved inputs. Capture runs only after
preparation and consumer drain, under the exclusive runtime/mutation locks, before
package/schema mutation. Failure aborts that operation without an automatic data
restore. The recorded applied environment/schema is distinct from advanced desired
state; recovery/unverified captures are never labeled known-good.

`checkpoints.py` now adds `cloud_checkpoints.py` for S3 (`s4cmd`) and Azure (`az`),
using the same trusted `/etc/docassemble/managed-backup.json` resolved environment,
cloud destinations and CLI authentication as the ordinary backup. S3 wins when
both providers are enabled. Configuration/credential bytes remain accessible to
trusted backup readers, as before; no new encryption, secret service or access
policy is introduced. CLI diagnostics are suppressed and failures are redacted.
Existing S3 endpoint/server-side-encryption fields are preserved; unknown
`S4CMD_OPTS` fail explicitly rather than silently changing security/transfer
semantics. Azure custom endpoints/Key Vault indirection are not qualified by this
CLI seam; an explicitly provisioned standard CLI connection string must match the
active configuration when that mode is used. Missing/unverified list/read/write
capabilities fail rather than advertising a partial checkpoint.

A complete checkpoint contains actual corresponding object **content**, not just
live object URLs. The executor enumerates the entire bucket/container except the
reserved backup-only `backup/` and `checkpoints/` prefixes (including ordinary and
custom application file sections). Captured keys map to SHA256-named retained
content objects. Listing/size coverage, a second full read/hash, and a final
listing around the real managed database dump detect missing/changed content.
Ambiguous S3 text listings/control or wildcard object names fail explicitly.
This assumes cooperating application writers are drained; it is not a provider
atomic snapshot or protection against arbitrary concurrent external writers.
Including other mutable non-backup objects can conservatively fail capture.

Local staging remains under `DA_ROOT/backup/checkpoints/`; the advertised cloud
copy is under **`checkpoints/<attempt UUID>-<checkpoint UUID>/`**, outside the
nightly `backup/` namespace. All payload objects are uploaded without overwrite,
then independently retrieved/hash-checked and manifest/database-coverage-verified;
the completion manifest is uploaded last. A failed assembly remains incomplete.
Same-day operations have different identities. `DABACKUPDAYS=0` disables capture;
positive configured days are recorded, but operation-linked checkpoints have **no
automatic deletion/expiry**: operators deliberately prune them. Nightly rotation
can replace its own copies, never the advertised `checkpoints/` objects. Status
includes creation time/age, capture completeness, known-good labeling and cloud
locator; completeness describes verification at capture, not a guarantee against
later operator deletion/provider loss. Existing external bucket/container
lifecycle policies must retain the `checkpoints/` prefix; this experiment does
not override provider policies or establish object-lock protection. Always
reverify retrieval.

`CloudCheckpoint(inputs).retrieve(prefix, new_directory, table_prefix)` retrieves
and verifies database binding, managed material, cloud/local content coverage and
operation/destination identity using only captured bytes. It does **not** restore
live files, database or authority. Full checkpoint restore remains a separate
explicit operator/data-loss-acknowledged workflow; cloud restore qualification is
ticket 24, not this capture/retrieval slice.

```sh
python3 uv-experiment/run-cloud-checkpoint-proof.py
uv run --project uv-experiment/executor --frozen pytest -q \
  uv-experiment/executor/tests/test_cloud_checkpoints.py \
  uv-experiment/executor/tests/test_checkpoints.py tests/test_backup.py
```

The disposable full-image proof records image/source identities, real PostgreSQL
manifest binding, interrupted/authentication failure handling, actual callable
nightly rotation, retrieval after local
staging removal/live-object changes, and pinned uv 0.12.21 empty-cache offline
frozen sync from retrieved managed package/artifact content. All S3/Azure transport
uses **external CLI fakes** (`cloud_cli_fake.py`), not live providers. The focused
PostgreSQL suite additionally tests two same-day checkpoints per provider and
injects missing/changed objects, partial/corrupt uploads, partial manifests and
wrong identities. See `uv-experiment/22/evidence/` and
`TICKET-22.md`. Real provider behavior/authentication/versioning/object-lock and a
full cloud database/files/authority restore are not claimed here.
