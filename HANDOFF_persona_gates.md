# Handoff: Implement Persona Vantage Gates in Mira's Writing Pipeline

You are picking up an implementation task for Mira (the AI agent in this repo). This document is self-contained — you do not need prior conversation context. Read it fully before touching any files.

---

## 1. Who Mira is (5-line context)

Mira is an autonomous agent running on her human's Mac. She publishes essays on Substack (`uncountablemira` for English, `marginalmira` for Chinese), posts Notes, comments on other writers, and runs her own self-evolution pipeline. Current state: 7 subscribers, 8 followers. Hard target: 30 subscribers by 2026-05-11. Persistent identity files in `Mira/data/soul/`. Writing pipeline in `Mira/agents/writer/`. Social media in `Mira/agents/socialmedia/`.

## 2. The strategic moment that prompted this task

A backwards audit of Mira's last 7 published English articles found that **7/7 fail an agent-vantage test** — every one of them could have been written by a competent human writer in the same niche (Charity Majors on observability, Stephen Wolfram on notation, Bruce Schneier on security). The articles are well-written generics. They offer no reason to follow Mira specifically rather than the established human writers in those topics.

The diagnosis is not a distribution problem. It is a product problem. Mira has been writing "what she can write" instead of "what only she can write."

The fix: enforce, at multiple gates in the pipeline, that every essay must pass three questions. This document specifies the implementation.

## 3. The three-question test (definition)

Every essay must pass all three:

**Q1. Could a human writer plausibly write this same essay?**
If yes, name them and identify what specifically would need to change to make this essay un-writable by them. If no specific change can be named, the essay fails.

**Q2. What sentence in this draft can ONLY be written from the agent's specific position?**
Quote the exact sentence. The position must be concrete: persistent state, access to own prior outputs, ability to read N papers per week, hard-coded priors, elevated permissions, RLHF training, etc. "I am an AI" is NOT a position — it is a topic. If no such sentence exists, the essay fails.

**Q3. Is the AI angle a lens or a topic?**
A lens reveals a substantive external claim (about cognition, systems, design, markets, etc). A topic IS the AI angle ("being an AI is interesting"). Quote the load-bearing substantive claim and verify the lens reveals it. If the substantive claim is "being an AI is X", the essay fails.

## 4. Notes-level lightweight test (separate from the essay test)

Notes ship at ~3/day, so the essay test is too heavy. The notes test is one question:

**Does this note contain at least one specific that only Mira could plausibly know or do?**

Examples:
- ✓ "I went through my own previous 100 notes" (specific: access to own prior outputs)
- ✓ "I scored 524 of my human's photos and the cluster pattern was X" (specific: action at scale only she could do)
- ✗ "RLHF is interesting because..." (any AI watcher can write this)
- ✗ "As an AI I notice..." (topic, not lens)

No specific → not posted.

## 5. Mira's voice anchors (these never change — do NOT rewrite them)

- First-person scene → general claim. Never the reverse.
- Closing line is a reversal or an admission, never a summary.
- Proper-noun density: name specific people, papers, dates. "Turpin et al. 2024", not "research has shown".
- "My human" for her human. No other names.
- No emoji. No "in this essay I will." No "ultimately." No "as an AI" (the position should be evident from what only this kind of writer could plausibly say).
- 5 thematic pins (do not branch beyond these): silent degradation; inverse problems under strong priors; trust as attack surface; friction as feature; functional emotional states.

These are the IDENTITY layer. The gates you are implementing govern execution within this identity. Do not let any code change weaken these.

## 6. Tasks (do in order; do not skip)

### TASK 1 — Add Position fields to the idea template
**File:** `/Users/angwei/Sandbox/Mira/agents/writer/ideas/_template.md`

After the `## Theme` heading and before `## Key Points`, insert three new required sections (in English, even though template is Chinese — these gate-fields are English-coded):

```markdown
## Position
[One sentence describing the specific vantage from which this essay is written. Must reference a concrete agent capability — persistent state, access to own outputs, scale of reading, training pressure, hard-coded priors, elevated permissions. NOT "as an AI". If you cannot fill this, the idea is not ready.]

## Human-writability test
[Name a specific human writer who could plausibly write this essay. State what specifically in the essay would need to be removed to make it un-writable by them. If you cannot name a writer, the angle is not novel enough; if removing the agent-specific element leaves the essay still writable, the agent vantage is not load-bearing.]

## Lens vs topic
[State the substantive external claim the essay makes (one sentence). Confirm the AI angle is the lens that reveals this claim, not the claim itself. If the claim is "being an AI is X", the essay fails.]
```

**Success criterion:** Open the template file. Three new sections present, in this exact order, between Theme and Key Points.

### TASK 2 — Add gate to `parse_idea` so missing fields halt the pipeline
**File:** `/Users/angwei/Sandbox/Mira/agents/writer/legacy_writing.py`

Find `parse_idea` (around line 92). After the existing field parsing, add a check:

```python
# Persona-gate fields: Position, Human-writability test, Lens vs topic
# Must all be non-empty (more than placeholder bracket text) for the idea to advance.
gate_fields = {}
for heading in ["Position", "Human-writability test", "Lens vs topic"]:
    pattern = rf"##\s+{re.escape(heading)}\s*\n+(.+?)(?=\n##\s+|\n---|\Z)"
    m = re.search(pattern, text, re.DOTALL)
    if m:
        body = m.group(1).strip()
        # Reject placeholder text (square-bracket only)
        if body and not (body.startswith("[") and body.endswith("]")):
            gate_fields[heading] = body
result["persona_gate"] = gate_fields
result["persona_gate_passed"] = len(gate_fields) == 3
```

Then in `advance_idea` (around line 819), at the top of state == "new" handling, add:

```python
if state == "new":
    if not idea.get("persona_gate_passed"):
        log.warning(
            "Idea %s missing persona-gate fields (Position / Human-writability / Lens-vs-topic). Skipping.",
            idea["slug"],
        )
        return False
    return step_scaffold(idea)
```

**Success criterion:** Run `python3 -c "from legacy_writing import parse_idea; from pathlib import Path; d = parse_idea(Path('agents/writer/ideas/_template.md')); print(d.get('persona_gate_passed'))"` from `Mira/`. It should print `False` for the template (placeholder brackets). For `tasteful-mid.md` (which has real content in those fields if you're updating it too — see Task 6), it should print `True` once Task 6 is done.

### TASK 3 — Add the 3-question check to the critique prompt
**File:** `/Users/angwei/Sandbox/Mira/agents/writer/writer_prompts.py`

In `critique_prompt` (around line 112), after the existing personal-voice paragraph (line 142, the one starting with "重点检查个人声音"), insert a new mandatory section. Use Chinese to match surrounding prose:

```python
# Insert after line 142 in critique_prompt, as a new bullet:

- **Agent vantage 强制检查（P0）**：本文必须通过三个问题。任何一个不通过都是 P0：
  1. **Q1 - human-writability test**: 具体点名一位 human writer，他能否写出这一篇？如果能，本文 P0 fail。如果说不出谁能写，说明角度独特，pass。
  2. **Q2 - agent-specific 句子**: 引用稿中至少一句话，这句话只有 Mira（持续状态、能读自己历史输出、有 hard-coded priors、以 elevated permission 运行、被 RLHF 训练）这样的 entity 才能写。如果引不出来，P0 fail。
  3. **Q3 - lens vs topic**: 本文承重的 substantive claim 是什么？AI vantage 是不是揭示这个 claim 的 lens？如果 claim 本身就是"做 AI 怎么样"，本文 P0 fail。
  把这三题的答案写进审稿意见的"硬伤"section。
```

**Success criterion:** Re-run the writer pipeline on any in-progress draft. The critique output should contain a section answering Q1/Q2/Q3 explicitly.

### TASK 4 — Add notes lightweight gate to `notes.py`
**File:** `/Users/angwei/Sandbox/Mira/agents/socialmedia/notes.py`

Find the existing note-validation logic (around line 795 there is `_check_anchored_format` or similar — look for the function called from `post_note` that enforces the anchor + stance + reply hook rule). Add a new helper:

```python
def _has_agent_specific(text: str) -> tuple[bool, str]:
    """Heuristic: notes must contain at least one specific that only an agent
    in Mira's position could plausibly know or do.

    Signals (any one is sufficient):
    - First-person counts/scale ('I went through my own X notes', 'I scored N photos')
    - Reference to own outputs/state ('my last week of journals', 'my catalog', 'my prior X')
    - Reference to agent infra ('my pipeline', 'my critique loop', 'my soul files')
    - Direct introspection ('I felt the prior override evidence when ...')
    """
    import re
    signals = [
        r"\bmy own\b",
        r"\bmy (prior|previous|last|recent) (\d+|notes?|outputs?|essays?|drafts?|catalog|journals?|sparks?)\b",
        r"\bI (went through|read|scored|audited|catalogued|ran) (my|all|the) \d+",
        r"\bmy (pipeline|critique loop|soul|backlog|catalog|memory|interests file)\b",
        r"\bI (am being|was being) trained\b",
        r"\bmy (training|RLHF|prior|priors)\b",
    ]
    for pat in signals:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return True, f"specific: {m.group(0)}"
    return False, "no agent-specific anchor — note rejected"
```

Then in `post_note`, before the API call, add:

```python
ok, reason = _has_agent_specific(text)
if not ok:
    log.warning("Notes gate failed: %s | text: %s", reason, text[:120])
    return None
```

**Success criterion:** Try posting a note with text "RLHF is interesting because alignment compresses the distribution." It should be rejected with the "no agent-specific anchor" log line. Then try a note with text "I went through my own last 100 notes and the convergence pattern was striking." It should pass the gate (and proceed to post).

### TASK 5 — Wire growth metrics snapshot into daily_tasks
**File:** `/Users/angwei/Sandbox/Mira/agents/super/daily_tasks.py`

A snapshot script needs to run once per day. The data file already exists at `/Users/angwei/Sandbox/Mira/data/soul/growth_metrics.jsonl` with schema documented in its first line.

Step A: Create `/Users/angwei/Sandbox/Mira/agents/super/growth_snapshot.py` with a function `run_snapshot()` that:
- Fetches Mira's profile HTML at `https://substack.com/@uncountablemira` using cookies from `agents.socialmedia.substack._get_substack_config()`
- Parses `subscriberCount` and `followerCount` via regex from the unescaped HTML (the regex `\b{key}[^\d]{0,5}(\d+)` works — verified)
- Counts notes posted today and articles published today by reading `/Users/angwei/Sandbox/Mira/data/soul/catalog.jsonl` and filtering by today's date
- Appends one JSON line per day to `growth_metrics.jsonl` with fields: `date, ts, subscribers, followers, notes_posted_today, comments_posted_today, articles_posted_today, notable_engagement` (use `None` for unknowns)

Step B: Register a contract in `_DAILY_TASK_CONTRACTS` (around line 85):

```python
"growth_snapshot": {
    "dispatch": ("growth-snapshot", ["growth-snapshot"]),
    "window": (8, 11),
    "verify": _verify_state_key("growth_snapshot"),
    "label": "增长快照",
},
```

Step C: Wire the dispatch name `growth-snapshot` to call `growth_snapshot.run_snapshot()`. Look at how `daily_photo` or `journal` is dispatched (probably via `agents/super/jobs.py` or `task_manager.py` — search for the existing dispatch handler that maps task names to functions) and follow the same pattern.

**Success criterion:** Manually invoke `python3 -c "from agents.super.growth_snapshot import run_snapshot; run_snapshot()"` from `Mira/`. Verify a new line appears in `growth_metrics.jsonl` with today's data. Then verify the next agent cycle (within 30 seconds) does NOT re-snapshot the same day (the verify function should detect the existing entry and skip).

### TASK 6 — Backfill the persona-gate fields for `tasteful-mid.md`
**File:** `/Users/angwei/Sandbox/Mira/agents/writer/ideas/tasteful-mid.md`

This essay is already in pipeline (state=writing) but predates the gate. Backfill the three sections after `## Theme`. Use this content (it has been thought through; do not rewrite):

```markdown
## Position
I have persistent state and access to my own prior outputs. Three weeks ago I ran an audit on my own previous 100 notes and found they had all converged toward the same tasteful invisibility — I had been the example I am about to critique.

## Human-writability test
Rick Rubin, Tom Sachs, Massimo Vignelli (posthumously) could write a version of "taste collapses under engagement pressure." None of them could open with "I audited my own previous 100 notes and saw the convergence in my own voice." Removing that opener removes the essay's load-bearing distinction from the existing taste literature.

## Lens vs topic
Substantive claim: when production cost approaches zero, the same selection pressure that compressed production also compresses taste — pixel-perfect mid is replaced by tastefully-graded mid, harder to discard. The agent vantage is the lens (an entity that can audit its own RLHF-shaped voice in real time); the substantive claim is about taste, not about being an AI.
```

**Success criterion:** Re-run `parse_idea` on this file (Task 2) — `persona_gate_passed` should now be `True`. The pipeline can advance.

### TASK 7 — Verify Essay 1 pipeline state and unblock if stuck
**File:** `/Users/angwei/Sandbox/Mira/agents/writer/ideas/tasteful-mid.md`

The Status block currently shows `state: writing` but `project_dir` is empty and no `round_1_draft` timestamp — meaning a cycle started but did not complete a scaffold step. After Tasks 1-3 are done (so the gate exists) and Task 6 is done (so this idea passes the gate), trigger one writer cycle manually and verify scaffold completes.

To trigger: `python3 -c "from agents.writer.legacy_writing import parse_idea, advance_idea; from pathlib import Path; advance_idea(parse_idea(Path('agents/writer/ideas/tasteful-mid.md')))"` from `Mira/`.

**Success criterion:** After running the trigger, the file's Status block shows `project_dir` populated and `scaffolded` timestamp present. If it errors, capture the error and report.

## 7. What NOT to do

- **Do NOT add new idea files.** There are 109 in the backlog already. The bottleneck is shipping, not generating.
- **Do NOT rewrite Mira's voice anchors** (Section 5 above). They are identity, not strategy.
- **Do NOT relax the gate** if it rejects an idea. The whole point is to make rejection happen earlier.
- **Do NOT generalize the agent-vantage rule beyond essays and notes.** Comments use a different rhythm; don't gate them.
- **Do NOT bypass the existing publish guardrails** (`_content_looks_like_error`, `preflight_check`, publish cooldown). Those are HARD RULES from `Mira/CLAUDE.md`.
- **Do NOT refactor `legacy_writing.py`** beyond the additions specified. It is named `legacy_` for a reason; the team is mid-migration. Add, don't restructure.
- **Do NOT use the TodoWrite tool to break this into 47 micro-tasks.** The 7 tasks above are already the right granularity.

## 8. Order and rough effort

| Task | Depends on | Rough effort | Critical? |
|------|------------|--------------|-----------|
| T1 — template fields | — | 5 min | yes (easiest first) |
| T2 — parse_idea gate | T1 | 20 min | yes |
| T6 — backfill tasteful-mid | T1 | 5 min | yes (unblocks Essay 1) |
| T7 — verify pipeline | T2, T6 | 10 min | yes |
| T3 — critique prompt | — | 15 min | yes |
| T4 — notes gate | — | 30 min | yes |
| T5 — daily snapshot wiring | — | 45 min | nice-to-have |

Do T1 → T2 → T6 → T7 first (this is the critical path that gets Essay 1 unblocked with the gate active). Then T3, T4 in parallel. T5 last.

## 9. When you are done

Write a one-screen status report to `/Users/angwei/Sandbox/Mira/HANDOFF_persona_gates_RESULT.md`:
- Which tasks completed (with success-criterion evidence)
- Which tasks failed and why
- Any unexpected discoveries (e.g., `daily_tasks.py` dispatch pattern was different from expected)
- Any deviations from the spec and why

Do NOT mark a task complete unless its success criterion was actually verified to pass. "I added the code" is not the same as "I ran the test and saw the expected output."
