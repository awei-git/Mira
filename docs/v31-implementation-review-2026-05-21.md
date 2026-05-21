# Mira V3.1 Implementation Review

Date: 2026-05-21

## Scope

Reviewed the V3.1 implementation surface against `docs/v3.1-architecture.html`, with emphasis on executable modules under `lib/mira`, workflow packs, dashboard queues, and the V3 test suite.

## Current State

| Area | Status | Notes |
|---|---:|---|
| Experience ledger / delta / commit | Implemented | `record_experience`, `PipelineExecutor`, `ExperienceLedger`, `MemoryCommitLog`, and gateway-backed commits are wired. |
| Memory security gateway | Implemented, still heuristic | Blocks untrusted writes, duplicate memory, obvious injection, secret-like material, unsupported causal claims, and evidence-free hypotheses. Needs stronger semantic contradiction checks later. |
| Workflow authoring layer | Implemented MVP | Four executable representative workflows: system health, intelligence briefing, article creation, A2A trust experiment. |
| Workflow security audit | Strengthened | Compilation now audits the command plus pack-level files and sibling skill metadata/Markdown before enablement, computes file hashes, and persists runtime audit artifacts. |
| Capability preflight | Implemented | Required connector gating blocks workflows before side effects. |
| Action risk approval | Implemented MVP | Risk grants and approval queue exist; public publish is blocked without approval. |
| Causal trace | Implemented MVP | Causal links now reference persisted causal evidence records; communication, article creation, and A2A trust runs produce L3 evidence. |
| Snapshot builder | Implemented MVP | Snapshot manifests include scoring/exclusion data and hashes. |
| Eval / north-star scorecards | Implemented MVP | Operational and strategic scorecards are exposed in dashboard snapshot. |
| Durable runtime / effect log | Implemented MVP | Idempotency keys, open/unknown status, and reconciliation queue are present. |
| Web review queues | Implemented MVP | Approval, memory commit, experiment, incident, and effect reconciliation queues are exposed. |
| Legacy runtime bridge | Implemented MVP | Legacy background jobs and task completions can write V3 experiences and prepare snapshots. |

## Work Completed In This Pass

- Added bundle-level workflow audit so a workflow command cannot enable a malicious `skill.yaml` or `SKILL.md` in the same pack.
- Added regression coverage proving compilation fails when a referenced skill Markdown contains a suspicious remote shell payload.
- Ran the local `a2a_trust_experiment` workflow once, producing an artifact and active hypothesis for the strategic north-star loop.
- Added persisted workflow audit artifacts with deterministic audit hashes for runtime workflow compilation.
- Ran the local `system_health` workflow once, producing `data/v3/workflow_audits/system_health-0ccff264c416.json`.
- Corrected the operational `causal_link_validity` hard gate so it rates asserted causal links rather than routine records with no causal claim.
- Added `data/v3/causal_evidence.jsonl` as the persisted causal evidence log and wired dashboard scoring to validate causal links against it.
- Ran the communication pipeline twice; the second run used prior memory, changed reply behavior, and wrote an L3 causal evidence record.
- Extended L3 causal evidence emission to repeated `article_creation` and `a2a_trust_experiment` workflow runs.
- Added dashboard/status visibility for causal evidence counts by level.

## Verification

- `tests/v3`: 60 passed.
- `v3_status --json`: strategic and operational scorecards now have no hard gate failures.
- Live dashboard evidence counts: L3 = 3, covering communication, article creation, and A2A trust.

## Remaining Gaps

1. More workflows need to emit L3/L4 causal evidence, especially non-executable catalog paths such as `podcast_production`, `self_evolution`, and `memory_maintenance`, before Mira can make broad claims that memory changed behavior across modules.
2. Workflow packs are representative, not complete for every catalog pipeline. The next highest-ROI packs are `self_evolution`, `memory_maintenance`, and `social_reactive`.
3. Security gateway checks are deterministic but shallow. Add structured finding types for privacy downgrade, undeclared tool use, and semantic contradiction.
4. Audit artifacts are now persisted with hashes, but they are not cryptographically signed with a private key.
5. Self-evolution is still experiment-shaped rather than fully canary/rollback-shaped.

## Next Move

Create executable MVP workflow packs for `podcast_production`, `self_evolution`, and `memory_maintenance`, then wire their causal evidence and review-queue behavior into the same runtime path.
