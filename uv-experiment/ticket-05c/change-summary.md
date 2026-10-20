# 05c bounded stopped-materialization change summary

Combined HEAD: ed1fa4cedd469c5450b3aaf41239a1001a68584b on uv-implementation; baseline 4d54c9a1afa15eabe701c19ad090c90af598a073. Review-fix parent: d88249cb89ae4fae24f9bd1dc06a5be0fb279079.

Implemented the task-authorized 05c1 end-to-end fallback, not full 05c or parent acceptance. Independent DB approval-attempt polling applies exact retained bytes frozen/offline/no-build as non-root under existing drain/host/child authority, stops in maintenance, binds journal and DB identities, and replays terminal reporting without reinstalling or releasing another attempt. Added separate unknown/materialized/history/status fields; public approval/retry stay fenced. Supervisor-approved explicit lifecycle-fixture-only DB seed (no production adoption) and narrow fixture migration/proof-driver corrections permit current-image regressions.

Changed source/docs/tracker:
- .scratch/uv-environment-implementation/issues/05-registry-install.md
- docassemble_base/docassemble/base/managed_preparation.py
- uv-experiment/TICKET-05-SPLIT.md
- uv-experiment/TICKET-05c.md
- uv-experiment/executor/src/docassemble_executor/cli.py
- uv-experiment/executor/src/docassemble_executor/installation.py
- uv-experiment/executor/src/docassemble_executor/journal.py
- uv-experiment/executor/tests/test_executor_packaging.py
- uv-experiment/executor/tests/test_installation.py
- uv-experiment/fixture/fixture_advanced_revision.py
- uv-experiment/fixture/image-build.sh
- uv-experiment/fixture/managed_baseline.py
- uv-experiment/fixture/seed_lifecycle_sources.py
- uv-experiment/preparation/installation_proof.py
- uv-experiment/preparation/tests/test_installation_postgres.py
- uv-experiment/preparation/tests/test_publication_api_key_writers_postgres.py
- uv-experiment/run-installation-proof.py
- uv-experiment/scenarios/service_sessions.py

Qualified evidence: 929 files under uv-experiment/05c/evidence/accepted/. Exact commands/output and precise fenced inventory: uv-experiment/TICKET-05c.md.

Verification: final pinned-uv container proof passes; PostgreSQL/preparation 393 passed; all 96 original current-image lifecycle scenarios pass; full native container 331 passed/no skips; act 294 passed/1 unchanged Supervisor skip and mypy 52 files; Ruff E9/F passes; source/doc whitespace check excludes verbatim generated evidence; 605 image-copy sources match, 647 host inputs hashed; four retained copied bundles verify.

Remaining fenced: full published schema/probe/all-role normal apply and DB-bound recovery reporting; complete HostEnvironment/normal/recovery/override observation/status bridge; boot/service polling, subsequent-attempt journal rollover and uncertain retry clearance; production managed-init/adoption; browser/API approval enablement and aggregate independent review; all remaining 05b2b6 account/profile/registration/default-role/secret/config/dependency/PackageAuth/grant/name/source/policy composites and actual-writer closure/races; 05a2b4 real credential-source/injection/private binding and broader target/source/build policy. Public approval/retry and parent-05 closure remain blocked, 06 blocked.

These diff/change-summary artifacts deliberately remain UNTRACKED. No branch switch or new-system changes.

## Review fixes — 2026-10-15 UTC

P1: separate installation_preparing from actual mutation; confirmed drain abort has a durable fenced aborted-before-sync result and DB aborted terminal phase. Initial report and replay preserve controller admission and do not exclude resumed normal consumers. P2: persist allowlisted failure.stage/failure.reason codes; no raw exception messages or credentials.

New verification: normally serving HTTP fixture + surviving consumer + lost-report drain abort, actual gate-failure receipts and journal round trips: 16 focused tests passed. Real PostgreSQL/preparation: 413 passed (+20 intentional). Pinned-tooling installation proof with current executor source mounted read-only: seven PASS groups. act: 308 passed (+14 intentional), 1 unchanged skip; mypy 52 source files unchanged. Ruff and whitespace checks pass. Exact final commands/output: ticket-05c/fix-evidence/. Original full-image lifecycle evidence above was not rerun; no new full-05c claim. See fix-summary.md and TICKET-05c.md.

The regenerated diff.patch is git diff 4d54c9a1afa15eabe701c19ad090c90af598a073..HEAD (combined original bounded slice plus review fix), captured using Python subprocess without shell redirection. All ticket-05c artifacts remain untracked. Independent re-review and all existing remainder fences are unchanged.
