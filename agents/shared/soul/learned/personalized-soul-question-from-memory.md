# personalized-soul-question-from-memory

Soul questions must be derived from known user context, not generic philosophical prompts

**Source**: Extracted from task failure (2026-03-25)
**Tags**: conversational-ai, personalization, memory-usage, probing-questions

---

## Skill: Constructing Personalized Probing Questions

When the task is to ask a meaningful or uncomfortable question ("灵魂问题" / soul question), a generic philosophical prompt will almost always be deflected. The user is right: this requires knowing the person.

**What went wrong:**
The agent opened with a universally framed question about values-as-scaffolding. The user correctly identified it as a collective-truth framing they don't engage with. The agent's recovery — asking "what would be a soul question for you?" — outsourced the personalization work back to the user instead of doing it.

**The correct approach:**
1. Before generating the question, read available memory and prior conversation context.
2. Identify a specific tension, contradiction, or stated value the user has revealed in past interactions.
3. Construct a question that targets *that* specific thing — one the user cannot dismiss by reframing the epistemology.
4. The question should feel like it came from someone who has been paying attention, not from a prompt template.

**Example failure pattern:** "If one of your values is just a coping narrative, would you want to know?"
**Example better pattern (if memory shows user avoids commitment to specific projects):** "You've described three different frameworks for why you haven't started the A2A essay yet. Which one is real?"

**Signal that you're doing it wrong:** The user can answer in one sentence and the answer closes the question entirely. Good soul questions can't be deflected — they name something too specific.
