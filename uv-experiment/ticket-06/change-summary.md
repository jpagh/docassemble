# Ticket 06 implementation review

Commit: `b229d98620bdb63128935b321fc82f334bf44df0` on `uv-implementation`.
Baseline: `e21069a59`. Independent acceptance review required.

Implemented targeted registry Update and explicit authorized-contribution Removal
through the existing prepare/approve/publication/maintenance pipeline. Parent
verified solve.lock preferences plus --upgrade-package select only the requested
unlock; necessary non-core movement remains possible and full diffs are retained.
Removal retains other contributions/transitives; identity/current grants survive
removal/reactivation. Non-owner update does not authorize requirement/source edits.
Production exact-core/absent-singleton fences remain. Only build-time disposable
fixture bootstrap ordering changed. Trivial 05b2b9 wording cleanup included.

Code/proof/test paths:
- docassemble_base/docassemble/base/managed_preparation.py
- docassemble_base/docassemble/base/managed_publication.py
- docassemble_webapp/docassemble/webapp/packages/managed.py
- uv-experiment/executor/src/docassemble_executor/registry_builder.py
- uv-experiment/fixture/seed_lifecycle_sources.py
- uv-experiment/preparation/registry_changes_image_proof.py
- uv-experiment/preparation/tests/test_registry_changes_postgres.py
- uv-experiment/preparation/tests/test_preparation_postgres.py
- uv-experiment/preparation/tests/test_publication_registration_postgres.py
- uv-experiment/run-image-bridge-proof.py
- uv-experiment/TICKET-05b2b9.md
- uv-experiment/TICKET-06.md
- uv-experiment/06/evidence/accepted/ (qualified logs, commands, source hashes,
  seven bundle manifests/project/locks/inventories and terminal/DB evidence)

Validation: full PostgreSQL/preparation 1003 passed (+40 intentional); new
registry-only 38 passed; registry+registration 200 passed; pinned uv0.12.21 full
image Update/Removal/conflict/transitive/failure/necessary-dependency-movement
proof passed; act372 passed/one unchanged skip/mypy57; Ruff/source verification/
staged whitespace checks passed. Exact commands and output: TICKET-06.md and
06/evidence/accepted/*-command.json + *.log. Source verification matches five
image-installed changed sources and verifies seven complete retained bundles.

Local ignored tracker checklist/dated Comments updated, acceptance pending review.
Development failures/evidence and prior-ticket review artifacts remain untracked.
This diff.patch and change-summary.md intentionally remain UNTRACKED. No staged
files remain. No new-system edits or branch switch.

Fenced: legacy installers/uninstall/repair entrypoints; general API07; config08;
ZIP09/Git10/private-index-and-credential-breadth11/direct-URL12; repair/uncertain
retry/cold recovery13; core upgrade14; rollback15; initialization/adoption16;
backup17/restore18/migration19; cache and universal operational/observation
breadth20; checkpoints21–23; broad qualification24. Restricted admin/image/public
registry/Python/build-target and non-DB external/plugin boundaries unchanged.
No ticket07+ acceptance. Next: independent ticket06 acceptance review, then07.
