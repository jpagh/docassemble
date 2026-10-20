# 05a2b4: Scoped HTTPS private-index wheel snapshots (bounded fallback)

Date: 2026-10-15 UTC. Branch: `uv-implementation`. Baseline:
`50e18046185603d1e364c4f585c821b4a890a7e2`.

**Bounded partial implementation, NOT complete 05a2b4/source parity or parent-05
acceptance.** The task-authorized fallback and supervisor-approved snapshot
refinement implement real authenticated private-index **wheel-only preparation**.
No public source-policy adapter, production private dispatch or credential
promotion is enabled. Parent 05 Status/checklist unchanged; 06+ remain blocked.
Independent review required; `accepted/` is the requested evidence destination,
not an acceptance verdict.

## Decision and implementation

Direct uv authentication has network-location credential reuse semantics; merely
setting named-index environment secrets would not establish same-host path
isolation. Supervisor approved a strict HTTPS snapshot downloader instead of an
authenticated uv proxy. Only administrator-declared direct AND transitive names
are fetched from their exact bound index. One local snapshot contains only those
names, preserving original HTTPS index/name/artifact provenance. uv resolves and
installs those retained wheels **offline**, with no public index/fallback and no
synthetic transitive requirements. Actual pinned-uv testing showed source-table
entries alone do not select transitive indexes; the aggregated offline default
index enforces the declared binding by construction. Because `--no-config` ignores
the default-index setting, the safe local default index is also passed explicitly
on every private uv command. No remote index or credentials are passed to uv.

Opt-in internal `RegistryBuilder(..., private_snapshot=PrivateIndexSnapshot(...))`
accepts captured policy:

```json
{"private_indexes":{"private":"https://index.example/private/simple/"},
 "private_bindings":{"distribution":"private","private-transitive":"private"}}
```

Operator configuration separately maps each index label to a reference file in a
trusted owner-only 0700 directory. Its regular, non-symlink 0600 file, owned by the
executor UID, contains `index_url`, `username`, `password`. The **independent
reference's exact index_url** must match the captured index, before any network
request. Changing the URL while retaining a label cannot reuse its credential.
Credential values are bounded, read afresh and never returned in results; existing
operator references are reusable and never consumed/deleted. This is operator
retention, NOT candidate-to-committed promotion or an authorized-updater API.
Trusted directory ancestry, DB/configuration and the OS identity remain operator
authority. Same-UID malicious code is not a security boundary.

HTTPS verifies TLS by default, with an explicit operator CA file option for the
local proof. No trusted-host/GitHub exception is introduced. Downloader Basic auth
is restricted to the configured index prefix and exact per-package wheel path;
**all redirects reject**, including same-host redirects. Credential-bearing index
URLs and artifact userinfo/token/query links reject without echoing input. There
is no ambient uv/pip credential/config inheritance. Source credentials never enter
uv arguments/environment, project/lock files, cache or retained bundles. Raw uv,
HTTP and parsing failures are withheld in favor of actionable redacted messages.

The bounded source policy requires simple UTF-8 HTML pages, 1–100 SHA256-bearing
`py3-none-any` wheel links per name (64 MiB per artifact, 1 MiB pages/metadata), no
sdists, query-bearing links or external/shared artifact locations. All advertised
wheels are checked, including metadata name/version and every declared dependency,
even inactive-marker declarations. Missing private bindings fail before uv;
unsupported transitive direct/Git/local sources reject structurally. This is
conservative rejection, not silently relaxed resolution. It does not implement
mixed public/private solving, general index ordering, arbitrary wheel platforms,
PEP 691/658, source builds or upstream-proxy content guarantees.

Original declarations/extras/specifiers/markers remain unchanged. Ordinary exact
core version AND artifact digest checks still apply; the proof uses a non-empty
private core baseline. Generated snapshot index pages, wheels, original solve
project/lock, clean HTTPS index provenance, apply project/lock and exact inventory
are retained under the existing restricted, fsynced content-addressed manifest.
Raw upstream HTML is read only in memory and is never retained; rejected pages
may contain credentials. Solve provenance
contains temporary local snapshot paths, not credentials, and is historical;
the apply project/lock use portable relative wheel paths. A relocated retained
bundle successfully frozen-syncs after the original build directory is gone.
Cold-cache/offline/frozen/no-build disposable sync remains mandatory. No live
runtime, revision, desired intent, installation or writer-composite mutation occurs.

Shared readiness validation requires an exact deterministic redacted source
warning for every captured binding. Newly selected private sources therefore
appear in the internal ready preview, without URLs or credential references.
Both PostgreSQL initialization paths accept the actual retained attestation and
reject omission of that preview. Existing public/container source gates still
reject private policies unless the internal builder is explicitly configured.

## Verification and evidence

Evidence: `05a2b4/evidence/accepted/`. Exact argv/exit codes are in
`*-command.json`; corresponding logs contain full output (trailing whitespace
normalized by capture.py). Retained manifests include all listed wheel/hidden
files, force-tracked for clean-checkout validation. No heredocs or shell
redirection were used. The local patch is generated by Python subprocess capture.

Source proof uses pinned image `da-uv-05c5-fix:local`,
`sha256:3ba622c2fbf1478eac35c35eabafb87f260473247812d41efa97640eed415e45`,
Python 3.12.14 and image-owned **uv 0.12.21**, non-root UID 1000, dropped
capabilities, no-new-privileges and read-only current repository source. This is
NOT a rebuilt/current image-installed implementation claim. Image/container
inspection, source hashes, version, redacted request observations, attestation
and all final retained bytes are included. Fixture TLS CA and operator references
are generated only inside the disposable container, not committed/exposed.

Exact final commands from repository root (all exit 0):

```sh
python3 uv-experiment/05a2b4/evidence/accepted/capture.py private-index python3 uv-experiment/run-private-index-proof.py
python3 uv-experiment/05a2b4/evidence/accepted/capture.py cleanup env PROOF_IMAGE=da-uv-05c5-fix:local PROOF_ROOT=uv-experiment/05a2b4/evidence/accepted/cleanup PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen python uv-experiment/preparation/container_cleanup_proof.py
python3 uv-experiment/05a2b4/evidence/accepted/capture.py postgres env PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
python3 uv-experiment/05a2b4/evidence/accepted/capture.py act act workflow_dispatch -W .act/checks.yml
python3 uv-experiment/05a2b4/evidence/accepted/capture.py native uv run --project uv-experiment/executor --frozen pytest -q -p no:cacheprovider uv-experiment/executor/tests/test_private_index.py uv-experiment/executor/tests/test_registry_builder.py
python3 uv-experiment/05a2b4/evidence/accepted/capture.py lint uvx ruff check --select E9,F docassemble_base/docassemble/base/managed_preparation.py uv-experiment/executor/src/docassemble_executor/private_index.py uv-experiment/executor/src/docassemble_executor/registry_builder.py uv-experiment/executor/tests/test_private_index.py uv-experiment/preparation/private_index_proof.py uv-experiment/preparation/container_cleanup_proof.py uv-experiment/preparation/tests/test_private_index_postgres.py uv-experiment/run-private-index-proof.py
python3 uv-experiment/05a2b4/evidence/accepted/capture.py source env PYTHONPATH=docassemble_base uv run --project uv-experiment/executor --frozen python uv-experiment/05a2b4/evidence/accepted/verify-evidence.py
git diff --check
```

Results:

- Private source: **nine PASS groups**: real Basic-auth HTTPS private wheel install,
  direct/transitive explicit binding, non-empty exact core and declared identity;
  unattended operator reference reuse, changed-source denial before requests and
  relocated frozen sync; same-host malicious link/redirect denial; TWO same-host
  private indexes with distinct credentials and no cross-scope exposure; missing
  binding/public namesake rejection with no fallback; unsupported transitive
  SSH/local/credential-bearing direct references; TLS default verification;
  raw/base64 secret absence from retained material and evidence; proof-only
  reference deletion after synchronous workers exit.
- Existing real Docker/PostgreSQL candidate-secret cleanup: **four PASS groups**.
  A live older orphan with a build descendant blocks deletion after lease reclaim,
  cancellation and newer worker exit; only trusted namespace destruction allows
  physical deletion. Userinfo/token/query fixtures and redacted status requalified.
  The proof now accepts `PROOF_IMAGE`, preserving its historical default; no
  historical evidence was overwritten. This is unchanged candidate cleanup
  authority, NOT integration of candidate secrets with the new snapshot consumer.
- PostgreSQL/preparation: **509 passed in 79.42s**. Intentional 507 -> 509:
  one real retained private readiness/preview test on upgrade and fresh schemas.
- Act: **372 passed, 1 skipped in 31.63s**; mypy **57 files, no issues**.
  Intentional 333 -> 372: 39 new source-named private-policy/reference/uv-env/
  redaction/transitive tests; +1 typed module. Existing bare-act Supervisor skip
  unchanged. No expected-count assertions/exclusions were changed.
- Focused native: **44 passed in 0.28s**. Ruff E9/F, retained-byte verification,
  15 source hashes and disposable raw/base64 evidence scan pass.

Development logs/material remain untracked in `05a2b4/evidence/development/`.
Initial proof failures exposed the actual pinned-uv transitive binding,
`--no-config` index handling and file-registry lock representation; these were
fixed before final qualification, without relaxing requirements or fallback.
The first PostgreSQL run was 507 passed/2 failed because the new test attempted
JSON serialization of a datetime; `default=str` fixed only the test scan. Final
full suite supersedes it. Task-created stopped development proof containers are
left intact; historical containers/images/volumes and evidence were not removed.

## Resolution and precise remaining fences

Implemented the authorized end-to-end **private wheel snapshot preparation**
fallback, not a plan-only handoff. `ticket-05a2b4/diff.patch` spans
`50e180461..HEAD`; it and `change-summary.md` are intentionally untracked.

**05a2b5 source remainder:** HTTPS Git refs/subdirectories/SHA identity and scoped
credential injection; private sdists/build-dependency authentication/source
builds; candidate-secret claim consumption by this builder and atomic
candidate-to-committed promotion/rotation/revocation; authorized updater reuse
without viewing via public adapters; production private policy configuration,
source authorization and dispatch; mixed public/private solving and broader
index ordering; direct wheel/archive URLs and immutable uploaded ZIPs; broader
source/target/build policy and aggregate source-parity qualification. Existing
candidate staging/cleanup remains usable only through its claim protocol; do not
hand its paths to this operator-reference consumer.

**Existing 05c5b operational remainder unchanged:** universal live observations/
all-role readiness/lost-success closure, cold uncertain/unreported recovery and
management-tables-unavailable boot restoration, restricted core-only resumption,
broader failed/recovered/crash rollover, explicit uncertain-work/schema/setup
clearance, config/dependency/policy refresh.

**Named remaining 05b2b6 writer composites/caller closure and races unchanged:**
users/views edit_user_profile_page, user_profile_page, user_add, custom_register,
invite, add_privilege, manage_account, deletion/MFA/email/multi-commit composites;
users/helpers login_or_register; users/signals on_user_login/on_register_hook/
on_password_change; MySignInForm.validate LDAP/default-role creation;
auth/views.oauth_callback; phonelogin/views.phone_login_verify; create_tables
get_role/get_user/populate_tables account/default-role/fix-role passes and
fix_secret/substitute_secret/cookie/session/auth/external-hook/plugin composites;
admin/views.config_page; startup.initialize/read_config; base/read_config and
Docker/initialize.sh dependency/index/PYTHONPACKAGES observation; executor
preparation/controller/CLI dependency/policy refresh; config.allowed,
ALLOW_UPDATES, DEVELOPER_CAN_INSTALL and name/source/dependency-policy writers;
utils/helpers install_zip/git/pip/uninstall; packages/views.update_package and
api.api_package; develop/views.playground_packages ZIP caller; update
clear_invalid_package/clear_invalid_package_auth/check_for_updates/
add_dependencies/fix_names/install_package/uninstall_package/update_versions;
tasks.update_packages/reset_server/direct update CLI; create_tables package
rewrites/add_dependencies and remaining PackageAuth/grant/source closure.

Production managed initialization/adoption, broader installed-material/store
qualification, update/remove/config import, coherent upgrade/rollback/adoption/
backup/image migration/general managed API breadth and **tickets 06+** stay fenced.
No new-system edits, branch switch, writer-composite closure or full
05a2b4/05a/05b/05c/parent-05 acceptance is claimed. Review gate remains required.

Next: 05a2b5 — remaining credential lifecycle/source support; existing 05c5b and named writer fences unchanged.
