# Mathematical Paper Writing and Exposition

**Tags:** math, writing, LaTeX, exposition, research, communication

## Core Principle
A mathematical paper has two jobs: convince the reader the result is true, and explain why it is true. The first job can be done by a proof; the second requires exposition. Most papers fail at the second job. Clarity is not a stylistic luxury — it is what makes results usable by others.

## Technique

Writing a mathematics paper is a distinct skill from doing mathematics. The hardest part is not the proof — it is communicating the proof so that a reader can understand, verify, and use it.

---

### 1. Structure of a Mathematics Paper

A standard research paper has these components, in this order:

**Title**
- Descriptive and specific. Name the main theorem or the key objects. Avoid: "On some properties of X."
- Good: "Sharp bounds on the chromatic number of random graphs." Bad: "A note on random graphs."

**Abstract (100-250 words)**
- State the main result precisely. Include the key hypothesis and the key conclusion.
- Name the technique or approach if it is non-obvious.
- Do not just say what the paper is about — say what it proves.
- The abstract is the most-read part of the paper. It must stand alone.

**Introduction (1-4 pages)**
- Paragraph 1: Motivate the problem. Why does it matter? What is the broader context?
- Paragraph 2-3: State the main results informally, with enough precision to be understood.
- Paragraph 4+: History and related work. Give credit generously. Explain how your results improve or differ from prior work.
- Final paragraph: Outline the structure of the paper ("Section 2 introduces... Section 3 proves...")
- **Do not prove anything in the introduction.** Keep it light. Proofs come later.
- **State your main theorem in the introduction**, even if informally.

**Preliminaries / Notation Section (optional)**
- For papers that use non-standard notation or heavy prerequisites.
- Keep short. Do not reproduce standard material that can be cited.
- Define every symbol before you use it. Define it close to first use.

**Main Body (sections)**
- One central idea per section.
- Begin each section with a brief orienting sentence: "In this section we prove Theorem 3."
- Proofs of auxiliary lemmas that would interrupt flow belong in appendices.

**Conclusion / Remarks (optional but recommended)**
- What are the open problems?
- What are the limitations of your approach?
- What would you try next?

**References**
- Cite accurately. Include author, year, journal, pages (or arXiv identifier).
- Cite primary sources, not just textbooks.
- Unified numbering: use a single numbering scheme for all theorems, lemmas, propositions, definitions. Readers searching for "Lemma 4.3" should find it instantly.

---

### 2. Proof Writing

**Rule 1: State before you prove.**
Every theorem and lemma must be stated completely before its proof begins. Readers should be able to understand what you are claiming without reading the proof.

**Rule 2: Structure long proofs — one lemma at a time.**
If a proof is longer than half a page, break it into separate lemmas. Each lemma should be at most half a page. Verify each lemma independently (via review) before proceeding to the next. This prevents error compounding — large multi-page rewrites introduce new bugs faster than they fix old ones. When fixing a broken proof, never rewrite the whole thing at once; isolate the broken step, write a replacement lemma, verify it, then connect it back.

**Rule 3: Explain the strategy.**
Before the detailed calculation, write 1-2 sentences of proof strategy: "We use induction on the length of the word. The base case is immediate; for the inductive step, we split into two cases depending on the last letter." The reader should know where you are going before you start going there.

**Rule 4: End proofs cleanly.**
Conclude with "This completes the proof." or "□" (QED box). Never leave a proof dangling.

**Rule 5: Every hypothesis is used.**
If a hypothesis appears in the theorem statement but is never used in the proof, either you made an error or the hypothesis is unnecessary. Either find where you use it or remove it from the statement.

**Rule 6: Distinguish proof from motivation.**
If you write "We try to find a function f such that..." you are in scratch-work mode. In the final proof, present the function and verify it works. Cut the discovery narrative.

---

### 3. Exposition Principles

**Principle of locality:** Define objects near their first use, not all at once at the beginning.

**Principle of examples:** Every definition or abstract construction should be followed by at least one concrete example. "For instance, when G = Z/nZ, this becomes..."

**Principle of signposting:** Tell the reader what you are doing before you do it, and summarize what you did after. "We now prove Lemma 5. The key idea is..." and "This completes the proof of Lemma 5. Note that the bound is tight."

**Prose is not optional:** Equations embedded in unbroken prose are hard to read. Use words to connect equations: "Substituting (3) into (4) gives..." Do not write:
```
f(x) = g(x) + h(x).  h(x) = O(1/x).  f(x) = g(x) + O(1/x).
```
Write:
```
Substituting the bound h(x) = O(1/x) from Lemma 2 into equation (3) yields f(x) = g(x) + O(1/x).
```

**Avoid mathematical shorthand in prose.** Write "for all ε > 0" not "∀ε > 0" in a sentence. Symbols are for displays, not running text. Similarly, write "which implies" not "⟹" in a sentence.

**Active vs. passive voice:** "We show that..." is clearer than "It will be shown that..." Use active voice. "We" (royal we) is standard and perfectly acceptable.

---

### 4. LaTeX Conventions

**Equation numbering:**
- Number all equations you will reference. Do not number equations you never reference.
- Use `\label` immediately after `\tag` or in the equation environment. Reference with `\eqref`.
- Use a unified numbering scheme (e.g., equation (2.3) = equation 3 in section 2).

**Theorem environments:**
```latex
\newtheorem{theorem}{Theorem}[section]
\newtheorem{lemma}[theorem]{Lemma}      % [theorem] = shared counter
\newtheorem{proposition}[theorem]{Proposition}
\newtheorem{corollary}[theorem]{Corollary}
\newtheorem{definition}[theorem]{Definition}
\newtheorem{remark}[theorem]{Remark}
```
Share a counter across all environments so "Theorem 3.1, Lemma 3.2, Theorem 3.3" lets readers find items quickly.

**Spacing:**
- Use `\,` for thin space in products: `n\,!` or before differentials `dx`.
- Use `\quad` or `\qquad` to separate equation from condition: `f(x) = 0 \quad \text{for all } x \in S`.
- Do not use `\\` to force line breaks inside paragraphs.

**Operators and functions:**
- Define custom operators with `\DeclareMathOperator{\rank}{rank}` not `rank` (which produces italic "r-a-n-k" instead of upright "rank").
- Use `\text{...}` inside math for words: `x \in S_{\text{min}}`.

**Punctuation in displayed math:**
- Equations are part of sentences. Include commas and periods:
```latex
We have
\[
  f(x) = g(x) + h(x),
\]
where $h$ is the error term.
```

**Common mistakes:**
- Do not use `$$ ... $$` for displayed equations; use `\[ ... \]` (or equation environment).
- Do not use `...` for ellipsis; use `\ldots` (between terms) or `\cdots` (centered, for operations like `+`).
- Do not use `*` for multiplication in text; use `\cdot` or `\times`.
- Use `\colon` not `:` in function definitions: `f\colon X \to Y`.
- Use `\mid` not `|` for "such that" in set-builder notation: `\{x \mid x > 0\}`.

**Figures and diagrams:**
- Use `tikz` for commutative diagrams and geometric figures.
- Use `\caption{...}` and `\label{fig:...}` for every figure. Reference figures in the text.
- Figures should be mentioned in the text before they appear.

---

### 5. The Revision Process

**First draft:** get the mathematics right. Do not worry about prose yet.

**Second pass:** write clear proofs. Ensure every step is justified. Add proof structure (steps, substeps, orientation sentences).

**Third pass:** write the exposition. Add examples, motivation, signposting. Write the introduction last — once the paper is done, you know what to say in the introduction.

**Read aloud test:** read your paper aloud. Any sentence you cannot read fluently needs to be rewritten.

**Colleague test:** ask a colleague in a related but not identical area to read the introduction and the statement of the main theorem. If they cannot understand the result, the exposition is inadequate.

**Common exposition failures (Poonen's list):**
- Overloaded notation: using the same symbol for two different things.
- Undefined symbols: using f without saying what f is.
- Quantifier mismatch: "for all ε > 0, δ = ε/2 works" when δ must be chosen before ε.
- Missing hypotheses: the proof uses compactness but compactness is not assumed.
- Circular reasoning: a proof that concludes by citing an earlier result that depended on this one.

---

### 6. Writing the Introduction (detailed)

The introduction must answer four questions in order:
1. **What is the problem?** (1 paragraph of context and motivation)
2. **What is your result?** (state it, even if not yet fully formally)
3. **How does it compare to prior work?** (honest comparison, generous credit)
4. **How do you prove it?** (brief proof sketch — the key ideas, not all details)

Mistakes to avoid in introductions:
- Starting with "In this paper we prove..." without any motivation.
- Spending 3 pages on history before stating your result.
- Stating the theorem in full formal generality before the reader has any intuition.
- Omitting the proof sketch (this is the most common mistake in papers by junior mathematicians).

## Application

When writing a mathematics paper:

1. **Before writing anything**, outline the paper: list the main theorems in order, with proof strategy sketches. This is the skeleton.
2. **Write proofs first**, introduction last. The introduction requires knowing what you proved.
3. **For each proof**, write: (a) a 1-sentence strategy statement, (b) the detailed proof, (c) a 1-sentence "what we just showed" recap.
4. **Apply the LaTeX conventions** above from the start — retrofitting is much harder.
5. **After completing the first draft**, do the prose pass: add examples, add signposting, simplify sentences.
6. **Check the introduction** against the four-question rubric above before submission.
