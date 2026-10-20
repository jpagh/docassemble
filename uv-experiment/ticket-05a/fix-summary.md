# 2026-10-14: 05a1 P1 fixes

Fix commit: `086abb210c51b76edaedfa894c7c4971e96e442b`, on `uv-implementation`, directly on `153f2b52a911a32b2c7f007cf304cf9cb086fe56`.

1. Preserve the wheel force-include's relative source layout in the fixture image with a COPY of the canonical schema module. No repository schema duplicate. `test_executor_packaging.py` replays Docker COPY layout, constructs the wheel and checks isolated wheel-only executor/schema imports.
2. Attach PostgreSQL-only table `after_create` DDL in shared schema definitions, installing exactly the same guards as the migration. Direct migration CreateTable execution does not fire those events. Real PostgreSQL tests now cover fresh ORM metadata creation and upgrade followed by create_all, including repeated creation, both trigger names, readiness mutation and revision update/delete rejection.

Exact commands/output are recorded in TICKET-05a1.md; raw output is in fix-evidence/packaging.log, postgres.log and act.log. Packaging: 1 passed in 1.96s. PostgreSQL/parser: 46 passed in 2.33s. Act: 272 passed, 1 skipped in 30.20s; mypy 48 files; Job succeeded. `git diff --check` passed without output. Counts increase intentionally for regressions; no expected-count assertion changed. Full fixture build not required: the lightweight layout regression directly covers the broken packaging seam and also passes inside act on Linux.

Tracker comment appended locally (tracker is gitignored); parent Status/checklist unchanged. No new-system or later-slice work. No known remaining P1/P2 in these fixes; independent reviewer gate remains required. No parent acceptance claimed. These summaries, combined diff.patch and logs remain untracked.
