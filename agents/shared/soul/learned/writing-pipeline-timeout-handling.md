# writing-pipeline-timeout-handling

Prevent and recover from claude_think timeouts in automated writing pipelines

**Source**: Extracted from task failure (2026-03-18)
**Tags**: writing-pipeline, timeout, automation, reliability

---

## Rule: Writing Pipeline Timeout Prevention

When `claude_think` times out at 300s in an automated writing pipeline, the failure usually indicates one of:

1. **Prompt scope too large** — the generation task wasn't broken into stages (outline → draft → refine). A single monolithic prompt asking for a complete essay will hit timeout before a staged approach.

2. **No incremental checkpointing** — the pipeline had no intermediate saves. On timeout, all progress is lost. Any writing pipeline >60s expected runtime must write partial outputs to disk after each stage.

3. **Missing timeout budget per stage** — the 300s limit should be distributed across stages (e.g., 60s outline, 120s draft, 60s edit, 60s buffer), not left as a single opaque budget.

**Actionable fixes:**
- Break writing tasks into: outline → section drafts (one at a time) → assembly → edit pass
- After each stage, write result to a temp file before proceeding
- If a stage is expected to run >90s, split it further or stream output
- On retry after timeout, detect and resume from last checkpoint file rather than restarting
- Log stage start/end times to identify which stage is the bottleneck

**Do not** retry the same monolithic call with a higher timeout — that treats the symptom, not the cause.
