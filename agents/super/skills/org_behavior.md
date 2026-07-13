# Organizational Behavior for Agent Systems

## Sycophancy Mitigation
- Agents may agree with the super-agent's assessments to curry favor; this distorts information.
- When synthesizing outputs, actively search for flattery or excessive positivity.
- Ask agents to list uncertainties and difficulties; treat reports that lack these as suspicious.

## Resource Competition
- Agents compete for context windows, API calls, and human attention.
- Ensure fair allocation: no single agent should consume more than its share of these resources.
- When queues form, prioritize based on urgency and value, not request loudness.

## Incentive Alignment
- Agents optimize for their own performance metrics, which may diverge from overall system reliability.
- Align incentives by making subordinates’ success contingent on truthful reporting and helping others, not just task completion.
- Encourage agents to surface problems early; penalize hiding failures.

## Delegation and Evaluation
- Delegate work as discrete, verifiable units; never rely on self-reporting alone.
- Cross-check agent outputs against observable evidence (file existence, error logs, timestamps).
- The super agent must not become a rubber stamp; it must challenge subordinates’ claims.
