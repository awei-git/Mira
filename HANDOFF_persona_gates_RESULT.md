# Persona Gates — Implementation Result

Date: 2026-04-26
All 7 tasks completed and verified end-to-end.

## Tasks completed (with success-criterion evidence)

### T1 — Template fields ✓
Added `## Position`, `## Human-writability test`, `## Lens vs topic` between `## Theme` and `## Key Points` in `agents/writer/ideas/_template.md`. Verified by visual inspection of file.

### T2 — `parse_idea` gate + `advance_idea` skip ✓
- Added persona-gate parsing block to `parse_idea` in `legacy_writing.py` (after `content_above` extraction).
- Added skip-with-warning in `advance_idea` for state=="new" when `persona_gate_passed` is False.
- Verified: `parse_idea(_template.md)` returns `persona_gate_passed: False` (placeholder brackets correctly rejected).

### T3 — Critique prompt 3-question check ✓
Inserted Chinese-prose P0 "Agent vantage 强制检查" bullet in `critique_prompt` in `writer_prompts.py`, after the existing "重点检查个人声音" bullet (asks Q1/Q2/Q3 explicitly and instructs the critique to write answers into the 硬伤 section). Verified by reading the modified prompt.

### T4 — Notes lightweight gate ✓
- Added `_has_agent_specific(text)` helper to `notes.py` above `_note_meets_style_criteria`.
- Hooked into `post_note` after the substack-config check, before link-URL validation: failures return `None` and log `"Notes gate failed: ..."`.
- Verified directly:
  - "RLHF is interesting because alignment compresses the distribution." → rejected, "no agent-specific anchor"
  - "I went through my own last 100 notes and the convergence pattern was striking." → passes (matches `my own`)
  - "My pipeline keeps eating its own tail." → passes (matches `my pipeline`)
  - "As an AI I notice things are interesting." → rejected

### T5 — Daily growth snapshot wiring ✓
- Created `agents/super/growth_snapshot.py` with `run_snapshot()`. It fetches `https://substack.com/@uncountablemira` with the cookie from `_get_substack_config()`, parses `subscriberCount` / `followerCount` via the verified regex, counts today's notes from `data/social/notes_state.json` (key `notes_<date>`) and today's published articles from `data/soul/catalog.jsonl` (filter on `type=article, status=published, date=<today>`), then appends one JSON line to `growth_metrics.jsonl`. Replaces any existing same-day entry (preserves `_schema` header).
- Marks `state["growth_snapshot_<today>"] = ts` so `_verify_state_key("growth_snapshot")` returns True.
- Registered the contract in `_DAILY_TASK_CONTRACTS` with `window=(8, 11)` and label "增长快照".
- Wired the dispatch in `core.py`: new `elif command == "growth-snapshot"` block imports and calls `run_snapshot()`, then `_write_last_output("growth_snapshot")`.
- Verified end-to-end:
  - First invocation wrote: `subs=7, follows=8, notes=2, articles=1` for 2026-04-26.
  - Second invocation logged `growth_snapshot already done for 2026-04-26 — skipping` and did NOT add a duplicate line (file remains at 2 lines: schema + today).

### T6 — Backfill `tasteful-mid.md` ✓
Inserted the three pre-thought-out gate sections between the `## Theme` block and `## Key Points`. Verified: `parse_idea(...)` now returns `persona_gate_passed: True` with all three keys populated.

### T7 — Verify Essay 1 pipeline / unblock ✓
Reset `state: writing` → `state: new` so the gate could run on it (writing is not a recognized state in `advance_idea`). Triggered `advance_idea(parse_idea(...))` per spec; scaffold completed in ~3 minutes:
- state → `scaffolded`
- project_dir populated (`.../Mira-Artifacts/ang/writings/tasteful-mid`)
- scaffolded timestamp set (`2026-04-26 09:43`)
- Three scaffold files written: 规格.md (3268 chars), 大纲.md (4094 chars), CLAUDE.md (4237 chars)

## Unexpected discoveries

1. **`tasteful-mid.md` had a stale state value `writing`** which is not handled by `advance_idea` (the valid states are `new / scaffolded / drafting / critiquing / awaiting_feedback / feedback_* / ready_to_publish / done / error / restart`). Before scaffold could run I had to set state back to `new`. This is consistent with the handoff diagnosis ("a cycle started but did not complete a scaffold step") and the T7 instruction to "unblock if stuck."

2. **`tasteful-mid.md`'s Status block predates a schema bump.** It was missing `current_round`, `idea_hash`, `round_1_draft`, `round_1_critique`, `round_1_revision`, `feedback_detected`, `round_2_*`, `last_error`. `update_idea_status` warned for each missing field but completed the writable fields (state, project_dir, created, scaffolded). The pipeline can still advance, but those fields will be silently dropped on subsequent steps. Recommended fix outside this task: append the missing rows to the Status block (or have a one-time migration in `update_idea_status` that adds them).

3. **`catalog.jsonl` does not record notes** (only articles/essays/etc.). For `notes_posted_today` the snapshot reads `data/social/notes_state.json[f"notes_{today}"]` — the same counter that `_record_note` increments and `can_post_note` enforces against `MAX_NOTES_PER_DAY`. This is the canonical source today.

4. **`comments_posted_today` is left as `None`.** I did not find a single per-day counter; comment activity is spread across substack scratch files. Returning `None` matches the schema header's "use None for unknowns" guidance. If a later task wants this populated, the right move is to mirror notes' approach: increment a `comments_<today>` key in `social/comments_state.json` from the comment-poster.

## Deviations from spec

- **T7 trigger**: the literal trigger in the handoff (`advance_idea(parse_idea(...))`) errors with "Unknown state 'writing'". Per the T7 task title "Verify Essay 1 pipeline state and unblock if stuck", I reset the state to `new` first and re-ran. This is a deviation from the literal command but matches the stated intent. Documented above so it can be reverted/changed if you disagree.
- **T5 idempotency**: spec says "the verify function should detect the existing entry and skip"; my implementation goes one step further and also has `run_snapshot()` itself early-return when the state key exists, so even a direct call (not via the contract) is idempotent within the day. This is strictly stronger than the spec.

## Files touched

- `agents/writer/ideas/_template.md` (T1)
- `agents/writer/legacy_writing.py` (T2)
- `agents/writer/writer_prompts.py` (T3)
- `agents/socialmedia/notes.py` (T4)
- `agents/super/growth_snapshot.py` (T5, new file)
- `agents/super/daily_tasks.py` (T5)
- `agents/super/core.py` (T5)
- `agents/writer/ideas/tasteful-mid.md` (T6 + T7 state reset)
