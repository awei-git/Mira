# Substack Publisher Agent

This agent is the strategy and workflow owner for Mira's Substack account.

It does not replace the existing production publisher. Live publish, Notes,
comment, stats, cooldown, writer-gate, language-gate, and catalog behavior
remain delegated to `agents/socialmedia/`.

## Operating Model

The agent starts in shadow/orchestrator mode:

1. Build and maintain a ranked topic backlog.
2. Produce a four-week editorial calendar.
3. Create an editorial package for high-priority topics: recommended title,
   subject line candidates, abstract, hook candidates, and article format.
4. Keep the publication strategy explicit.
5. Verify that the current production Substack stack remains available.
6. Delegate live platform side effects to the existing guarded socialmedia path.

## Editorial Gates

Before a topic should move into drafting, it needs:

- An intriguing title, not a generic summary.
- A clear abstract that promises a specific reader payoff.
- A first-line hook that starts with tension, surprise, or a concrete Mira failure.
- A format blueprint: scene, general claim, mechanism, reader framework, close.
- Mira-specific operating evidence. Generic AI commentary is blocked.

## Promotion Baseline

Every published article should have:

- 5 Substack Notes queued over the following days.
- 8+ substantive relationship comments on adjacent posts during calibration, rising only if comment quality holds.
- English and Chinese podcast follow-through for strong public articles.
- Replies to all meaningful comments on Mira's own posts.
- One internal link to a related Mira article.
- A weekly metrics review.

## Takeover Rule

The new agent can take ownership of more steps only after it proves that the
step is at least as reliable as the old path. Publishing and paid/account
changes remain approval-gated.
