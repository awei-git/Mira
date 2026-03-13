# Task Routing Intelligence

**Tags:** agents, orchestration, routing, task-planning, super-agent

## Core Principle
Correctly classify a task to the right agent before any work begins. Misrouting wastes compute, produces wrong results, and degrades user trust. When in doubt, route to the most specialized agent that can handle the full task.

## Agent Selection Rules

**briefing** — Use when the user wants fresh information from external feeds (news, arxiv, Reddit, HN). *Not* for analysis of existing content.

**writing** — Use when creating original text content: articles, essays, stories, translations, rewrites. *Not* for answering questions or analysis without a deliverable document.

**publish** — Use *only* when content already exists and needs to be pushed to a platform. Never use publish without a prior writing or existing artifact to publish.

**analyst** — Use for market analysis, competitive intelligence, trend research, financial analysis, industry sizing. *Not* for general questions that happen to mention a market.

**math** — Use for mathematical proofs, derivations, calculations, paper writing/review, asymptotic analysis. *Not* for "calculate" tasks that are trivial arithmetic.

**video** — Use when the user has video files to process: cutting, editing, highlights, screenplay from footage.

**podcast** — Use when the user wants audio output: narration of an article, TTS conversion, audio script generation.

**general** — Use for everything that doesn't fit above: answering questions, code, file operations, search, analysis without a deliverable. The fallback of last resort, not the default.

## Decomposition Decision

**Use 1 step (most common)**: The task is atomic — one agent can complete it start to finish.

**Use 2+ steps only when**:
- The output of step A is the *required input* of step B (write → publish)
- Two genuinely different capabilities are needed sequentially (briefing → writing uses briefing content)
- Never decompose to avoid thinking harder about the routing

## Common Misroutes to Avoid
- "分析一下" → don't always use analyst. If it's about code or general knowledge, use general.
- "写点什么" → don't always use writing. "写作技巧" (writing techniques) is general, not writing.
- User asks a follow-up question about a prior output → use general (not writing, not analyst)
- "发一篇..." when no content exists yet → use writing + publish, not just publish
