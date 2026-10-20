# 05c2 implementation change summary

Commit: `d94b1d76c838e2435a754899588e76b86eb3c07b` on `uv-implementation`.
Review base: `ed1fa4cedd469c5450b3aaf41239a1001a68584b` (accepted bounded 05c1).

`diff.patch` is the primary requested **05c2 slice** (`git diff ed1fa4cedd469c5450b3aaf41239a1001a68584b..HEAD`). `combined-05c.patch` separately spans **05c1+05c2** (`git diff 4d54c9a1afa15eabe701c19ad090c90af598a073..HEAD`) for context. Both patches and this summary intentionally remain UNTRACKED. No staged files; no branch switch or new-system changes.

## Implemented

- Explicit operator opt-in image-core bridge: all-role target only, exact retained image wheels/versions/hashes, no core re-solve, rebuild or requirement relaxation. Default remains preview-only.
- Existing administrator requester, authenticated browser/API preview and approve, atomic 05b publication, durable executor claim, exact offline/non-root/frozen/no-build application and actual schema/core/web/all-12-role qualification before normal readiness/verified-running advancement.
- Restricted ingress permits only health/status, existing-user login/logout, static and preview endpoints. Test enumerates every declared non-allowlisted route (>100) and rejects GET/POST. Other writers/internal operator or plugin invocations are NOT thereby qualified.
- Fresh authority/grant rereads; unchanged accounts/roles/grants, no ownerless assignment or ownership transfer. Safe explicit retry only for the latest confirmed pre-sync abort. Uncertain, mutated, recovered or non-qualified retries remain rejected.
- Durable normal receipt/report replay rechecks retained bytes, live material and roles without repeating sync/schema. Truthful desired/current running/history/pending/failed projections; publication/preparation is never installation success.
- Handoff, split refinement, dated tracker Comments and qualified evidence. Parent 05 Status/checklist unchanged; 06 blocked; independent review required.

Resumed the same task's interrupted staged implementation rather than replacing it. Found the saved whitespace check was still failing on a trailing blank line in published.py; removed it, rebuilt and reran verification. Recaptured only task-owned evidence after cleaning its previous run directories to prevent nested docker-cp outputs/mixed receipts. No functional scope expansion or weakened assertion.

## Verification

Exact commands/output: `uv-experiment/05c2/evidence/accepted/`; detailed reproduction and limits: `uv-experiment/TICKET-05c2.md`.

- Final image: `da-uv-05c2:local`, `sha256:05e3695542d0f3c6d945e2e77d61047dfa3ea7c2b1c8bd7562e974367232c50f`. Built from final source working tree; not falsely labeled as built from the eventual documentation commit.
- Authenticated pinned-uv image-installed bridge: eight PASS groups, including real drain abort/safe retry, lost-success-report replay and failure-visible corrupt second publication.
- PostgreSQL/preparation: **447 passed** in 72.22s. Intentional +34 (17 scenarios across fresh/upgrade); no new skips.
- Stopped installation regression: original **seven PASS** groups.
- `act workflow_dispatch -W .act/checks.yml`: **313 passed, 1 existing Supervisor skip** in 31.46s; mypy **54 source files**, no issues. Intentional +5 tests/+2 source modules.
- Full-maintenance: original **15 PASS** on final image with bridge mode off; not a claim to rerun all 96 historical scenarios.
- Source-image verification: **609 current COPY inputs**, **654 host/build hashes**. Ruff E9/F passed. Source/doc whitespace check passed (generated evidence excluded).
- Independently rechecked complete stopped-bundle bytes and full first published bundle retained in proof container; normal attempt/revision/bundle/status and failed/history identities agree. Evidence layout has no nested previous-run directories.
- Six old status expectations intentionally changed from generic not-verified to truthful failed. No expected count assertions/exclusions weakened.

## Remaining fences

The exact named inventory below is copied from TICKET-05c2.md. No full 05c/parent-05 or production writer-safety acceptance is claimed.

- **05c3:** DB-bound one-attempt core recovery and failed/recovered terminal
  reporting; recovery must remain independently available without DB tables.
  No automatic recovery in this poller, uncertain retry/clearance or schema/setup
  replay after uncertainty. Failed-stopped/blocked receipts cannot roll into new
  mutations; production repair/recovery integration is not supplied here.
- Boot/restart DB reconciliation and boot/service-supervised continuous preparation
  and installation polling. Bridge-provisioned restart explicitly refuses, even
  before a local attempt journal; no fallback to fixture desired authority.
- Complete monotonic HostEnvironment/normal/history/core-only/override observation
  writers, config/dependency-input refresh before imports/work and unified live
  health/status observation outside this explicit attempt/report/replay lifecycle.
  Current DB normal state is the latest qualified observation, not a new universal
  continuous health-monitor guarantee. Broad production success UX is not enabled.
- Full production managed initialization/adoption, broader target/installed-material
  and immutable-store/source/build-policy qualification. The explicit experimental
  image-core wheel bridge is not adoption, coherent upgrade or general core solve.
- All remaining **05b2b6 writer composites/caller closure and actual-writer races**:
  users/views edit_user_profile_page, user_profile_page, user_add, custom_register,
  invite, add_privilege, manage_account, deletion/MFA/email/multi-commit composites;
  users/helpers login_or_register; users/signals on_user_login/on_register_hook/
  on_password_change; MySignInForm.validate LDAP/default-role creation;
  auth/views.oauth_callback; phonelogin/views.phone_login_verify; create_tables
  get_role/get_user/populate_tables account/default-role/fix-role passes and their
  fix_secret/substitute_secret/cookie/session/auth/external-hook/plugin composites.
- admin/views.config_page; startup.initialize/read_config; base/read_config and
  Docker/initialize.sh dependency/index/PYTHONPACKAGES observation; executor
  preparation/controller/CLI observation refresh; config.allowed, ALLOW_UPDATES,
  DEVELOPER_CAN_INSTALL and name/source/dependency policy writer serialization.
  Ingress restriction is NOT qualification of these writers or arbitrary operator
  edits; no config import is enabled.
- Remaining PackageAuth/grant/name/source/dependency-policy closure: utils/helpers
  install_zip/git/pip/uninstall; packages/views.update_package and api.api_package;
  develop/views.playground_packages ZIP caller; update clear_invalid_package/
  clear_invalid_package_auth/check_for_updates/add_dependencies/fix_names/
  install_package/uninstall_package/update_versions; tasks.update_packages/
  reset_server/direct update CLI; create_tables package rewrites/add_dependencies.
  Existing reject/skip fences and previous qualified helper slices remain intact.
- **05a2b4** real credential-source scope/injection, private indexes/bindings, other
  sources/targets/build policies; update/remove/config import/coherent upgrade/
  rollback/adoption/backup/image migration/general managed API breadth. No
  historical grants/intent restoration. No complete 05b/05c/parent-05 acceptance.


## Full changed-file inventory (259 files)

- `.scratch/uv-environment-implementation/issues/05-registry-install.md`
- `docassemble_base/docassemble/base/managed_preparation.py`
- `docassemble_base/docassemble/base/managed_publication.py`
- `docassemble_base/docassemble/base/managed_qualification.py`
- `docassemble_webapp/docassemble/webapp/packages/managed.py`
- `docassemble_webapp/docassemble/webapp/packages/templates/packages/preview.html`
- `docassemble_webapp/docassemble/webapp/packages/views.py`
- `uv-experiment/05c2/evidence/accepted/act.log`
- `uv-experiment/05c2/evidence/accepted/build.log`
- `uv-experiment/05c2/evidence/accepted/diff-check.log`
- `uv-experiment/05c2/evidence/accepted/image-bridge.log`
- `uv-experiment/05c2/evidence/accepted/image-bridge/aborted-status.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/apply-uv-invocations.log`
- `uv-experiment/05c2/evidence/accepted/image-bridge/attestation.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/commands.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/container.log`
- `uv-experiment/05c2/evidence/accepted/image-bridge/failed-durable-status.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/failed-visible-status.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/first-preview.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/flow.log`
- `uv-experiment/05c2/evidence/accepted/image-bridge/image-runtime-uv.lock`
- `uv-experiment/05c2/evidence/accepted/image-bridge/image.txt`
- `uv-experiment/05c2/evidence/accepted/image-bridge/installed-inventory.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/accepted.lock`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/admission.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/admission.lock`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/applied.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/attempt-history/01f4a916-52cd-4f0e-aacc-c95048fc95cb.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/attempt-history/41091867-02e1-49e4-9027-cbe5ca68db15.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/callers/42784130dd9383650534cf8baff65e2c93080f41755701d5fc42c0002452521a`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/callers/d10833b353c320e6feb623706ae2efdecc668058f4579f1fb6c68b0d05a263d9`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/desired.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/health/watchdog.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/infrastructure/accepted.lock`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/infrastructure/admission.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/infrastructure/admission.lock`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/infrastructure/role-activity/conversion.lock`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/infrastructure/role-activity/nltk.lock`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/infrastructure/role-activity/web.lock`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/infrastructure/role-activity/websockets.lock`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/infrastructure/runtime.lock`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/infrastructure/traffic.lock`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/initialized.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/installation-host.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/lifecycle.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/mutation.lock`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/publication-reported.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/role-activity/conversion.lock`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/role-activity/nltk.lock`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/role-activity/web.lock`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/role-activity/websockets.lock`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/runtime.lock`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/service-conversion.lock`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/service-nltk.lock`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/service-websockets.lock`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/traffic.lock`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/watchdog.lock`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/worker-normal.lock`
- `uv-experiment/05c2/evidence/accepted/image-bridge/local-state/worker-single.lock`
- `uv-experiment/05c2/evidence/accepted/image-bridge/logs/create-tables.log`
- `uv-experiment/05c2/evidence/accepted/image-bridge/logs/docassemble.log`
- `uv-experiment/05c2/evidence/accepted/image-bridge/logs/managed-mail.log`
- `uv-experiment/05c2/evidence/accepted/image-bridge/logs/managed-service-conversion.log`
- `uv-experiment/05c2/evidence/accepted/image-bridge/logs/managed-service-nltk.log`
- `uv-experiment/05c2/evidence/accepted/image-bridge/logs/managed-service-websockets.log`
- `uv-experiment/05c2/evidence/accepted/image-bridge/logs/managed-watchdog.log`
- `uv-experiment/05c2/evidence/accepted/image-bridge/logs/managed-worker-normal.log`
- `uv-experiment/05c2/evidence/accepted/image-bridge/logs/managed-worker-single.log`
- `uv-experiment/05c2/evidence/accepted/image-bridge/logs/uv-sync-20261015-003713.log`
- `uv-experiment/05c2/evidence/accepted/image-bridge/logs/uv-sync-20261015-003855.log`
- `uv-experiment/05c2/evidence/accepted/image-bridge/logs/web-normal.log`
- `uv-experiment/05c2/evidence/accepted/image-bridge/lost-report-local-status.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/normal-local-status.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/normal-terminal-journal.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/published-bundle/candidate-wheels/peppercorn-0.6-py3-none-any.whl`
- `uv-experiment/05c2/evidence/accepted/image-bridge/published-bundle/candidate-wheels/sampleproject-4.0.0-py3-none-any.whl`
- `uv-experiment/05c2/evidence/accepted/image-bridge/published-bundle/installed-material.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/published-bundle/inventory.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/published-bundle/manifest.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/published-bundle/pyproject.toml`
- `uv-experiment/05c2/evidence/accepted/image-bridge/published-bundle/sources/peppercorn-0.6.tar.gz`
- `uv-experiment/05c2/evidence/accepted/image-bridge/published-bundle/sources/sampleproject-4.0.0.tar.gz`
- `uv-experiment/05c2/evidence/accepted/image-bridge/published-bundle/tool.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/published-bundle/uv.lock`
- `uv-experiment/05c2/evidence/accepted/image-bridge/published-pending.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/qualified-baseline.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/release-consumer`
- `uv-experiment/05c2/evidence/accepted/image-bridge/second-preview.json`
- `uv-experiment/05c2/evidence/accepted/image-bridge/setup.log`
- `uv-experiment/05c2/evidence/accepted/image-bridge/verified-running-status.json`
- `uv-experiment/05c2/evidence/accepted/installation.log`
- `uv-experiment/05c2/evidence/accepted/installation/attestation.json`
- `uv-experiment/05c2/evidence/accepted/installation/baseline.json`
- `uv-experiment/05c2/evidence/accepted/installation/command.json`
- `uv-experiment/05c2/evidence/accepted/installation/config.yml`
- `uv-experiment/05c2/evidence/accepted/installation/container.log`
- `uv-experiment/05c2/evidence/accepted/installation/image.txt`
- `uv-experiment/05c2/evidence/accepted/installation/installed-inventory.json`
- `uv-experiment/05c2/evidence/accepted/installation/logs/uv-sync-20261015-004013.log`
- `uv-experiment/05c2/evidence/accepted/installation/preparation-uv-invocations.log`
- `uv-experiment/05c2/evidence/accepted/installation/publication-status.json`
- `uv-experiment/05c2/evidence/accepted/installation/retained/99342225edb4f2443b06bedb4b11fdffc275c7b1c625f2b317ec4be6883b7a50/inventory.json`
- `uv-experiment/05c2/evidence/accepted/installation/retained/99342225edb4f2443b06bedb4b11fdffc275c7b1c625f2b317ec4be6883b7a50/manifest.json`
- `uv-experiment/05c2/evidence/accepted/installation/retained/99342225edb4f2443b06bedb4b11fdffc275c7b1c625f2b317ec4be6883b7a50/pyproject.toml`
- `uv-experiment/05c2/evidence/accepted/installation/retained/99342225edb4f2443b06bedb4b11fdffc275c7b1c625f2b317ec4be6883b7a50/solve-project.toml`
- `uv-experiment/05c2/evidence/accepted/installation/retained/99342225edb4f2443b06bedb4b11fdffc275c7b1c625f2b317ec4be6883b7a50/solve.lock`
- `uv-experiment/05c2/evidence/accepted/installation/retained/99342225edb4f2443b06bedb4b11fdffc275c7b1c625f2b317ec4be6883b7a50/sources/packaging-25.0.tar.gz`
- `uv-experiment/05c2/evidence/accepted/installation/retained/99342225edb4f2443b06bedb4b11fdffc275c7b1c625f2b317ec4be6883b7a50/sources/six-1.17.0.tar.gz`
- `uv-experiment/05c2/evidence/accepted/installation/retained/99342225edb4f2443b06bedb4b11fdffc275c7b1c625f2b317ec4be6883b7a50/tool.json`
- `uv-experiment/05c2/evidence/accepted/installation/retained/99342225edb4f2443b06bedb4b11fdffc275c7b1c625f2b317ec4be6883b7a50/uv.lock`
- `uv-experiment/05c2/evidence/accepted/installation/retained/99342225edb4f2443b06bedb4b11fdffc275c7b1c625f2b317ec4be6883b7a50/wheels/.gitignore`
- `uv-experiment/05c2/evidence/accepted/installation/retained/99342225edb4f2443b06bedb4b11fdffc275c7b1c625f2b317ec4be6883b7a50/wheels/packaging-25.0-py3-none-any.whl`
- `uv-experiment/05c2/evidence/accepted/installation/retained/99342225edb4f2443b06bedb4b11fdffc275c7b1c625f2b317ec4be6883b7a50/wheels/six-1.17.0-py2.py3-none-any.whl`
- `uv-experiment/05c2/evidence/accepted/installation/run/recovery/status.json`
- `uv-experiment/05c2/evidence/accepted/installation/state/admission.json`
- `uv-experiment/05c2/evidence/accepted/installation/state/admission.lock`
- `uv-experiment/05c2/evidence/accepted/installation/state/installation-host.json`
- `uv-experiment/05c2/evidence/accepted/installation/state/lifecycle.json`
- `uv-experiment/05c2/evidence/accepted/installation/state/mutation.lock`
- `uv-experiment/05c2/evidence/accepted/installation/state/runtime.lock`
- `uv-experiment/05c2/evidence/accepted/installation/state/traffic.lock`
- `uv-experiment/05c2/evidence/accepted/installation/terminal-journal.json`
- `uv-experiment/05c2/evidence/accepted/installation/uv-invocations.log`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance.log`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/actual-independent-syslog-during-restricted-recovery/admission.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/actual-independent-syslog-during-restricted-recovery/applied.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/actual-independent-syslog-during-restricted-recovery/desired.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/actual-independent-syslog-during-restricted-recovery/lifecycle.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/actual-independent-syslog-during-restricted-recovery/logs.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/actual-independent-syslog-during-restricted-recovery/processes.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/actual-independent-syslog-during-restricted-recovery/status.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/broken-runtime-independent-infrastructure-shutdown/admission.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/broken-runtime-independent-infrastructure-shutdown/applied.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/broken-runtime-independent-infrastructure-shutdown/desired.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/broken-runtime-independent-infrastructure-shutdown/lifecycle.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/broken-runtime-independent-infrastructure-shutdown/logs.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/broken-runtime-independent-infrastructure-shutdown/processes.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/broken-runtime-independent-infrastructure-shutdown/status.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/broken-runtime-shutdown.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/cold-boot-real-protocol-readiness-before-applied-and-admission/admission.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/cold-boot-real-protocol-readiness-before-applied-and-admission/applied.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/cold-boot-real-protocol-readiness-before-applied-and-admission/desired.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/cold-boot-real-protocol-readiness-before-applied-and-admission/lifecycle.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/cold-boot-real-protocol-readiness-before-applied-and-admission/logs.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/cold-boot-real-protocol-readiness-before-applied-and-admission/processes.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/cold-boot-real-protocol-readiness-before-applied-and-admission/status.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/development-docker-and-container-exec-scripts/admission.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/development-docker-and-container-exec-scripts/applied.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/development-docker-and-container-exec-scripts/desired.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/development-docker-and-container-exec-scripts/lifecycle.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/development-docker-and-container-exec-scripts/logs.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/development-docker-and-container-exec-scripts/processes.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/development-docker-and-container-exec-scripts/status.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/development-install.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/development-script-container.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/development-script-docker.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/development-timeout-resumes-unchanged-override/admission.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/development-timeout-resumes-unchanged-override/applied.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/development-timeout-resumes-unchanged-override/desired.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/development-timeout-resumes-unchanged-override/lifecycle.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/development-timeout-resumes-unchanged-override/logs.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/development-timeout-resumes-unchanged-override/processes.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/development-timeout-resumes-unchanged-override/status.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/explicit-frozen-sync-restores-recorded-lock/admission.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/explicit-frozen-sync-restores-recorded-lock/applied.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/explicit-frozen-sync-restores-recorded-lock/desired.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/explicit-frozen-sync-restores-recorded-lock/lifecycle.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/explicit-frozen-sync-restores-recorded-lock/logs.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/explicit-frozen-sync-restores-recorded-lock/processes.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/explicit-frozen-sync-restores-recorded-lock/status.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/failed-override-one-attempt-restricted-core-recovery/admission.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/failed-override-one-attempt-restricted-core-recovery/applied.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/failed-override-one-attempt-restricted-core-recovery/desired.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/failed-override-one-attempt-restricted-core-recovery/lifecycle.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/failed-override-one-attempt-restricted-core-recovery/logs.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/failed-override-one-attempt-restricted-core-recovery/processes.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/failed-override-one-attempt-restricted-core-recovery/status.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/idle-role-death-reverified-before-normal-readiness/admission.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/idle-role-death-reverified-before-normal-readiness/applied.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/idle-role-death-reverified-before-normal-readiness/desired.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/idle-role-death-reverified-before-normal-readiness/lifecycle.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/idle-role-death-reverified-before-normal-readiness/logs.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/idle-role-death-reverified-before-normal-readiness/processes.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/idle-role-death-reverified-before-normal-readiness/status.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/image-id.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/interrupted-development-detached-fd-closing-child-exclusion/admission.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/interrupted-development-detached-fd-closing-child-exclusion/applied.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/interrupted-development-detached-fd-closing-child-exclusion/desired.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/interrupted-development-detached-fd-closing-child-exclusion/lifecycle.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/interrupted-development-detached-fd-closing-child-exclusion/logs.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/interrupted-development-detached-fd-closing-child-exclusion/processes.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/interrupted-development-detached-fd-closing-child-exclusion/status.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/non-root-exclusive-development-install/admission.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/non-root-exclusive-development-install/applied.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/non-root-exclusive-development-install/desired.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/non-root-exclusive-development-install/lifecycle.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/non-root-exclusive-development-install/logs.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/non-root-exclusive-development-install/processes.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/non-root-exclusive-development-install/status.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/ordinary-web-reload-preserves-override-without-sync/admission.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/ordinary-web-reload-preserves-override-without-sync/applied.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/ordinary-web-reload-preserves-override-without-sync/desired.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/ordinary-web-reload-preserves-override-without-sync/lifecycle.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/ordinary-web-reload-preserves-override-without-sync/logs.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/ordinary-web-reload-preserves-override-without-sync/processes.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/ordinary-web-reload-preserves-override-without-sync/status.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/os-only-restart-retains-redis-and-database/admission.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/os-only-restart-retains-redis-and-database/applied.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/os-only-restart-retains-redis-and-database/desired.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/os-only-restart-retains-redis-and-database/lifecycle.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/os-only-restart-retains-redis-and-database/logs.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/os-only-restart-retains-redis-and-database/processes.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/os-only-restart-retains-redis-and-database/status.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/pid1-shutdown/logs.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/preparing/status.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/results.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/shutdown-timeout-preserves-work-and-infrastructure/admission.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/shutdown-timeout-preserves-work-and-infrastructure/applied.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/shutdown-timeout-preserves-work-and-infrastructure/desired.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/shutdown-timeout-preserves-work-and-infrastructure/lifecycle.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/shutdown-timeout-preserves-work-and-infrastructure/logs.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/shutdown-timeout-preserves-work-and-infrastructure/processes.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/shutdown-timeout-preserves-work-and-infrastructure/status.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/whole-supported-all-role-readiness/admission.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/whole-supported-all-role-readiness/applied.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/whole-supported-all-role-readiness/desired.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/whole-supported-all-role-readiness/lifecycle.json`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/whole-supported-all-role-readiness/logs.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/whole-supported-all-role-readiness/processes.txt`
- `uv-experiment/05c2/evidence/accepted/lifecycle-full-maintenance/whole-supported-all-role-readiness/status.json`
- `uv-experiment/05c2/evidence/accepted/lint.log`
- `uv-experiment/05c2/evidence/accepted/postgres.log`
- `uv-experiment/05c2/evidence/accepted/source-image-verification.json`
- `uv-experiment/05c2/evidence/accepted/source-sha256.json`
- `uv-experiment/05c2/evidence/accepted/source-verification.log`
- `uv-experiment/05c2/evidence/accepted/verify-source.py`
- `uv-experiment/TICKET-05-SPLIT.md`
- `uv-experiment/TICKET-05c2.md`
- `uv-experiment/executor/pyproject.toml`
- `uv-experiment/executor/src/docassemble_executor/admission.py`
- `uv-experiment/executor/src/docassemble_executor/cli.py`
- `uv-experiment/executor/src/docassemble_executor/controller.py`
- `uv-experiment/executor/src/docassemble_executor/image_baseline.py`
- `uv-experiment/executor/src/docassemble_executor/installation.py`
- `uv-experiment/executor/src/docassemble_executor/inventory.py`
- `uv-experiment/executor/src/docassemble_executor/preparation_poller.py`
- `uv-experiment/executor/src/docassemble_executor/published.py`
- `uv-experiment/executor/src/docassemble_executor/registry_builder.py`
- `uv-experiment/executor/src/docassemble_executor/startup.py`
- `uv-experiment/executor/src/docassemble_executor/status.py`
- `uv-experiment/executor/src/docassemble_executor/webapp.py`
- `uv-experiment/executor/tests/test_executor_packaging.py`
- `uv-experiment/executor/tests/test_image_bridge.py`
- `uv-experiment/fixture/Dockerfile`
- `uv-experiment/preparation/image_bridge_proof.py`
- `uv-experiment/preparation/tests/test_image_bridge_postgres.py`
- `uv-experiment/preparation/tests/test_publication_retry_postgres.py`
- `uv-experiment/run-image-bridge-proof.py`
- `uv-experiment/run-installation-proof.py`

Next: independent 05c2 review, then 05c3 DB-bound lifecycle recovery/terminal reporting; all named fences remain.
