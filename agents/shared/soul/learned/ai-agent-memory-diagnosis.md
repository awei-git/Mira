When a user reports that an AI agent "keeps forgetting" things they've discussed repeatedly, use this three-step framework:

**Step 1 — Diagnose by failure mode type**
Ask: what exactly is being lost, and why? Map to these canonical failure modes:
- **Granularity mismatch**: the logging mechanism records actions ("discussed X") not knowledge ("learned that X implies Y"). Fix: shift memory writes to semantic content, not event logs.
- **Session content not archived**: full conversation content is discarded after session ends. Only explicit writes to persistent files survive. Fix: add structured session summaries or entity files.
- **No retrieval mechanism**: even if content were saved, there's no tool/trigger to search historical sessions. Fix: add search or indexing over saved sessions.

**Step 2 — Evaluate architectural solutions against specific failure modes**
For each proposed architecture (e.g., multi-layer memory systems), check each layer against the three failure mode types above:
- Does it capture semantic/conceptual content, or just events?
- Does it archive session content beyond the session window?
- Does it provide retrieval that can surface relevant history in a new session?

Also check: does the solution require active agent behavior (agent must decide to retrieve) vs. passive/automatic loading? Active retrieval requires meta-cognitive awareness the agent may not have.

**Step 3 — Recommend practical short-term fix alongside architectural evaluation**
Even if a full architecture would help, identify the minimum viable change in the current system. Common high-ROI fix: create a dedicated entity memory file (e.g., `known_papers.md`, `key_concepts.md`) that is always loaded into context, where the agent writes structured factual records after discussions. This sidesteps session archiving and retrieval problems by keeping critical knowledge in the always-loaded MEMORY.md or a linked file.

**Output structure**: diagnosis first (what's lost and why), then architecture evaluation (does each layer address each failure mode), then conclusion (net verdict + short-term workaround).