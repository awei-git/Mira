---
activation_trigger: "Apply when surveying multiple math papers on a topic to build a coherent map of results, connections, and open gaps across the literature."
---

# Literature Synthesis

**Tags:** research, math, literature-review, paper-reading, knowledge-management

## Core Principle
Survey research papers by extracting the essential contribution of each, mapping connections between them, and identifying what remains unknown — reading for gaps matters as much as reading for results.

## Technique

Mathematical research does not happen in isolation. Every new result sits in a web of prior work. Effective literature synthesis is about building a mental map of that web quickly and accurately.

**Phase 1: Scoping — find the right papers**

- **Start from a known anchor.** Begin with one highly relevant paper (a survey, a seminal result, or the paper that prompted your investigation).
- **Chase references in both directions.** Read the anchor's bibliography (backward search) and find papers that cite it (forward search via Google Scholar, Semantic Scholar, or MathSciNet).
- **Identify the key authors.** In most subfields, 3-5 research groups produce the majority of results. Track their recent work.
- **Use arXiv effectively.** Search by subject class (e.g., math.CO, math.AG) and keyword. Skim abstracts to filter. Check the "related papers" suggestions.
- **Stop when references cycle.** When new papers keep citing the same set of works you have already seen, you have reached saturation for the current scope.

**Phase 2: Extraction — read each paper efficiently**

Not all sections deserve equal attention. Use this priority order:

1. **Abstract and introduction** — What is the main result? What was open before? Read carefully.
2. **Statement of main theorems** — Read the precise statement. Understand every hypothesis and every conclusion.
3. **Proof sketch or proof overview** (if present) — Understand the high-level strategy before diving into details.
4. **Key lemmas** — Identify which lemmas are new and which are standard tools. The new lemmas often contain the real intellectual contribution.
5. **Full proofs** — Read in detail only for papers central to your work. For peripheral papers, the theorem statement and proof idea suffice.
6. **Related work and discussion sections** — These often contain the most honest assessment of limitations and open problems.

**Phase 3: Connection — build the map**

- **Create a result dependency graph.** For each key theorem, note which prior results it depends on. This reveals the logical structure of the field.
- **Identify technique clusters.** Group papers by methodology (algebraic, probabilistic, topological, computational). Notice when a technique from one cluster crosses into another — these are often breakthrough papers.
- **Track evolving bounds.** In many fields, progress is a sequence of improving bounds (better constants, weaker hypotheses, stronger conclusions). Maintain a timeline.
- **Note contradictions or tensions.** When two papers seem to claim conflicting things, investigate carefully. Often the difference is a subtle hypothesis. Resolving the apparent contradiction deepens understanding.

**Phase 4: Gap identification — find what is missing**

This is where research opportunities live:

- **Stated open problems.** Many papers end with explicit conjectures or open questions. Collect these.
- **Unstated gaps.** A result holds for dimension 2 and dimension >= 4 but nobody mentions dimension 3. A technique works for primes but nobody tried prime powers. These implicit gaps are often the most accessible entry points.
- **Missing connections.** Two subfields study the same objects with different names and different tools. Bridging them can yield results in both directions.
- **Assumption barriers.** When every paper in a line of work makes the same strong assumption (e.g., smoothness, compactness, finite generation), asking "what happens without this assumption" is a natural research direction.

## Application

When beginning a research investigation in a new area:

1. **Find 1-2 anchor papers** (recent survey preferred). Read their introductions thoroughly.
2. **Build a reading list of 10-20 papers** by chasing references. Prioritize by relevance, not chronology.
3. **Extract from each paper:** (a) main result statement, (b) proof technique in one sentence, (c) what it improved over prior work, (d) stated open problems.
4. **Organize findings into a structured summary:** result dependency graph, technique inventory, bound timeline, and gap list.
5. **Identify the 2-3 most promising gaps** based on your available tools and interests. These become candidate research directions.
6. **Revisit the literature as your own work progresses.** New results change which papers are relevant. The map is never final.
