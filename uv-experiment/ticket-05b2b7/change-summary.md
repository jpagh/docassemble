# 05b2b7 bounded SQL writer implementation

Commit: ea8dc4f8ddbd51c8e1b4157cb564e3e1d969e48c on uv-implementation; baseline 3a774a5f6784461ba9b25a168aa29674382e7434.

Supervisor approved the one bounded sub-split. SQL views/helper integrated; both mandatory P2 fixes completed. NOT final writer convergence or parent-05 acceptance; independent review required; 06+ blocked.

Validation: actual-source PostgreSQL98; full PostgreSQL607 (+98 intentional); act372 passed/one unchanged skip/mypy57; private HTTPS9, recovery9, continuous6 plus normal fixture, image bridge8; lint/25 hashes/private retained bytes/7 image executor module hashes pass. Historical image reused; changed SQL views qualified by actual-source tests, not rebuilt-image proof.

## Precise remaining fenced list


Paths relative to docassemble_webapp/docassemble/webapp unless stated otherwise.
Prior separately qualified helpers stay qualified; enclosing composites do not
inherit qualification merely by calling them.

### Remaining writer composites/callers/races (not closed)

- users/views.edit_user_profile_page **delete_account/delete_account_complete**
  user_interviews + delete_user_data enclosing multi-store branches; users/views
  **custom_register, invite, manage_account**. SQL profile/MFA/email, user_add,
  user_profile_page and add_privilege branches above are the only new closures.
- users/helpers.**invite_user, login_or_register**; users/signals.**on_user_login,
  on_register_hook, on_password_change**; interview/helpers.**fix_secret** and
  remaining direct **substitute_secret** enclosing callers; cookie/session/auth
  and external manage_global_objects/manage_tts_objects/manage_chat_logs plugin
  effects/independent commits. Prior built-in transaction-owned SQL modes alone
  remain qualified; no arbitrary plugin atomicity claim.
- users/forms.**MySignInForm.validate** LDAP/default-role account creation;
  auth/views.**oauth_callback**; phonelogin/views.**phone_login_verify**;
  create_tables.**get_role, get_user, populate_tables** account/default-role and
  fix-user-roles passes, their registration/auth/secret/session/hook composites.
- admin/views.**config_page** cloud/local save + fail-closed observation;
  **startup.initialize/read_config** policy refresh; **base/read_config** and
  **Docker/initialize.sh** dependency/index/PYTHONPACKAGES observation; executor
  **preparation/controller/CLI** dependency/policy refresh before imports/work;
  **config.allowed, ALLOW_UPDATES, DEVELOPER_CAN_INSTALL** and name/source/
  dependency-policy writer serialization. Operator edits retain the explicit
  observed-snapshot/drift boundary, never a SQL/file atomicity claim. Config
  imports remain unsupported; no refresh capability is inferred from reads.
- utils/helpers.**install_zip_package, install_git_package, install_pip_package,
  uninstall_package**; packages/views.**update_package**;
  packages/api.**api_package**; develop/views.**playground_packages** ZIP caller.
  Existing managed reject/prepare fences stay intact; no unsupported grant/source
  or intent mutation is re-enabled.
- update.**clear_invalid_package, clear_invalid_package_auth, check_for_updates,
  add_dependencies, fix_names, install_package, uninstall_package, update_versions**;
  tasks/tasks.**update_packages, reset_server**; direct update CLI callers;
  create_tables.**populate_tables** package rewrites/**add_dependencies**. Existing
  managed reject/skip guards are NOT actual-writer race closure. Remaining
  PackageAuth/grant/name/source/dependency-policy complete caller audit and races
  remain required; prior PackageProjection/deletion proof is unchanged.

### Other unchanged parent-05/later fences

- **05c5b** universal live observation/status/all-role readiness/lost-success
  closure; cold uncertain/unreported recovery, management-table-outage boot
  restoration, restricted core-only service/admin resumption; broader failed/
  recovered/crash/same-revision rollover; explicit uncertain-work/schema/setup
  clearance; config/dependency/policy refresh. No automatic retry, heartbeat
  release, uncertain-journal deletion, schema replay or data restore is added.
- **05a2b5** HTTPS Git refs/subdirectories/SHA and scoped authentication, private
  sdists/build authentication, candidate claim consumption/promotion/rotation/
  revocation and authorized updater credential reuse, production private policy/
  source authorization/dispatch, mixed indexes/direct URLs/immutable ZIPs, broader
  source/target/build qualification. The bounded private-wheel snapshot is NOT
  full source parity or production private dispatch.
- Production managed initialization/adoption, broader installed-material/store
  qualification, update/remove/config import, coherent upgrade/rollback/adoption/
  backup/image migration/general managed API breadth and **tickets 06+** remain
  fenced. Existing restricted experimental approval does not become general
  production approval. NO parent-05/05b/05c/full writer-safety acceptance claim.


## Exact commands and results
### act-command (exit 0)

`act workflow_dispatch -W .act/checks.yml`

### continuous-command (exit 0)

`env PROOF_IMAGE=da-uv-05c5-fix:local PROOF_CONTAINER_NAME=da-05b2b7-continuous PROOF_EVIDENCE_DIR=uv-experiment/05b2b7/evidence/accepted/continuous python3 uv-experiment/run-continuous-work-proof.py`

### diff-check-command (exit 0)

`git diff --cached --check`

### diff-whitespace-before-command (exit 2)

`git diff --cached --check`

### focused-command (exit 0)

`env PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -x -p no:cacheprovider uv-experiment/preparation/tests/test_publication_profile_views_postgres.py`

### image-bridge-command (exit 0)

`env PROOF_IMAGE=da-uv-05c5-fix:local PROOF_CONTAINER_NAME=da-05b2b7-image-bridge PROOF_EVIDENCE_DIR=uv-experiment/05b2b7/evidence/accepted/image-bridge python3 uv-experiment/run-image-bridge-proof.py`

### image-source-command (exit 0)

`python3 -c 'import hashlib, pathlib, subprocess, json; names=['"'"'installation'"'"','"'"'controller'"'"','"'"'durable_work'"'"','"'"'preparation'"'"','"'"'published'"'"','"'"'boot_observation'"'"','"'"'uvsync'"'"']; expected={name:hashlib.sha256(pathlib.Path('"'"'uv-experiment/executor/src/docassemble_executor/'"'"'+name+'"'"'.py'"'"').read_bytes()).hexdigest() for name in names}; script='"'"'import importlib,hashlib,json; from pathlib import Path; names='"'"'+repr(names)+'"'"'; print(json.dumps({name:hashlib.sha256(Path(importlib.import_module("docassemble_executor."+name).__file__).read_bytes()).hexdigest() for name in names},sort_keys=True))'"'"'; actual=json.loads(subprocess.check_output(['"'"'docker'"'"','"'"'exec'"'"','"'"'da-05b2b7-image-bridge'"'"','"'"'/usr/share/docassemble/executor-venv/bin/python'"'"','"'"'-c'"'"',script], text=True)); print(json.dumps(actual, indent=2,sort_keys=True)); assert actual==expected; print('"'"'PASS seven image-installed executor modules match current source; changed SQL views are NOT installed in this historical image'"'"')'`

### lint-command (exit 0)

`uvx ruff check --select E9,F docassemble_webapp/docassemble/webapp/users/views.py docassemble_webapp/docassemble/webapp/users/helpers.py uv-experiment/preparation/tests/test_publication_profile_views_postgres.py uv-experiment/preparation/private_index_proof.py uv-experiment/run-private-index-proof.py`

### postgres-command (exit 0)

`env PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py`

### private-index-command (exit 0)

`env PROOF_EVIDENCE_DIR=uv-experiment/05b2b7/evidence/accepted/private-index PROOF_CONTAINER_NAME=da-05b2b7-private-index python3 uv-experiment/run-private-index-proof.py`

### recovery-command (exit 0)

`env PROOF_IMAGE=da-uv-05c5-fix:local PROOF_CONTAINER_NAME=da-05b2b7-recovery PROOF_EVIDENCE_DIR=uv-experiment/05b2b7/evidence/accepted/recovery PROOF_FLOW_ACTION=recovery python3 uv-experiment/run-image-bridge-proof.py`

### source-command (exit 0)

`env PYTHONPATH=docassemble_base uv run --project uv-experiment/executor --frozen python uv-experiment/05b2b7/evidence/accepted/verify-evidence.py`

## Changed files (complete)

.scratch/uv-environment-implementation/issues/05-registry-install.md
docassemble_webapp/docassemble/webapp/users/helpers.py
docassemble_webapp/docassemble/webapp/users/views.py
uv-experiment/05b2b7/evidence/accepted/README.md
uv-experiment/05b2b7/evidence/accepted/act-command.json
uv-experiment/05b2b7/evidence/accepted/act.log
uv-experiment/05b2b7/evidence/accepted/capture.py
uv-experiment/05b2b7/evidence/accepted/continuous-command.json
uv-experiment/05b2b7/evidence/accepted/continuous.log
uv-experiment/05b2b7/evidence/accepted/continuous/before-restart.json
uv-experiment/05b2b7/evidence/accepted/continuous/commands.json
uv-experiment/05b2b7/evidence/accepted/continuous/container.log
uv-experiment/05b2b7/evidence/accepted/continuous/continuous-apply-invocations.log
uv-experiment/05b2b7/evidence/accepted/continuous/continuous-normal-status.json
uv-experiment/05b2b7/evidence/accepted/continuous/continuous-pending.json
uv-experiment/05b2b7/evidence/accepted/continuous/continuous-preview.json
uv-experiment/05b2b7/evidence/accepted/continuous/invalidated-status.json
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture.log
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/aborted-status.json
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/apply-uv-invocations.log
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/attestation.json
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/commands.json
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/container.log
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/first-preview.json
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/image-runtime-uv.lock
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/image.txt
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/installed-inventory.json
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/accepted.lock
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/admission.json
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/admission.lock
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/applied.json
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/attempt-history/ec718419-885a-4939-8a13-70bbf34551c5.json
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/callers/a100a4d073f923e949f32d72cc01f3bf7c2a9dc2f8f29292d9844a27f5386932
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/callers/b8463e1e5901c645900b20b3984df206ae44b2bbd9378e1ef621fa464acbcee0
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/callers/bb8499215fb58923fa7f0244f526ab1da5902d77e3c49c9ba93a3da67867703d
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/desired.json
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/health/watchdog.json
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/infrastructure/accepted.lock
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/infrastructure/admission.json
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/infrastructure/admission.lock
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/infrastructure/role-activity/conversion.lock
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/infrastructure/role-activity/nltk.lock
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/infrastructure/role-activity/web.lock
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/infrastructure/role-activity/websockets.lock
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/infrastructure/runtime.lock
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/infrastructure/traffic.lock
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/initialized.json
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/installation-host.json
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/lifecycle.json
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/mutation.lock
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/publication-reported.json
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/role-activity/conversion.lock
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/role-activity/nltk.lock
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/role-activity/web.lock
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/role-activity/websockets.lock
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/runtime.lock
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/service-conversion.lock
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/service-nltk.lock
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/service-websockets.lock
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/traffic.lock
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/watchdog.lock
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/worker-normal.lock
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/local-state/worker-single.lock
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/logs/create-tables.log
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/logs/docassemble.log
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/logs/managed-mail.log
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/logs/managed-service-conversion.log
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/logs/managed-service-nltk.log
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/logs/managed-service-websockets.log
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/logs/managed-watchdog.log
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/logs/managed-worker-normal.log
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/logs/managed-worker-single.log
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/logs/uv-sync-20261015-043934.log
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/logs/uv-sync-20261015-044117.log
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/logs/web-normal.log
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/lost-report-local-status.json
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/normal-local-status.json
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/normal-terminal-journal.json
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/normal.log
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/published-bundle/candidate-wheels/peppercorn-0.6-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/published-bundle/candidate-wheels/sampleproject-4.0.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/published-bundle/installed-material.json
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/published-bundle/inventory.json
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/published-bundle/manifest.json
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/published-bundle/pyproject.toml
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/published-bundle/sources/peppercorn-0.6.tar.gz
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/published-bundle/sources/sampleproject-4.0.0.tar.gz
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/published-bundle/tool.json
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/published-bundle/uv.lock
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/published-pending.json
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/qualified-baseline.json
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/release-consumer
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/setup.log
uv-experiment/05b2b7/evidence/accepted/continuous/normal-fixture/verified-running-status.json
uv-experiment/05b2b7/evidence/accepted/continuous/old-receipt.json
uv-experiment/05b2b7/evidence/accepted/continuous/pending-status.json
uv-experiment/05b2b7/evidence/accepted/continuous/resumed-status.json
uv-experiment/05b2b7/evidence/accepted/continuous/supervisor/celery.log
uv-experiment/05b2b7/evidence/accepted/continuous/supervisor/celerysingle.log
uv-experiment/05b2b7/evidence/accepted/continuous/supervisor/conversion.log
uv-experiment/05b2b7/evidence/accepted/continuous/supervisor/cron.log
uv-experiment/05b2b7/evidence/accepted/continuous/supervisor/durable-installation.log
uv-experiment/05b2b7/evidence/accepted/continuous/supervisor/durable-preparation.log
uv-experiment/05b2b7/evidence/accepted/continuous/supervisor/exim4.log
uv-experiment/05b2b7/evidence/accepted/continuous/supervisor/nltk.log
uv-experiment/05b2b7/evidence/accepted/continuous/supervisor/supervisord.conf
uv-experiment/05b2b7/evidence/accepted/continuous/supervisor/supervisord.log
uv-experiment/05b2b7/evidence/accepted/continuous/supervisor/supervisord.pid
uv-experiment/05b2b7/evidence/accepted/continuous/supervisor/syslogng.log
uv-experiment/05b2b7/evidence/accepted/continuous/supervisor/watchdog.log
uv-experiment/05b2b7/evidence/accepted/continuous/supervisor/websockets.log
uv-experiment/05b2b7/evidence/accepted/continuous/verify.log
uv-experiment/05b2b7/evidence/accepted/diff-check-command.json
uv-experiment/05b2b7/evidence/accepted/diff-check.log
uv-experiment/05b2b7/evidence/accepted/diff-whitespace-before-command.json
uv-experiment/05b2b7/evidence/accepted/diff-whitespace-before.log
uv-experiment/05b2b7/evidence/accepted/focused-command.json
uv-experiment/05b2b7/evidence/accepted/focused.log
uv-experiment/05b2b7/evidence/accepted/image-bridge-command.json
uv-experiment/05b2b7/evidence/accepted/image-bridge.log
uv-experiment/05b2b7/evidence/accepted/image-bridge/aborted-status.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/apply-uv-invocations.log
uv-experiment/05b2b7/evidence/accepted/image-bridge/attestation.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/commands.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/container.log
uv-experiment/05b2b7/evidence/accepted/image-bridge/failed-durable-status.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/failed-visible-status.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/first-preview.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/flow.log
uv-experiment/05b2b7/evidence/accepted/image-bridge/image-runtime-uv.lock
uv-experiment/05b2b7/evidence/accepted/image-bridge/image.txt
uv-experiment/05b2b7/evidence/accepted/image-bridge/installed-inventory.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/accepted.lock
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/admission.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/admission.lock
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/applied.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/attempt-history/63ec2f81-194e-4a3d-9829-4dda52010fb9.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/attempt-history/74d1672a-7828-4706-96d3-da9ee5c1f48e.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/callers/4d5d2c924a95cc435ecce70aabf2d005dcc927f362049f60379beefe73cfeacd
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/callers/5585704307bfa7bbbf747d1d3db5878492208510cf80689082b3e3915d89ae91
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/callers/87e5462e45d2eec66575b6ca250565c39619264ac502a8403176b03d7fb2d4ef
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/callers/c4c20789b8847ea28330817f4a9473822a1093ee615707ae20ee5a659b1972f6
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/callers/cecaa2840287d93ce257af62bd3a7e419958ea4277110be4a77e93dd6bf409a7
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/callers/d1f50dc698df8d56dc771f448864a244dfc629bc4c7e7647e135295e40fa29ad
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/desired.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/health/watchdog.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/infrastructure/accepted.lock
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/infrastructure/admission.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/infrastructure/admission.lock
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/infrastructure/role-activity/conversion.lock
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/infrastructure/role-activity/nltk.lock
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/infrastructure/role-activity/web.lock
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/infrastructure/role-activity/websockets.lock
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/infrastructure/runtime.lock
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/infrastructure/traffic.lock
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/initialized.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/installation-host.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/lifecycle.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/mutation.lock
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/publication-reported.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/role-activity/conversion.lock
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/role-activity/nltk.lock
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/role-activity/web.lock
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/role-activity/websockets.lock
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/runtime.lock
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/service-conversion.lock
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/service-nltk.lock
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/service-websockets.lock
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/traffic.lock
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/watchdog.lock
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/worker-normal.lock
uv-experiment/05b2b7/evidence/accepted/image-bridge/local-state/worker-single.lock
uv-experiment/05b2b7/evidence/accepted/image-bridge/logs/create-tables.log
uv-experiment/05b2b7/evidence/accepted/image-bridge/logs/docassemble.log
uv-experiment/05b2b7/evidence/accepted/image-bridge/logs/managed-mail.log
uv-experiment/05b2b7/evidence/accepted/image-bridge/logs/managed-service-conversion.log
uv-experiment/05b2b7/evidence/accepted/image-bridge/logs/managed-service-nltk.log
uv-experiment/05b2b7/evidence/accepted/image-bridge/logs/managed-service-websockets.log
uv-experiment/05b2b7/evidence/accepted/image-bridge/logs/managed-watchdog.log
uv-experiment/05b2b7/evidence/accepted/image-bridge/logs/managed-worker-normal.log
uv-experiment/05b2b7/evidence/accepted/image-bridge/logs/managed-worker-single.log
uv-experiment/05b2b7/evidence/accepted/image-bridge/logs/uv-sync-20261015-044344.log
uv-experiment/05b2b7/evidence/accepted/image-bridge/logs/uv-sync-20261015-044526.log
uv-experiment/05b2b7/evidence/accepted/image-bridge/logs/web-normal.log
uv-experiment/05b2b7/evidence/accepted/image-bridge/lost-report-local-status.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/normal-local-status.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/normal-terminal-journal.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/published-bundle/candidate-wheels/peppercorn-0.6-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/image-bridge/published-bundle/candidate-wheels/sampleproject-4.0.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/image-bridge/published-bundle/installed-material.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/published-bundle/inventory.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/published-bundle/manifest.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/published-bundle/pyproject.toml
uv-experiment/05b2b7/evidence/accepted/image-bridge/published-bundle/sources/peppercorn-0.6.tar.gz
uv-experiment/05b2b7/evidence/accepted/image-bridge/published-bundle/sources/sampleproject-4.0.0.tar.gz
uv-experiment/05b2b7/evidence/accepted/image-bridge/published-bundle/tool.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/published-bundle/uv.lock
uv-experiment/05b2b7/evidence/accepted/image-bridge/published-pending.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/qualified-baseline.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/release-consumer
uv-experiment/05b2b7/evidence/accepted/image-bridge/second-preview.json
uv-experiment/05b2b7/evidence/accepted/image-bridge/setup.log
uv-experiment/05b2b7/evidence/accepted/image-bridge/verified-running-status.json
uv-experiment/05b2b7/evidence/accepted/image-source-command.json
uv-experiment/05b2b7/evidence/accepted/image-source.log
uv-experiment/05b2b7/evidence/accepted/lint-command.json
uv-experiment/05b2b7/evidence/accepted/lint.log
uv-experiment/05b2b7/evidence/accepted/postgres-command.json
uv-experiment/05b2b7/evidence/accepted/postgres.log
uv-experiment/05b2b7/evidence/accepted/private-index-command.json
uv-experiment/05b2b7/evidence/accepted/private-index.log
uv-experiment/05b2b7/evidence/accepted/private-index/attestation.json
uv-experiment/05b2b7/evidence/accepted/private-index/command.json
uv-experiment/05b2b7/evidence/accepted/private-index/container.json
uv-experiment/05b2b7/evidence/accepted/private-index/container.log
uv-experiment/05b2b7/evidence/accepted/private-index/image.json
uv-experiment/05b2b7/evidence/accepted/private-index/requests.json
uv-experiment/05b2b7/evidence/accepted/private-index/retained/b15c843137fdbe95b847caf43b92ea59c1addeb9b0cf16582c89dfea8cf7b9d8/inventory.json
uv-experiment/05b2b7/evidence/accepted/private-index/retained/b15c843137fdbe95b847caf43b92ea59c1addeb9b0cf16582c89dfea8cf7b9d8/manifest.json
uv-experiment/05b2b7/evidence/accepted/private-index/retained/b15c843137fdbe95b847caf43b92ea59c1addeb9b0cf16582c89dfea8cf7b9d8/private-indexes/alpha/private-core/private_core-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/retained/b15c843137fdbe95b847caf43b92ea59c1addeb9b0cf16582c89dfea8cf7b9d8/private-indexes/alpha/private-one/private_one-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/retained/b15c843137fdbe95b847caf43b92ea59c1addeb9b0cf16582c89dfea8cf7b9d8/private-indexes/alpha/private-two/private_two-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/retained/b15c843137fdbe95b847caf43b92ea59c1addeb9b0cf16582c89dfea8cf7b9d8/private-indexes/snapshot/private-core/index.html
uv-experiment/05b2b7/evidence/accepted/private-index/retained/b15c843137fdbe95b847caf43b92ea59c1addeb9b0cf16582c89dfea8cf7b9d8/private-indexes/snapshot/private-one/index.html
uv-experiment/05b2b7/evidence/accepted/private-index/retained/b15c843137fdbe95b847caf43b92ea59c1addeb9b0cf16582c89dfea8cf7b9d8/private-indexes/snapshot/private-two/index.html
uv-experiment/05b2b7/evidence/accepted/private-index/retained/b15c843137fdbe95b847caf43b92ea59c1addeb9b0cf16582c89dfea8cf7b9d8/private-sources.json
uv-experiment/05b2b7/evidence/accepted/private-index/retained/b15c843137fdbe95b847caf43b92ea59c1addeb9b0cf16582c89dfea8cf7b9d8/pyproject.toml
uv-experiment/05b2b7/evidence/accepted/private-index/retained/b15c843137fdbe95b847caf43b92ea59c1addeb9b0cf16582c89dfea8cf7b9d8/solve-project.toml
uv-experiment/05b2b7/evidence/accepted/private-index/retained/b15c843137fdbe95b847caf43b92ea59c1addeb9b0cf16582c89dfea8cf7b9d8/solve.lock
uv-experiment/05b2b7/evidence/accepted/private-index/retained/b15c843137fdbe95b847caf43b92ea59c1addeb9b0cf16582c89dfea8cf7b9d8/sources/private_core-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/retained/b15c843137fdbe95b847caf43b92ea59c1addeb9b0cf16582c89dfea8cf7b9d8/sources/private_one-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/retained/b15c843137fdbe95b847caf43b92ea59c1addeb9b0cf16582c89dfea8cf7b9d8/sources/private_two-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/retained/b15c843137fdbe95b847caf43b92ea59c1addeb9b0cf16582c89dfea8cf7b9d8/tool.json
uv-experiment/05b2b7/evidence/accepted/private-index/retained/b15c843137fdbe95b847caf43b92ea59c1addeb9b0cf16582c89dfea8cf7b9d8/uv.lock
uv-experiment/05b2b7/evidence/accepted/private-index/retained/b15c843137fdbe95b847caf43b92ea59c1addeb9b0cf16582c89dfea8cf7b9d8/wheels/private_core-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/retained/b15c843137fdbe95b847caf43b92ea59c1addeb9b0cf16582c89dfea8cf7b9d8/wheels/private_one-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/retained/b15c843137fdbe95b847caf43b92ea59c1addeb9b0cf16582c89dfea8cf7b9d8/wheels/private_two-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/retained/cb612318e0625d8d3bd385f215d05faa8fc1c5c3ad478cb5253073fa005336e7/inventory.json
uv-experiment/05b2b7/evidence/accepted/private-index/retained/cb612318e0625d8d3bd385f215d05faa8fc1c5c3ad478cb5253073fa005336e7/manifest.json
uv-experiment/05b2b7/evidence/accepted/private-index/retained/cb612318e0625d8d3bd385f215d05faa8fc1c5c3ad478cb5253073fa005336e7/private-indexes/alpha/private-core/private_core-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/retained/cb612318e0625d8d3bd385f215d05faa8fc1c5c3ad478cb5253073fa005336e7/private-indexes/alpha/private-one/private_one-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/retained/cb612318e0625d8d3bd385f215d05faa8fc1c5c3ad478cb5253073fa005336e7/private-indexes/beta/private-two/private_two-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/retained/cb612318e0625d8d3bd385f215d05faa8fc1c5c3ad478cb5253073fa005336e7/private-indexes/snapshot/private-core/index.html
uv-experiment/05b2b7/evidence/accepted/private-index/retained/cb612318e0625d8d3bd385f215d05faa8fc1c5c3ad478cb5253073fa005336e7/private-indexes/snapshot/private-one/index.html
uv-experiment/05b2b7/evidence/accepted/private-index/retained/cb612318e0625d8d3bd385f215d05faa8fc1c5c3ad478cb5253073fa005336e7/private-indexes/snapshot/private-two/index.html
uv-experiment/05b2b7/evidence/accepted/private-index/retained/cb612318e0625d8d3bd385f215d05faa8fc1c5c3ad478cb5253073fa005336e7/private-sources.json
uv-experiment/05b2b7/evidence/accepted/private-index/retained/cb612318e0625d8d3bd385f215d05faa8fc1c5c3ad478cb5253073fa005336e7/pyproject.toml
uv-experiment/05b2b7/evidence/accepted/private-index/retained/cb612318e0625d8d3bd385f215d05faa8fc1c5c3ad478cb5253073fa005336e7/solve-project.toml
uv-experiment/05b2b7/evidence/accepted/private-index/retained/cb612318e0625d8d3bd385f215d05faa8fc1c5c3ad478cb5253073fa005336e7/solve.lock
uv-experiment/05b2b7/evidence/accepted/private-index/retained/cb612318e0625d8d3bd385f215d05faa8fc1c5c3ad478cb5253073fa005336e7/sources/private_core-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/retained/cb612318e0625d8d3bd385f215d05faa8fc1c5c3ad478cb5253073fa005336e7/sources/private_one-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/retained/cb612318e0625d8d3bd385f215d05faa8fc1c5c3ad478cb5253073fa005336e7/sources/private_two-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/retained/cb612318e0625d8d3bd385f215d05faa8fc1c5c3ad478cb5253073fa005336e7/tool.json
uv-experiment/05b2b7/evidence/accepted/private-index/retained/cb612318e0625d8d3bd385f215d05faa8fc1c5c3ad478cb5253073fa005336e7/uv.lock
uv-experiment/05b2b7/evidence/accepted/private-index/retained/cb612318e0625d8d3bd385f215d05faa8fc1c5c3ad478cb5253073fa005336e7/wheels/private_core-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/retained/cb612318e0625d8d3bd385f215d05faa8fc1c5c3ad478cb5253073fa005336e7/wheels/private_one-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/retained/cb612318e0625d8d3bd385f215d05faa8fc1c5c3ad478cb5253073fa005336e7/wheels/private_two-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/retained/ea2f304c7d523d6978d40b8db7e4c23c99d82f160972e93c26c001d4b3383121/inventory.json
uv-experiment/05b2b7/evidence/accepted/private-index/retained/ea2f304c7d523d6978d40b8db7e4c23c99d82f160972e93c26c001d4b3383121/manifest.json
uv-experiment/05b2b7/evidence/accepted/private-index/retained/ea2f304c7d523d6978d40b8db7e4c23c99d82f160972e93c26c001d4b3383121/private-indexes/alpha/private-core/private_core-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/retained/ea2f304c7d523d6978d40b8db7e4c23c99d82f160972e93c26c001d4b3383121/private-indexes/alpha/private-one/private_one-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/retained/ea2f304c7d523d6978d40b8db7e4c23c99d82f160972e93c26c001d4b3383121/private-indexes/alpha/private-two/private_two-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/retained/ea2f304c7d523d6978d40b8db7e4c23c99d82f160972e93c26c001d4b3383121/private-indexes/snapshot/private-core/index.html
uv-experiment/05b2b7/evidence/accepted/private-index/retained/ea2f304c7d523d6978d40b8db7e4c23c99d82f160972e93c26c001d4b3383121/private-indexes/snapshot/private-one/index.html
uv-experiment/05b2b7/evidence/accepted/private-index/retained/ea2f304c7d523d6978d40b8db7e4c23c99d82f160972e93c26c001d4b3383121/private-indexes/snapshot/private-two/index.html
uv-experiment/05b2b7/evidence/accepted/private-index/retained/ea2f304c7d523d6978d40b8db7e4c23c99d82f160972e93c26c001d4b3383121/private-sources.json
uv-experiment/05b2b7/evidence/accepted/private-index/retained/ea2f304c7d523d6978d40b8db7e4c23c99d82f160972e93c26c001d4b3383121/pyproject.toml
uv-experiment/05b2b7/evidence/accepted/private-index/retained/ea2f304c7d523d6978d40b8db7e4c23c99d82f160972e93c26c001d4b3383121/solve-project.toml
uv-experiment/05b2b7/evidence/accepted/private-index/retained/ea2f304c7d523d6978d40b8db7e4c23c99d82f160972e93c26c001d4b3383121/solve.lock
uv-experiment/05b2b7/evidence/accepted/private-index/retained/ea2f304c7d523d6978d40b8db7e4c23c99d82f160972e93c26c001d4b3383121/sources/private_core-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/retained/ea2f304c7d523d6978d40b8db7e4c23c99d82f160972e93c26c001d4b3383121/sources/private_one-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/retained/ea2f304c7d523d6978d40b8db7e4c23c99d82f160972e93c26c001d4b3383121/sources/private_two-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/retained/ea2f304c7d523d6978d40b8db7e4c23c99d82f160972e93c26c001d4b3383121/tool.json
uv-experiment/05b2b7/evidence/accepted/private-index/retained/ea2f304c7d523d6978d40b8db7e4c23c99d82f160972e93c26c001d4b3383121/uv.lock
uv-experiment/05b2b7/evidence/accepted/private-index/retained/ea2f304c7d523d6978d40b8db7e4c23c99d82f160972e93c26c001d4b3383121/wheels/private_core-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/retained/ea2f304c7d523d6978d40b8db7e4c23c99d82f160972e93c26c001d4b3383121/wheels/private_one-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/retained/ea2f304c7d523d6978d40b8db7e4c23c99d82f160972e93c26c001d4b3383121/wheels/private_two-1.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/private-index/uv-version.txt
uv-experiment/05b2b7/evidence/accepted/recovery-command.json
uv-experiment/05b2b7/evidence/accepted/recovery.log
uv-experiment/05b2b7/evidence/accepted/recovery/aborted-status.json
uv-experiment/05b2b7/evidence/accepted/recovery/apply-uv-invocations.log
uv-experiment/05b2b7/evidence/accepted/recovery/attestation.json
uv-experiment/05b2b7/evidence/accepted/recovery/commands.json
uv-experiment/05b2b7/evidence/accepted/recovery/container.log
uv-experiment/05b2b7/evidence/accepted/recovery/first-preview.json
uv-experiment/05b2b7/evidence/accepted/recovery/image-runtime-uv.lock
uv-experiment/05b2b7/evidence/accepted/recovery/image.txt
uv-experiment/05b2b7/evidence/accepted/recovery/installed-inventory.json
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/accepted.lock
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/admission.json
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/admission.lock
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/applied.json
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/attempt-history/b8f62fb2-e0c9-4e3b-b54c-e64cc254998f.json
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/attempt-history/be874a73-2b1e-463e-bbfb-426c93d46ac4.json
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/callers/27b2f02e71b8a8ee47176ad73692fe119c015ac2ab1339f83594513f76357cc2
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/callers/cae115fdce3ebfb7bab999baed13de5d4b8c5110c47ab99faf1f378512d7e4c8
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/callers/cb718e67d5b8ecdfbd555224b8b6472d03254191c38bd5a6512fbc93e0e1bc16
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/callers/d7db66d92c358d61d437b94844ddb76cb81dce834c619bf701dda3b3b9c4b3f7
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/callers/d90acfb84fd3b9841e80b2e3a0c51ee91de5d1ebb541f0b4edca1a5b0d684cdf
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/callers/f8bbed6971bbe59a157688c12ea1006df009d2a94c0027e116057466e6271c48
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/desired.json
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/health/watchdog.json
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/infrastructure/accepted.lock
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/infrastructure/admission.json
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/infrastructure/admission.lock
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/infrastructure/role-activity/conversion.lock
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/infrastructure/role-activity/nltk.lock
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/infrastructure/role-activity/web.lock
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/infrastructure/role-activity/websockets.lock
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/infrastructure/runtime.lock
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/infrastructure/traffic.lock
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/initialized.json
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/installation-host.json
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/lifecycle.json
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/mutation.lock
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/publication-reported.json
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/role-activity/conversion.lock
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/role-activity/nltk.lock
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/role-activity/web.lock
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/role-activity/websockets.lock
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/runtime.lock
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/service-conversion.lock
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/service-nltk.lock
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/service-websockets.lock
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/traffic.lock
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/watchdog.lock
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/worker-normal.lock
uv-experiment/05b2b7/evidence/accepted/recovery/local-state/worker-single.lock
uv-experiment/05b2b7/evidence/accepted/recovery/logs/create-tables.log
uv-experiment/05b2b7/evidence/accepted/recovery/logs/docassemble.log
uv-experiment/05b2b7/evidence/accepted/recovery/logs/managed-mail.log
uv-experiment/05b2b7/evidence/accepted/recovery/logs/managed-service-conversion.log
uv-experiment/05b2b7/evidence/accepted/recovery/logs/managed-service-nltk.log
uv-experiment/05b2b7/evidence/accepted/recovery/logs/managed-service-websockets.log
uv-experiment/05b2b7/evidence/accepted/recovery/logs/managed-watchdog.log
uv-experiment/05b2b7/evidence/accepted/recovery/logs/managed-worker-normal.log
uv-experiment/05b2b7/evidence/accepted/recovery/logs/managed-worker-single.log
uv-experiment/05b2b7/evidence/accepted/recovery/logs/uv-sync-20261015-043616.log
uv-experiment/05b2b7/evidence/accepted/recovery/logs/uv-sync-20261015-043801.log
uv-experiment/05b2b7/evidence/accepted/recovery/logs/uv-sync-20261015-043901.log
uv-experiment/05b2b7/evidence/accepted/recovery/logs/uv-sync-20261015-043903.log
uv-experiment/05b2b7/evidence/accepted/recovery/logs/web-normal.log
uv-experiment/05b2b7/evidence/accepted/recovery/lost-report-local-status.json
uv-experiment/05b2b7/evidence/accepted/recovery/normal-local-status.json
uv-experiment/05b2b7/evidence/accepted/recovery/normal-terminal-journal.json
uv-experiment/05b2b7/evidence/accepted/recovery/published-bundle/candidate-wheels/peppercorn-0.6-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/recovery/published-bundle/candidate-wheels/sampleproject-4.0.0-py3-none-any.whl
uv-experiment/05b2b7/evidence/accepted/recovery/published-bundle/installed-material.json
uv-experiment/05b2b7/evidence/accepted/recovery/published-bundle/inventory.json
uv-experiment/05b2b7/evidence/accepted/recovery/published-bundle/manifest.json
uv-experiment/05b2b7/evidence/accepted/recovery/published-bundle/pyproject.toml
uv-experiment/05b2b7/evidence/accepted/recovery/published-bundle/sources/peppercorn-0.6.tar.gz
uv-experiment/05b2b7/evidence/accepted/recovery/published-bundle/sources/sampleproject-4.0.0.tar.gz
uv-experiment/05b2b7/evidence/accepted/recovery/published-bundle/tool.json
uv-experiment/05b2b7/evidence/accepted/recovery/published-bundle/uv.lock
uv-experiment/05b2b7/evidence/accepted/recovery/published-pending.json
uv-experiment/05b2b7/evidence/accepted/recovery/qualified-baseline.json
uv-experiment/05b2b7/evidence/accepted/recovery/recovery-durable-status.json
uv-experiment/05b2b7/evidence/accepted/recovery/recovery-installed-inventory.json
uv-experiment/05b2b7/evidence/accepted/recovery/recovery-local-status.json
uv-experiment/05b2b7/evidence/accepted/recovery/recovery-retry.log
uv-experiment/05b2b7/evidence/accepted/recovery/recovery-terminal-journal.json
uv-experiment/05b2b7/evidence/accepted/recovery/recovery-uv-invocations.log
uv-experiment/05b2b7/evidence/accepted/recovery/recovery.log
uv-experiment/05b2b7/evidence/accepted/recovery/release-consumer
uv-experiment/05b2b7/evidence/accepted/recovery/second-preview.json
uv-experiment/05b2b7/evidence/accepted/recovery/setup.log
uv-experiment/05b2b7/evidence/accepted/recovery/verified-running-status.json
uv-experiment/05b2b7/evidence/accepted/source-command.json
uv-experiment/05b2b7/evidence/accepted/source-hashes.json
uv-experiment/05b2b7/evidence/accepted/source.log
uv-experiment/05b2b7/evidence/accepted/verify-evidence.py
uv-experiment/TICKET-05a2b4.md
uv-experiment/TICKET-05b2b7.md
uv-experiment/preparation/private_index_proof.py
uv-experiment/preparation/tests/test_publication_profile_views_postgres.py
uv-experiment/run-private-index-proof.py

Diff and this summary intentionally UNTRACKED. Four task proof containers left intact; prior untracked artifacts preserved. Initial staged whitespace check failed only on copied service logs; normalization then recheck passed.
