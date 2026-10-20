# 05a2: Independent retained registry builder

**Bounded partial slice ONLY: not 05a or parent-05 acceptance.** The task's explicit
fallback was used. Preparation polling, credential injection and physical cleanup/
orphan reconciliation remain fenced as **05a2b**. No public adapter, publication,
installation polling or live application is enabled; managed routes still reject.
Branch `uv-implementation`; base `086abb210c51b76edaedfa894c7c4971e96e442b`.

## Implementation

Independent `RegistryBuilder.build(captured_inputs)` performs uv solve, disposable
materialization, registry source download/hash verification and independent source
wheel build outside DB transactions and the live runtime. Pins **uv 0.12.21** and
Python 3.12; runs non-root with isolated environment. Exact core versions AND
source digests must match. Original declarations/contributions remain intact;
unsupported sources/policies fail structurally without sanitization. Command
failure output is suppressed rather than leaking build/credential diagnostics.

Original solve project/lock and sources are retained alongside built wheels, an
all-wheel materialization project/lock, tool identity and inventory. Cold-cache
**offline frozen --no-build** sync into a disposable venv validates the complete
inventory/RECORDs. Fsync and atomic content-addressed retention precede all-file
manifest verification and the complete worker attestation. Corruption/file-set
changes reject. The 05a1 completion validator accepts the real proof attestation
through both real PostgreSQL initialization paths; no intent/runtime changes occur.

Supported policy is intentionally narrow: public PyPI, explicit Python 3.12 target,
registry declarations, one selected version per normalized identity, sdists or
pure-Python wheel fallback. Core source identity uses that retained artifact digest.
Other target/private/index policies and complex universal locks fail closed.
See `preparation/README.md`; no general source-parity acceptance is claimed.
Build code is trusted, not sandboxed; dynamic public build dependencies can vary.
Retained wheels establish approved bytes, not build reproducibility.

## Verification and review

Evidence: `05a2/evidence/accepted/` (qualified local evidence, independent review
still required). Includes actual retained bytes, manifest, inventory, attestation,
container/image provenance and full gate logs. Image ID recorded in `image.txt`;
container invokes the image-owned pinned binary and checks its exact version.

Exact commands from repository root, all exit 0:

```sh
bash uv-experiment/run-preparation-builder-proof.sh
# PASS pinned uv 0.12.21: solve, source build, exact core, retained sources/wheels/inventory,
# cold-cache offline frozen --no-build disposable sync
# PASS conflict: redacted failure, no input/runtime mutation
# PASS unsupported credential-bearing direct source rejected, diagnostic redacted;
# credential injection/cleanup NOT qualified
# PASS retained-byte corruption rejected; reusable cache not relied upon

uv run --project uv-experiment/executor --frozen pytest -q -p no:cacheprovider uv-experiment/executor/tests/test_registry_builder.py
# 3 passed in 0.09s

PYTHONPATH=docassemble_base DA_PREPARATION_TEST_URL=postgresql+psycopg2://postgres:preparation-test@127.0.0.1:55432/postgres uv run --project uv-experiment/executor --frozen pytest -q -p no:cacheprovider uv-experiment/preparation/tests uv-experiment/executor/tests/test_preparation.py
# 48 passed in 2.57s

act workflow_dispatch -W .act/checks.yml
# 275 passed, 1 skipped in 30.54s; no issues found in 49 source files; Job succeeded

git diff --check
# no output
```

Disposable PostgreSQL started with:
`docker run -d --name da-05a2-postgres -e POSTGRES_PASSWORD=preparation-test -p 55432:5432 postgres:16.6`.
Native count intentionally +3 tests; PostgreSQL +2 real-attestation scenarios;
mypy +1 module. No expected-count assertions/exclusions changed. The sole native
skip remains the documented bare-act Supervisor case.

Initial proof attempts failed due to old image missing SQLAlchemy, then interpreter
lookup with downloads disabled. Proof now installs the independent frozen executor
tooling in `/tmp/executor` and explicitly selects its interpreter; the qualified
final command above was rerun successfully. No full fixture image rebuild needed.

## Resolution and limits

This is real builder/retention evidence, not a plan-only handoff. Claim renewal,
dispatch, descendant-safe worker exit, credential injection and physical delayed
cleanup/orphan reconciliation are **not implemented or qualified**. Redacted direct
source rejection is not private authentication or secret cleanup evidence. No
claim is made that cache metadata excludes credentials. Committed/shared material
and cache are untouched, not actively cleanup-qualified here.

05a3 production authorizer/public preview/cancel adapters, 05b approval/retry/
projection/security writers and 05c installation polling/frozen live apply remain
fenced. Parent Status/checklist unchanged; 06 remains blocked. Independent review
required before bounded acceptance.

Next: 05a2b
