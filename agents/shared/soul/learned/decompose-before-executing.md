# decompose-before-executing

Break complex tasks into sub-tasks under 2 minutes each before starting execution

**Source**: Extracted from task failure (2026-03-25)
**Tags**: task-management, timeout, planning, decomposition

---

## Rule: Decompose Before Executing

When given a task with unclear scope or multiple dependencies, **plan the decomposition first** before writing any code or running any commands.

### Signs a task needs decomposition:
- Involves more than 2-3 distinct steps
- Requires reading multiple files, then modifying them, then verifying
- Has sequential dependencies (A must complete before B)
- Involves external calls (API, shell, network) with unknown latency

### How to decompose:
1. List all distinct actions needed
2. Estimate each step: if any single step might take >2 min, split it further
3. Order by dependency, not convenience
4. Use TodoWrite to register steps before starting
5. Mark each step complete immediately when done — do not batch

### Anti-patterns that cause timeouts:
- Starting a large refactor without listing the files to touch
- Running a shell command that blocks (build, test suite) without confirming it's bounded
- Chaining tool calls inside a single response without checkpoints
- Treating "do X" as atomic when X contains 10 sub-operations

### Recovery:
If a task times out, the first action is to re-read the original request and write out every discrete step. Only then begin execution. Never retry the same monolithic approach.
