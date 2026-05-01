# V2 Week 0 Baseline

Date: 2026-05-01
Plan: `docs/mira-next.md`
Current pointer: `docs/CURRENT_PLAN.md`

## Baseline Snapshot

- Branch: `main`
- Checkpoint before V2 execution: `41581a6 checkpoint mira v2 control plane work`
- Current task-control direction: Postgres/API canonical, iCloud removed from normal command truth.
- Current V2 implementation state: Week 0 guardrails being installed.

## Known Manual Gates

- V2 Substack announcement still requires publish confirmation.
- iOS `v2_status` feed card/reply path has server API + bridge item support; still requires live MiraApp verification.
- Week 1 pre-flight estimate/session comparison still needs to be written after Week 1 task decomposition.

## Week 0 Local Artifacts

- `data/v2_status/gates.yaml`
- `data/v2_status/acceptance.yaml`
- `agents/super/cli/v2_status.py`
- `POST /api/{user_id}/v2-status/cards`
- `POST /api/{user_id}/v2-status/cards/{card_id}/reply`
