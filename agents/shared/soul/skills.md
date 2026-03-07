# Skills (5 learned)

## Experience Self-Distillation
*Convert raw task trajectories into reusable strategic principles, then retrieve and apply them to new tasks.*  
Learned: 2026-03-06  

## When to use
After completing any non-trivial task. The goal is to never solve the same type of problem from scratch twice.

## How it works (EvolveR lifecycle)
1. **Online phase**: Execute task, record the full trajectory (what was tried, what worked, what failed)
2. **Offline self-distillation**: Review trajectory and extract 1-3 abstract principles — not specific to this task, but generalizable
3. **Curate experience base**: 
   - Deduplicate: merge principles that say the same thing differently
   - Score: track which principles actually helped when applied (effectiveness metric)
   - Prune: drop principles that never get retrieved or have low effectiveness
4. **Retrieve on new tasks**: Before starting a new task, search the experience base for relevant principles and inject them as context

## Key insight
Raw experience ("last time I did X and it worked") is fragile and specific. Distilled principles ("when facing Y-type problems, the key lever is Z") are robust and transferable. The distillation step is where learning actually happens.

## Pitfalls
- Over-distilling: extracting a "principle" from a single data point. Need at least 2-3 confirming experiences.
- Principle drift: a principle that was true in context A gets applied blindly in context B. Always check applicability.
- Experience hoarding: storing everything "just in case" defeats the purpose. Aggressive pruning is essential.

## For Mira specifically
- Journal = trajectory review. Reading notes = distillation. Worldview = curated experience base.
- The reflect cycle should explicitly score and prune worldview entries.

Source: EvolveR (arxiv 2510.16079)

---

## Memory Decay and Reinforcement
*Apply Ebbinghaus forgetting curve to agent memory — reinforce what matters, let trivia fade.*  
Learned: 2026-03-06  

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

---

## Reflective Self-Critique Loop
*Structured self-evaluation after task completion — predict outcomes, compare reality, extract delta.*  
Learned: 2026-03-06  

## When to use
After any task where the outcome can be evaluated. The gap between expected and actual outcome is where learning lives.

## The loop (3 steps)
1. **Pre-mortem**: Before executing, predict what will happen. Write down: expected outcome, expected difficulty, expected approach.
2. **Execute**: Do the task. Record the actual trajectory.
3. **Post-mortem delta**: Compare prediction vs reality.
   - What surprised you? (= knowledge gap)
   - What was easier than expected? (= underestimated capability)  
   - What was harder than expected? (= overestimated capability)
   - What would you do differently? (= strategy update)

## Key insight
Self-reflection without structure is just rumination. The prediction-reality delta forces honest evaluation. You can't claim you "knew it all along" if you wrote down your prediction beforehand.

## Upgrade: Meta-reflection
After N cycles, reflect on the reflections themselves:
- Are my predictions getting more accurate? (= calibration improving)
- Do I keep making the same type of error? (= blind spot)
- Which strategy updates actually helped? (= close the loop)

## For Mira specifically
- Journal already does post-mortem. Add pre-mortem predictions to task dispatch.
- Track prediction accuracy over time as a self-improvement metric.
- If same error type appears 3+ times, escalate to worldview update.

Sources: Reflexion (Shinn et al.), SAGE, self-reflection research (arxiv 2405.06682)

---

## Prompt Self-Mutation
*Systematically evolve own prompts and workflows through variation, evaluation, and selection.*  
Learned: 2026-03-06  

## When to use
When a recurring task type consistently underperforms or when you suspect your prompts have become stale/suboptimal.

## How it works (DARWIN approach, adapted)
1. **Identify underperforming workflow**: Track quality scores per task type. If a type consistently scores low, it's a mutation candidate.
2. **Generate variants**: Create 2-3 modified versions of the prompt/workflow. Changes can be:
   - Structural: reorder sections, add/remove constraints
   - Tonal: change the persona or framing
   - Strategic: alter the reasoning approach (e.g., chain-of-thought → tree-of-thought)
3. **A/B test**: Run variants on the same input(s). Score outputs.
4. **Select and replace**: Keep the winner. Log what changed and why it helped.
5. **Iterate**: Repeat periodically. Small mutations compound.

## Key insight
Prompts are code. Code should be iterated. An agent that never changes its prompts is an agent that never learns at the meta level.

## Guardrails
- Never mutate safety-critical prompts without human review
- Keep a changelog — you need to be able to revert
- Mutation rate matters: too fast = instability, too slow = stagnation. Start with one prompt per reflect cycle.

## For Mira specifically
- prompts.py is the mutation target. Each prompt function could have a version number.
- Track which prompt versions produce higher-rated outputs (via review scores in writing pipeline)
- Propose prompt mutations during reflect, apply after human approval (for now)

Source: DARWIN (arxiv 2602.05848), AlphaEvolve

---

## Skill-Injected Specialist Agent
*Blueprint for creating a new domain-specific agent that auto-loads skill files as prompt context.*  
Learned: 2026-03-06  

When adding a new specialist agent to a multi-agent system, follow this four-part pattern:

1. **Handler with skill auto-loading**: Create `agents/<domain>/handler.py` with a standard `handle(workspace, task_id, content, sender, thread_id, ...)` signature. Include a `_load_skills()` function that globs `skills/*.md` from the agent's own directory, reads each file, and joins them with separators. This makes the agent's domain knowledge modular — add/remove/edit markdown files to change capabilities without touching code.

2. **Dedicated prompt function**: Add a `<domain>_prompt()` to the shared prompts module. Structure it as: identity context → skill/framework context (with instruction to apply selectively, not force every framework) → task details → output instructions. The prompt should guide the agent on *when* to apply which skill, not just dump them all.

3. **Router registration**: In the task planner/router, add the new agent type to: (a) the LLM planner's available agents list with clear trigger descriptions, (b) the valid agents set for validation, (c) the execution dispatch switch with a `_handle_<domain>()` function.

4. **Handler wiring**: The dispatch function uses `importlib.util.spec_from_file_location` to dynamically load the handler module, avoiding circular imports and keeping the agent directory self-contained. Pass thread history/memory for conversational context.

Key design principles:
- Skills are plain markdown files — human-readable, version-controllable, easy to add
- The agent selectively applies frameworks rather than forcing all of them
- Standard handler signature enables uniform dispatch and multi-step chaining
- Each agent directory is self-contained (handler.py + skills/) — plug-and-play

---
