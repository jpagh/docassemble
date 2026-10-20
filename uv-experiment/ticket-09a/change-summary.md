# Ticket 09a change summary

Baseline `b229d9862`; accepted tickets 07 and 08 changes already present.
Working-tree changes only; nothing committed.

- `docassemble_base/docassemble/base/managed_artifacts.py` (new): restricted
  content-addressed durable store, authoritative metadata identity, symlink/`..`
  fence, configured-owner root reads, atomic retention.
- `managed_preparation.py`: `prepare_zip`, artifact binding, material
  registration/reconciliation/cleanup.
- `managed_publication.py`: `artifact_ref` and committed-material recording.
- `managed_qualification.py`, `packages/managed.py`: authorization/identity
  integration.
- `registry_builder.py`: local-artifact path binding plus pre/post retained
  constraint validation.
- `preparation_poller.py`, `preparation_containers.py`, `durable_work.py`:
  artifact-root plumbing and cleanup.
- `pyproject.toml`, `fixture/Dockerfile`, `.act/checks.yml`: packaging, image
  inclusion, root-read UID for the native suite.
- `uv-experiment/preparation/tests/test_zip_install_postgres.py`,
  `uv-experiment/executor/tests/test_registry_builder.py`,
  `test_preparation_containers.py`, `zip_fixture.py`, `zip_install_proof.py`.
- Evidence: `09a/evidence/accepted/`. Handoff: `uv-experiment/TICKET-09a.md`.

Independent review `openai-codex-work/gpt-6-astra`: two P1s found and fixed;
re-review No issues found / OK.
