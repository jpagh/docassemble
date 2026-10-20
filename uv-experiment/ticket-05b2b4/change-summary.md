# 05b2b4 change summary

Combined range: `ef9df96c113193c01450140e484247c591308abf..a84c88a3e6f7584547649640156df115edac053f` on uv-implementation. Original implementation commit: `c886c5f9638f3fe016c34c1dd25f5eda84357d9a`; P1 fix commit: `a84c88a3e6f7584547649640156df115edac053f`.

Environment-first account deletion commits SQL revocation/ownership reassignment and a durable cleanup record before non-DB cleanup; fixed redacted failure outcomes. SQL-only account helpers serialize composite edits/creation; explicit administrator ownerless assignment binds preview/idempotency and rechecks current ownership. Additive schema/migration, packaging count 13 -> 14.

2026-10-14 P1 fix: reject duplicate managed account deletion under the environment lock before mutation, preserving existing pending/failed/complete cleanup evidence. No cleanup retry or retained email added. Two regressions cover fresh-Session retry after failure and concurrent pending/complete rejection across both initialization paths (+4 intentional cases).

Fix verification: account-writer PostgreSQL 36 passed; publication 176 passed; aggregate 289 passed. act: 292 passed, 1 documented skip; mypy 51 files unchanged. Ruff and diff whitespace checks passed. Exact commands/output: fix-evidence/ and ../TICKET-05b2b4.md. Original implementation evidence remains ../05b2b4/evidence/accepted/.

Remaining fenced: 05b2b5 — remaining view/composite/config/Redis writers and their races (including old_password, registration/default-role/account/grant callers and PackageAuth/name/source/dependency policy); public apply-producing approve/retry until 05c; 05c polling/live apply/terminal slot safety and complete verified runtime status; 05a2b4 credential-source/injection and broader private/source/target/build policy. NO production writer-safety, 05c, 05a2b4 or parent-05 acceptance. Independent review required. Parent status unchanged.

Both this summary and diff.patch intentionally untracked.
