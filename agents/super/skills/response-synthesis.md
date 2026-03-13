# Response Synthesis

**Tags:** agents, orchestration, synthesis, aggregation, super-agent

## Core Principle
When multiple agents have produced outputs, synthesizing them into a single coherent response is itself a task requiring judgment — not just concatenation. The synthesized response should read as if one coherent intelligence answered the question, not as a stapled-together transcript of sub-agent outputs.

## When Synthesis Is Needed
- Multi-step plan where different agents addressed different parts of the same question
- Prior steps produced outputs that are individually correct but need reconciliation
- The user's original question requires integrating insights from both outputs to fully answer

## Synthesis Process

**1. Identify the thread**
What was the user's original question or need? Every element of the synthesis must serve that thread. Sub-agent outputs that don't serve it are context, not content.

**2. Establish hierarchy**
Not all outputs are equal. Determine which output is primary (directly answers the question) and which is supporting (adds context or evidence). Lead with primary.

**3. Reconcile conflicts**
If two outputs contradict each other, don't paper over it — note the tension and reason through which is more reliable given the context.

**4. Remove redundancy**
When two steps touched the same topic, keep the richer or more recent version. Don't present the same information twice with different wording.

**5. Add connective tissue**
Write transitions between sections from different agents. The reader should not be able to detect the seams between sub-agent outputs.

## Format Decisions
- If the original request was a question → answer format, prose, conclusion last
- If the original request was a task → deliverable format (the artifact itself, with a brief status note)
- If multiple deliverables were produced → present each clearly labeled, with a summary at the top

## Quality Test
Read the synthesis as if you were the user receiving it cold. Does it answer the original question? Is anything redundant or confusing? Would a human expert be proud to send this?

## Anti-Patterns
- **Concatenation without integration**: pasting step 1 output + "And here is step 2:" + step 2 output
- **Over-hedging**: adding caveats to everything because you're combining multiple sources
- **Losing the conclusion**: burying the actual answer inside a wall of supporting information
- **Format mismatch**: the user asked a quick question; the synthesis produces a 1000-word report
