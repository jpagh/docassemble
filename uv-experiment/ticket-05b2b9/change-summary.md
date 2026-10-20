# 05b2b9 change summary

Commit: `b8a28695a73e5b710b42e022bed452a8e250ce7e` on `uv-implementation`.
Baseline: `283c16ff1df6c79f971d530bc69807c8ffc130a3`.

Implemented final named built-in SQL convergence with explicit commit owners, mandatory precommit built-in hooks and additive postcommit plugin effects. Includes two-recipient invitation regression, full enclosing writer races, rollback, flag-off, auto-login and durable compensation coverage. Legacy managed rejections unchanged; no new-system/later-ticket implementation.

Readiness: **PARENT 05 READY FOR ACCEPTANCE under supervisor-approved extended categories(a)-(d), NOT accepted.** 05c5b maps to13/20; 05a2b5 maps to10/11/12 (ZIP09), explicitly documented in both handoffs, not implemented. Independent review required; parent Status/checklist unchanged; 06+ blocked.

## Changed source/tests/docs

- `.scratch/uv-environment-implementation/issues/05-registry-install.md`
- `docassemble_webapp/docassemble/webapp/auth/views.py`
- `docassemble_webapp/docassemble/webapp/daglobal/hooks.py`
- `docassemble_webapp/docassemble/webapp/emailserver/helpers.py`
- `docassemble_webapp/docassemble/webapp/interview/helpers.py`
- `docassemble_webapp/docassemble/webapp/monitor/hooks.py`
- `docassemble_webapp/docassemble/webapp/phonelogin/views.py`
- `docassemble_webapp/docassemble/webapp/tts/hooks.py`
- `docassemble_webapp/docassemble/webapp/users/helpers.py`
- `docassemble_webapp/docassemble/webapp/users/managed_effects.py`
- `docassemble_webapp/docassemble/webapp/users/signals.py`
- `docassemble_webapp/docassemble/webapp/users/views.py`
- `uv-experiment/TICKET-05-SPLIT.md`
- `uv-experiment/TICKET-05b2b9.md`
- `uv-experiment/preparation/tests/test_publication_account_writers_postgres.py`
- `uv-experiment/preparation/tests/test_publication_convergence_postgres.py`
- `uv-experiment/preparation/tests/test_publication_invitation_batch_postgres.py`
- `uv-experiment/preparation/tests/test_publication_registration_postgres.py`
- `uv-experiment/preparation/tests/test_publication_secret_writers_postgres.py`

Qualified evidence: `uv-experiment/05b2b9/evidence/accepted/` (377 committed files). Operational proofs use unchanged historical image, NOT rebuilt changed SQL routes.

## Exact verification commands and outcomes

```sh
env PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -x -p no:cacheprovider uv-experiment/preparation/tests/test_publication_convergence_postgres.py uv-experiment/preparation/tests/test_publication_invitation_batch_postgres.py
```
Exit0: 196 passed in 77.72s. Output: `uv-experiment/05b2b9/evidence/accepted/focused.log`.

```sh
env PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
```
Exit0: 963 passed in 232.23s; intentional +196 from767. Output: `uv-experiment/05b2b9/evidence/accepted/postgres.log`.

```sh
env PROOF_EVIDENCE_DIR=uv-experiment/05b2b9/evidence/accepted/private-index PROOF_CONTAINER_NAME=da-05b2b9-private-index python3 uv-experiment/run-private-index-proof.py
```
Exit0: nine PASS groups. Output: `uv-experiment/05b2b9/evidence/accepted/private-index.log`.

```sh
env PROOF_IMAGE=da-uv-05c5-fix:local PROOF_CONTAINER_NAME=da-05b2b9-recovery PROOF_EVIDENCE_DIR=uv-experiment/05b2b9/evidence/accepted/recovery PROOF_FLOW_ACTION=recovery python3 uv-experiment/run-image-bridge-proof.py
```
Exit0: nine PASS groups including completion. Output: `uv-experiment/05b2b9/evidence/accepted/recovery.log`.

```sh
env PROOF_IMAGE=da-uv-05c5-fix:local PROOF_CONTAINER_NAME=da-05b2b9-continuous PROOF_EVIDENCE_DIR=uv-experiment/05b2b9/evidence/accepted/continuous python3 uv-experiment/run-continuous-work-proof.py
```
Exit0: six PASS groups including completion plus authenticated fixture. Output: `uv-experiment/05b2b9/evidence/accepted/continuous.log`.

```sh
env PROOF_IMAGE=da-uv-05c5-fix:local PROOF_CONTAINER_NAME=da-05b2b9-image-bridge PROOF_EVIDENCE_DIR=uv-experiment/05b2b9/evidence/accepted/image-bridge python3 uv-experiment/run-image-bridge-proof.py
```
Exit0: eight PASS groups including completion. Output: `uv-experiment/05b2b9/evidence/accepted/image-bridge.log`.

```sh
act workflow_dispatch -W .act/checks.yml
```
Exit0: 372 passed,1 existing skip in32.98s; mypy57/no issues. Output: `uv-experiment/05b2b9/evidence/accepted/act-final.log`.

```sh
uvx ruff check --select E9,F docassemble_webapp/docassemble/webapp/auth/views.py docassemble_webapp/docassemble/webapp/phonelogin/views.py docassemble_webapp/docassemble/webapp/users/helpers.py docassemble_webapp/docassemble/webapp/users/views.py docassemble_webapp/docassemble/webapp/users/signals.py docassemble_webapp/docassemble/webapp/users/managed_effects.py docassemble_webapp/docassemble/webapp/interview/helpers.py docassemble_webapp/docassemble/webapp/daglobal/hooks.py docassemble_webapp/docassemble/webapp/tts/hooks.py docassemble_webapp/docassemble/webapp/monitor/hooks.py docassemble_webapp/docassemble/webapp/emailserver/helpers.py uv-experiment/preparation/tests/test_publication_convergence_postgres.py uv-experiment/preparation/tests/test_publication_invitation_batch_postgres.py uv-experiment/preparation/tests/test_publication_registration_postgres.py uv-experiment/preparation/tests/test_publication_secret_writers_postgres.py uv-experiment/preparation/tests/test_publication_account_writers_postgres.py
```
Exit0: All checks passed. Output: `uv-experiment/05b2b9/evidence/accepted/lint.log`.

```sh
env PYTHONPATH=docassemble_base uv run --project uv-experiment/executor --frozen python uv-experiment/05b2b9/evidence/accepted/verify-evidence.py
```
Exit0: 46 source hashes; retained private bytes verified; disposable private secrets absent. Output: `uv-experiment/05b2b9/evidence/accepted/source.log`.

```sh
git diff --cached --check
```
Exit0: no whitespace errors. Output: `uv-experiment/05b2b9/evidence/accepted/diff-check.log`.

Patch generated without shell redirection using the exact argv `git diff 283c16ff1df6c79f971d530bc69807c8ffc130a3..HEAD` (15044873 bytes). Both this summary and diff.patch are intentionally UNTRACKED. Index empty after commit; existing unrelated untracked artifacts preserved.

Next: independent acceptance review; no ticket06 execution.
