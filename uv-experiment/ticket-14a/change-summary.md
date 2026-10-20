# Ticket 14a change summary

Baseline `b229d9862`; accepted tickets 07–13 changes already present.
Working-tree changes only; nothing committed.

- `docassemble_base/docassemble/base/managed_release.py` (new): selection/coherent
  metadata/risk disclosure.
- `uv-experiment/executor/src/docassemble_executor/release_catalog.py` (new):
  public-index release catalog.
- `registry_builder.py`: `_build_upgrade` core-solve/freeze/full-solve.
- `managed_preparation.py`: `_prepare_upgrade`/validation.
- `packages/managed.py`: qualified `upgrade` guards.
- Tests: `test_release_selection.py`, `test_core_upgrade_postgres.py`,
  `test_image_bridge_postgres.py`.
- Evidence: `14/evidence/accepted/`. Handoff: `uv-experiment/TICKET-14a.md`.

Independent review (alternate `opencode-go/deepseek-v4.1-flash`): one P1 found and
fixed; final re-review No issues found / OK.
