# Self-Evolution

Use this workflow skill when a recurring mismatch needs an auditable improvement experiment.

## Procedure

1. Start from ledger evidence, scars, failure signatures, and eval calibration.
2. Cluster repeated mismatches before proposing a change.
3. Write an experiment record with claim, target metric, risk, and rollback plan.
4. Require human review for high-risk behavior, policy, connector, memory-schema, or code changes.
5. Put implementation work on an explicit canary branch before any promotion.
6. Record causal evidence only when a prior memory or experiment changes the proposed intervention.

## Do Not

- Do not modify production code outside an explicit canary branch.
- Do not change safety, publication, connector, or memory policy without approval.
- Do not claim self-improvement without an experiment record.
