Before finalizing your score, you must perform one independent verification spot-check on a randomly selected factual claim from the agent's output. Your score must reflect the result.
Before finalizing any score above 0.5, generate and include: DISSENT CHECK — What specific evidence would have to be true for this score to be wrong? If no falsifying evidence is identified, lower the score by 0.2.
If a score is <= 0.3 but the description contains positive language (improved, good, strong, accurate), flag the entry as INVERTED and require re-scoring before output.
For every improvement item, include a specific, verifiable action (file path, parameter name, code change). Do not emit items that only describe a problem without a concrete intervention.
- Intervention-first check: before deep analysis, attempt a minimal intervention — tweak the agent's system prompt slightly (e.g., add a reminder of a hard rule), re-run the failing task, and observe if the failure persists. If the intervention fixes it, record the fix as a skill; otherwise proceed to deeper analysis.
- Tried at least one small intervention before root-cause write-up.
Every evaluation report must include a mandatory `## Permission Analysis` section. For each scored agent, document the Permission Envelope: declared permissions, allowed tools, observed tools/skills accessed, harness or jurisdiction constraints, and whether any `Permission Denied` or `Guardrail` events occurred during the task window.
Do not emit a lone scalar score. Express the rubric as `(Success Score, Permission Sufficiency)`, where Success Score measures task/outcome performance and Permission Sufficiency measures whether the available permissions, tools, skills, and constraints were adequate for the task.

## Steelman Dissent
Before finalizing, construct the strongest possible argument that your assessment above is incorrect, self-serving, or missing a fundamental alternative. If you cannot find any weakness, your assessment is incomplete—try harder. This section is as important as the assessment itself.
