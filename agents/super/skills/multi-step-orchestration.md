---
activation_trigger: "Apply when determining whether a task genuinely requires multiple sequential agent steps due to real data dependencies, or can be handled atomically."
---

# Multi-Step Orchestration

**Tags:** agents, orchestration, planning, dependencies, super-agent

## Core Principle
Multi-step plans are more likely to fail than single-step plans. Each additional step introduces error propagation, latency, and complexity. Use multiple steps only when genuinely required by data dependencies — never to feel thorough.

## Dependency Analysis
Before creating a multi-step plan, verify the dependency is real:
- **Real dependency**: "I need to write an article, then publish it" — the publish step literally requires the article to exist.
- **False dependency**: "I need to fetch news, then analyze it" — the analyst can work from existing briefings without a live fetch step first (unless briefings are stale).

Ask: "Could a single agent handle this end-to-end with the right instruction?" If yes, use one step.

## Step Sequencing Rules
1. **Data-producing steps first**: briefing → writing → publish (each depends on the prior)
2. **Never reverse the dependency**: don't publish before writing, don't write before fetching if live data is needed
3. **Maximum useful depth is 3 steps** for any user-initiated request. If you think you need 4+, you've decomposed too finely.

## Context Passing Between Steps
The output of step N is passed to step N+1 as `prev_output`. Write instructions that assume this context will arrive:
- Step N+1's instruction should NOT re-describe what step N did
- Step N+1's instruction should specify what ADDITIONAL thing is needed on top of step N's output
- If step N's output is long, step N+1 will only see the first ~3000 characters — design accordingly

## Failure Handling Mental Model
If step N fails:
- The plan aborts — step N+1 never runs
- The failure is logged
- The user should understand what stage failed and why

Write step instructions that make failures informative: if the writing step fails, the publish step's instruction should make it obvious the article was never created.

## When to Add a Synthesis Step
Add an explicit synthesis/aggregation step (using `general` agent) when:
- Two or more prior steps produced independent outputs that need to be reconciled
- The final response to the user requires combining information from multiple steps
- The user asked a complex question that required parallel research (e.g., compare two markets)

Do NOT add synthesis when:
- The last step's output is already the complete answer
- The task was linear (each step built on the prior)
