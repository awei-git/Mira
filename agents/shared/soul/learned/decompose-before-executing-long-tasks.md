# decompose-before-executing-long-tasks

Break complex tasks into sub-tasks under ~3 minutes each before starting execution

**Source**: Extracted from task failure (2026-03-25)
**Tags**: task-management, planning, timeouts, decomposition

---

## Rule: Decompose Long Tasks Before Execution

When given a task that could plausibly take more than 3-5 minutes, **stop and decompose it first** before writing any code or making any changes.

### Signs a task needs decomposition:
- Touches multiple files or systems
- Involves multiple distinct phases (e.g., research → implement → test → document)
- Has ambiguous scope ("refactor X", "add feature Y")
- Involves iterative steps where each step depends on previous results

### How to decompose:
1. Use `TodoWrite` to list all sub-tasks before starting any of them
2. Estimate each sub-task: if any single item feels like >3 minutes, split it further
3. Execute one sub-task at a time, marking complete before moving on
4. After each sub-task, checkpoint: does the plan still make sense?

### Why this prevents timeouts:
Timeouts (like the 10-minute limit in task runners) happen when a single execution block tries to do too much. Decomposition ensures each atomic unit of work completes well within limits, produces observable progress, and allows recovery if something fails midway.

### Anti-patterns to avoid:
- Starting to write code before the full plan is clear
- Treating "I know what to do" as equivalent to "this will fit in one step"
- Bundling research + implementation + verification into a single undivided action
