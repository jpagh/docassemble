# Bounded 05c1 review fixes — 2026-10-15 UTC

Branch: `uv-implementation` (unchanged).
Fix commit: `ed1fa4cedd469c5450b3aaf41239a1001a68584b`.
Parent: `d88249cb89ae4fae24f9bd1dc06a5be0fb279079`.
Combined diff baseline: `4d54c9a1afa15eabe701c19ad090c90af598a073`.

## Changes

- P1: journal `installation_preparing` precedes drain; `mutation_started` precedes actual sync only after successful drain. Confirmed drain abort reports a durable, identity/fence-bound `aborted-before-sync` result, explicitly `mutation_started=false`, `child_safe=false`, `normal_verified=false`. Initial report and replay hold MutationLock but do not acquire exclusive RuntimeLock or alter controller-restored admission. PostgreSQL records `aborted` and clears the active attempt. DB runtime remains unknown; no normal verification claimed.
- P2: durable allowlisted failure stage/reason pairs distinguish disabled updates, absent/invalid baseline, target mismatch, bundle corruption, context, drain, sync and inventory failures. Raw exception text is not persisted. Unknown diagnostic codes reject; old receipts lacking the optional field remain replayable.
- Tests: normally serving HTTP backend/ingress with active request and surviving shared runtime consumer exercises actual poller/controller timeout and lost-report replay. Fixture role probes and DB installation service are stubbed in this native test; separate real PostgreSQL tests verify abort slot release with shared consumers and diagnostic journal/DB replay. Four actual gate failures assert receipt codes; eight journal round trips assert retention.
- Updated the dated ticket note and appended tracker Comments; parent Status/checklist unchanged. No branch switch or new-system edits.

Committed files:

1. `.scratch/uv-environment-implementation/issues/05-registry-install.md`
2. `uv-experiment/TICKET-05c.md`
3. `uv-experiment/executor/src/docassemble_executor/installation.py`
4. `uv-experiment/executor/src/docassemble_executor/journal.py`
5. `uv-experiment/executor/tests/test_installation.py`
6. `uv-experiment/preparation/tests/test_installation_postgres.py`

## Exact final verification commands and output

Full outputs are retained under `fix-evidence/` (untracked). Commands were run via Python subprocess capture, without shell redirection/heredocs.

```sh
uv run --project uv-experiment/executor --frozen pytest -q -p no:cacheprovider uv-experiment/executor/tests/test_installation.py
# 16 passed in 2.20s; exit 0 (installation-tests.log)

PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
# 413 passed in 68.22s (0:01:08); exit 0 (postgres.log)

PROOF_IMAGE=da-uv-05c:local python3 uv-experiment/run-installation-proof.py
# PASS independent DB preparation -> internal atomic approval with exact retained digest 0fab9503bfda1aa3c9e4e0bf1e54476917d5b7167787aa1848cb43ad31a99f67
# PASS non-root offline frozen --no-build materialization; terminal durable before DB reporting; normal NOT verified
# PASS live child read authority forbids terminal replay/slot release
# PASS lost DB reporting replay in independent CLI WITHOUT retained store/reinstallation/recovery; desired unchanged
# PASS config/env update-disable, target mismatch and retained corruption gates before mutation
# PASS crash before local journal and after mutation-start remain fenced; no installation/recovery loop
# PASS no Flask/docassemble runtime imports; Redis/Celery absent from execution container
# exit 0 (installation-proof.log)

act workflow_dispatch -W .act/checks.yml
# 308 passed, 1 skipped in 31.67s
# Success: no issues found in 52 source files
# Job succeeded; exit 0 (act.log)

uv tool run ruff check --select E9,F uv-experiment/executor/src/docassemble_executor/installation.py uv-experiment/executor/src/docassemble_executor/journal.py uv-experiment/executor/tests/test_installation.py uv-experiment/preparation/tests/test_installation_postgres.py
# All checks passed!; exit 0 (lint.log)

git diff --check
# no output; exit 0
git diff --cached --check
# no output; exit 0
```

Intentional count increases: PostgreSQL/preparation 393 -> 413 (+18 reason/abort cases and +2 invalid-code cases, both schema initialization paths); act 294 -> 308 (+8 journal, +4 gate, +2 serving timeout/lost-report cases). The existing Supervisor skip and 52 mypy source files are unchanged; no count assertions/exclusions changed.

Installation proof uses retained image `sha256:b4f13c2f36c9c64009512d537e9a79f535ec987fe0347dc316453f9aa83e0e83` for pinned tooling with current executor source mounted read-only through PYTHONPATH. This is not a rebuilt-image provenance claim. New proof bytes are in `fix-evidence/installation/`; prior tracked evidence was preserved. The original 96 lifecycle and full-native-container runs were not rerun for these narrow fixes.

## Artifacts and fences

`diff.patch` was regenerated using `subprocess.check_output(['git', 'diff', '4d54c9a1afa15eabe701c19ad090c90af598a073..HEAD'])`, written with Python (equivalent to the requested combined diff without prohibited shell redirection). Combined patch size: 38,989,043 bytes. `diff.patch`, `change-summary.md`, this file and fix evidence remain UNTRACKED.

Remaining fences unchanged: full published normal/recovery lifecycle-to-DB integration, complete runtime observations, boot/service polling and subsequent-attempt rollover/uncertain retry clearance, production adoption, remaining named writer composites/policy races, public approve/retry, credential-source/private-source policy and parent-05/06 acceptance. An abort receipt intentionally remains the local replay fence; its DB attempt is terminal, not uncertain. Independent re-review required.

An initial `git add` returned an ignored-parent-directory warning for the already-tracked `.scratch` path, while staging the six intended files; the subsequent checked commit succeeded. No files remain staged.
