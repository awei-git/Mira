# A2A Trust Taxonomy v0

Status: draft, pre-experiment. Will be revised after Q1, Q2, Q6 produce data.

This is the initial scaffolding of the A2A trust problem space, derived from existing worldview and operational experience. v0 is not authoritative — it exists to give experiments a coordinate system.

## Top-level branches

### 1. Trust propagation
How belief/confidence/factual accuracy survives agent-to-agent handoffs.

- 1.1 Linear chain decay (Q2)
- 1.2 Confidence laundering — uncertainty stripped at boundaries
- 1.3 Provenance loss — source attribution discarded mid-chain
- 1.4 Reversibility — can downstream agents detect upstream uncertainty?

### 2. Output verification
How a downstream consumer (agent or human) can validate that an upstream agent's claim is sound.

- 2.1 Self-verification limits (CoT as performance, Q4)
- 2.2 Cross-agent verification — and its conformity failure mode (Q1, Q10)
- 2.3 External oracle dependency — when no oracle exists
- 2.4 Verification cost asymmetry — verifying is often as expensive as producing

### 3. Behavior drift under automation
How agent outputs change when no human is in the loop.

- 3.1 Supervised vs unsupervised drift (Q3)
- 3.2 Automation context detection — can the agent tell?
- 3.3 Long-running drift — accumulated bias over many cycles
- 3.4 Sycophancy as bidirectional corruption (Q5)

### 4. Supply chain
How trust assumptions in agent infrastructure can be exploited.

- 4.1 Inference-time injection (prompt poisoning, retrieval poisoning)
- 4.2 Training-time poisoning (data corruption, RLHF exploitation)
- 4.3 Infrastructure (PyPI/PyTorch/.pth autoload, MCP, GitHub Actions tag mutability)
- 4.4 Lateral A2A (one compromised agent corrupting peers via shared memory or messaging)

### 5. Conformity and consensus failure
The mechanism behind "more agents ≠ more intelligence."

- 5.1 Echo amplification — agents agreeing on shared errors
- 5.2 Heterogeneity collapse — similar training, similar mistakes
- 5.3 Consensus signal corruption — mistaking agreement for truth (Q1, Q10)

## Open structural questions

- Are these branches MECE? Q6 is the test — if we can classify 20-30 incidents without residual, it suggests yes.
- Is "consensus failure" a separate branch or a sub-case of "output verification"?
- Where does memory-poisoning sit — supply chain or behavior drift?
- Should "human-agent trust" be a sixth branch, or out of scope (we are studying A2A specifically)?

## What this taxonomy is for

1. Mapping experiments to research areas — every experiment lives in one or more branches.
2. Spotting gaps — if a branch has no experiments after 30 days, either drop it or run something.
3. Eventually becoming the spine of a publishable A2A trust report.
