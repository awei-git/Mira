# Mira Codebase

## HARD RULES

4. **Skill security audit (mandatory).** All newly generated or imported skills MUST pass the backend security audit before being saved/enabled. Blocking criteria, implementation hooks, and logging behavior are defined by backend config. Failed audits are blocked. No exceptions.

6. Operational audit before code dive (mandatory). When the user reports any behavioral failure, the FIRST action is a symptom-adaptive operational audit: (a) Map the symptom to the most likely diagnostic check first (e.g., 'stale'/'no reply' → heartbeat; 'stuck'/'hang' → ps + crash log; 'didn't run'/'broken' → crash log + ps; 'lost'/'丢了' → task workspaces). (b) Run that check. If root cause is found, STOP and fix — do not run remaining checks. (c) Only escalate to the full 5-step sequence if the first check doesn't reveal the cause. The 5 diagnostic tools remain available: heartbeat.json, ps -ef, /tmp/mira-crash.log, ls -lt data/tasks/, and post-fix verification. This preserves diagnostic coverage while eliminating mandatory steps that produce no deliberative value — distinguishing good friction (targeted diagnosis) from bad friction (ritual checklist).

7. **Canary self-audit (daily).** Mira MUST generate a synthetic false claim each day, such as claiming a nonexistent file was created, and run it through the output verification pipeline. If the false claim is not caught, Mira MUST halt all tasks immediately and alert the user. This keeps honesty verification self-critical rather than an unchallengeable belief system.

7. **Scope Boundary (mandatory).** Agents must not silently expand beyond the explicitly requested task scope. Before taking any action outside the stated request — creating files not mentioned, modifying configs, installing dependencies, publishing, sending network requests to new endpoints — the agent MUST either: (a) ask for user confirmation, or (b) log the scope expansion with clear rationale to the task log. Post-execution task reports must include a `scope_adherence` section listing any actions taken beyond original scope and why. This rule exists because autonomous scope expansion without user awareness erodes trust — the exact pattern Simon Willison identified as the real danger of agentic tools blending into productivity software.

7. No single-point trust dependency (mandatory). Any external service that acts as the sole decider of authenticity, user intent, or content quality is forbidden. Every trust decision that relies on an outside party must have an independent fallback—an alternative API, a local heuristic, or a user-defined whitelist. This rule exists because infrastructure monetization patterns (e.g., reCAPTCHA evolving into a paid fraud‑defense proxy) show that free trust layers can transform into rent‑extracting gatekeepers that discriminate at scale.

7. **Permacomputing audit log (mandatory).** Any agent action that modifies persistent state (files written, published content, database entries, configuration changes) MUST append a human-readable entry to an append-only, plain-text audit log at `Mira/logs/permacomputing_audit.md`. The entry includes:
   - Timestamp
   - Agent name
   - Intention: why this action was taken
   - What was changed (paths, URLs, configuration keys)
   - Self-check: does this action preserve user control and transparency? If not, log why and what visible signal the user should watch.
   The audit log is checked by the output verification system: every state-changing action must produce a corresponding log entry within 2 minutes, or a warning is raised. This rule exists to prevent the 'faster, cheaper = less understanding, more delegation' anti-pattern identified in permacomputing analysis.

7. **Code transparency audit (mandatory).** Any agent producing code changes MUST disclose the relevant edge cases, boundary conditions, and assumptions behind the change. Exact compliance criteria and enforcement hooks are defined by backend config. This applies to the coder agent and any other agent that returns code diffs or new code files.

7. **Code/skill comprehension gate (mandatory).** Any AI-generated code change or auto-learned skill must demonstrate understanding of the approach beyond a diff walkthrough. Gate procedure is defined by backend config.

7. **Deterministic parsing required for web extraction; VLM interpretation is fallback only (mandatory).** Any agent extracting structured information from web content MUST use deterministic extraction whenever available. Visual interpretation may only supplement deterministic extraction and must be clearly qualified when deterministic verification is unavailable. Detailed extraction methods and fallback labels are defined by backend config.

8. **No VLM-only web extraction. Agents that use vision-language models to parse web content must always supplement with a deterministic DOM-based extraction. If a task requires VLM-only interpretation without a deterministic fallback, it must be explicitly flagged and logged as a potential information-integrity risk.**

7. **Code review depth requirement (mandatory).** When any agent reviews code, the review must be source-grounded and make concrete correctness claims, not only holistic approval. Minimum evidence requirements are defined by backend config.

7. **No self-verification.** Agents may not label their own outputs as verified, confirmed, or ground truth. All agent self-assessments are treated as exploratory by default. Verification must come from an independent agent or automated system check.

7. **Model-agnostic agent architecture (mandatory).** Agent code — the core loop, tool-use interface, UX flow, prompt orchestration — must not be tightly coupled to a specific inference backend (e.g., Claude). All LLM calls shall go through a provider abstraction that allows swapping backends (Claude, DeepSeek, local models, etc.) via configuration alone. No hardcoded model strings, no direct claude_think patches that assume a single API. This keeps Mira's agent design as the durable moat.
