# Ticket 07 change summary

Baseline `b229d9862` (ticket 06 accepted). Working-tree changes only; nothing
committed.

- `docassemble_webapp/docassemble/webapp/packages/managed.py`: `api_request`
  (API-key auth fail-closed), `package_change` prepare-only legacy shim,
  restart-required guard, distinguished/redacted error codes, `candidate_id`
  in status, nonempty-scope enforcement for every actor.
- `docassemble_webapp/docassemble/webapp/packages/api.py`: managed-mode routes
  `/api/package`, `/api/package_update_status`, `/api/package_preview` delegate
  to `api_request`.
- `docassemble_base/docassemble/base/managed_publication.py`: distinguish
  `candidate expired`/`candidate not ready`; re-authorize recorded idempotency
  results before body-mismatch handling in `approve`/`retry`.
- `docassemble_base/docassemble/base/managed_preparation.py`: `status` returns
  `candidate_id`.
- `docassemble_base/docassemble/base/managed_qualification.py`:
  `bridge_http_path` admits the two managed API routes.
- `uv-experiment/preparation/tests/test_managed_api_postgres.py` (new):
  PostgreSQL/Redis API integration suite.
- `uv-experiment/preparation/image_bridge_proof.py`: real-container API
  transport assertions before the existing browser→API publication flow.
- `uv-experiment/executor/tests/test_image_bridge.py`: ingress allowlist
  expectations updated for the two managed API routes.
- Evidence: `07/evidence/accepted/` (build, pending proof, focused, postgres,
  act, lint, source hashes). Handoff: `uv-experiment/TICKET-07.md`.

Independent review by `openai-codex/gpt-6-astra`: No issues found / OK.
