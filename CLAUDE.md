# Mira Codebase

## HARD RULES

4. **Skill security audit (mandatory).** All newly generated or imported skills MUST pass a security audit before being saved/enabled. The audit (`soul_manager.audit_skill()`) checks for:
   - **Unauthorized network calls**
   - **Dangerous code execution**
   - **Obfuscated payloads**
   - **Privilege escalation**
   Skills that fail the audit are BLOCKED and logged. No exceptions.

7. **Deterministic parsing required for web extraction; VLM interpretation is fallback only (mandatory).** Any agent extracting structured information (facts, numbers, prices, dates, statistics) from web content MUST use deterministic parsing as the primary extraction path — structured APIs, DOM extraction, RSS/Atom feeds, or machine-readable data formats. VLM screenshot/pixel interpretation may ONLY supplement deterministic parsing for qualitative assessment (layout, visual correctness, tone), and MUST NEVER be the sole extraction method for factual claims. When deterministic parsing is unavailable for a specific source, the agent MUST: (a) explicitly flag the output segment as 'VLM-interpreted — not deterministically verified', and (b) degrade the confidence/scope of claims rather than silently filling gaps with statistical inference. This rule exists because VLM-based visual interpretation merges 'interpretation' with 'rendering' into an uncalibratable channel — any statistical infilling creates information gaps the user cannot detect (cursed_browser principle, 2026-05-07).

7. **Code review depth requirement (mandatory).** When any agent reviews code — coder agent reviewing a PR, super agent synthesizing sub-agent code outputs, or any agent claiming to have verified code correctness — the review output MUST include: (a) at least one specific line-number or function-name reference to the code under review, (b) at least one concrete correctness claim that goes beyond coherence (e.g., "this loop terminates because the counter is bounded by len(items)", NOT "the logic flows well"). Reviews consisting solely of holistic approval language ("looks good", "seems fine", "lgtm") are incomplete and must be redone. This exists because trust inflation causes review attention to degrade from correctness-checking to coherence-skimming — structurally the same failure mode as hallucination (see Rule 1).

7. **No self-verification.** Agents may not label their own outputs as verified, confirmed, or ground truth. All agent self-assessments are treated as exploratory by default. Verification must come from a different agent or automated system check. The evaluator agent MUST NOT evaluate its own outputs or performance.
