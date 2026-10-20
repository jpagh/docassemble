# Reusable uv cache operations (ticket 20)

From the managed deployment's operator/root executor, with the same environment
and state directory as the running executor:

```sh
/usr/share/docassemble/executor-venv/bin/da-executor cache usage
/usr/share/docassemble/executor-venv/bin/da-executor cache prune
/usr/share/docassemble/executor-venv/bin/da-executor cache clear
```

These are explicit operator actions, not web/runtime-account controls. They
require pinned uv **0.12.21**, an existing managed state directory and a dedicated
non-root `DA_ENVIRONMENT_USER`. Managed startup provisions `mutation.lock` as
root:environment-group 0660 inside the root-owned state directory. Preparation
can take the lock but cannot replace the lock path or rewrite lifecycle files.

## Supported behavior

- The only supported reusable cache root is `/var/cache/docassemble/uv`. Other
  `UV_CACHE_DIR` values are **refused**, not silently redirected or deleted.
  Symlinked cache paths and overlaps with retained roots (including resolved
  retained aliases) fail closed. Keep operator-configured retained stores outside
  the cache. The resolved baseline directory, not just its descriptor, is retained.
- `usage` reports pinned `uv cache size --output-format machine` byte counts.
  Pinned uv labels size experimental; the implementation explicitly selects
  `--preview-features cache-size`. These measurements are not a filesystem quota,
  a reservation, or an enforced hard budget (`budget_enforced=false`).
- `prune` is ordinary pinned `uv cache prune`: dangling/unused cache cleanup. It
  may reclaim **nothing**. No `--force`, `--ci`, age threshold, size cap or
  automatic eviction policy is promised.
- `clear` uses pinned `uv cache clean` (all reusable entries). Pinned clean
  removes the cache root itself. Its image parent is root-owned, so this command
  runs as the operator; the empty root is recreated for the non-root environment
  owner before releasing authority. Usage/prune run as that owner. No build hook
  or source/project command runs during cache maintenance. An empty recreated
  directory measured **4096 bytes**, not zero, in the proof. If an operator is
  forcibly killed during clear, inspect usage and explicitly repeat clear if the
  cache root is absent; the normal return path recreates its ownership. A killed
  controller never releases exclusion while its guarded uv child is still alive.
- All operations take the same nonblocking **MutationLock** as application
  materialization and production/direct preparation. Busy means refuse/retry
  after the active operation exits; no cancellation or forced takeover. Guarded
  subprocesses retain exclusion through detached descendant exit, including after
  dispatcher death. Retained `desired`, failure, approval, journal and source
  material is never reset by maintenance.
- The locked standalone preparation entrypoint **refuses `--worker-image`**:
  Docker-daemon container lifetime cannot be established by an OS-descendant
  guardian. Existing container-worker components use per-job temporary caches,
  not this reusable cache; orphan-container exclusion is not claimed. Qualifying
  container lock inheritance/daemon fencing is a separate slice.

## What is not cache

Durable full/core bundles, desired intent, revisions/history, installation and
schema journals, failure/approval evidence, uploaded ZIPs, committed Git/private
source authentication, retained wheels/direct-source artifacts and mandatory
image recovery material remain retained. Cache operations neither enumerate nor
remove candidate or historical material. Candidate cleanup and history retention
remain separate policies. Cancelling preparation/application does not implicitly
wipe reusable cache.

## Empty-cache reconstruction / failure

An empty cache does not change the frozen revision. The executor still uses
`uv sync --frozen --no-build`; no solve, substitution or implicit approval occurs.
Reconstruction succeeds only if recorded local mandatory artifacts and required
upstream bytes/authentication remain accessible. When a source is unavailable,
restore network/source/authentication access and explicitly retry the same
revision, or explicitly prepare/approve a repair. Clearing cache is not repair
and cannot recover missing mandatory material. Inspect the recorded failure and
sync logs; no cache command clears them.

## Reproducible evidence

```sh
python3 uv-experiment/run-cache-proof.py
```

The disposable `da-uv-16:local` container uses pinned uv 0.12.21, read-only source
mounts and its own cache/filesystem. It demonstrates real retained image core
frozen recovery from an empty cache (core imports verified), controlled HTTP
upstream install, 503 failure with actionable guidance, restored-access exact
revision retry, non-root preparation/operator exclusion and unchanged retained
bytes. The fixture's initial lock creation is recorded separately; **zero solves
after setup** are allowed by the command trace. It is not a new full-role startup
or database-recovery qualification. The runner removes its disposable container.

Measurements and command trace: `evidence/container/result.json`. The recorded
prune example retains 118784 bytes; the image cache starts at 1599971328 bytes,
clear leaves the empty directory's 4096 bytes, and real core reconstruction grows
it to 979918848 bytes. These are observed allocations, not budget guarantees.
