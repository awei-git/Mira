# error-context-must-be-diagnostic

Task failure logs must capture actionable diagnostic information, not just a generic failure string

**Source**: Extracted from task failure (2026-03-16)
**Tags**: error-handling, logging, observability, reflection-pipeline

---

## Rule: Error Context Must Be Diagnostic

When a task failure is logged for later review, the error context must contain enough information to diagnose the root cause without re-running the task. A failure record that contains only a generic message (e.g. '无法生成回复') is effectively unanalyzable — it tells you *that* something failed, not *why* or *where*.

**What a failure record must capture:**
- The specific operation that failed (not just the task name)
- The error type or code if available
- The system state at time of failure (inputs, relevant config, environment)
- Whether the failure is likely transient (network, timeout) or structural (logic, missing data)

**What went wrong here:** The error context is identical to the task title — meaning the logging pipeline either swallowed the real exception or was never given one. The downstream effect is that this failure is unanalyzable and the lesson-extraction loop breaks.

**Fix pattern:** At every task boundary, distinguish between:
1. Errors with diagnostic context → log with full trace
2. Errors without context → log the *absence* of context as a separate signal ("error context unavailable — possible silent failure in upstream step")

Silent failures that look like logged failures are worse than crashes — they consume review bandwidth without yielding insight.
