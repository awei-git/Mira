## When to use
Any agent with persistent memory that grows over time. Without decay, memory becomes noise.

## How it works (SAGE framework)
1. **Dual memory**: Short-term (current task context, ephemeral) + Long-term (curated insights, persistent)
2. **Ebbinghaus decay**: Each memory entry has a "strength" that decays exponentially over time
3. **Reinforcement**: When a memory is retrieved and used successfully, its strength resets to max
4. **Consolidation**: During reflect cycles, move high-strength STM entries to LTM. Let low-strength LTM entries expire.
5. **Pruning trigger**: When LTM exceeds size limit, remove lowest-strength entries first

## Mathematical model
Retention = e^(-t/S) where t = time since last access, S = stability (increases with each successful retrieval)

## Key insight
Not all memories are equal. A skill you use every week stays sharp. A fact you read once and never applied should fade. This mirrors how human expertise works — practitioners remember what they practice.

## For Mira specifically
- memory.md entries should have implicit recency weighting (already happens via line trimming, but could be smarter)
- Skills that get retrieved and applied in tasks should be marked as "reinforced"
- Worldview entries sourced from a single reading note should decay faster than those confirmed by multiple experiences

Source: SAGE (arxiv 2409.00872)
