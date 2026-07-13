---
activation_trigger: "Apply when multi-step research needs to move across concepts, papers, claims, or domains without losing path context between retrieval hops."
---

# Multi-Hop Navigation

**Tags:** research, explorer, retrieval, multi-hop, concept-graph

## Problem

Independent similarity queries lose path context across hops. A retrieval step that asks only "what is most similar to my query?" can jump to nearby material without preserving why the prior concept mattered or how the next concept is supposed to connect.

## Pattern

Before each retrieval step, write an explicit triple:

`[FROM: current concept] [TO: target concept] [BRIDGE: the specific sub-question that connects them]`

The bridge should name the relation being tested, not just restate keywords.

## Execution

Frame each retrieval call as:

"Given I am at FROM, what do I need to reach TO?"

Do not treat the hop as "what matches my query?" The retrieval should seek the connecting mechanism, evidence, citation trail, shared term, causal link, or dependency that justifies moving from FROM to TO.

## Path Log

Maintain a running concept graph in the task scratchpad:

- Nodes: concepts, papers, claims, methods, datasets, people, or institutions already visited.
- Edges: discovered relationships, with the bridge question and source/evidence that supports each connection.
- Branches: plausible next concepts not yet followed.
- Dead ends: hops that failed or produced weak evidence.

Use this graph so later hops can backtrack, branch, or explain why a path was chosen.

## Generalization Note

When new information invalidates a prior node, such as a paper being retracted or a fact being updated, the path log lets the agent re-route from the last valid node rather than restarting from scratch.
