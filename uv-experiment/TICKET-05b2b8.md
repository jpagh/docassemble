# 05b2b8: Remaining feasible registration/invitation/default-account SQL writers

Date: 2026-10-15 UTC. Branch: `uv-implementation`. Baseline:
`ea8dc4f8ddbd51c8e1b4157cb564e3e1d969e48c`.

**Implemented convergence work, NOT parent-05 readiness.** Exact DB-affecting
secret/ownership/deletion composites below remain fenced. Parent Status/checklist
unchanged; tickets 06+ remain blocked. Independent review required. The accepted/
layout identifies the evidence destination, not an acceptance verdict. No branch
switch or new-system edits.

## Objective and supervisor rulings

Implement the remaining independently committing writers where the authorized
ordering is possible: environment lock, same-connection complete SQL authority
mutation, commit, then non-SQL effects. Preserve flag-off bodies. Do not pretend
external writes are transactional (publication contract section 5).

Two blocking inspection decisions were obtained rather than inventing semantics:

1. KEEP all legacy package/update/remove managed rejections. Managed package
   intent is written only by publication's immutable revision/contribution/Package
   projection; legacy writers bypass that and some pip-install before DB completion.
   These are deliberately superseded operations, not races claimed closed here.
2. Fence admin config save and startup/read_config/executor dependency-input
   observation/refresh by exact name. A shared cross-process observation/dirty-state
   design is absent; explicit configuration import/observation belongs to ticket 08.
   This does not claim authorization serialization against live policy changes.
   Even with that scope ruling, DB-affecting migration composites below prevent
   any parent-05 readiness statement.

## Implementation

- `users/helpers.invite_user`: lock and expire preloaded SQL authority before
  checking the actor, role and existing account. Flush for the signing ID, then
  commit invitation AND token once. Email is post-commit. An uncertain delivery
  failure revokes the invitation under a newly acquired environment lock and
  commits that compensation; delivery is neither atomic nor automatically retried.
- `users/views.invite` POST: refresh/recheck administrative authority under lock;
  construct the entire batch and tokens in one SQL transaction before any mail.
  Per-invitation failed delivery is revoked in a fresh serialized transaction.
  The flag-off per-invitation legacy commit/send sequence is preserved.
- `users/views.custom_register` POST: acquire before invitation and account reads;
  account, auth, invitation/default role and last-login commit together before
  email/signal/authentication effects. Mail-failure deletion reacquires the lock.
  `assign_registration_role` is an internal caller-commit SQL helper, not an
  independently committing adapter. The built-in registration signal receives
  `authority_committed=True` and does not duplicate that role transaction.
- Standalone `users/signals.on_register_hook` SQL role/last-login mutation refreshes
  loaded state under the lock and commits once before calling the still-fenced
  `login_or_register` migration. No arbitrary signal/plugin writer is qualified.
- `users/forms.MySignInForm.validate` LDAP account/default-role creation locks
  after external credential verification and before SQL account/role reads.
- `auth/views.oauth_callback` and `phonelogin/views.phone_login_verify` account SQL
  sections lock before identity reads and commit account plus last-login before
  issuing login/session effects. Their later secret/ownership migrations remain
  fenced; qualification of an account section does NOT qualify the whole route.
- `create_tables.get_role/get_user/populate_tables` account/default-role/role-repair
  pass locks the caller's session connection, including direct helper calls.
  `get_user` refreshes the supplied persistent role after lock acquisition.
  The population account pass commits once before separately serialized API-key
  setup; managed package-discovery/source rewriting remains skipped. Role repair
  now uses SQLAlchemy `.unique()` before consuming collection-joined results,
  required for the actual repair path to execute (including flag-off).
- `update_last_login` itself is intentionally unchanged: timestamp-only SQL is not
  publication authority. `api_verify` invokes it on a separate Session INSIDE the
  publication authorizer; adding a generic environment lock there would deadlock.
  A new real PostgreSQL regression proves it completes while that lock is held.
  OAuth/phone account sections already hold their own lock through this commit.

SQL failures propagate to the request/caller session rollback boundary. No
separate connection, universal transaction wrapper, new schema, source policy,
permission policy, ingress relaxation or production initialization/adoption is added.

## Actual-writer qualification and limits

**160 new PostgreSQL cases**, fresh AND upgrade initialization. Twelve source-body
variants: helper invite, view invite, registration role helper, standalone register
signal, bootstrap role, bootstrap account, full bootstrap population/repair pass,
LDAP account creation, OAuth account section, phone account section, ordinary
custom registration and invitation-role custom registration.

Both concurrent lock directions independently observe `pg_blocking_pids` before
release. Writer-first role revocation rejects approval without intent; publication-
first leaves one immutable revision/attempt/Package/grant despite later authority
changes. Independent observers see unchanged uncommitted account/protocol/intent
rows. Every included main SQL variant has flush-then-commit-failure rollback,
missing-baseline rejection and exactly-one-successful-commit checks; all legacy
variants have flag-off/no-managed-lock coverage. Additional cases prove stale
inviter rejection after lock acquisition, post-commit mail/signal visibility,
serialized mail-failure compensation behind an actual concurrent publisher,
default-role repair, invitation-supplied role assignment, and non-reentrant
last-login behavior.

Tests compile exact production function/method bodies and use real SQLAlchemy
Sessions/PostgreSQL, actual Publication/PreviewAuthorizer/PackageProjection and
reflected minimal account schemas. Forms, LDAP/OAuth verification, Flask/current-
user objects, db_adapter, mail, login and migration callbacks are supplied.
This is NOT full Flask/decorator/form/authentication/cryptography/plugin/store
qualification. SQL account-section tests explicitly stub the remaining migration;
no enclosing composite inherits qualification from them.

## Verification and evidence

Evidence: `05b2b8/evidence/accepted/`. Primary exact argv and exits are retained in
`*-command.json`; complete merged normalized output in corresponding `*.log`.
All final commands below exited 0, run from repository root as single-line commands:

```sh
python3 uv-experiment/05b2b8/evidence/accepted/capture.py focused env PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -x -p no:cacheprovider uv-experiment/preparation/tests/test_publication_registration_postgres.py
python3 uv-experiment/05b2b8/evidence/accepted/capture.py postgres env PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
python3 uv-experiment/05b2b8/evidence/accepted/capture.py private-index env PROOF_EVIDENCE_DIR=uv-experiment/05b2b8/evidence/accepted/private-index PROOF_CONTAINER_NAME=da-05b2b8-private-index python3 uv-experiment/run-private-index-proof.py
python3 uv-experiment/05b2b8/evidence/accepted/capture.py recovery env PROOF_IMAGE=da-uv-05c5-fix:local PROOF_CONTAINER_NAME=da-05b2b8-recovery-final PROOF_EVIDENCE_DIR=uv-experiment/05b2b8/evidence/accepted/recovery PROOF_FLOW_ACTION=recovery python3 uv-experiment/run-image-bridge-proof.py
python3 uv-experiment/05b2b8/evidence/accepted/capture.py continuous env PROOF_IMAGE=da-uv-05c5-fix:local PROOF_CONTAINER_NAME=da-05b2b8-continuous-final PROOF_EVIDENCE_DIR=uv-experiment/05b2b8/evidence/accepted/continuous python3 uv-experiment/run-continuous-work-proof.py
python3 uv-experiment/05b2b8/evidence/accepted/capture.py image-bridge env PROOF_IMAGE=da-uv-05c5-fix:local PROOF_CONTAINER_NAME=da-05b2b8-image-bridge-final PROOF_EVIDENCE_DIR=uv-experiment/05b2b8/evidence/accepted/image-bridge python3 uv-experiment/run-image-bridge-proof.py
python3 uv-experiment/05b2b8/evidence/accepted/capture.py act-final act workflow_dispatch -W .act/checks.yml
python3 uv-experiment/05b2b8/evidence/accepted/capture.py lint-final uvx ruff check --select E9,F docassemble_webapp/docassemble/webapp/users/views.py docassemble_webapp/docassemble/webapp/users/helpers.py docassemble_webapp/docassemble/webapp/users/signals.py docassemble_webapp/docassemble/webapp/users/forms.py docassemble_webapp/docassemble/webapp/auth/views.py docassemble_webapp/docassemble/webapp/phonelogin/views.py docassemble_webapp/docassemble/webapp/create_tables.py uv-experiment/preparation/tests/test_publication_registration_postgres.py
python3 uv-experiment/05b2b8/evidence/accepted/capture.py source env PYTHONPATH=docassemble_base uv run --project uv-experiment/executor --frozen python uv-experiment/05b2b8/evidence/accepted/verify-evidence.py
python3 uv-experiment/05b2b8/evidence/accepted/capture.py diff-check git diff --cached --check
```

Final results:

- Focused: **160 passed in 43.16s**.
- Full publication/preparation: **767 passed in 149.76s**. Intentional baseline
  **607 -> 767** adds these 160 cases; no exclusions, skips or count assertions changed.
- Private HTTPS index: **nine PASS groups**, including retained bytes, secret
  absence, corrected query-before-SHA256 rejection and relocated offline frozen sync.
- Recovery: **nine PASS groups including completion**; continuous: **six PASS
  groups including completion**, plus its unchanged authenticated normal fixture;
  image bridge: **eight PASS groups including completion**.
- Final act: **372 passed, 1 skipped in 33.39s**, mypy **57 files/no issues**.
  Expected counts and documented bare-act Supervisor skip unchanged.
- Ruff E9/F: **All checks passed!** Source/retention verifier: **38 hashes verified**,
  all private retained bundles byte-valid, raw/base64 disposable secrets absent.
- `image-source-command.json` retains exact additional Python argv and hashes:
  seven image-installed executor modules match current source (installation,
  controller, durable_work, preparation, published, boot_observation, uvsync).
  Changed SQL writers are NOT installed in this historical image.

Operational proofs use existing `da-uv-05c5-fix:local`,
`sha256:3ba622c2fbf1478eac35c35eabafb87f260473247812d41efa97640eed415e45`,
Python 3.12.14, uv 0.12.21. Private-index proof reads current mounted source;
operational proofs exercise unchanged image-installed executor. Actual changed
SQL writers are qualified by source-body PostgreSQL tests, not historical routes.
No current-source image rebuild is claimed.

Initial parallel operational runs failed `pg_isready` 10-second boot deadlines;
continuous hit the tool's 600-second timeout (no returned exit code). A subsequent
sequential recovery reached preparation but failed with Docker filesystem 100%
full / only 587M available. Retained under `initial-parallel/` and
`initial-sequential/`, explicitly FAILED development evidence. Supervisor approved
cleanup, recorded in `cleanup-*-command.json`/logs: removed only this run's failed
containers and evidence-copied final operational containers; additionally removed
stopped `da-05c4-continuous` after confirming its committed accepted evidence,
pruned unused build cache, and removed the unreferenced image tag
`da-uv-05c5:local` / `sha256:693e4479f9f637d46da8b45a5cc62fdd7b6899baa8a9fe5117f2293ff563637f`.
All named volumes, evidence, historical running containers and current fixed image
were preserved. Final operational reruns were sequential and passed. The run's
small stopped private-index fixture remains. No Colima expansion.

`development-suite-runs/` retains intermediate passing counts before removing the
unneeded timestamp lock and adding the non-reentrancy regression; they are not
final-source acceptance evidence. Earlier focused development failures were test
namespace omissions; the real joinedload repair-path issue was fixed as above.
The initial staged whitespace check found three log EOF blank lines; normalized
log whitespace (not retained bundle bytes), preserved `diff-whitespace-before.log`,
and reran the staged check successfully. No heredocs or shell redirection used.

## Precise remaining fenced list

Paths relative to `docassemble_webapp/docassemble/webapp` unless stated otherwise.
These are exact-name contract rationales, NOT closed races or implied acceptance.
Existing restricted managed image ingress continues to exclude these composites.

### DB-affecting composites: NOT closed; 06+ BLOCKED

- `users/views.edit_user_profile_page:delete_account` — `user_interviews` independently commits shared/interview cleanup before revocation; revoking first destroys metadata needed for the existing cleanup semantics without a separately designed capture/cleanup protocol.
- `users/views.edit_user_profile_page:delete_account_complete` — same metadata/commit conflict, including deletion of shared interviews; the qualified `delete_user_data` helper does not qualify this caller.
- `users/views.manage_account` authenticated branch — same pre-revocation interview cleanup, plus logout/session effects; a lock held only through its first cleanup commit is insufficient.
- `users/views.manage_account` anonymous branch — `reset_user_dict`/`delete_temp_user_data` independently commit mixed SQL/store cleanup; moving them after one commit does not absorb their SQL mutations.
- `users/helpers.login_or_register` — `sub_temp_user_dict_key`, `save_user_dict_key`, `fix_secret` and `sub_temp_other` interleave SQL ownership commits with session/hook effects; post-commit dispatch alone leaves DB mutations outside the authority transaction.
- `users/signals.on_user_login` — enclosing `login_or_register` migration above remains DB-affecting; the timestamp update is not an authorization mutation or closure of that caller.
- `users/signals.on_register_hook` enclosing migration — role/last-login SQL is now qualified; subsequent `login_or_register` ownership/secret migration still independently commits.
- `users/views.custom_register` enclosing registration signal/auto-login migration — account/auth/invitation-role SQL is now qualified; downstream built-in login migration and arbitrary signal SQL writers cannot be classified as non-DB effects.
- `users/signals.on_password_change` — `fix_secret` still combines independently dispatched built-in SQL and arbitrary plugin effects; no common enclosing commit owner exists.
- `interview/helpers.fix_secret` — session `newsecret` publication depends on `substitute_secret` migration; adding a wrapper commit would not separate arbitrary hook SQL/external effects safely.
- `interview/helpers.substitute_secret` remaining direct callers `auth/views.oauth_callback`, `phonelogin/views.phone_login_verify`, and `interview/helpers.fix_secret` — built-in SQL modes accept caller-owned flushes, but enclosing session/ownership callers and arbitrary hook commits are not transaction-owned as a whole.
- `auth/views.oauth_callback` enclosing migration — account SQL section now qualified; later `sub_temp_user_dict_key`/`save_user_dict_key`/`substitute_secret`/`sub_temp_other` still mix independently committing ownership and hooks.
- `phonelogin/views.phone_login_verify` enclosing migration — account SQL section now qualified; later ownership/secret migration has the same independent-commit conflict.
- `users/helpers.set_user_info:old_password` arbitrary plugin dispatch — prior built-in caller-owned SQL qualification is unchanged, not a guarantee that custom `manage_global_objects`/`manage_tts_objects`/`manage_chat_logs` implementations do not independently commit or perform pre-commit external effects.

The shared hook callables dispatch both built-in SQL and arbitrary extensions.
Moving those entire calls after commit would move their DB authorization/ownership
writes outside the single transaction; moving them before commit would keep external
effects before commit. A safe split needs an explicit plugin/ownership migration
protocol rather than treating DB-affecting callbacks as permitted non-DB effects.
No silent rejection removal, plugin transactional guarantee or automatic retry added.

### By-design legacy operation fences: unchanged, NOT races claimed closed

- `utils/helpers.install_zip_package`, `install_git_package`, `install_pip_package`, `uninstall_package` — legacy Package/PackageAuth/source mutation bypasses immutable contribution/revision publication; rejection remains first.
- `packages/views.update_package`, `packages/api.api_package`, `develop/views.playground_packages` install branch — managed unsupported/prepare gates or rejecting ZIP helper remain; no new legacy writer path is enabled.
- `update.clear_invalid_package`, `clear_invalid_package_auth`, `check_for_updates`, `add_dependencies`, `fix_names`, `install_package`, `uninstall_package`, `update_versions` — legacy cleanup/discovery/name/source/installed-version/pip reconciliation is superseded by managed publication/frozen execution and remains rejected.
- `tasks/tasks.update_packages`, `reset_server`, direct `update` CLI, direct `create_tables` schema CLI — managed legacy execution/restart/schema entrypoints remain rejected.
- `create_tables.populate_tables` package rewrite/`add_dependencies` pass — managed skip remains; the new account-pass integration does not enable package discovery or source rewriting.

### Config observation/import: exact deferred ticket-08 seams

- `admin/views.config_page` cloud/local save + fail-closed observation — no shared cross-process snapshot/dirty-state protocol exists; file/cloud writes are outside SQL atomicity and observation/import requires ticket 08 design.
- `startup.initialize` / its `read_config` policy refresh, `docassemble_base/docassemble/base/read_config.py`, `Docker/initialize.sh` dependency/index/PYTHONPACKAGES observation — observed file/operator changes are not serialized policy authority; no refresh claim is inferred from reads.
- executor `docassemble_executor.preparation`, `controller`, `cli` dependency/policy refresh before imports/work — frozen approved inputs are unchanged; adding new cross-process config authority/refresh is not done here.
- `config.allowed`, application `ALLOW_UPDATES`, `DEVELOPER_CAN_INSTALL` live policy changes — their observation/reload serialization remains unqualified; existing restricted registry ingress and snapshot/drift boundary remain, not SQL/file atomicity.

### Other unchanged parent/later fences

- **05c5b** universal live/status/all-role/lost-success observation closure, cold uncertain/unreported recovery, management-table-outage boot restoration, restricted core-only resumption, broader failed/recovered/crash/same-revision rollover and explicit uncertain-work/schema/setup clearance remain unchanged.
- **05a2b5** HTTPS Git refs/subdirectories/SHA/scoped authentication, private sdists/build authentication, candidate consumption/promotion/rotation/revocation and authorized updater credential reuse, production private policy/dispatch, mixed indexes/direct URLs/ZIPs and broader source/target/build qualification remain unchanged.
- Production managed initialization/adoption, general update/remove/config import/API, coherent upgrade/rollback/backup/image migration and tickets 06+ remain fenced; restricted experimental approval is not general production approval.

## Resolution and limits

Feasible account/invitation/default-role SQL integrations and actual-writer races
implemented together. This is not a plan-only handoff and not another proposed
sub-split. Exact unfitted composites remain explicit, with contract rationales.
**PARENT 05 IS NOT READY FOR ACCEPTANCE; 06+ remain blocked.** Independent review
must assess these changes and the remaining DB migration fences. Parent Status
and checklist are unchanged. Requested diff.patch and change-summary.md remain
untracked and span the supplied baseline through the implementation commit.

Next: independent review; close the exact DB-affecting ownership/secret/deletion
composites before parent 05 acceptance. No later-ticket implementation authorized.
