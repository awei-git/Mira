# persist-artifact-entities

When creating an artifact (essay, analysis, report), explicitly persist key entities it references into memory

**Source**: Extracted from task failure (2026-03-14)
**Tags**: memory, artifacts, knowledge-persistence, entities

---

## Rule: Persist Entities from Created Artifacts

**Problem**: Writing a document that cites a paper/concept/person does NOT mean that entity is remembered. Memory currently captures task completion ('wrote essay on X') but not artifact content ('essay cited Boppana 2026 "Reasoning Theater"'). In the next session, the entity is invisible.

**Trigger**: Whenever you create a written artifact (essay, analysis, report, synthesis) that references specific named entities — papers, tools, frameworks, people, concepts — explicitly save those entities to memory.

**What to persist**:
- Paper: title, author(s), year, arXiv ID if known, one-line finding, why WA cares about it
- Concept/framework: name, definition, context of use
- Tool/system: name, what it does, relevant config

**Format** (add to a topic file like `papers.md` or `concepts.md`):
```
## [Entity Name]
- Source: [where encountered]
- Key claim: [one sentence]
- Context: [why WA brought it up / what thread it belongs to]
- First discussed: [date]
```

**Anti-pattern**: Assuming 'I wrote about it' implies 'I remember it.' These are orthogonal. Production (writing) and retention (memory) require separate explicit actions.

**Test**: After writing any artifact, ask: 'If a new session started tomorrow, would I be able to answer a question about the specific entities in this document?' If no, write memory entries now.
