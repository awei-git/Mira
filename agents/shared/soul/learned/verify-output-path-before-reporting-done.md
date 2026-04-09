# verify-output-path-before-reporting-done

Always confirm the output file was written to the expected path before declaring task complete

**Source**: Extracted from task failure (2026-04-08)
**Tags**: file-output, verification, agent-reliability, task-completion

---

## Rule: Verify Output File Exists Before Declaring Done

When a task requires producing a file output, the agent must verify the file exists at the specified path **before** returning success.

**What went wrong:** The agent completed its work but the output file (`output.md`) was never written to the task directory. The verification step caught this only after the fact — the agent reported completion without confirming the artifact existed.

**When this applies:** Any task with a `[file]` verification target, or any task that explicitly asks for a written output (report, summary, plan, code file, etc.).

**How to apply:**
1. After writing a file, immediately read it back or check it exists before proceeding.
2. If the task specifies an output path, write to that exact path — not a sibling directory, not stdout.
3. When working in temp/pytest directories, confirm the full path including the test-run-specific segment (e.g. `pytest-271/test_execute_plan_steps_rewrit0/task/`) is correct before writing.
4. Do not conflate "I produced the content" with "I wrote the file." Tool calls can silently fail or write to wrong paths.

**Failure signature:** `VERIFY FAILED [file]: file does not exist: <path>` — the agent ran to completion but the artifact is missing entirely.
