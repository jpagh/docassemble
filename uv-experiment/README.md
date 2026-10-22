# uv environment experiment

## Current target (ticket 25 — not yet container-qualified)

The fixture now provisions conventional uv-managed CPython **3.14.4** with uv
**0.12.21** into protected `UV_PYTHON_INSTALL_DIR`. The independent executor and
runtime are created from that same executable. The managed runtime defaults to
`/usr/share/docassemble/managed-runtime3.14`, derived by the executor from its
observed major/minor; `DA_RUNTIME_VENV` remains an explicit override. Stock
`/usr/share/docassemble/local3.14` (docassemble 1.10.7) is never reused or mutated
by managed sync/reconstruction. Cron and Exim paths are rendered at image build.

Every baseline names one exact observed interpreter target; there is no version
allow-list. Both project and lock must declare the matching exact minor
constraint. Old 3.12 locks refuse without repair. Image construction and isolated
candidate preparation may solve/build sdists (including pinned numpy 2.2.6 and
scipy 1.15.3); retained apply/rollback/restore use `--frozen --no-build`. The narrow
interrupted runtime-fault proof remains the existing exception.

Serving remains Gunicorn, not the system-built `mod_wsgi.so-3.14`: this retarget
is **not Apache/mod_wsgi certification**. Host checks observed 3.14.4, but no
3.14 image build or qualification matrix is claimed yet. Ticket 24 remains
historical **3.12.14** evidence only. See [`TICKET-25.md`](TICKET-25.md) for the
implementation-only outcome and [`24/OPERATOR.md`](24/OPERATOR.md) for fresh gates,
three image builds and the unchanged full 30-phase acceptance command.

## Complete-experiment qualification (ticket 24)

The sections below preserve the original ticket-01 through 04 proof history;
statements that later tickets are unimplemented are historical, not current
capability claims. Tickets 05–23's accepted work remains uncommitted on
`uv-implementation`. Final qualification is **not accepted** merely by running a
harness or reading a handoff.

See [`24/OPERATOR.md`](24/OPERATOR.md) for the repeatable current-image build,
full PostgreSQL/act/lint gates, container matrix, evidence interpretation,
recovery/cache/cold-restore/full local-and-cloud restore walkthrough and explicit
security/fixture limitations. Run `python3 uv-experiment/run-qualification.py`
only against disposable infrastructure. Its summary requires every container
phase and is separate from reviewer acceptance. Failed/subset evidence is never
complete qualification. Ticket-24 results are recorded in
[`TICKET-24.md`](TICKET-24.md).

## Original independent browser recovery proof

This directory is the ticket 01 deliverable for the uv-environment
implementation series: a repeatable disposable-container demonstration that

1. pins and records the experimental image, uv and target identity,
2. retains a core-only uv bundle outside the disposable cache,
3. restores that core into the single runtime venv with independent tooling
   after the runtime is broken or a frozen installation is interrupted,
4. serves authenticated, restricted administration (normal admin login,
   configuration editing, logs, recovery/failure status, fixture-scoped repair)
   while user imports/hooks, preloaded interviews, Playground, package
   management, API, interviews and workers stay disabled,
5. exercises compatible advanced and incompatible databases without running
   older setup, stamping, or rolling back, and
6. fails explicitly, with workloads stopped and no automatic retry loop, when
   recovery material or network artifacts are unavailable.

It is a feasibility proof, not the integrated production lifecycle. Full-consumer
Supervisor integration, publication/candidate records, managed package APIs,
full drain handling, rollback and adoption remain with later tickets.

Ticket 03 adds frozen managed startup, exact installed inventories, protected
tooling, startup-flag semantics and explicit adoption/migration gates. See
[`TICKET-03.md`](TICKET-03.md) and run `./uv-experiment/run-boot-proof.sh`.
Ordinary bundles retain image-built wheels (including prepared registry-sdist
wheels) and sync with `--frozen --no-build`, because frozen sync alone can still
resolve source-build dependencies. `runtime-fault` is a separate
operator-only source bundle for the interruption proof.

The user-approved ticket 04 split adds the running web slice's safe maintenance
foundation in [`TICKET-04a.md`](TICKET-04a.md). Run
`./uv-experiment/run-maintenance-proof.sh` for admission/drain, controller crash
and surviving-child proofs. The remaining 04b integration and full original
acceptance are now complete; see the 04b3b handoff below. Ticket 05 can start.

The user further split 04b into 04b1/04b2/04b3.
[`TICKET-04b1.md`](TICKET-04b1.md) adds independent experimental Supervisor,
watchdog admission and child-safe finite-helper leases. Run
`./uv-experiment/run-consumer-proof.sh`.

The user further split 04b2: [`TICKET-04b2a.md`](TICKET-04b2a.md) qualifies both
real Celery prefork workers with Redis broker proofs. Run
`./uv-experiment/run-worker-proof.sh`. Message admission is atomic with closure;
consumption pauses reversibly while accepted active/reserved/scheduled work
finishes. Only idle workers warm-exit; guardians retain surviving children.
[`TICKET-04b2b.md`](TICKET-04b2b.md) adds the real Socket.IO, Pattern msgpack and
local UNO services, with live accepted-caller scopes for nested requests after
closure. Run `./uv-experiment/run-service-proof.sh`.
[`TICKET-04b2c.md`](TICKET-04b2c.md) adds scheduled/manual helpers, separate
trusted backup phases and actual Exim delivery/queue proofs. Run
`./uv-experiment/run-helper-mail-proof.sh`.

The user further split 04b3. [`TICKET-04b3a.md`](TICKET-04b3a.md) adds explicit
legacy CLI/shell, low-level installer, intent-writer and stale-task fences. Run
`./uv-experiment/run-bypass-proof.sh`. [`TICKET-04b3b.md`](TICKET-04b3b.md) completes
supervised non-root development override, safe ordinary reloads, independent
infrastructure shutdown and all-role readiness behind admission. Run
`./uv-experiment/run-full-maintenance-proof.sh`. Final acceptance passes 96 real
container scenarios and 286 native tests, no skips; provenance and independent
review are in [`full-maintenance/evidence/accepted/`](full-maintenance/evidence/accepted/VERIFICATION.md).
04b3/04b/04 are complete and ticket 05 is unblocked, not implemented.

Ticket 02 also extracts reusable existing backup execution. See
[`backup/README.md`](backup/README.md) for the callable operation, limitations
and its separate container proof.

## Layout

| Path | Purpose |
| --- | --- |
| `executor/` | Image-built, uv-locked independent executor (`da-executor`). Owns journal, probes, frozen core restore, schema compatibility, restricted web/maintenance serving. |
| `fixture/` | Disposable image Dockerfile, build script, fixture configuration with observable sentinels, retained core/runtime bundles, slow-build fault package. |
| `fixture/Dockerfile` | Pinned base image digest + pinned uv release; builds bundles and the executor venv at image build. |
| `scenarios/proof.py` | The scenario driver and assertions; writes captured evidence. |
| `run-proof.sh` | The repeatable recovery test command. |
| `run-boot-proof.sh` | Ticket 03 frozen boot, flag/ownership/inventory and offline-restart verification. |
| `run-backup-proof.sh` | Ticket 02 standalone/scheduled backup and disposable restore verification. |
| `run-maintenance-proof.sh` | Ticket 04a web admission/drain, interruption and child-authority verification. |
| `run-consumer-proof.sh` | Ticket 04b1 supervised launch, finite-helper drain and remaining launch fences. |
| `run-worker-proof.sh` | Ticket 04b2a real workers, Redis queue/acknowledgement state, timeout, interruptions and recovery gates. |
| `run-service-proof.sh` | Ticket 04b2b real websocket/Pattern/local UNO protocols, nested callers, disconnects, timeout and child-lifetime authority. |
| `run-helper-mail-proof.sh` | Ticket 04b2c actual cron/manual phases, backups, SMTP body/event persistence and Exim retention/redelivery. |
| `run-bypass-proof.sh` | Ticket 04b3a unsupported CLI/shell, installer/intent/API and stale-task fences through normal/drain/recovery. |
| `run-full-maintenance-proof.sh` | Ticket 04b3b development transports, graceful reload, all-role readiness and independent shutdown. |
| `backup/` | Backup extraction documentation and reviewed evidence; no new checkpoint policy. |
| `scenarios/browser-smoke.sh` | Optional real Chrome login, CodeMirror edit/save, log access and screenshot supplement. |
| `evidence/` | Captured raw evidence per run (status documents, journals, logs, summaries). |

## Running

```bash
# From the repository root: build the pinned image, then run every scenario.
./uv-experiment/run-proof.sh

# Re-run scenarios against an image that already exists.
SKIP_BUILD=1 ./uv-experiment/run-proof.sh

# Keep fixtures for inspection rather than removing them after evidence capture.
SKIP_BUILD=1 ./uv-experiment/run-proof.sh --keep-containers

# Override the base image digest or the local image tag.
DOCASSEMBLE_OS_IMAGE=jhpyle/docassemble-os@sha256:... PROOF_IMAGE=... ./uv-experiment/run-proof.sh
```

The command is intentionally Docker-only: the proof is about what actually
happens in a disposable container, not about static inspection. It uses
in-container PostgreSQL 18 and Redis from the pinned base image and publishes a
host port only for HTTP checks. The historical tested target was Linux aarch64,
CPython 3.12.14, with uv 0.12.21. The current fixture requests managed 3.14.4;
its container qualification is pending. Docker must support the pinned image's
architecture. Reserve
ports 18080/18081 and the `da-uv-proof*` names: the harness replaces its own
previous fixtures. Do not use the fixture credentials or configuration in
production. The CLI `init-db`, `apply-runtime`, and `recover` commands are
operator/fault-injection tools, not production package-management APIs.

For the optional macOS browser supplement, first retain the normal snapshot
with `--keep-containers`, then run:

```bash
./uv-experiment/scenarios/browser-smoke.sh
```

This additionally requires Chrome and `chrome-devtools`, uses port 18082 and
`da-uv-browser-proof`, and switches the CLI daemon to an isolated headless
profile. It never uses personal browser cookies and removes its profile and
container on exit.

## What the scenarios prove

| Scenario | Contract exercised |
| --- | --- |
| `normal-boot-sentinels` | Six fixture sentinels (user import, hook import and execution, configured module preload, interview resource preload, user-package favicon) all execute during normal service; a real core Celery worker is ready as a positive control; admin config/logs work. |
| `broken-runtime-recovery` | Removing the core webapp breaks the runtime probe; restart restores the retained core-only bundle with `uv sync --frozen`; restricted admin serves normal login, config, logs and status; sentinels/interviews/workers are absent; unauthenticated and non-admin access is rejected; normal readiness stays false. |
| `interrupted-install-recovery` | A frozen runtime apply is deliberately killed after the journal records mutation start; restart performs exactly one automatic core restore, preserves desired revision r2 and failure evidence, and never retries the failed upgrade. |
| `fixture-repair-action` | The administrator-only repair action restores a deleted core file even with distribution metadata intact (`--reinstall`); it preserves desired/applied intent. |
| `advanced-database-restart` | A compatible advanced schema is accepted and restarting restricted recovery performs no schema work or stamping. |
| `incompatible-database-stop` | An unknown schema revision stops recovery into static maintenance, leaves the schema untouched, and does not retry on restart. |
| `offline-cache-cleared` | With the runtime broken, cache cleared and network unavailable, the executor, PostgreSQL, Redis and the maintenance endpoint still start without runtime imports; restoration fails explicitly; workloads stay stopped and authenticated recovery is not advertised. |
| `missing-recovery-material` | Removing the retained bundle fails explicitly with workloads stopped and no retry loop. |

## Identity and evidence

`/usr/share/docassemble/managed/bundles/manifest.json` inside the image records
the base image digest, repository commit, uv version, CPython patch version,
installed core versions, architecture/OS/libc, and SHA-256 hashes of the retained
project definitions and local fixture sources. In the accepted pre-commit run,
the recorded repository commit identifies the parent of the unpublished
worktree, not the feature's source commit. Source hashes and the separately
captured immutable image ID identify the actual build. The scenario driver copies that manifest, the lifecycle journal, status documents,
database schema revision and executor logs into `evidence/run-<timestamp>/`,
with a human-readable `summary.md`. Raw runs are ignored; reviewed acceptance
artifacts are retained under `evidence/accepted/`.

Focused tests and type checks (host):

```bash
uv run --project uv-experiment/executor --frozen pytest uv-experiment/executor/tests/test_controller_decisions.py
uv run --project uv-experiment/executor --frozen pytest uv-experiment/executor/tests
uv tool run --with types-PyYAML --with types-psycopg2 mypy --ignore-missing-imports uv-experiment/executor/src uv-experiment/scenarios
```

The proof includes existing login/CSRF/session behavior and real configuration
save requests, not merely successful GETs. Configuration save in recovery does
not invoke legacy restart; request explicit repair to reload and validate it.

## Limitations and findings

- The executor implements only the proof-scoped lifecycle: it starts
  infrastructure directly, serves the recovered core through gunicorn, and
  exposes independent maintenance HTTP. Ticket 04a adds web request drain and
  child-held authority; 04b1 adds separate experimental Supervisor/watchdog and
  admitted finite jobs; 04b2a adds both Celery workers and reversible broker
  drain; 04b2b adds websocket/Pattern/local UNO lifetimes and accepted nested
  callers; 04b2c adds scheduled helpers and actual local Exim delivery/queues.
  04b3a adds the named legacy CLI/installer/intent/stale-task fences. 04b3b completes
  supervised development override, graceful ordinary reloads, independent
  infrastructure shutdown and full all-role acceptance. Durable package
  publication records and production migration remain later tickets.
- Core restoration re-downloads locked artifacts through the shared disposable
  uv cache. A lock is not an artifact archive: clearing the cache while offline
  prevents recovery, as the offline scenario demonstrates.
- The runtime fixture adds `fixture_advanced_revision`, an additive table
  migration absent from retained recovery core. Its exact revision is explicitly
  qualified in the image's fixture-only compatibility inventory; unknown
  revisions fail closed. Fresh setup stamps its head after ORM creation, so the
  scenario explicitly materializes the additive fixture table before snapshotting. Recovery/restart leaves both the newer revision and
  table intact, with authentication/configuration/log queries exercised and the
  setup log unchanged. This synthetic test does not certify arbitrary production
  schema upgrades or prove that every application query works.
- The scenario driver is a host-side harness, not part of the image. It uses
  CSRF-aware form posts and real cookie sessions for the authentication checks.
- Local source copies stand in for this unpublished feature build; they do not
  introduce a production policy to archive unpublished core wheels.
- The fixture's `slowfixture` package exists only to make a frozen sync
  interruptible. Production code never depends on it.
