# 05c5 implementation result

Implemented **task-authorized bounded 05c5a**, NOT full 05c5: immediate failed qualified frozen apply restores the retained core once in stopped mode, then records/replays an attempt-bound failed terminal. Recovery never succeeds the failed attempt, changes desired intent, reruns schema/setup or opens readiness/recovery administration.

Current commit: `50e180461` on `uv-implementation`; original bounded implementation: `59573c58b34e08ea6d2e840f5b8920b479130c96`.
Baseline: `ac5499e0dd22539f6e048c5083462ccc73c9ef64`.
No branch switch or new-system edits. Parent 05 Status/checklist unchanged; 06 blocked.

## 2026-10-15 UTC P1 fix

Superseded observations no longer bypass recovery-target validation: the existing
check now runs against the attempt's published revision before terminal write or
slot release, independently of generation. Only projection updates remain gated
on current generation. Combined foreign-image/newer-observation regression fails
before and passes after on BOTH fresh/upgraded schemas. Latest reruns:
PostgreSQL/preparation **507 passed** (intentional +2); act **333 passed, 1
unchanged skip**, mypy **56 files clean**; fresh fix-image recovery **9 PASS**,
continuous **6 PASS plus fixture**, image bridge **8 PASS**. Full exact commands,
outputs, image/hash provenance and unchanged fences: `fix-summary.md` and
`fix-evidence/`. Original accepted qualification below is preserved historical
context, not relabeled as post-fix output. Independent re-review required.

## Changed files

- `docassemble_base/docassemble/base/managed_preparation.py`: separate recovery identity/result status fields.
- `uv-experiment/executor/src/docassemble_executor/controller.py`: optional offline stopped core restoration, preserving serving-recovery default.
- `uv-experiment/executor/src/docassemble_executor/installation.py`: immediate one-attempt restoration, child-safe durable recovery receipt, exact attempt binding, serialized core-only/unknown reporting and replay fences.
- `uv-experiment/executor/tests/test_core_recovery.py`: six native marker/child/exclusion/replay/failed-sync tests.
- `uv-experiment/preparation/tests/test_core_recovery_postgres.py`: seven reporting/authorization/invalid-evidence/late-observation cases on both schema initialization paths.
- `uv-experiment/preparation/{image_bridge_proof.py,core_recovery_retry_proof.py}`: real uv failure/recovery/report outage/replay and real administrator-authorized retry rejection.
- `uv-experiment/run-image-bridge-proof.py`, `run-continuous-work-proof.py`: recovery action and task-specific evidence/container names, preserving existing defaults/assertions.
- `uv-experiment/TICKET-05c5.md`, `TICKET-05-SPLIT.md`: bounded implementation and explicit refinement/fences.
- `.scratch/uv-environment-implementation/issues/05-registry-install.md`: dated Comments append only.
- `uv-experiment/05c5/evidence/accepted/`: qualified commands/output, manifests, inventories, journals, status and source provenance.

## Validation

All final commands exited 0. Exact commands and output are in `TICKET-05c5.md` and `05c5/evidence/accepted/*-command.json` / `*.log`.

- Full pinned fixture build: `da-uv-05c5:local`, `sha256:693e4479f9f637d46da8b45a5cc62fdd7b6899baa8a9fe5117f2293ff563637f`; not derived.
- Recovery: nine PASS groups including completion; actual non-root frozen uv permission failure -> one actual offline retained-core restoration; lost DB report/replay with candidate store hidden; truthful failed desired/history/recovery/no-active-slot, no normal/recovery availability; actual authorized uncertain retry rejected.
- Continuous: six PASS groups including completion; original image-bridge eight, stopped installation seven, fixture boot 25; published boot five reconciliations plus unavailable-table fail-closed case.
- PostgreSQL/preparation: 505 passed in 79.51s (491 ->505 intentional, +14 new tests).
- `act workflow_dispatch -W .act/checks.yml`: 333 passed, one unchanged Supervisor skip in 32.20s; mypy 56 files/no issues (327 ->333 intentional, +6 native tests).
- Focused native: 36 passed. Source: 614 image-COPY inputs match, 666 host/build inputs hashed. Ruff E9/F and source/doc whitespace checks passed.

Development failures and superseded outputs remain untracked under `05c5/evidence/development/`: early timer/closure/import proof-driver errors, disk pressure, and the initial optional-recovery mypy error. Full image and every final proof reran after the last source fix. Supervisor-approved cleanup preserved historical containers, all named volumes/evidence and the current proof image; task-owned containers were removed after evidence capture. Final Docker free space 18G.

## Remaining explicitly fenced

**05c5b**: universal live observation/status/all-role readiness/lost-success health closure beyond the explicit bridge; cold unreported/uncertain apply recovery and tables-unavailable boot restoration; restricted core-only administration/resumption; crash-before-terminal reconstruction; broader failed/recovered/crash rollover; explicit authorized uncertain-work/schema/setup clearance; config/dependency/policy observation refresh. Successful restored core here is stopped, never available restricted service.

**Writer composites/callers/races**: users/views profile/add/custom_register/invite/add_privilege/manage_account/deletion/MFA/email/multi-commit composites; users/helpers login_or_register; users/signals login/register/password hooks; MySignInForm LDAP/default-role writes; OAuth/phone-login callbacks; create_tables account/default-role/fix-role passes and secret/cookie/session/auth/external-hook/plugin composites. admin config save; startup/base/Docker dependency/index/PYTHONPACKAGES observations; executor preparation/controller/CLI policy refresh; config.allowed/ALLOW_UPDATES/DEVELOPER_CAN_INSTALL/name/source/dependency-policy serialization. PackageAuth/grant/name/source closure for utils install helpers, packages view/API, Playground ZIP caller, update cleanup/install/version/dependency/name writers, task update/reset/direct CLI and create_tables package rewrites. Exact source-named inventory remains in TICKET-05c5.md.

**05a2b4/later**: credential-source decision/injection/private indexes/bindings; other sources/targets/build policies; production initialization/adoption, broader material/store qualification; update/remove/config import/coherent upgrade/rollback/backup/image migration/general managed API breadth. No complete 05c5/05c/parent-05, writer-safety or 05a2b4 acceptance.

## Artifacts and review

`diff.patch` is byte-for-byte `git diff ac5499e0dd22539f6e048c5083462ccc73c9ef64..HEAD`, captured via Python subprocess without shell redirection. Both requested artifacts remain **UNTRACKED**. No staged files remain. Independent reviewer gate remains required.

Next: 05c5b after independent review of bounded 05c5a.
