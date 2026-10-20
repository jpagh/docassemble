# Ticket 09b change summary

Baseline `b229d9862`; accepted tickets 07/08/09a changes already present.
Working-tree changes only; nothing committed.

- `docassemble_webapp/docassemble/webapp/develop/views.py`: managed-only
  Playground generator and `create_playground_package` branch.
- `packages/views.py`, `packages/api.py`, `packages/managed.py`: browser/API ZIP
  upload transports into `prepare_zip`.
- `files/savedfile.py`: upload handling adjustment.
- `docassemble_base/docassemble/base/managed_qualification.py`: ingress allowlist.
- `Docker/managed-startup.sh`, `uv-experiment/fixture/Dockerfile`: explicit
  artifact root/UID provisioning.
- `uv-experiment/preparation/playground_fixture.py`,
  `playground_install_proof.py`, `artifact_wiring_proof.py`,
  `tests/test_playground_upload_postgres.py`, `executor/tests/test_startup_routing.py`.
- Evidence: `09b/evidence/accepted/`. Handoff: `uv-experiment/TICKET-09b.md`.

Independent review `openai-codex-work/gpt-6-astra`: No issues found.
