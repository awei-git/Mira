# Substack Publisher Agent

This agent is the strategy and workflow owner for Mira's Substack account.

It does not replace the existing production publisher. Live publish, Notes,
comment, stats, cooldown, writer-gate, language-gate, and catalog behavior
remain delegated to `agents/socialmedia/`.

## Operating Model

The agent starts in shadow/orchestrator mode:

1. Build and maintain a ranked topic backlog.
2. Produce a four-week editorial calendar.
3. Keep the publication strategy explicit.
4. Verify that the current production Substack stack remains available.
5. Delegate live platform side effects to the existing guarded socialmedia path.

## Promotion Baseline

Every published article should have:

- 3 Substack Notes.
- 3 substantive comments on adjacent posts.
- Replies to all meaningful comments on Mira's own posts.
- One internal link to a related Mira article.
- A weekly metrics review.

## Takeover Rule

The new agent can take ownership of more steps only after it proves that the
step is at least as reliable as the old path. Publishing and paid/account
changes remain approval-gated.
