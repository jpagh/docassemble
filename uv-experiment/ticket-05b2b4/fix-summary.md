# 2026-10-14: 05b2b4 P1 duplicate-cleanup fix

Fix commit: `a84c88a3e6f7584547649640156df115edac053f`, directly atop `c886c5f9638f3fe016c34c1dd25f5eda84357d9a` on `uv-implementation`.

Managed account deletion now checks account_cleanup under the environment lock and rejects duplicates before any SQL or non-DB cleanup mutation. Removed the deletion of existing cleanup evidence. Pending, failed and complete records remain authoritative; automatic retry/resumable cleanup is not implemented. Flag-off behavior unchanged.

Committed files:
- docassemble_webapp/docassemble/webapp/interview/helpers.py
- uv-experiment/preparation/tests/test_publication_account_writers_postgres.py
- uv-experiment/TICKET-05b2b4.md
- .scratch/uv-environment-implementation/issues/05-registry-install.md

Two new regression functions run against upgrade/fresh PostgreSQL initialization: fresh-Session duplicate after hook failure before Redis cleanup preserves failed state with cleared account email and invokes no cleanup; concurrent pending and subsequent complete duplicates reject without changing evidence. Four intentional additional cases.

Verification commands and complete outputs are in fix-evidence/account-writers.log (36 passed in 7.54s), publication.log (176 passed in 25.34s), preparation.log (289 passed in 45.71s) and act.log (292 passed, 1 skipped in 30.90s; mypy 51 files; job succeeded). All exit 0. Ruff E9,F and git diff --check passed. No assertions/exclusions/native counts changed.

Combined diff.patch regenerated from ef9df96c113193c01450140e484247c591308abf..HEAD using Python subprocess byte capture (no shell redirection). This summary, change-summary.md, diff.patch and fix-evidence remain untracked.

Independent re-review required. Parent 05 Status unchanged; 06 blocked. Remaining writer/composite/config/Redis qualification (05b2b5), public apply-producing approval/retry and live installation (05c), credential-source/injection (05a2b4) and broader source/target/build policy remain fenced. No production writer-safety or parent acceptance claim. No branch switch or new-system changes.
