# Ticket 12 change summary

Baseline `b229d9862`; accepted tickets 07–11 changes already present.
Working-tree changes only; nothing committed.

- `docassemble_base/docassemble/base/managed_direct.py` (new): direct URL
  declarations/identity/conflict/redaction.
- `uv-experiment/executor/src/docassemble_executor/direct_sources.py` (new):
  fetch/hash/retain/reuse, mutation/expiry.
- `managed_preparation.py`, `managed_publication.py`, `registry_builder.py`:
  direct prepare/approve/publication/builder integration.
- `preparation_poller.py`: resolved-path registration + existence verification.
- Tests: `test_direct_sources.py`, `test_direct_url_postgres.py`,
  `direct_url_proof.py`; `test_managed_api_postgres.py`/`test_image_bridge_postgres.py`
  obsolete rejection cases removed.
- Evidence: `12/evidence/accepted/`. Handoff: `uv-experiment/TICKET-12.md`.

Independent review (alternate `opencode-go/deepseek-v4.1-flash`): OK with notes,
no P0/P1; two P2 retention fixes applied; two P2s recorded.
