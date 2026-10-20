# Same-target cold restore (experimental single-container seam)

The independent executor restores a trusted existing full backup **before** boot
selects an environment. No application venv or uv cache is a restore input.
This is not migration, a general restore UI, or a coordinated interview-data
checkpoint. The normal backup access/security model is unchanged: backup readers
may recover usable source credentials.

## Operator procedure

1. Exclude the old all-role deployment, including installers and descendants.
   Stop/remove it and prevent its restart or access to the replacement deployment.
   Lease expiry, copied host/PID/heartbeat/applied flags are never exclusion proof.
2. Provide an **empty** replacement PostgreSQL database and necessary roles. Do
   not point the restore command at a live database. Package the existing local
   backup's `config.yml` and `files/` as a tar archive (regular files/directories
   only). Config/files are restored with their original ownership/modes. Use the
   managed dump and its adjacent `<dump>.managed/` from ticket 17 unchanged.
3. In a root-owned **0700** directory, create a root-owned **0600** JSON file:

   ```json
   {"format":1,"dump_sha256":"<SHA-256 of dump>","incarnation":"<fresh UUID>","old_deployment_excluded":true}
   ```

   This explicit **operator acknowledgement is the exclusion authority** for
   the bounded experiment. There is no automatic Docker-stop verifier and no
   default authorization. Do not copy an old UUID. The command rejects missing
   authority, a different dump, an existing replacement DB, or interrupted local
   restore evidence. An interrupted restore requires operator recovery/new empty
   replacement inputs, not automatic replay over a partial DB.
4. With independently provisioned executor tooling (not application Python):

   ```sh
   da-executor restore --dump /backup/database --files /backup/config-files.tar \
     --authorization /root/restore-authority/restore.json \
     --database-url '<replacement SQLAlchemy PostgreSQL URL>' --prefix '<table prefix>'
   da-executor boot
   ```

   Restore uses the backup's original absolute managed-store paths and numeric
   owners, so provision the same single-container path/account layout. The
   restored configuration must name the replacement DB/infrastructure; no config
   migration or automatic endpoint substitution is performed. Destination parent
   symlinks, archive links/traversal and unlisted managed objects are refused.

## Safety and observability

- Matching image/target/tool only. Frozen exact desired project/lock consumption;
  web-selected core and the actually verified recovery selection survive. No
  resolver, image-default reset, source substitution or copied-health admission.
- `DAUPDATEONSTART=true` and `initial` allow verified initialization. `false`
  cannot start an unverified cold restore. Read-only/unsupported-role/update
  disable gates still apply. A later verified compatible local environment may
  use the existing no-mutation boot-resumption path.
- Missing/corrupt current or required recovery objects block service. Missing
  unrelated history is recorded in durable `cold_restore.incomplete_backup`,
  `unrestorable_revision_ids`, and prominent `INCOMPLETE BACKUP` warnings in
  startup logs and authorized/runtime status. Preparation input ancestry is
  provenance, not a requirement to reinstall unrelated historical solutions.
  History is retained; missing bytes are never fabricated.
- New incarnation fences copied claims and receipts. Unpublished candidates are
  cancelled with advanced fences; candidate-only secrets become unusable/deleted.
  Published candidate attestations and committed authentication remain intact.
  Redis/Celery tasks are never installation authority.
- Copied failures/uncertain schema and lifecycle evidence remain blocked for
  explicit operator recovery, not restart retry. A historically failed/uncertain
  backup remains in maintenance; this ticket does not reconstruct a restricted
  runtime merely to offer its repair UI. A fully bound lost-success receipt can be
  reconciled as history, never local health. Successful setup evidence uses only
  non-mutating compatibility checks after frozen runtime reconstruction. A new
  restore attempt is issued once; interrupted/failed restore installation is not
  automatically reissued. Data is never restored automatically after install
  failure.
- Restored credentials may be revoked/expired. Retained artifacts can work
  without upstream access; any actually required unavailable upstream input
  fails without solving or substituting a public source.

## Qualification

`../run-cold-restore-proof.py` uses two disposable `da-uv-16:local` containers and
an ephemeral backup volume. It creates an actual selected 1.10.14 fixture core
upgrade (repacked real image wheels; **not** a published upstream release), backs
up real PostgreSQL/config/files/authentication, stops the source deployment, and
runs the independent restore CLI in the replacement with no old venv/cache.
Required-object damage blocks, unrelated history damage warns while current
web/all-12-role readiness succeeds, successful setup is not repeated, revoked
fixture authentication is rejected, and pinned uv 0.12.21 fails frozen unavailable
upstream consumption without changing the lock. The image's baked build-time uv
log is excluded by an independently captured immutable-image prefix, not mistaken
for restore invocations. Credential-bearing backup bytes stay in the disposable
volume, not repository evidence. Containers/volume are removed in `finally`.

Evidence is under `evidence/`; ticket results are recorded in `../TICKET-18.md`.
Independent review/acceptance belongs to the supervisor, not this implementation.
