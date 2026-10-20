# Ticket 10b change summary

Baseline `b229d9862`; accepted tickets 07–10a changes already present.
Working-tree changes only; nothing committed.

- `docassemble_base/docassemble/base/managed_git.py`: credential parsing/redaction,
  scoped authorization, dotless-host refusal, `parse_supported_requirement`.
- `managed_preparation.py`, `managed_publication.py`: credential staging/
  committed reuse/revocation.
- `docassemble_webapp/docassemble/webapp/packages/managed.py`, `forms.py`,
  `templates/packages/update_package.html`, `static/app/update_package.js`:
  explicit-name transport.
- `uv-experiment/executor/src/docassemble_executor/git_sources.py`,
  `registry_builder.py`, `preparation_containers.py`, `preparation_poller.py`:
  scoped fetch/checkout/build and worker credential fences.
- `uv-experiment/executor/tests/test_git_sources.py`,
  `uv-experiment/preparation/private_git_proof.py`,
  `uv-experiment/preparation/git_container_proof.py`.
- Evidence: `10b/evidence/accepted/`. Handoff: `uv-experiment/TICKET-10b.md`.

Independent review (alternate `opencode-go/deepseek-v4.1-flash`): two P1s found
and fixed; blocker re-review No issues found / OK. Three P2s recorded.
