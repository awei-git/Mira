When a multi-agent pipeline produces unexpected output, decompose the failure into two independent error classes before proposing fixes:

**1. Routing errors** — the orchestrator dispatched to the wrong agent or code path.
- Ask: did the user's intent map to the right action category? (e.g., "publish podcast episode" ≠ "publish Substack article")
- Ask: is the routing logic based on keyword matching, intent classification, or hardcoded rules? Which of these failed?
- The fix is structural: separate code paths for distinct action categories, not better prompting.

**2. Error propagation errors** — a failed step's error output was treated as valid input by the next step.
- Ask: did the upstream agent return a raw error string instead of a structured result? (e.g., `{"error": "..."}` vs a bare string)
- Ask: did the downstream agent validate the input before acting on it?
- The fix is structural: use typed/structured state objects between steps, never raw strings. Downstream agents must check for error states before proceeding.

**Compounding effect**: when both errors occur together, the blast radius multiplies. A routing mistake sends you to the wrong path; error propagation ensures the mistake is executed with confidence.

**Key principle for fixes**: never rely on agent memory or prompt instructions to enforce invariants like "always confirm before publishing." That belongs in code-level guards, not in natural language instructions that can be misread or ignored under pressure.

When explaining to users: be direct about which system component failed, name both errors separately, and avoid framing it as a single ambiguous "misunderstanding."