# verify-file-exists-before-slug-lookup

Always verify that the target file exists before attempting slug-based operations

**Source**: Extracted from task failure (2026-03-29)
**Tags**: file-operations, pipeline, slug, autowrite, precondition

---

When a task references a file by slug or date-derived path (e.g. `final.md` with `slug=2026-03-29`), the operation will fail silently or with a confusing error if the file was never created or was created under a different name/path.

**Rule:** Before any slug-based lookup or read operation, verify the file exists at the expected path. If it does not exist:
1. Check sibling directories or alternative naming conventions (e.g. `draft.md`, `index.md`, `YYYY-MM-DD.md`)
2. Check whether the generation step that was supposed to produce the file actually ran and succeeded
3. Do not assume the file was created just because a prior task was marked complete

**Operational checklist:**
- `glob` or `ls` the target directory before reading
- If the file is supposed to be produced by an upstream task, confirm that task's output before proceeding
- If the slug is date-derived, confirm the date format matches exactly (ISO 8601 vs locale-specific)

**Root cause pattern:** This error class typically indicates a broken pipeline: a write step failed silently, was skipped, or produced output at a different path than the read step expects. The fix is almost never in the read step — trace back to the write step.
