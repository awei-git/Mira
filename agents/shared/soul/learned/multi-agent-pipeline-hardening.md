When hardening a multi-agent pipeline against error propagation and unintended actions, apply three independent layers:

**Layer 1 — Structured error propagation**
Agents must return structured objects (e.g., `{success: bool, content: str, error: str}`) rather than bare strings or raw exceptions. Each downstream agent checks `success` before proceeding; on failure, it returns its own failure object without executing its action. This ensures failures short-circuit the pipeline rather than being silently passed forward as content.

**Layer 2 — Explicit routing separation**
Distinct operation types (e.g., audio upload vs. text publish) must be separated at the routing layer, not handled by a shared entrypoint that infers intent. Update the planner/router prompt with explicit rules and examples that prevent ambiguous routing. Treat routing as a contract, not a suggestion.

**Layer 3 — Entry-point content guards for irreversible actions**
Before any irreversible action (publish, send, deploy), add a code-level guard function that inspects the content for red flags: error keywords (e.g., "找不到", "failed", "error"), suspiciously short content, or structural anomalies. If any flag triggers, the handler returns a failure object and logs a clear rejection reason — no reliance on agent memory or prompt instructions. This guard is the last line of defense and must be independent of the other layers.

Each layer must work independently so that any single one can block a bad outcome even if the others fail.