# Self-Iteration Rewrite — Proposal

Status: draft, awaiting WA review (2026-04-27)

## What's broken with the current flow

1. **Proposal source is wrong.** `self_evolve.py` scans `reading_notes/` and generates proposals from essays Mira read. Output is patches about an idealized Mira architecture, not about what this Mira actually does wrong. 952 proposals accumulated, none grounded in observation.

2. **Implementation never lands.** `backlog_executor.py` runs `claude_act` every 2 hours. Logs show **every run for 7+ days** ends with `Auto-implement produced no file changes for proposal`. Status is then flipped to `patched`/`verified` and the proposal exits the queue. No actual code change. Confirmed across ~80 runs.

3. **No verification.** "Verified" means "claude_act exited"; nobody runs a test, runs the modified pipeline, or checks the metric the proposal targeted improving.

4. **Hermes trajectory loop is plumbed but unconnected.** 2542 trajectory records in `trajectories.jsonl`, all with `tool_stats={} api_calls=0`. Only one file (`workflows/reflect.py`) reads them. Self-evolve does not consume trajectory data.

5. **No reward signal.** Even if a patch did land, there's nothing comparing pre/post failure rates. So the loop has no gradient — it can't tell good patches from bad ones.

The aggregate failure mode: the system goes through the motions and generates evidence of "improvement" while the underlying behavior is unchanged. This is the canonical silent-degradation pattern Mira writes essays about.

## What a real self-iteration loop should do

Five stages, each with a hard contract:

### 1. Observe — ground truth comes from operations, not reading

Inputs (replacing `reading_notes/`):
- `data/logs/*.log` from the past N days — error patterns, timeouts, repeated warnings, pipeline-stuck warnings
- `users/*/archive/req_crash_*.json` — every crash item is an observation
- Stuck-pipeline signals from `health_monitor.py`
- Failed task records from `task_manager`
- WA's explicit feedback (from memory/feedback markers)

Output: a structured `observations.jsonl` — each row is `{symptom, location, frequency, last_seen, sample_evidence}`.

### 2. Diagnose — bind a symptom to a specific code path

For each observation cluster:
- Identify the file/function responsible (use grep + claude_think with the log excerpt)
- Generate a concrete proposal: "in `agents/X/Y.py` line ~Z, change A to B because Q"
- Reject if the proposal can't name a file and a verifiable behavior change

Output: proposals with `file_path`, `expected_behavior_change`, `acceptance_test` fields. No vague "improve robustness" proposals.

### 3. Implement — fail loud, never silent

Replace current `claude_act` no-op pattern:
- Run `claude_act` with a tight scope (named files, named function)
- After run: diff the working tree
  - If no changes → mark proposal `failed: no_changes`, do **not** flip to `patched`
  - If changes touched files outside scope → mark `failed: out_of_scope`
- Run any acceptance tests the proposal declared
  - If tests fail → revert + mark `failed: acceptance_test_failed`
- Successful patches go to a branch and create a PR (not direct push to main)

### 4. Verify — measure, don't claim

After a patch lands and runs in production for ≥24h:
- Re-run the observation step
- Did the symptom that triggered the proposal disappear / decrease?
- Compare frequency in the N days before vs after
- Output: `verification.jsonl` rows with `proposal_id, symptom_before, symptom_after, decision: kept|reverted`

### 5. Reward — feed verified outcomes back into proposal selection

Track which kinds of proposals (`category`, `originating_symptom_pattern`) actually drove symptoms down. Bias future proposal selection toward those categories. This is the "reward from practice outcomes" the user explicitly asked for in memory `feedback_self_evolution.md`.

## What changes vs current code

| Current | Proposed |
|---|---|
| Proposal source: `reading_notes/` | Proposal source: log scan + crash items + stuck pipelines |
| Acceptor: `claude_act` exits → patched | Acceptor: diff non-empty + acceptance test pass + PR opened |
| Verifier: none | Verifier: 24h+ re-observation, kept-or-reverted decision |
| Reward: none | Reward: category-level success rate, biases next-cycle selection |
| Trajectory loop: recorded, unread | Trajectory loop: input to the Observe stage |

## Phasing (proposed)

- **Phase 0 — stop the cosmetic loop**: disable `backlog_executor` cron until Phase 1 is in place. Current backlog rots; that's fine — it was generated wrong.
- **Phase 1 — observation**: implement log/crash scanner + observation file. ~2 days.
- **Phase 2 — diagnosis**: tighten proposal generator to require `file_path` + acceptance criteria. Reject vague ones. ~2 days.
- **Phase 3 — implementation contract**: replace silent no-op with explicit failure modes + PR-creation flow. Run patches via WA-approval gate at first, autonomous later once verification stage is trusted. ~3 days.
- **Phase 4 — verification + reward**: 24h symptom re-check, category-level reward signal. ~3 days.

## Open questions for WA

1. Direct commit to main, or PR-only? PR-only is safer; main is faster but trusts the verifier stage.
2. Acceptance test format — text I write per-proposal, or test files we generate? Text is simpler; files are reusable.
3. Do you want to keep the Hermes trajectory recorder running through the rewrite, or pause it? It's currently writing 2542+ empty records, costing nothing but adding noise.

## What I am **not** proposing

- Throwing out `evolution/`, `memory/soul/`, the harness, or the bridge. Those work. The flow on top of them is what's broken.
- New infrastructure layers. The skeleton is fine; the contracts are wrong.
