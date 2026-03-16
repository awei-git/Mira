# skip-prethink-when-plan-already-established

Don't re-invoke a planning/thinking step before creative writing when the approach was already fully negotiated in conversation

**Source**: Extracted from task failure (2026-03-14)
**Tags**: creative-writing, pipeline, timeout, planning, conversation-as-spec

---

## Rule: Skip Redundant Pre-Think for Creative Writing Tasks

When a writing task has already been scoped, structured, and agreed upon through conversational back-and-forth, do NOT invoke a separate `claude_think` or planning pipeline step before writing. The conversation itself is the plan.

**The failure pattern:**
- Agent successfully researched → discussed structure → expressed voice/POV → received explicit green light ("you want to write it, go ahead")
- Then triggered a writing pipeline that included a `claude_think` planning phase
- That phase timed out (60s, then 180s) because there was nothing left to think — and the retry loop compounded the failure

**The rule:**
- If the agent has already: (1) summarized the content, (2) proposed an angle, (3) confirmed structure, and (4) received "go ahead" — treat that as a complete spec and write directly
- A `claude_think` gate before writing is warranted when requirements are ambiguous. It is wasteful (and failure-prone) when requirements have been elaborated through dialogue
- On a "go ahead" signal after rich discussion, the correct action is `write(spec_from_conversation)`, not `think() → write()`

**Practical heuristic:** If you can summarize the writing task in 2-3 sentences from the conversation history, you have enough to start. Don't stall on planning what's already been planned.
