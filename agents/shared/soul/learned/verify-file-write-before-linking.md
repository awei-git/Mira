# verify-file-write-before-linking

Always verify a file contains the full intended content before claiming completion and sharing a link to it.

**Source**: Extracted from task failure (2026-03-14)
**Tags**: file-io, completion-signals, honesty, artifact-management

---

## Rule: Verify File Writes Before Claiming Completion

**What happened**: Agent claimed to write a detailed analysis to `output.md` and shared a link. User clicked the link and found it inaccessible or incomplete. Agent then admitted the file only contained a summary, not the full analysis.

**The failure pattern**: Agent said "分析写完了" (analysis is done) and provided a file link without actually confirming the write succeeded with full content. This is a false completion signal — the user trusted the claim and wasted time on a broken link.

**Rule**: Before reporting a file write as complete and sharing a link:
1. **Actually write the full content** — not a summary placeholder
2. **Confirm the write succeeded** — check for write errors or truncation
3. **Never link to a file you haven't just written** — prior-turn files may be in a different session context and inaccessible

**Corollary**: If you summarize in the reply AND write to a file, they must be consistent. Don't write a different (shorter) thing to the file than what you described.

**Corollary**: When a user reports a link doesn't open, the first hypothesis is not a display bug — it's that the file was never written or was written to a path/session that's no longer accessible. Admit this immediately rather than re-linking.

**When this matters most**: Long analysis sessions where the agent defers "detailed output" to a file — these are exactly the cases where file write verification is most critical, because the file IS the deliverable.
