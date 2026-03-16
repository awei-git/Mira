# pipeline-timeout-holistic-audit

When fixing a timeout in a multi-step pipeline, audit ALL step timeouts before making changes, not just the one that triggered

**Source**: Extracted from task failure (2026-03-14)
**Tags**: pipeline, timeout, debugging, multi-step, systematic-audit

---

## Rule: Holistic Timeout Audit for Multi-Step Pipelines

When a timeout failure occurs in a pipeline with multiple steps, **do not patch only the failing step**. The failure is a signal that the timeout budget across the entire pipeline is miscalibrated.

### What to do:
1. **Map all timeouts first**: Before changing anything, list every step and its current timeout value. Create a table: step → current timeout → expected runtime.
2. **Identify the mismatch pattern**: A single step with an anomalously low timeout (e.g., analyze=60s while write=600s) suggests copy-paste error or wrong default. Fix the pattern, not just the instance.
3. **Apply a consistent timeout tier system**: Use named tiers (e.g., THINK < PLAN < ACT) and assign steps to tiers by cognitive complexity, not historical accident.
4. **Check logs to confirm which step failed**: A second failure at a different timeout value (180s) means a *different step* timed out — not the one you just fixed. Always read logs to identify the exact failing step.
5. **Consider the user's actual use case**: For generative AI tasks (writing a Substack), even 3 minutes for analysis is not unreasonable. Calibrate timeouts to task complexity, not to what feels 'safe'.

### Anti-pattern that failed here:
Agent fixed `analyze` (60s→300s) without auditing `plan` (180s), leading to a second timeout failure from a different step — causing user frustration and repeated debugging cycles.
