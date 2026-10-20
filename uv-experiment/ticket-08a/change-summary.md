# Ticket 08a change summary

Baseline `b229d9862`; ticket-07 changes already present in the working tree.
Working-tree changes only; nothing committed.

- `docassemble_base/docassemble/base/managed_config.py` (new):
  `dependency_snapshot` canonical dependency-input observation with
  `config.load` overlay + underscore normalization, whole-string-then-segments
  `PYTHONPACKAGES`, validated pip policy, and fail-closed secret references.
- `docassemble_base/docassemble/base/managed_publication.py`:
  `Publication.observe_config` writes monotonic `observed_config` generations
  under the environment-first lock.
- `docassemble_base/docassemble/base/managed_preparation.py`:
  `Preparation._config_drift` read-only pending-drift projection in
  `status`/`publication_status`.
- `docassemble_webapp/docassemble/webapp/admin/views.py`: managed
  save→observe→report with fail-closed 503.
- `docassemble_webapp/docassemble/webapp/packages/managed.py`:
  `observe_saved_configuration` internal factory entry point.
- `uv-experiment/executor/pyproject.toml`, `uv-experiment/fixture/Dockerfile`,
  `uv-experiment/executor/tests/test_executor_packaging.py`: ship and verify the
  new shared module.
- `uv-experiment/preparation/tests/test_config_observation_postgres.py` (new):
  114-case observation suite.
- Evidence: `08a/evidence/accepted/`. Handoff: `uv-experiment/TICKET-08a.md`.

Independent review `openai-codex/gpt-6-astra`: P1 underscore-alias normalization
found and fixed; re-review No issues found / OK.
