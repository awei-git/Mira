When asked to find papers on a research topic, follow this pattern:

1. **Anchor on known canonical papers first** — identify the 2-3 most-cited works the user already suspects exist (e.g., "Turpin et al. 2023"). Use these as reference points for dates, venues, and methodology vocabulary.

2. **Decompose the claim into searchable sub-questions** — break the thesis into distinct empirical approaches:
   - Behavioral/intervention evidence (e.g., truncation experiments, biased prompts)
   - Representational/probing evidence (e.g., linear probes on hidden states)
   - Mechanistic/causal evidence (e.g., activation patching, attention analysis)
   Search each separately to avoid missing methodology-specific papers.

3. **Search arxiv with methodological keywords, not just topic keywords** — e.g., for "CoT unfaithfulness," search both "chain-of-thought faithfulness" AND "probing reasoning" AND "post-hoc rationalization LLM." Include year ranges to catch recent work.

4. **Structure output per paper**:
   - arxiv ID (e.g., 2305.04388)
   - Authors + year + venue
   - One-sentence core finding
   - Which sub-question it addresses (behavioral / probing / mechanistic)

5. **Flag the strongest evidence** — distinguish papers that show correlation (behavioral) vs. causal/representational evidence that the answer is encoded *before* generation begins. The latter is typically stronger for the "post-hoc rationalization" claim.

6. **Note recency gradient** — sort or flag the most recent papers (last 6-12 months) separately, as this field moves fast and the user likely knows the 2023 classics already.