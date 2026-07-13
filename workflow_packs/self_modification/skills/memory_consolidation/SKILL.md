# Memory Consolidation

Use this workflow skill when memory needs review before compaction, merge, archive, or quarantine.

## Procedure

1. Inspect snapshot items and recent experience records.
2. Identify stale, redundant, contradictory, or suspicious memory candidates.
3. Produce a review artifact before any destructive or irreversible change.
4. Stage destructive compaction behind approval and effect-log reconciliation before any adapter can mutate the kernel.
5. Route risky changes to the memory commit queue.
6. Prefer `no_kernel_change` when the review finds no durable lesson.

## Do Not

- Do not delete, overwrite, compact, or archive memory directly from this MVP workflow.
- Do not run a compaction adapter without approval and effect-log reconciliation.
- Do not release quarantined memory without review.
- Do not create causal links without persisted causal evidence.
