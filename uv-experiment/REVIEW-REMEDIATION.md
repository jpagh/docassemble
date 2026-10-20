# Approved-model re-review remediation backlog

Date: 2026-10-16 UTC. The first review pass for several tickets ran on an
alternate model while both Codex workspaces were rate-limited. After the quota
reset, those tickets were re-reviewed with the approved `gpt-6-astra` model. The
re-reviews surfaced genuine gaps the alternate model missed or under-rated. This
document records them; the affected trackers have their acceptance withdrawn
pending remediation.

## Clean re-reviews (approved model)

- **11a** — No issues found / OK.
- **14a** — No issues found / OK (scoped to the 14a-accepted state).

## Findings and required fixes

### 10a — public HTTPS Git identity (OK with notes)

- **P2 fixed:** `git_sources.resolve_source` searched both namespaces for a
  qualified ref; `refs/heads/...` and `refs/tags/...` are now resolved
  exclusively, with dual lookup only for unqualified refs. (Regression tests to
  add: missing and competing qualified refs.)

### 10b — scoped Git credentials (BLOCK)

- **P1 fixed:** `read_git_credential`'s retained branch did not require a live
  owned claim nor membership in the claim's `git_credentials`; a cancelled/
  expired/fenced claim could obtain committed credential bytes. Now requires
  `owned` and a matching reference.
- **P1 remediation implemented, review pending — staging races with workers and
  cleanup:** the post-registration write now holds the environment/candidate/
  material locks and refuses changed candidate/material eligibility before writing.
  Real-poller interleaving coverage claims/fails on the missing file, reconciles
  worker exit and cleanup, then verifies the delayed writer cannot leave bytes
  behind a tombstone. Crash tests retain registration before/after file creation.
- **P2 limitation explicitly recorded:** container preparation rejects every
  credential-bearing candidate, including committed references copied into
  unrelated preparations. `TICKET-10b.md` explicitly limits credential support to
  the in-process path; the public worker-image Git proof does not qualify it.
  Scoping container refusal remains a follow-up.

### 11b — scoped index credentials (BLOCK)

- **P1 remediation implemented, review pending — build-dependency path:** 11c
  extends the credential-fetched offline snapshot to bound PEP 517 sdists. A
  separate hashed build copy interposes a backend shim that validates static and
  hook-discovered prerequisites against exact registry bindings before uv sees
  them; local/direct/unknown sources fail closed. The original source remains
  retained. Pinned-uv proof covers private source builds, missing bindings,
  rotation/revocation, redirects, TLS and secret-free cold offline application.
  See `TICKET-11c.md`; 11b/parent acceptance is not restored.

### 12 — direct URLs (BLOCK)

- **P1 remediation implemented, review pending — artifact registration races
  cleanup:** the dispatcher supplies a trusted retain callback through
  `RegistryBuilder` and direct resolution. Retention/verification and registration
  now hold reconciliation's environment lock before building. Deterministic
  PostgreSQL interleavings cover exposure/registration and fetch/build windows;
  committed/shared preservation is unchanged.
- **P1 remediation implemented, review pending — automatic direct-worker exit
  cleanup:** Linux direct jobs now use a per-claim subreaper guardian. Only its
  durable receipt after reaping all descendants through `ECHILD` authorizes
  `worker_exited`; the receipt flock spans terminal writes. Actual dispatcher
  tests cover cancellation, builder crash, dispatcher-crash recovery, and
  terminal-write exclusion. Guardian crash, missing proof and unsupported
  platforms fail closed. Non-direct dispatch is unchanged. Evidence:
  `12/evidence/remediation/`; acceptance is not restored.

### 13 — repair from recovery (BLOCK)

- **P1 fixed:** the repair/retry guard required `requester == actor` for retry,
  so another authorized administrator could not retry a repair revision. Retry is
  now admin-or-still-authorized-original-approver; approval stays requester-only.
  (Regression test to add: two administrators.)
- **P1 fixed:** the recovery GET now fetches the pending repair candidate's
  authorized status and renders readiness, the redacted `intent_change.repair_diff`,
  desired diff, recovery runtime, warnings and readiness digest before enabling
  approval. Re-reviewed OK; 13 acceptance restored.

### 14b — gated upgrade apply (BLOCK)

- **P1 fixed:** the schema-clearance file is now opened with `O_NOFOLLOW` and
  validated for regular-file/owner/not-group-other-writable with a bounded read.
- **P1 remediation implemented, review pending — observed schema clearance:**
  read the actual database revision using the independent config/probe before
  recording or consuming clearance, including persisted clearances. Missing,
  unreadable or changed revisions fail closed with `schema-clearance-required`.
- **P1 remediation implemented, review pending — selected core baseline:**
  publication atomically records the selected core bundle/release and core
  inventory separately from recovery. Executor gates, selected-release health
  and installed Alembic metadata, retained-wheel preparation, durable same-image
  restart state and prior-core recovery follow that selection. Recovery promotes
  only after schema and all-role health success. Ordinary operations retain exact
  selected-core protection. See `TICKET-14b.md` for validation and remaining proof.
- **P1 evidence produced, independent review pending — required integration proof:**
  `TICKET-14b-PROOF.md` records the fresh pinned-uv/container proof, with actual
  changed core inventories from explicitly synthetic coherent fixture releases
  (image and PyPI latest both 1.10.13). Real uv/schema/all-role success, delayed
  recovery promotion, one prior-selected-core recovery, observed-schema clearance
  and two same-image restart boundaries passed. This does not claim a newer
  published registry release or mark 14b/parent acceptance. Exact commands,
  negative runs and qualification limits are retained in `14b/evidence/`.

## Effect on acceptance

Acceptance for **10b, 11b, 12, 13, 14b** is withdrawn pending the open fixes
above; **10a** stands with the P2 fixed; **11a** and **14a** stand. Parent 10 and
11 remain incomplete until 10b/11b (and the new 11c build-dependency slice) close.


## Status: all re-review remediation closed (2026-10-16)

10a (P2 fixed), 10b (P1s fixed, re-reviewed OK), 11a (clean), 11b via 11c
(build-dependency path fixed, re-reviewed OK), 12 (P1s fixed, re-reviewed OK),
13 (P1s fixed, re-reviewed OK), 14a (clean), 14b (code + container proof
re-reviewed OK). No open remediation items remain. Parent 10, 11 and 14
acceptance restored.
