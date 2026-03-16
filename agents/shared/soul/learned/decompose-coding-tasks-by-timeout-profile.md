# decompose-coding-tasks-by-timeout-profile

Split research tasks and code-modification tasks into separate, smaller steps to avoid timeout kills

**Source**: Extracted from task failure (2026-03-14)
**Tags**: task-management, timeout, agent-workflow, coding-tasks

---

## Rule: Decompose Coding Tasks by Timeout Profile

**Context**: Task workers have a fixed `CLAUDE_TIMEOUT_ACT` (e.g. 600s). Research tasks (web search + summarize) typically finish in 2-5 minutes. Code development tasks (read architecture → understand dependencies → write module → integrate) often exceed 10 minutes when non-trivial.

**The failure pattern**: A single task that mixes research + code modification will appear to succeed on the research leg, then silently timeout on the coding leg. The user sees "处理失败" with no partial output.

**Rule**: When a task requires both *understanding existing code* and *writing new code*, always split into at least two sub-tasks:
1. `read-and-summarize`: "Read [file/module], describe the architecture and integration points" — fast, safe
2. `implement`: "Given this architecture [paste summary], write [specific module]" — focused, bounded

**Heuristics for splitting**:
- Any task requiring >3 file reads before writing → split at the read boundary
- Any task touching >2 files → split per file or per logical unit
- "Add X to existing system" → always split into (explore existing) + (implement X)

**Anti-pattern**: Saying "加上Notes功能" in a single task when Notes requires reading publish.py, understanding post schema, writing notes.py, and integrating — this is 4+ subtasks disguised as one instruction.

**Recovery**: If a timeout occurs, ask "what did you accomplish before timeout?" — partial work may already exist on disk.
