# 05a2b4 bounded private wheel snapshot change summary

Commit: 3a774a5f6784461ba9b25a168aa29674382e7434 on uv-implementation; baseline 50e180461.

Implemented supervisor-approved HTTPS private wheel snapshots, independent exact-source-scoped restricted operator references, explicit direct/transitive bindings, offline pinned-uv resolution/frozen materialization, redacted readiness source warnings and real HTTPS/source-isolation/redaction/TLS evidence. No public private-source dispatch or candidate promotion enabled.

Validation: real HTTPS pinned uv0.12.21 nine PASS groups; Docker candidate-secret cleanup four PASS groups; PostgreSQL/preparation509 PASS; act372 PASS/one existing skip/mypy57; native44 PASS; source/retained bytes/redaction and lint PASS.

Changed paths (115):
- `.scratch/uv-environment-implementation/issues/05-registry-install.md`
- `docassemble_base/docassemble/base/managed_preparation.py`
- `uv-experiment/05a2b4/evidence/accepted/act-command.json`
- `uv-experiment/05a2b4/evidence/accepted/act.log`
- `uv-experiment/05a2b4/evidence/accepted/capture.py`
- `uv-experiment/05a2b4/evidence/accepted/cleanup-command.json`
- `uv-experiment/05a2b4/evidence/accepted/cleanup.log`
- `uv-experiment/05a2b4/evidence/accepted/cleanup/attestation.json`
- `uv-experiment/05a2b4/evidence/accepted/cleanup/receipts/101eb176-b750-417a-8815-8615153449f0/lock`
- `uv-experiment/05a2b4/evidence/accepted/cleanup/receipts/101eb176-b750-417a-8815-8615153449f0/receipt.json`
- `uv-experiment/05a2b4/evidence/accepted/cleanup/receipts/9b46df81-6b95-4c14-a3fe-9aab73956366/inputs.json`
- `uv-experiment/05a2b4/evidence/accepted/cleanup/receipts/9b46df81-6b95-4c14-a3fe-9aab73956366/lock`
- `uv-experiment/05a2b4/evidence/accepted/cleanup/receipts/9b46df81-6b95-4c14-a3fe-9aab73956366/receipt.json`
- `uv-experiment/05a2b4/evidence/accepted/cleanup/receipts/9b46df81-6b95-4c14-a3fe-9aab73956366/result.json`
- `uv-experiment/05a2b4/evidence/accepted/cleanup/retained/77e6f9ea097cdcab84147be365d463c23dece28444dfcb5e0e075147075aeacb/inventory.json`
- `uv-experiment/05a2b4/evidence/accepted/cleanup/retained/77e6f9ea097cdcab84147be365d463c23dece28444dfcb5e0e075147075aeacb/manifest.json`
- `uv-experiment/05a2b4/evidence/accepted/cleanup/retained/77e6f9ea097cdcab84147be365d463c23dece28444dfcb5e0e075147075aeacb/pyproject.toml`
- `uv-experiment/05a2b4/evidence/accepted/cleanup/retained/77e6f9ea097cdcab84147be365d463c23dece28444dfcb5e0e075147075aeacb/solve-project.toml`
- `uv-experiment/05a2b4/evidence/accepted/cleanup/retained/77e6f9ea097cdcab84147be365d463c23dece28444dfcb5e0e075147075aeacb/solve.lock`
- `uv-experiment/05a2b4/evidence/accepted/cleanup/retained/77e6f9ea097cdcab84147be365d463c23dece28444dfcb5e0e075147075aeacb/sources/packaging-25.0.tar.gz`
- `uv-experiment/05a2b4/evidence/accepted/cleanup/retained/77e6f9ea097cdcab84147be365d463c23dece28444dfcb5e0e075147075aeacb/sources/six-1.17.0.tar.gz`
- `uv-experiment/05a2b4/evidence/accepted/cleanup/retained/77e6f9ea097cdcab84147be365d463c23dece28444dfcb5e0e075147075aeacb/tool.json`
- `uv-experiment/05a2b4/evidence/accepted/cleanup/retained/77e6f9ea097cdcab84147be365d463c23dece28444dfcb5e0e075147075aeacb/uv.lock`
- `uv-experiment/05a2b4/evidence/accepted/cleanup/retained/77e6f9ea097cdcab84147be365d463c23dece28444dfcb5e0e075147075aeacb/wheels/.gitignore`
- `uv-experiment/05a2b4/evidence/accepted/cleanup/retained/77e6f9ea097cdcab84147be365d463c23dece28444dfcb5e0e075147075aeacb/wheels/packaging-25.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/cleanup/retained/77e6f9ea097cdcab84147be365d463c23dece28444dfcb5e0e075147075aeacb/wheels/six-1.17.0-py2.py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/lint-command.json`
- `uv-experiment/05a2b4/evidence/accepted/lint.log`
- `uv-experiment/05a2b4/evidence/accepted/native-command.json`
- `uv-experiment/05a2b4/evidence/accepted/native.log`
- `uv-experiment/05a2b4/evidence/accepted/postgres-command.json`
- `uv-experiment/05a2b4/evidence/accepted/postgres.log`
- `uv-experiment/05a2b4/evidence/accepted/private-index-command.json`
- `uv-experiment/05a2b4/evidence/accepted/private-index.log`
- `uv-experiment/05a2b4/evidence/accepted/source-command.json`
- `uv-experiment/05a2b4/evidence/accepted/source-hashes.json`
- `uv-experiment/05a2b4/evidence/accepted/source.log`
- `uv-experiment/05a2b4/evidence/accepted/source/attestation.json`
- `uv-experiment/05a2b4/evidence/accepted/source/command.json`
- `uv-experiment/05a2b4/evidence/accepted/source/container.json`
- `uv-experiment/05a2b4/evidence/accepted/source/container.log`
- `uv-experiment/05a2b4/evidence/accepted/source/image.json`
- `uv-experiment/05a2b4/evidence/accepted/source/requests.json`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/96d36621b1b5332cb3b1d9a23f36f760c38991d3155d8c84367d0711f001ab4d/inventory.json`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/96d36621b1b5332cb3b1d9a23f36f760c38991d3155d8c84367d0711f001ab4d/manifest.json`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/96d36621b1b5332cb3b1d9a23f36f760c38991d3155d8c84367d0711f001ab4d/private-indexes/alpha/private-core/private_core-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/96d36621b1b5332cb3b1d9a23f36f760c38991d3155d8c84367d0711f001ab4d/private-indexes/alpha/private-one/private_one-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/96d36621b1b5332cb3b1d9a23f36f760c38991d3155d8c84367d0711f001ab4d/private-indexes/alpha/private-two/private_two-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/96d36621b1b5332cb3b1d9a23f36f760c38991d3155d8c84367d0711f001ab4d/private-indexes/snapshot/private-core/index.html`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/96d36621b1b5332cb3b1d9a23f36f760c38991d3155d8c84367d0711f001ab4d/private-indexes/snapshot/private-one/index.html`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/96d36621b1b5332cb3b1d9a23f36f760c38991d3155d8c84367d0711f001ab4d/private-indexes/snapshot/private-two/index.html`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/96d36621b1b5332cb3b1d9a23f36f760c38991d3155d8c84367d0711f001ab4d/private-sources.json`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/96d36621b1b5332cb3b1d9a23f36f760c38991d3155d8c84367d0711f001ab4d/pyproject.toml`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/96d36621b1b5332cb3b1d9a23f36f760c38991d3155d8c84367d0711f001ab4d/solve-project.toml`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/96d36621b1b5332cb3b1d9a23f36f760c38991d3155d8c84367d0711f001ab4d/solve.lock`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/96d36621b1b5332cb3b1d9a23f36f760c38991d3155d8c84367d0711f001ab4d/sources/private_core-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/96d36621b1b5332cb3b1d9a23f36f760c38991d3155d8c84367d0711f001ab4d/sources/private_one-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/96d36621b1b5332cb3b1d9a23f36f760c38991d3155d8c84367d0711f001ab4d/sources/private_two-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/96d36621b1b5332cb3b1d9a23f36f760c38991d3155d8c84367d0711f001ab4d/tool.json`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/96d36621b1b5332cb3b1d9a23f36f760c38991d3155d8c84367d0711f001ab4d/uv.lock`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/96d36621b1b5332cb3b1d9a23f36f760c38991d3155d8c84367d0711f001ab4d/wheels/private_core-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/96d36621b1b5332cb3b1d9a23f36f760c38991d3155d8c84367d0711f001ab4d/wheels/private_one-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/96d36621b1b5332cb3b1d9a23f36f760c38991d3155d8c84367d0711f001ab4d/wheels/private_two-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a6348b15fe02988c5fb4c7b5d1530e9e211eea6b4a1ce1a91104eb1e62412ff1/inventory.json`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a6348b15fe02988c5fb4c7b5d1530e9e211eea6b4a1ce1a91104eb1e62412ff1/manifest.json`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a6348b15fe02988c5fb4c7b5d1530e9e211eea6b4a1ce1a91104eb1e62412ff1/private-indexes/alpha/private-core/private_core-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a6348b15fe02988c5fb4c7b5d1530e9e211eea6b4a1ce1a91104eb1e62412ff1/private-indexes/alpha/private-one/private_one-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a6348b15fe02988c5fb4c7b5d1530e9e211eea6b4a1ce1a91104eb1e62412ff1/private-indexes/alpha/private-two/private_two-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a6348b15fe02988c5fb4c7b5d1530e9e211eea6b4a1ce1a91104eb1e62412ff1/private-indexes/snapshot/private-core/index.html`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a6348b15fe02988c5fb4c7b5d1530e9e211eea6b4a1ce1a91104eb1e62412ff1/private-indexes/snapshot/private-one/index.html`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a6348b15fe02988c5fb4c7b5d1530e9e211eea6b4a1ce1a91104eb1e62412ff1/private-indexes/snapshot/private-two/index.html`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a6348b15fe02988c5fb4c7b5d1530e9e211eea6b4a1ce1a91104eb1e62412ff1/private-sources.json`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a6348b15fe02988c5fb4c7b5d1530e9e211eea6b4a1ce1a91104eb1e62412ff1/pyproject.toml`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a6348b15fe02988c5fb4c7b5d1530e9e211eea6b4a1ce1a91104eb1e62412ff1/solve-project.toml`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a6348b15fe02988c5fb4c7b5d1530e9e211eea6b4a1ce1a91104eb1e62412ff1/solve.lock`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a6348b15fe02988c5fb4c7b5d1530e9e211eea6b4a1ce1a91104eb1e62412ff1/sources/private_core-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a6348b15fe02988c5fb4c7b5d1530e9e211eea6b4a1ce1a91104eb1e62412ff1/sources/private_one-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a6348b15fe02988c5fb4c7b5d1530e9e211eea6b4a1ce1a91104eb1e62412ff1/sources/private_two-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a6348b15fe02988c5fb4c7b5d1530e9e211eea6b4a1ce1a91104eb1e62412ff1/tool.json`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a6348b15fe02988c5fb4c7b5d1530e9e211eea6b4a1ce1a91104eb1e62412ff1/uv.lock`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a6348b15fe02988c5fb4c7b5d1530e9e211eea6b4a1ce1a91104eb1e62412ff1/wheels/private_core-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a6348b15fe02988c5fb4c7b5d1530e9e211eea6b4a1ce1a91104eb1e62412ff1/wheels/private_one-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a6348b15fe02988c5fb4c7b5d1530e9e211eea6b4a1ce1a91104eb1e62412ff1/wheels/private_two-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a942465b1acecc7ba573822b012dd471f064ee8822d7011428419db8944165c0/inventory.json`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a942465b1acecc7ba573822b012dd471f064ee8822d7011428419db8944165c0/manifest.json`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a942465b1acecc7ba573822b012dd471f064ee8822d7011428419db8944165c0/private-indexes/alpha/private-core/private_core-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a942465b1acecc7ba573822b012dd471f064ee8822d7011428419db8944165c0/private-indexes/alpha/private-one/private_one-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a942465b1acecc7ba573822b012dd471f064ee8822d7011428419db8944165c0/private-indexes/beta/private-two/private_two-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a942465b1acecc7ba573822b012dd471f064ee8822d7011428419db8944165c0/private-indexes/snapshot/private-core/index.html`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a942465b1acecc7ba573822b012dd471f064ee8822d7011428419db8944165c0/private-indexes/snapshot/private-one/index.html`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a942465b1acecc7ba573822b012dd471f064ee8822d7011428419db8944165c0/private-indexes/snapshot/private-two/index.html`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a942465b1acecc7ba573822b012dd471f064ee8822d7011428419db8944165c0/private-sources.json`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a942465b1acecc7ba573822b012dd471f064ee8822d7011428419db8944165c0/pyproject.toml`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a942465b1acecc7ba573822b012dd471f064ee8822d7011428419db8944165c0/solve-project.toml`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a942465b1acecc7ba573822b012dd471f064ee8822d7011428419db8944165c0/solve.lock`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a942465b1acecc7ba573822b012dd471f064ee8822d7011428419db8944165c0/sources/private_core-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a942465b1acecc7ba573822b012dd471f064ee8822d7011428419db8944165c0/sources/private_one-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a942465b1acecc7ba573822b012dd471f064ee8822d7011428419db8944165c0/sources/private_two-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a942465b1acecc7ba573822b012dd471f064ee8822d7011428419db8944165c0/tool.json`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a942465b1acecc7ba573822b012dd471f064ee8822d7011428419db8944165c0/uv.lock`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a942465b1acecc7ba573822b012dd471f064ee8822d7011428419db8944165c0/wheels/private_core-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a942465b1acecc7ba573822b012dd471f064ee8822d7011428419db8944165c0/wheels/private_one-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/retained/a942465b1acecc7ba573822b012dd471f064ee8822d7011428419db8944165c0/wheels/private_two-1.0-py3-none-any.whl`
- `uv-experiment/05a2b4/evidence/accepted/source/uv-version.txt`
- `uv-experiment/05a2b4/evidence/accepted/verify-evidence.py`
- `uv-experiment/TICKET-05-SPLIT.md`
- `uv-experiment/TICKET-05a2b4.md`
- `uv-experiment/executor/src/docassemble_executor/private_index.py`
- `uv-experiment/executor/src/docassemble_executor/registry_builder.py`
- `uv-experiment/executor/tests/test_private_index.py`
- `uv-experiment/preparation/README.md`
- `uv-experiment/preparation/container_cleanup_proof.py`
- `uv-experiment/preparation/private_index_proof.py`
- `uv-experiment/preparation/tests/test_private_index_postgres.py`
- `uv-experiment/run-private-index-proof.py`

## Remaining fences


Implemented the authorized end-to-end **private wheel snapshot preparation**
fallback, not a plan-only handoff. `ticket-05a2b4/diff.patch` spans
`50e180461..HEAD`; it and `change-summary.md` are intentionally untracked.

**05a2b5 source remainder:** HTTPS Git refs/subdirectories/SHA identity and scoped
credential injection; private sdists/build-dependency authentication/source
builds; candidate-secret claim consumption by this builder and atomic
candidate-to-committed promotion/rotation/revocation; authorized updater reuse
without viewing via public adapters; production private policy configuration,
source authorization and dispatch; mixed public/private solving and broader
index ordering; direct wheel/archive URLs and immutable uploaded ZIPs; broader
source/target/build policy and aggregate source-parity qualification. Existing
candidate staging/cleanup remains usable only through its claim protocol; do not
hand its paths to this operator-reference consumer.

**Existing 05c5b operational remainder unchanged:** universal live observations/
all-role readiness/lost-success closure, cold uncertain/unreported recovery and
management-tables-unavailable boot restoration, restricted core-only resumption,
broader failed/recovered/crash rollover, explicit uncertain-work/schema/setup
clearance, config/dependency/policy refresh.

**Named remaining 05b2b6 writer composites/caller closure and races unchanged:**
users/views edit_user_profile_page, user_profile_page, user_add, custom_register,
invite, add_privilege, manage_account, deletion/MFA/email/multi-commit composites;
users/helpers login_or_register; users/signals on_user_login/on_register_hook/
on_password_change; MySignInForm.validate LDAP/default-role creation;
auth/views.oauth_callback; phonelogin/views.phone_login_verify; create_tables
get_role/get_user/populate_tables account/default-role/fix-role passes and
fix_secret/substitute_secret/cookie/session/auth/external-hook/plugin composites;
admin/views.config_page; startup.initialize/read_config; base/read_config and
Docker/initialize.sh dependency/index/PYTHONPACKAGES observation; executor
preparation/controller/CLI dependency/policy refresh; config.allowed,
ALLOW_UPDATES, DEVELOPER_CAN_INSTALL and name/source/dependency-policy writers;
utils/helpers install_zip/git/pip/uninstall; packages/views.update_package and
api.api_package; develop/views.playground_packages ZIP caller; update
clear_invalid_package/clear_invalid_package_auth/check_for_updates/
add_dependencies/fix_names/install_package/uninstall_package/update_versions;
tasks.update_packages/reset_server/direct update CLI; create_tables package
rewrites/add_dependencies and remaining PackageAuth/grant/source closure.

Production managed initialization/adoption, broader installed-material/store
qualification, update/remove/config import, coherent upgrade/rollback/adoption/
backup/image migration/general managed API breadth and **tickets 06+** stay fenced.
No new-system edits, branch switch, writer-composite closure or full
05a2b4/05a/05b/05c/parent-05 acceptance is claimed. Review gate remains required.

Next: 05a2b5 — remaining credential lifecycle/source support; existing 05c5b and named writer fences unchanged.
