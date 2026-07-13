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
7. Maintain an active growth recovery sprint when distribution drops.

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

## Growth Recovery Sprint

When the publication loses distribution momentum, the Substack agent keeps a
separate recovery sprint in `data/social/substack_agent/growth_recovery.json`
and a readable report in `growth_recovery_report.md`.

The sprint is measured from real local artifacts:

- `publication_stats.json` for articles, aggregate subscribers, and article engagement.
- `notes_state.json` for posted Notes and queued article follow-ups.
- `growth_state.json` for outbound relationship comments and touched targets.
- `comment_metrics.json` for author replies and comment outcomes.

The recovery target is one flagship article per week, 5-7 Notes, 8-12
relationship comments, at least five distinct relationship targets, one earned
reply, one restack, one new subscriber, and one recommendation or collaboration
path. The tracker stores aggregate counts only; subscriber names and emails are
not copied into sprint state.

## Takeover Rule

The new agent can take ownership of more steps only after it proves that the
step is at least as reliable as the old path. Publishing and paid/account
changes remain approval-gated.
