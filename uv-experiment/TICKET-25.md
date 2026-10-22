# 25: Managed CPython 3.14 retarget

Date: 2026-10-22 UTC. Branch: `uv-implementation`. Ticket 24 accepted with the
3.12-versus-3.14 gap disclosed. **Accepted 2026-10-22 (bounded experimental
scope; independent `gpt-6-astra` reviews, six findings closed before
qualification).**

## Implementation

- **Target**: the managed runtime is a uv-managed CPython **3.14.4** (pinned uv
  0.12.21) used for both the executor and the runtime venv, matching the
  repository image, which moved to 3.14 in `b6533949f`. The runtime lives at a
  distinct derived `/usr/share/docassemble/managed-runtime3.14`; the stock
  `/usr/share/docassemble/local3.14` is never written, replaced or synced into.
- **One source of truth**: `docassemble_base/docassemble/base/managed_python.py`
  derives the target from the observing interpreter. The four exact-match
  refusals (`image_baseline`, `installation`, `private_index`,
  `registry_builder`, `managed_qualification`) stay fail-closed; no allow-list
  and no major.minor prefix comparison exists.
- **Binding before consumption**: `bundles.verify_python_target` requires both
  the project and the lock to match the target; `sync_frozen` returns the
  existing failed `SyncResult` with the constant diagnostic
  `retained project and lock Python target mismatch` before staging, locks,
  subprocesses or runtime mutation — no new exception category — and the
  selected-baseline has its own independent check after digest verification.
- **Artifact policy**: image construction performs 14 attested builds from
  hash-verified original sdists (source row recorded in `source-artifacts.json`
  inside the bundle hash); variants consume them only on exact source-row match.
  Candidate builds fetch the **published wheel** whenever the lock offers one
  compatible with the target — real tag matching including abi3 — and build only
  genuinely sdist-only packages. numpy/scipy have no cp314 wheel and are reused
  from the verified image-built wheels after the registry lock has selected them.
- **Network boundary**: registry artifact fetches (sdist and wheel) refuse
  redirects before any off-lock request is issued.
- **Fixture coherence**: cron/Exim runtime selection is rendered from the managed
  path with fail-closed substitution in both image builds; `.act/checks.yml`
  pins uv 0.12.21, provisions 3.14.4, asserts the exact patch and cannot skip.

## Qualification

Acceptance run `full4-20261022T005356Z`:

- **30/30 phase exits 0**, 291 min, `missing_phases: []`,
  `container_gates_passed: true`,
  `sources_and_protected_services_unchanged: true`, zero leftover containers and
  zero disposable volumes.
- Images: `da-uv-24-qual:local` `bf1d83397c46`, `da-uv-24-old:local`
  `cf5b109050ba` (RELEASE 1.10.12), `da-uv-24-new:local` `f5cd374b9079`
  (1.10.16); linux/arm64, CPython 3.14.4, uv 0.12.21.
- Gates on the same tree: `act` **1996 passed, 2 skipped** with mypy clean over
  76 source files; PostgreSQL/preparation **2475 passed, 24 skipped**.
- Measured in the real image: 28 construction builds — 14 core/variant plus 14
  attested producer builds — with the boot sync count unchanged at 6; the whole
  `core-upgrade` phase performs 52 uv build invocations, and a core solve
  resolves 238 wheel downloads against 12 genuine source builds.

## Boundaries / disclosure

- Retained from ticket 24: external S3/Azure CLIs are filesystem fakes, core
  releases 1.10.14–1.10.16 are synthetic repacks of current image code, and
  package locks certify neither arbitrary data compatibility nor upstream
  availability. Artifact checkpoints are not full-deployment backups.
- 12 sdist-only packages are still compiled per candidate solve: `docopt`,
  `pylatex`, `tailer`, `titlecase`, `rauth`, `pyu2f`, `links-from-link-header`,
  `docassemblekvsession`, `docassemble-pattern`, `s4cmd`, `netifaces`,
  `python-ldap`. numpy and scipy are not among them; they are reused.
- This tooling now targets **only the image's 3.14 interpreter**. The 3.12 target
  is no longer qualified by this harness — a deliberate plan decision, not an
  omission. Re-qualification is required before any 3.12 claim.
- **Gate limitation**: `sources_and_protected_services_unchanged` compares
  protected-service *identity* (`Id`/`StartedAt`), not availability.
  `da-postgres` and `da-redis` sat stopped for 14 hours after an operator
  software update and the gate did not flag it; the host PostgreSQL gate could
  only run after they were restarted.
- Environmental interruptions, recorded rather than excused: run `full3`
  (`00:54Z` predecessor) lost `restore-local` when the operator's software update
  restarted all five protected services at 21:41:40Z mid-phase; the phase passed
  standalone 40 minutes later (15m26s). The earlier `full2` run recorded
  `container_gates_passed: false` for the same class of interference.
- All 30-phase evidence (~869 MB) remains **local only** under
  `uv-experiment/25/evidence/`; the pushed branch is code-only.

## Resolution

Accepted on the bounded experimental scope. Six independent review findings were
closed before qualification: a recursive-TOML parse escape from the refusal
contract; build-time pair checks ordered after consumption; a checkpoint test
that passed with its guard bypassed; permissive METADATA admission; a `fixed`
intent predicate that excluded the very packages reuse targeted; and redirects
followed before validation. The detailed implementation log is retained locally
at `.scratch/uv-environment-implementation/reviews/IMPL-314-ticket25-detail.md`.
Next: none.
