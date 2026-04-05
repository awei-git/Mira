# verify-output-path-before-task-completion

Always confirm the exact output file path exists before marking a task as complete

**Source**: Extracted from task failure (2026-04-05)
**Tags**: file-io, task-verification, agent-reliability, output-validation

---

When a task requires producing a file output, the agent must verify the output file actually exists at the expected path before reporting success.

**The failure pattern:** The agent completed execution but the verification step failed because the output file was never written to the expected location (`task/output.md` inside the pytest temp directory). The task reported completion without confirming the artifact existed.

**Rule:** After any file-writing operation, immediately verify with an existence check (e.g., `ls`, `os.path.exists`, or equivalent) that the file was created at the exact intended path — not just that the write operation returned without error.

**Common causes of this failure:**
- Writing to a relative path that resolves differently than expected
- Writing to a parent directory instead of the expected subdirectory
- Silent failure in the write step (exception caught but not re-raised)
- Path constructed from variables where one component was empty or wrong

**How to apply:**
1. After constructing the output path, log or assert the full absolute path before writing.
2. After writing, confirm the file exists at that exact absolute path.
3. If the file does not exist post-write, treat it as a task failure — do not return success.
4. In test contexts especially, use `tmp_path / 'task' / 'output.md'` style construction and verify each path segment exists.
