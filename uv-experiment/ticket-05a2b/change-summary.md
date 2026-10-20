# 05a2b implementation handoff

Commit: `de474ccd3f2de0c0bf351df11a1327ad3229b261` on `uv-implementation`.
Base: `c0013fd8cdb80d070be71aa260c18ceefd39d9e6`.

Bounded partial fallback, not 05a/parent acceptance. Added preparation-only
independent polling, atomic queued/expired-claim discovery, renewal during build,
reclaim and late-result fencing. Fixed P2 racing identical-bundle retention with
verified reuse/staging discard; unrelated filesystem errors remain failures.

Sources: preparation.py, new preparation_poller.py, registry_builder.py;
tests: test_registry_builder.py, test_preparation_postgres.py;
proofs: new preparation/poller_proof.py and run-preparation-poller-proof.sh;
docs: preparation/README.md, TICKET-05-SPLIT.md, new TICKET-05a2b.md;
tracker: appended dated comment, parent Status unchanged;
evidence: 05a2b/evidence/accepted/ logs, provenance and retained bytes.

Exact final commands/outcomes (all exit 0):

- `bash uv-experiment/run-preparation-builder-proof.sh`: all four PASS lines,
  including pinned uv 0.12.21, source build, exact core, offline cold-cache sync,
  redacted conflict/unsupported-source failure and retained corruption rejection.
- `bash uv-experiment/run-preparation-poller-proof.sh`: two PASS lines; independent
  PostgreSQL -> actual pinned builder -> retained ready, then idle. No install or
  intent/runtime mutation; worker exit NOT inferred.
- `uv run --project uv-experiment/executor --frozen pytest -q -p no:cacheprovider uv-experiment/executor/tests/test_registry_builder.py`: 5 passed in 0.08s.
- `PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py`: 58 passed in 7.73s.
- `act workflow_dispatch -W .act/checks.yml`: 277 passed, 1 skipped in 29.84s;
  no issues found in 50 source files; Job succeeded.
- `git diff --check` and `git diff HEAD^ HEAD --check`: no output.
- Post-commit `verify_retained` on both final qualified bundles: PASS.

Intentionally +2 native tests, +10 PostgreSQL cases, +1 mypy module. No exclusion
or expected-count assertions changed. Sole skip remains bare-act Supervisor.

Fenced as 05a2b2: scoped credential injection, delayed physical deletion after
ALL workers exit, trusted OS orphan reconciliation. Conservative unexited worker
rows block cleanup; this is not production cleanup-ready. Only public credential-
free PyPI is supported. Broader target/private-index/binding/build-policy work,
05a3/05b/05c and parent acceptance remain fenced. Independent review required.

This file and diff.patch are intentionally UNTRACKED; pre-existing ticket-05a/
and ticket-05a2/ handoffs remain untouched. No staged files remain.

Next: independent bounded-slice review, then 05a2b2.

## 2026-10-14 P1 fix update

Commit `de474ccd3f2de0c0bf351df11a1327ad3229b261` drains renewal before
ready or failed writes, preventing uncertain renewal from racing completion.
Event-controlled PostgreSQL regression adds 4 cases (success/failure x upgrade/
fresh), intentionally raising 58 to 62; registry tests remain 5, act remains
277 passed/1 documented skip, mypy remains 50 source files. Both container proofs
pass. Exact rerun commands/output: `fix-summary.md` and `fix-evidence/`.
`diff.patch` is regenerated as the combined base-to-HEAD diff, not fix-only.
All handoff files remain untracked; no staged files remain. Re-review required.
