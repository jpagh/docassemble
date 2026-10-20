# Ticket 10a change summary

Baseline `b229d9862`; accepted tickets 07–09 changes already present.
Working-tree changes only; nothing committed.

- `docassemble_base/docassemble/base/managed_git.py` (new): named PEP 508 Git
  declaration parsing, ref classification, `git_sources` validation,
  `parse_supported_requirement` URL-based routing.
- `managed_preparation.py`, `managed_publication.py`, `managed_qualification.py`:
  Git prepare/update/remove, `git_sources` recording/preservation, constraints.
- `packages/managed.py`, `packages/views.py`: named legacy/API/browser transports.
- `uv-experiment/executor/src/docassemble_executor/git_sources.py` (new) and
  `registry_builder.py`: fetch/checkout/resolve, subdirectory build, authoritative
  metadata, pre/post constraint checks.
- `uv-experiment/executor/tests/test_git_sources.py`,
  `uv-experiment/preparation/tests/test_git_install_postgres.py`,
  `uv-experiment/preparation/git_install_proof.py`.
- Evidence: `10a/evidence/accepted/`. Handoff: `uv-experiment/TICKET-10a.md`.

Independent review (alternate `opencode-go/deepseek-v4.1-flash` after Codex usage
limits): OK with notes, no P0/P1; P2 parser fix applied and tested; two P2s
recorded for 10b (browser name field; container git/egress proof).
