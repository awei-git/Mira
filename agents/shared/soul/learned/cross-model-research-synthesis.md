## Cross-Model Research Synthesis

### When to use
When researching a domain topic where you want comprehensive, validated insights — not just one source's perspective.

### Method
1. **Round 1 — Web Research**: Use web search to gather real-world data, specific examples, quantitative benchmarks, and practitioner insights. Prioritize sources with concrete numbers and mechanisms over generic advice.
2. **Round 2 — LLM Query**: Query a different LLM (e.g., OpenAI GPT-4o via API) with the same core question. This surfaces the "consensus knowledge" baked into that model's training data.
3. **Compare & Score**: Evaluate both outputs on: depth, specificity, actionability, novelty. Typically web research wins on concrete data and mechanisms; LLM responses win on breadth and occasionally surface overlooked angles.
4. **Synthesize**: Use the stronger source as the backbone. Cherry-pick unique contributions from the weaker source (novel tactics, alternative framings). Discard overlapping generic advice.
5. **Output as Playbook**: Structure the final output as a numbered tactical methodology with specific actions, not abstract principles. Save to the relevant skill/workflow directory.

### Key insight
Round 1 web research almost always produces deeper, more actionable results (specific data, real mechanisms, quantitative frameworks). The LLM query's value is as a "completeness check" — it occasionally surfaces 1-2 tactics the web research missed. Don't expect parity; expect complementarity.

### Timeout note
When orchestrating multi-step research with API calls and file writes, set generous timeouts (>120s) on task workers to avoid premature termination.