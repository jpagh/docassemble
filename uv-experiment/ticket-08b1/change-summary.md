# Ticket 08b1 change summary

Baseline `b229d9862`; accepted ticket-07 and 08a changes already present in the
working tree. Working-tree changes only; nothing committed.

- `docassemble_base/docassemble/base/managed_preparation.py`: `_prepare_config`
  (admin-only observed-snapshot binding, combined contributions, core/conflict
  rejection), `_fresh` observation staleness, `config_diff`, `_validate_result`
  config handling, config status projection.
- `docassemble_base/docassemble/base/managed_config.py`:
  `config_import_policy` (private-policy fail-closed, snapshot re-validation,
  public-only index).
- `docassemble_base/docassemble/base/managed_publication.py`: explicit
  config-approval fence.
- `docassemble_base/docassemble/base/managed_qualification.py`:
  `require_qualified_inputs` config handling.
- `docassemble_webapp/docassemble/webapp/packages/managed.py`:
  `PreviewAuthorizer` config prepare/approve branch and
  `_capture_approval_authority` config skip.
- `uv-experiment/executor/src/docassemble_executor/registry_builder.py`:
  `validate_public_policy`/`build` config operation with no single requirement.
- `uv-experiment/executor/tests/test_registry_builder.py`,
  `uv-experiment/preparation/tests/test_config_import_postgres.py` (new): tests.
- Evidence: `08b1/evidence/accepted/`. Handoff: `uv-experiment/TICKET-08b1.md`.

Independent review `openai-codex/gpt-6-astra`: No issues found / OK with notes.
