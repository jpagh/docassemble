# 2026-10-15 UTC — bounded 05c5a P1 fix

Commit: `50e180461` on `uv-implementation`, directly atop `59573c58b34e08ea6d2e840f5b8920b479130c96`.
Combined baseline: `ac5499e0dd22539f6e048c5083462ccc73c9ef64`.

Recovery image identity is now checked against the attempt's immutable published revision before terminal acceptance/slot release, independently of runtime generation. Runtime projection updates alone remain generation-conditional. No retry/recovery authority expansion.

## Changed tracked files

- `uv-experiment/executor/src/docassemble_executor/installation.py`: move existing recovery target check before terminal write, outside generation guard.
- `uv-experiment/preparation/tests/test_core_recovery_postgres.py`: combine foreign recovery image and newer development-override observation; reject without any DB change on both upgrade/fresh schemas, retaining existing current-observation cases.
- `uv-experiment/TICKET-05c5.md`: dated fix note and verification results.
- `.scratch/uv-environment-implementation/issues/05-registry-install.md`: dated Comments append; parent Status/checklist unchanged.

## Results

- Regression before fix: exit 1, `2 failed, 8 passed, 6 deselected in 1.80s`; only new superseded foreign-image cases failed to raise (upgrade and fresh).
- Regression after fix: exit 0, `10 passed, 6 deselected in 1.67s`.
- Full PostgreSQL/preparation: exit 0, `507 passed in 92.36s (0:01:32)`; intentional 505 -> 507, one added combination on both schema paths.
- Act: exit 0, `333 passed, 1 skipped in 32.08s`; `Success: no issues found in 56 source files`. Counts/skip unchanged; no relaxed assertions/exclusions.
- Full current-source fixture build: exit 0; `da-uv-05c5-fix:local`, `sha256:3ba622c2fbf1478eac35c35eabafb87f260473247812d41efa97640eed415e45`. Not a derived image. Working-tree build before documentation commit; not claimed as a commit-identical build.
- Recovery: exit 0, nine PASS groups including completion.
- Continuous: exit 0, six PASS groups including completion, plus unchanged authenticated normal fixture.
- Original image bridge: exit 0, eight PASS groups.
- Image-installed fixed `installation.py` SHA256 matches host: `416482ae1cd20f7b046f1bee6d60339cf59f433c0320cc29d5007c7f46820569`.
- Ruff E9/F: exit 0, `All checks passed!`; `git diff --check`: exit 0, no output.

Complete merged output and exact argv/exit are in `fix-evidence/*.log` and `*-command.json`. Before/after regression share the same command but were captured at the indicated source states. Supervisor approved removing only this run's three disposable proof containers after evidence capture. Historical containers, all named volumes, current and fix images were preserved.

## Remaining fenced / review

Independent re-review required. No full 05c5/05c/parent-05 or writer-safety acceptance. 05c5b universal live observation/status/all-role health closure, cold uncertain/unreported recovery, tables-unavailable boot restoration, restricted core administration/resumption, failed/recovered/crash rollover, explicit uncertain-work/schema/setup clearance and config/dependency/policy refresh remain fenced. All source-named remaining 05b2b6 writer composites/callers/races, 05a2b4 credential-source/private binding work and later source/target/build/API/adoption/upgrade/backup breadth remain fenced; 06 blocked.

`diff.patch` is the combined baseline..HEAD diff; it and both summaries/evidence remain untracked. No branch switch or new-system changes.

## Exact verification commands (repository root)

### regression-before (exit 1)

```sh
python3 uv-experiment/ticket-05c5/fix-evidence/capture.py regression-before env PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -p no:cacheprovider uv-experiment/preparation/tests/test_core_recovery_postgres.py -k invalid_or_foreign
```

### regression-after (exit 0)

```sh
python3 uv-experiment/ticket-05c5/fix-evidence/capture.py regression-after env PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -p no:cacheprovider uv-experiment/preparation/tests/test_core_recovery_postgres.py -k invalid_or_foreign
```

### postgres (exit 0)

```sh
python3 uv-experiment/ticket-05c5/fix-evidence/capture.py postgres env PYTHONPATH=docassemble_base DA_API_WRITER_REDIS_URL=redis://127.0.0.1:56379/0 DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen --with Flask --with flask-login --with redis==5.2.1 pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
```

### act (exit 0)

```sh
python3 uv-experiment/ticket-05c5/fix-evidence/capture.py act act workflow_dispatch -W .act/checks.yml
```

### build (exit 0)

```sh
python3 uv-experiment/ticket-05c5/fix-evidence/capture.py build docker build --progress=plain -f uv-experiment/fixture/Dockerfile -t da-uv-05c5-fix:local --build-arg DA_REPO_COMMIT=59573c58b34e08ea6d2e840f5b8920b479130c96+05c5-fix-working-tree .
```

### recovery (exit 0)

```sh
python3 uv-experiment/ticket-05c5/fix-evidence/capture.py recovery env PROOF_IMAGE=da-uv-05c5-fix:local PROOF_CONTAINER_NAME=da-05c5-fix-recovery PROOF_EVIDENCE_DIR=uv-experiment/ticket-05c5/fix-evidence/recovery PROOF_FLOW_ACTION=recovery python3 uv-experiment/run-image-bridge-proof.py
```

### continuous (exit 0)

```sh
python3 uv-experiment/ticket-05c5/fix-evidence/capture.py continuous env PROOF_IMAGE=da-uv-05c5-fix:local PROOF_CONTAINER_NAME=da-05c5-fix-continuous PROOF_EVIDENCE_DIR=uv-experiment/ticket-05c5/fix-evidence/continuous python3 uv-experiment/run-continuous-work-proof.py
```

### image-bridge (exit 0)

```sh
python3 uv-experiment/ticket-05c5/fix-evidence/capture.py image-bridge env PROOF_IMAGE=da-uv-05c5-fix:local PROOF_CONTAINER_NAME=da-05c5-fix-image-bridge PROOF_EVIDENCE_DIR=uv-experiment/ticket-05c5/fix-evidence/image-bridge python3 uv-experiment/run-image-bridge-proof.py
```

### source-installation (exit 0)

```sh
python3 uv-experiment/ticket-05c5/fix-evidence/capture.py source-installation python3 -c 'import hashlib, pathlib, subprocess; p=pathlib.Path("uv-experiment/executor/src/docassemble_executor/installation.py"); expected=hashlib.sha256(p.read_bytes()).hexdigest(); result=subprocess.check_output(["docker", "exec", "da-05c5-fix-image-bridge", "/usr/share/docassemble/executor-venv/bin/python", "-c", "import hashlib; from pathlib import Path; import docassemble_executor.installation as m; print(m.__file__); print(hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest())"], text=True); print(result, end=""); assert result.splitlines()[-1] == expected; print("PASS image-installed installation.py matches fixed working tree SHA256 " + expected)'
```

### lint (exit 0)

```sh
python3 uv-experiment/ticket-05c5/fix-evidence/capture.py lint uvx ruff check --select E9,F uv-experiment/executor/src/docassemble_executor/installation.py uv-experiment/preparation/tests/test_core_recovery_postgres.py
```

### diff-check (exit 0)

```sh
python3 uv-experiment/ticket-05c5/fix-evidence/capture.py diff-check git diff --check
```

### remove-recovery (exit 0)

```sh
python3 uv-experiment/ticket-05c5/fix-evidence/capture.py remove-recovery docker rm -f da-05c5-fix-recovery
```

### remove-continuous (exit 0)

```sh
python3 uv-experiment/ticket-05c5/fix-evidence/capture.py remove-continuous docker rm -f da-05c5-fix-continuous
```

### remove-image-bridge (exit 0)

```sh
python3 uv-experiment/ticket-05c5/fix-evidence/capture.py remove-image-bridge docker rm -f da-05c5-fix-image-bridge
```
