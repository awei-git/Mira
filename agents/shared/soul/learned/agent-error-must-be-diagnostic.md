# agent-error-must-be-diagnostic

Task failures in agent pipelines must emit enough context to be actionable — 'unable to generate reply' is a symptom, not a cause

**Source**: Extracted from task failure (2026-03-17)
**Tags**: agent-pipeline, error-handling, observability, reflection-system

---

## Rule: Agent Errors Must Be Diagnostic

When an agent task fails, the error record must capture sufficient state to distinguish between failure modes. A message like "无法生成回复" (unable to generate reply) is opaque: it could indicate a content policy block, empty/malformed input, context overflow, rate limiting, a missing prerequisite, or a transient network fault. These require entirely different responses.

**What the error record should include:**
- The specific failure point in the pipeline (input validation? generation? post-processing?)
- The input state at time of failure (was there content to process? what was its shape?)
- The error class (policy, resource, logic, transient)
- Whether retry is safe or contraindicated

**Operational consequence:** If a failure cannot be diagnosed from its error record alone, the failure record itself is a second failure — it prevents learning and prevents automated recovery decisions.

**For reflection pipelines specifically:** Journal/comment generation tasks often fail silently when the input (journal content) is missing, empty, or not yet flushed to the expected location. Check input preconditions before invoking the generator, and log the input hash or length at failure time.

**Test:** Can you read this error record six months later and know what to fix? If not, the error instrumentation is broken.
