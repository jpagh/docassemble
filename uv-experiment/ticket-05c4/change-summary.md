# 05c4a implementation summary

Branch: uv-implementation. Baseline: 821b6f48c4f2a33d5b8efe1e73c17a92aed57947.
Commit: ac5499e0dd22539f6e048c5083462ccc73c9ef64.

Implemented the task-authorized bounded end-to-end refinement, not full 05c4 or
parent-05 acceptance. No branch switch/new-system changes; parent Status unchanged.

## Changes

- New durable_work.py: Supervisor-owned continuous independent PostgreSQL
  preparation/installation loops, shared sampled live status and HostEnvironment
  observations. Explicit DA_DURABLE_WORK_STORE + existing qualified image baseline.
- boot_observation.py/controller.py: reconciliation-first startup; exact already
  reported no-pending normal receipt cold resumption after fresh material/schema
  usability/core/web/all-role qualification, without sync/schema/setup/journal replay.
- installation.py: same-incarnation, fresh-material health retention without
  periodically closing healthy traffic; full recheck on lost reporting/changed
  incarnations; old-success replay fencing and known-terminal archival before
  subsequent work. Old reports never release another attempt's slot.
- cli.py/supervision.py: fixed independent daemon entrypoints, non-root preparation,
  supervised autorestart; no Flask/Celery/Redis dispatch authority.
- Five native regressions; nine PostgreSQL cases on both schema initialization paths.
  New continuous real-image proof and isolated regression-proof destinations.
- TICKET-05c4.md, split refinement, dated tracker comment and qualified evidence
  under uv-experiment/05c4/evidence/accepted/.

## Verification

Exact commands/argv/exits and output: TICKET-05c4.md and accepted/*-command.json.
Final image: sha256:8de38ee79bcde59bade650faddf2e3a7336c1f9395b4827221f332a34281ce93.

- Continuous proof: six PASS groups, including actual authenticated durable
  preparation/publication/frozen application, Supervisor restart, healthy cold
  resumption, subsequent-attempt isolation and current health invalidation.
- Image bridge: original eight PASS groups.
- Published boot: five successful reconciliations and unavailable-table fail-closed.
- Stopped installation: original seven PASS groups.
- Fixture boot: original 25 PASS scenarios (existing local recovery only).
- PostgreSQL/preparation: 491 passed; act: 327 passed/one existing skip;
  mypy56 clean; focused native35 passed; Ruff/whitespace pass.
- Provenance: 613 image source inputs matched; 663 host/build inputs hashed.
- Counts intentionally +18 PostgreSQL, +5 native, +1 typed module; no exclusions
  or existing assertion/count reductions.

## Remaining fences

05c4b: universal live status/normal-history-core-only-override/HostEnvironment
writers outside this explicit supervised published-normal path; complete health/UX
surfaces and unchanged/hung-process periodic protocol qualification; cold
unreported/lost-success and restricted-service resumption; broader same-revision/
crash rollover; complete config/dependency/policy observation refresh.

Still excluded: DB-bound independent core recovery/failed-recovered reporting,
automatic data restore, uncertain-work retry/clearance/schema replay/heartbeat slot
release; all remaining named 05b2b6 account/profile/registration/default-role/
secret/auth/external-hook/plugin and config/PackageAuth/grant/name/source/dependency
writer composites/callers/closure races; 05a2b4 real credential-source injection/
private binding; broader sources/targets/build policy, production initialization/
adoption, update/remove/config import/coherent upgrade/rollback/backup/image
migration/general API and later tickets. Exact caller inventory is repeated in
TICKET-05c4.md. No complete 05b/05c/parent-05 acceptance; 06 remains blocked.

Independent review required. Intermediate failures/resource-cleanup development
artifacts remain untracked. These two requested review artifacts are deliberately
untracked; diff.patch is the direct git diff 821b6f48..HEAD output captured with
Python rather than forbidden shell redirection. No staged files remain.

Next: independent review of bounded 05c4a, then separately scoped 05c4b.
