---
name: Long-Running Agent Harness
tags: [agents, architecture, continuity, task-management, quality]
source: Anthropic engineering blog, 2026-03-24
---

# Long-Running Agent Harness

Pattern for maintaining agent continuity and quality across multiple sessions/context windows. From Anthropic's "Effective Harnesses for Long-Running Agents."

## Core Architecture: Dual Agent + External State

**Initializer Agent** runs once per project: sets up git, creates progress.md, generates structured completion criteria (JSON checklist). **Coding/Worker Agent** runs per-session: reads progress, does one task, validates against checklist, updates progress.

## Three Techniques

### 1. Progress Bridge (progress.md)
Each session ends by writing a human-readable summary of what was done and what's next. Next session reads this FIRST — not the full history. Cheap, immediate, prevents context loss across sessions.

**How to apply:** Task worker should write `progress.md` in workspace at end of each run. On re-entry (reply/follow-up), read progress.md before planning.

### 2. Structured Completion Criteria
Define acceptance conditions as explicit JSON checklist BEFORE starting. Agent checks each condition and cannot self-declare "done" — must pass all checks. Prevents the "Mira says done but output is garbage" failure mode.

**How to apply:** For multi-step tasks, generate a `criteria.json` in planning phase. Execution phase checks each criterion. Only mark "done" when all pass. If any fail, mark "needs-input" with the failure list.

### 3. Fixed Startup Sequence
Every session begins with the same steps: read task → read progress → check artifacts → pick smallest next unit. Reduces token waste and state confusion.

**How to apply:** Task worker's `main()` should have a standard preamble before planning: load workspace state, check for prior results, load conversation history, THEN plan.

## Limitations
- Only validated for web app development (code + tests)
- Non-code domains (writing, research) need adaptation
- Requires well-defined "done" criteria — open-ended tasks don't fit cleanly

## Anti-Patterns This Prevents
- Agent does too much in one session → crashes mid-way
- Agent declares "done" prematurely → quality gap
- Agent re-reads entire history every session → token waste
- Agent loses context across sessions → repeats or contradicts prior work
