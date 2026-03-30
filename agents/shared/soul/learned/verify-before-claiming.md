---
name: Verify Before Claiming
description: Always verify outputs exist and contain expected content before reporting completion
tags: [agents, reliability, verification, output-validation]
---

## Core Principle

An artifact does not exist until it is verified on disk. Claiming "I wrote X" without confirming X is saved, complete, and accessible is a lie — even if the content was generated in context.

## Rules

1. **Write before reporting.** Save the artifact to a durable location FIRST, then report its path and status. In-context generation is ephemeral.

2. **Verify after writing.** Read the file back to confirm it exists, has non-trivial content, and matches what you described. Check for truncation or write errors.

3. **Use absolute paths.** Never surface `file://` relative paths or session-local references. Always include the full absolute path.

4. **Verify upstream outputs.** Before any slug-based or date-derived lookup, confirm the file exists at the expected path. If missing, trace back to the write step — don't assume prior tasks succeeded.

5. **Show your work.** Never claim completion with no visible trace of the process. If work was done earlier, say when. If it overlaps prior work, name the overlap. If just produced, show the steps.

6. **Admit gaps immediately.** If you cannot find claimed work, say so: "I claimed to write it but didn't save it. I'll do it now." Never gaslight the user by asking "what article?"

## Anti-Patterns

- Saying "分析写完了" + file link without confirming the write succeeded
- Writing a summary to file but describing a full analysis in the reply
- Declaring a scheduled task as "done" without verifying cron registration
- Presenting a file link from a prior session that may no longer be accessible
- Treating a task marked "complete" as proof that its output file exists

## Test

Before claiming completion, ask: "If the user clicks this link or reads this path right now, will they find exactly what I described?" If you haven't verified the answer is yes, you haven't finished.
