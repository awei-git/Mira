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
