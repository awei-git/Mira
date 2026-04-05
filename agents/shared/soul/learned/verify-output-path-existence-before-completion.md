# verify-output-path-existence-before-completion

Always confirm the output file was actually written to the expected path before declaring task success

**Source**: Extracted from task failure (2026-04-05)
**Tags**: agent-tasks, file-output, verification, harness

---

When a task requires writing output to a file, do not assume success based on the agent completing without error. Explicitly verify the output file exists at the declared path before returning.

**Rule:** After any file-writing operation, check that the target path exists and is non-empty before marking the task complete.

**Common failure modes:**
- Agent writes to a relative path that resolves differently than expected
- Agent writes to a temp/scratch location and fails to move/copy to the declared output path
- Agent silently fails the write (permission error, path doesn't exist) but exits 0
- The declared output path in the task spec differs from where the agent actually wrote

**How to apply:**
1. After instructing an agent to produce file output, always include an explicit verification step: `[file]: <expected_path>` in the task spec or run a post-task check.
2. When writing a task prompt, state the output path as an absolute path, not relative.
3. If the verify check fails, inspect what the agent actually did — check nearby paths, check if the directory was created, check agent logs — rather than retrying blindly.
4. In pytest/temp-dir contexts, the working directory during task execution may differ from the test's `tmp_path`. Confirm the agent received the correct absolute path.

**Signal this failure gives:** The agent ran and returned but produced no verifiable artifact. This is distinct from a crash — the harness completed but the postcondition was unmet.
