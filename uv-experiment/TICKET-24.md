# 24: Final experimental-container qualification

Date: 2026-10-20 UTC. Branch: `uv-implementation`. Tickets 07–23 accepted.
**Accepted 2026-10-20 (bounded experimental scope; independent `gpt-6-astra`
review — accept with conditions, conditions and the five carried residuals
recorded verbatim below).**

## Implementation

- `run-qualification.py` reruns the accepted scenario/source/lifecycle harnesses
  in isolated disposable containers with new evidence destinations, actual
  image/Python/platform/uv identities, source hashes, invocation counts,
  per-phase argv/exits/durations, protected-service identity checks and cleanup.
  Subsets or failed/missing phases cannot report complete qualification
  (`container_gates_passed = complete and unchanged and not --only`).
- `source_qualification_proof.py` exports only non-secret bundle hashes and
  inventories; `direct_url_proof.py` additionally executes the supported `.zip`
  sdist form, not only wheels.
- `checkpoint_restore_proof.py` qualifies full local **and** cloud rewind for
  both providers, including retrieved checkpoint bytes after local staging
  removal, corresponding local/cloud files, credential bytes/scope, failed
  requested captures before mutation, SIGKILL-after-drop gating and explicit
  restore completion.
- `recovery_repair_proof.py` extends the coherent-upgrade run with real
  restricted browser login/CSRF, repair preparation, ready refresh, approval and
  an explicit frozen apply after the matching operator schema clearance.
- Four native source-named tests protect all-gates-required semantics, both cloud
  restores, uv-audit counting and the host-service protection list.

## Qualification

Final acceptance run `accept-clean-20261020T130737Z`:

- **30/30 phases exit 0**, 17 930 s (≈5 h), `missing_phases: []`,
  `container_gates_passed: true`, `sources_and_protected_services_unchanged: true`.
- Zero leftover disposable containers and zero `da-*` volumes.
- Image `da-uv-24-qual:local` = `sha256:dfce538a21af9f22…01341`, built from
  revision `89eddac69`; linux/arm64, CPython **3.12.14**, uv **0.12.21**
  (`aarch64-unknown-linux-musl`). That 3.12.14 is the uv-managed *managed runtime*
  interpreter; it differs from the repository image's 3.14 serving path — see the
  Python-target divergence under Boundaries. Replacement pair `da-uv-24-old:local`
  `4b4d982bf0d8` (RELEASE 1.10.12) / `da-uv-24-new:local` `59eae89710d6` (1.10.16).
- Longest phases: `restore-azure` 2246 s, `restore-s3` 2195 s,
  `cloud-checkpoint` 1676 s, `helpers_mail` 1395 s, `image-migration` 1091 s.

Separate current non-container gates on the same revision: `act`
**1731 passed, 8 skipped** with mypy clean over **75 source files**; PostgreSQL/
preparation **2473 passed, 24 skipped**. A measured successful apply is not slow:
`uv sync --frozen --no-build` 16 s, schema 17 s, operation start to serving 48 s;
the phase totals are dominated by restarts, injected failures, restores and
migrations, not by package installation.

Prior run `accept-final-20261020T053515Z` was also 30/30 but honestly reported
`container_gates_passed: false`: `vaultwarden` `StartedAt` advanced exactly 24 h
during the run — the operator's 04:00 CST daily volume backup restarting an
unchanged container (identical ID and digest). The check was **not** weakened;
the clean run above was scheduled outside that window and passed it.

Operator-authorized product change during qualification: a busy mutation-lock
acquisition (`MutationLockBusy(BlockingIOError)`, raised only at the flock
deadline) is translated to `InstallationBusy` so the durable worker treats it as
a no-work turn instead of latching `durable runtime is not normally verified`.
This removes a latched recovery outage; admission, authorization, schema,
checkpoint and 13.2 fences are unchanged, and no other exception is reclassified.

## Boundaries / disclosure

Carried residuals from the evidence review, quoted verbatim:

> 1. **F2:** repeated contention/starvation, unchanged stale gate/status, no
>    fairness or cumulative busy bound; `mutation-busy` plus stalled generation
>    are diagnostics, not recovery. A fresh runtime/authorized boot recovery is
>    distinct from unlatching a damaged old run.
> 2. **F3:** public hashes are masked in the core driver; cite independent outer
>    image/source identity and label copies diagnostic projections.
> 3. **F4:** no persistent valid-tar/prewrite-redaction/binary/exclusion
>    retention regressions were added; manually verify the five actual
>    `retained:true` records and record the follow-up/explicit disposition of the
>    prior review condition.
> 4. **F9:** historical exception handler execution is established, exact
>    initiating subtype/site and ordering are not. If needed later, separately
>    authorize a bounded credential-free worker diagnostic; copying the existing
>    fixed message is insufficient.
> 5. **F8 and sampling:** successful runtime protocol qualification plus unchanged
>    live incarnations is not continuous protocol liveness; two-second polling is
>    not instruction-level gate monitoring, and observed success is bounded in time.
>
> **F4 verification result/addendum:** only the uv audit copy was retained; final
> recovery status and the three legacy Supervisor/durable-worker log copies have
> `retained:false` with reported non-regular/symlink-parent exclusion. Two absent
> durable logs are still listed in
> `browser-repair-result.json.evidence_paths`. The manual five-successful-capture
> condition was **not met**. Behavioral observations survived, but complete raw
> final control-plane diagnostics and failure-free durable-worker logs are **not
> certified**. Acceptance of this actual diagnostic loss requires an explicit
> disposition beyond describing F4 as test debt.
>
> The qualification remains a single-container experimental fixture with
> synthetic releases and fake external S3/Azure CLIs, not live-provider,
> arbitrary production-data/source compatibility, continuous protocol-liveness,
> or rolling-availability certification. Full current
> native/PostgreSQL/act/lint gate results must be referenced separately; this
> directory is not evidence of their execution.

**F4 disposition (explicit, as required):** accepted as a real diagnostic-loss
defect. The certified path is supported by surviving behavioral projections —
status, sync, HTTP readiness, worker status, generation advancement — while
complete raw final control-plane diagnostics and failure-free durable-worker logs
are *not* claimed. The two advertised-but-absent `evidence_paths` entries are
recorded as reporting debt; they are neither repaired nor hidden in this ticket.
No wording in this record asserts five successful captures or an
unconditional all-green result.

**Python-target divergence (recorded 2026-10-20 after operator correction):** the
managed runtime qualified here is a uv-managed **CPython 3.12.14** venv (`local3.12`)
served by the executor's own `gunicorn` (`webapp.py` uses
`runtime_venv/bin/gunicorn`). The repository image moved to Python 3.14 in
`b6533949f` on 2026-05-02, an ancestor of this revision, and serves through
`mod_wsgi.so-3.14` from `local3.14`; the experiment's first commit is 2026-10-02 and
`uv-experiment/README.md:129` still declares the 3.12.14 target. Upstream
docassemble 1.10.x supports both interpreters (1.9.9 moved the image to 3.14 and most
3.14 defects were fixed by 1.9.12), so this is a **qualification-scope gap, not a
product defect** — but **no 3.14 behaviour was exercised by any phase**, and this
acceptance does not extend to the 3.14 deployment path.

The 3.12 assumption is structural rather than a pin: `image_baseline.py:79` writes
`'target': {'python': '3.12'}`, and `installation.py:512`, `private_index.py:91`,
`registry_builder.py:51` and `managed_qualification.py:25` **refuse** any baseline
declaring another target; `paths.py:43`, `inventory.py:74,92`,
`installation.py:708` (`UV_PYTHON`) and `registry_builder.py:284` encode the venv
name, the `lib/python3.12/site-packages` layout and the candidate
`requires-python = "==3.12.*"`. The qualification image therefore provisions its own
3.12 runtime instead of the stock 3.14 install, which is why `local3.14` still holds
the OS image's stale docassemble 1.10.7. Retargeting is ticket 25; until it passes,
the experimental runtime remains one interpreter generation behind its own image.

Other boundaries: cloud restores use the shared manifest/storage contract with
filesystem-transport CLI fakes that inject auth-failure, partial/interrupted/
corrupt upload, missing/changed object and overwrite-refusal behaviour; provider
encryption flags are constructed and pinned but are stripped by the fake, so real
SSE/SSE-C/KMS and network/auth/throttling semantics are unqualified. Core
releases 1.10.14–1.10.16 are synthetic repacks of current image code. Package
locks cannot certify arbitrary data compatibility or upstream availability.
Artifact checkpoints are not full-deployment backups.

Evidence policy: all 30-phase evidence remains **local only** under
`uv-experiment/24/evidence/`; it contains the test device password, internal
network ranges and the host name, so it is never committed. The remote branch
`uv-implementation-code` is code-only and squashed. Review artifacts:
`.scratch/uv-environment-implementation/reviews/REVIEW-acceptance-evidence.md`.

## Resolution

Accepted with the five limitations above recorded verbatim, the false aggregate
gate of the first run preserved rather than rewritten, the F4 diagnostic loss
explicitly dispositioned, and the 3.12-versus-3.14 scope gap disclosed rather than
implied away. Operator walkthrough and repeatable commands:
[`24/OPERATOR.md`](24/OPERATOR.md). Next: 25 (retarget the managed runtime to the
image's Python), which must re-run this matrix before any 3.14 claim.
