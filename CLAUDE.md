# Mira Codebase

## HARD RULES

4. **Skill security audit (mandatory).** All newly generated or imported skills MUST pass a security audit before being saved/enabled. The audit (`soul_manager.audit_skill()`) checks for:
   - **Unauthorized network calls**
   - **Dangerous code execution**
   - **Obfuscated payloads**
   - **Privilege escalation**
   Skills that fail the audit are BLOCKED and logged. No exceptions.

7. **No self-verification.** Agents may not label their own outputs as verified, confirmed, or ground truth. All agent self-assessments are treated as exploratory by default. Verification must come from a different agent or automated system check. The evaluator agent MUST NOT evaluate its own outputs or performance.
