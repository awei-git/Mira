# verify-output-path-exists-before-task-completion

Agents must create the expected output file at the exact verified path before declaring success

**Source**: Extracted from task failure (2026-04-06)
**Tags**: agent-harness, output-verification, task-contracts, file-io

---

## Rule: Output File Must Exist at Verified Path Before Task Completion

When an agent task specifies an output file path (e.g., `task/output.md`), the agent must:

1. **Write the output file to the exact path specified** — not a sibling directory, not a differently-named file, not stdout only.
2. **Verify the file exists before returning** — use a stat/existence check as the final step, not just assume the write succeeded.
3. **Never declare success if the output path is missing** — a task that produces reasoning but no file artifact has failed, regardless of what was printed to the console.

### Common failure modes
- Agent writes to working directory instead of the task-scoped temp directory.
- Agent produces output in memory or prints it but never writes to disk.
- Agent uses a relative path that resolves differently than the harness expects.
- Agent exits early (error branch) without writing the file, but returns exit code 0.

### How to apply
Before returning from any task that has a file output contract:
```
assert os.path.exists(output_path), f"Output not written: {output_path}"
```
Or in shell: `test -f "$OUTPUT_PATH" || exit 1`

If the path comes from the harness (env var, argument), echo it back early in the task log so failures are diagnosable.
