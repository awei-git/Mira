# Week 1 Pre-Flight Estimate

Date: 2026-05-01
Plan section: `docs/mira-next.md` Week 1

## Scope

Week 1 is intentionally narrow: make iPhone task submission canonical through API/Postgres and prove the runtime can execute a submitted task to a truthful terminal state.

## Estimated Work

| Task | Owner | Estimate |
|---|---:|---:|
| Postgres/pgvector environment check and schema decision | Claude Code session | 1h |
| migration runner / initial control schema alignment | Claude Code session | 2h |
| task queue + runtime DB hard-fail behavior | Claude Code session | 3h |
| audit event skeleton alignment | Claude Code session | 1h |
| bridge contract endpoints and heartbeat behavior | Claude Code session | 3h |
| mDNS / pinned cert app integration handoff | Claude Code session + MiraApp | 3h |
| minimal LLMProvider adapter wrapper | Claude Code session | 2h |
| STABILITY.md + identity_core draft | Claude Code session + WA | 1.5h |
| CI grep rules for banned paths | Claude Code session | 1h |
| substack production freeze verification | Claude Code session | 0.5h |
| 5-task iPhone smoke and service restart | Claude Code session + WA | 2h |

Total estimate: 20h

## Reality Check

First implementation session comparison is pending. If the first real build session deviates by more than 50%, Week 1 must be descoped before continuing.
