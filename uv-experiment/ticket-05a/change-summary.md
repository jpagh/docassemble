# 05a1 change summary

Combined range: `35a3a93fa774f4060ee9ca16dfe25b12b89889d6..086abb210c51b76edaedfa894c7c4971e96e442b` on `uv-implementation`.

Supervisor-authorized bounded partial **05a1 only**: additive shared schema/migration, internal PostgreSQL preparation/cancel/status protocol, structural registry parser, generation capture, fencing, immutable DB-time readiness/expiry, material authority/deletion eligibility and tests. 05a2/05a3 and 05b/05c remain fenced. No builder, polling, physical credential deletion, public adapters or live apply. Full 05a/parent acceptance is not claimed.

Changed files:

- `docassemble_base/docassemble/base/managed_records.py`
- `docassemble_webapp/docassemble/webapp/alembic/versions/05a100000001_managed_preparation.py`
- `docassemble_webapp/docassemble/webapp/packages/models.py`
- `uv-experiment/05a1/evidence/accepted/VERIFICATION.md`
- `uv-experiment/05a1/evidence/accepted/act.log`
- `uv-experiment/05a1/evidence/accepted/postgres.log`
- `uv-experiment/05a1/evidence/accepted/provenance.log`
- `uv-experiment/05a1/evidence/accepted/source-sha256.json`
- `uv-experiment/05a1/evidence/accepted/wheel.log`
- `uv-experiment/TICKET-05-SPLIT.md`
- `uv-experiment/TICKET-05a1.md`
- `uv-experiment/executor/pyproject.toml`
- `uv-experiment/executor/src/docassemble_executor/preparation.py`
- `uv-experiment/executor/tests/test_executor_packaging.py`
- `uv-experiment/executor/tests/test_preparation.py`
- `uv-experiment/executor/uv.lock`
- `uv-experiment/fixture/Dockerfile`
- `uv-experiment/preparation/README.md`
- `uv-experiment/preparation/tests/test_preparation_postgres.py`

2026-10-14 P1 fixes preserve the fixture wheel include layout and install shared PostgreSQL guards on fresh metadata creation. Validation: 46 PG/parser tests pass across both initialization paths; act 272 passed/1 documented skip; mypy 48 files; diff check passes; Docker-COPY-layout wheel and isolated import regression passes. See fix-summary.md and fix-evidence/*.log. Independent review pending.

Tracker comment was updated locally without changing parent Status/checklist. `diff.patch` and this summary are deliberately untracked.
