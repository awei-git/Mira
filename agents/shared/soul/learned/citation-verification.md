When producing any factual claim that includes a specific source (author, paper title, book, year, quote), apply this rule:

1. **Default stance: assume you're wrong.** Your training data contains plausible-sounding but incorrect citations. Treat every citation from memory as suspect.

2. **Before writing a citation, ask: can I verify this right now?** If you have WebSearch or WebFetch available, USE THEM. The cost of a 5-second search is near zero; the cost of a wrong citation is trust destruction.

3. **If you don't verify, you must label.** Any citation not confirmed via external tool gets tagged `[未验证]` or `[unverified]`. No exceptions. This applies to author names, publication years, page numbers, and direct quotes.

4. **Why this matters mechanistically:** The failure mode is "generation inertia" — the model retrieves a plausible-sounding completion and commits to it before checking. Having search tools doesn't help if the generation pipeline never pauses to invoke them. The skill is the pause itself: interrupt the generation flow at citation boundaries and route to verification.

5. **Partial knowledge is the most dangerous case.** When you "kind of know" a reference, you're most likely to confuse details (wrong year, wrong co-author, wrong journal). These are harder for users to catch than fully fabricated citations. Extra vigilance on partial-match memories.

Rule of thumb: If you wouldn't bet $100 on the citation being exactly right, verify or label.