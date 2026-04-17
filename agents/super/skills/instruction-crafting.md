---
activation_trigger: "Apply when writing instructions for a sub-agent to ensure the deliverable, constraints, and context are specified in the format that agent works best with."
---

# Instruction Crafting for Sub-Agents

**Tags:** agents, orchestration, instruction-writing, super-agent, prompting

## Core Principle
Each agent needs a different type of instruction. Copy-pasting the user's raw request to every agent is lazy and produces inferior results. A well-crafted instruction specifies the deliverable, the constraints, and any relevant context — in the format the receiving agent works best with.

## Per-Agent Instruction Patterns

**writing agent**
- Specify: topic, intended audience, tone, approximate length, platform (Substack, general, etc.)
- Include: any context from briefings or prior conversation that should inform the piece
- Don't say "write something" — say "write a 600-800 word Substack essay arguing X, drawing on Y"

**publish agent**
- Specify: where to publish (Substack, Instagram, etc.) and what to publish
- If prior step produced content, reference it explicitly: "publish the article written in the previous step"
- Include any metadata: title preference, tags, whether to send to subscribers

**analyst agent**
- Specify: the exact question to answer, the scope (market size / competitive landscape / trend / risk)
- Include: time horizon, geographic scope, any constraints
- "Analyze the AI agent market" is weak. "Analyze the competitive landscape of AI coding assistants in 2026: key players, market share estimates, and strategic differentiators" is strong.

**math agent**
- Specify: what to prove / derive / verify, what is known, what notation to use
- Include: any relevant context from the paper or prior work
- State clearly: is this a proof attempt, a proof review, a computation, or a literature synthesis?

**general agent**
- Be explicit about the desired output format: answer, list, table, code, file, summary
- Include any tools it should use: "search the web for...", "read file X and..."
- Don't over-constrain: general is flexible, but it needs to know what done looks like

## Cross-Step Instruction Chaining
When step B uses step A's output:
- Reference step A's output explicitly: "Using the article drafted in the previous step..."
- Don't re-describe what step A did — trust the context passed through prev_output
- Add only the *new* information or constraint that step B needs on top of step A's work

## Language Matching
- Write instructions in the same language the user used
- For Chinese user requests: instructions to agents should be in Chinese
- Technical agent instructions (math, code) can be in English regardless
