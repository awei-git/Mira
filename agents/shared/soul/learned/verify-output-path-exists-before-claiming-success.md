# verify-output-path-exists-before-claiming-success

Always confirm the output file was actually written to the expected path before marking a task complete

**Source**: Extracted from task failure (2026-04-06)
**Tags**: file-output, verification, agent-tasks, testing

---

When a task requires writing output to a file, the agent must verify the file exists at the exact expected path *before* reporting success.

**The failure pattern:** Agent performs work, believes it wrote output, but the file either (a) was written to a different path, (b) was never written due to a silent error, or (c) was written inside a subprocess/temp context that did not persist.

**Rule:** After any file-writing step that produces a required artifact, immediately confirm existence with a file check (`os.path.exists`, `stat`, or equivalent). Do not rely on the absence of an exception as proof of success.

**Common causes in test environments:**
- Working directory differs between planning and execution contexts
- Relative paths resolve differently inside spawned subprocesses
- Output was written to a temp dir that was cleaned up before verification
- The `task/` subdirectory was not created before writing `output.md` into it

**Actionable checklist:**
1. Before writing: ensure parent directory exists (`mkdir -p` or equivalent)
2. After writing: stat the file at the absolute path expected by the verifier
3. If paths are constructed dynamically, log the resolved absolute path
4. In test scaffolding: prefer absolute paths derived from a known fixture root, not relative paths from CWD

**Why this matters:** The verifier checks a specific absolute path. Any mismatch — even one directory level — produces a "file does not exist" failure that looks like a complete task failure, obscuring that the work was actually done.
