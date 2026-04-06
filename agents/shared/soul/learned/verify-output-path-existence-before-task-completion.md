# verify-output-path-existence-before-task-completion

Always confirm the exact output file path exists and is written before marking a task complete

**Source**: Extracted from task failure (2026-04-05)
**Tags**: file-output, verification, agent-reliability, task-completion

---

## Rule: Verify Output File Existence Before Task Completion

When a task requires producing file output, the agent must explicitly confirm the file exists at the expected path before declaring success.

**What went wrong:** The agent completed execution without verifying that `/private/var/.../task/output.md` was actually written. The verification step caught a missing file — meaning the agent either wrote to the wrong path, failed silently, or never wrote at all.

**Actionable steps:**
1. After any file-write operation, immediately read back or stat the file to confirm it exists.
2. If the output path is constructed dynamically (temp dirs, pytest fixture dirs), log the resolved path before writing — never assume the path is what you intended.
3. If writing fails silently (no error thrown but file absent), treat that as a hard failure, not a recoverable warning.
4. When operating in temp directories (e.g. `/tmp`, `/var/folders`), be aware that paths can be session-scoped and may not persist across subprocess boundaries.

**Pattern to watch for:** Task specs that reference files in OS temp directories or test fixture directories are especially prone to path resolution mismatches. Confirm the working directory context matches expectations before writing.
