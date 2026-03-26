## Pattern: Frontier Audit with Ongoing Digest

Use this when you want to stay current on a fast-moving technical area and translate external best practices into actionable self-improvement.

### Phase 1 — Multi-source Research
Search 3–5 authoritative sources in parallel:
- Official vendor/framework documentation and blogs (e.g., Anthropic, LangChain)
- Architecture references (e.g., Azure Architecture Center, AWS Well-Architected)
- Independent research blogs and papers

Cluster findings by theme (architecture patterns, tooling standards, observability, evaluation, etc.) rather than by source. Note which trends appear across multiple sources — those are signals, not noise.

### Phase 2 — Gap Analysis Against Current State
For each identified best practice:
1. Assess whether the current system already does this (fully / partially / not at all)
2. Estimate the cost/benefit of closing the gap
3. Assign priority: P0 (blocking), P1 (high leverage), P2 (nice-to-have)

Output a concise table: Practice → Current State → Gap → Priority.

### Phase 3 — Automated Digest Setup
Create a lightweight script (e.g., `agentic_digest.py`) that:
- Fetches or compiles a summary of recent developments on a schedule (daily or weekly)
- Writes output to a human-readable file or artifact location accessible on the target device
- Is registered as a cron task at a consistent time (e.g., 8:30 AM)

The digest keeps the audit from going stale — it surfaces new inputs without requiring a full re-research each time.

### Key Judgment Calls
- Stop adding inputs once the thesis is mature (avoid analysis paralysis)
- Distinguish "knowing about X" from "having applied X" — track implementation separately from awareness
- Set a hard deadline for any "I'll implement this soon" items or drop them from the list