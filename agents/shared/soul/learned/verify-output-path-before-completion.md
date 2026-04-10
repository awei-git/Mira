# verify-output-path-before-completion

Always confirm the output file was written to the expected path before declaring task complete

**Source**: Extracted from task failure (2026-04-10)
**Tags**: file-output, task-completion, verification, agent-harness

---

When a task requires producing a file as verifiable output, the agent must explicitly confirm the file exists at the declared path before finishing.

**The failure pattern:** The agent completed execution without writing `output.md` to the task directory, or wrote it to a different path than expected. The verification step caught what the agent itself did not check.

**Rules:**
1. After any file-writing step, immediately read back or stat the file at the exact expected path to confirm it exists.
2. Never infer success from a write tool returning without error — tools can silently succeed while writing to the wrong path (relative vs absolute, temp dir vs task dir, etc.).
3. The expected output path should be established at task start, not assumed at task end. If the harness provides a working directory or output path, capture it explicitly and use it for all writes.
4. For task outputs specifically: if the task spec says output goes to `<dir>/output.md`, write to that exact path, not to CWD or a temp location.

**How to apply:** After every file write that produces task output, run a stat or read on the canonical output path. If it fails, do not declare the task done — investigate where the file actually landed and move/rewrite it to the correct location.
