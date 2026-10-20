# Ticket 11a change summary

Baseline `b229d9862`; accepted tickets 07–10 changes already present.
Working-tree changes only; nothing committed.

- `docassemble_base/docassemble/base/managed_config.py`: private index policy
  observation/import/commit, `config_import_policy` private branch, no public
  fallback, TLS exceptions.
- `uv-experiment/executor/src/docassemble_executor/private_index.py`:
  `effective_config`, `validate_private_policy` TLS exceptions/config guard,
  `_source_unavailable` categories.
- `uv-experiment/executor/tests/test_private_index.py`,
  `uv-experiment/preparation/tests/test_config_import_postgres.py`,
  `test_config_publication_postgres.py`: tests.
- Evidence: `11a/evidence/accepted/`. Handoff: `uv-experiment/TICKET-11a.md`.

Independent review (alternate `opencode-go/deepseek-v4.1-flash`): OK with notes,
no P0/P1; two P2s recorded for 11b.
