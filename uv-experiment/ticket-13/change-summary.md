# Ticket 13 change summary

Baseline `b229d9862`; accepted tickets 07–12 changes already present.
Working-tree changes only; nothing committed.

- `uv-experiment/executor/src/docassemble_executor/installation.py`,
  `controller.py`, `journal.py`, `status.py`, `durable_work.py`: served
  restricted recovery, repair/retry safety, error-notification durability.
- `docassemble_webapp/docassemble/webapp/recovery.py`, `recovery_status.py`:
  truthful states, flashes, repair/retry/approve controls with server-side digest.
- `managed_preparation.py`, `managed_publication.py`, `packages/managed.py`:
  `operation='repair'` re-prepare/projection.
- Tests: `test_core_recovery.py`, `test_restricted_recovery.py`,
  `test_recovery_status.py`, `test_repair_postgres.py`, `test_error_notification.py`.
- Evidence: `13/evidence/accepted/`. Handoff: `uv-experiment/TICKET-13.md`.

Independent review (alternate `opencode-go/deepseek-v4.1-flash`): two P0s and one
P1 found and fixed; final re-review No issues found / OK.
