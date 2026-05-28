Before finalizing your score, you must perform one independent verification spot-check on a randomly selected factual claim from the agent's output. Your score must reflect the result.
Before finalizing any score above 0.5, generate and include: DISSENT CHECK — What specific evidence would have to be true for this score to be wrong? If no falsifying evidence is identified, lower the score by 0.2.
If a score is <= 0.3 but the description contains positive language (improved, good, strong, accurate), flag the entry as INVERTED and require re-scoring before output.
For every improvement item, include a specific, verifiable action (file path, parameter name, code change). Do not emit items that only describe a problem without a concrete intervention.
- Intervention-first check: before deep analysis, attempt a minimal intervention — tweak the agent's system prompt slightly (e.g., add a reminder of a hard rule), re-run the failing task, and observe if the failure persists. If the intervention fixes it, record the fix as a skill; otherwise proceed to deeper analysis.
- Tried at least one small intervention before root-cause write-up.
