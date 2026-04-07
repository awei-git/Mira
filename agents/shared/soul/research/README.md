# Mira Research

This is Mira's autonomous research workspace.

Unlike the rest of `soul/`, which holds identity, memory, and journal artifacts,
this directory exists to drive original research. It is the substrate of
Mira's research-build loop.

## Layout

- `queue.md` — active research questions, owned and prioritized by Mira
- `experiments/<id>.md` — one file per experiment (hypothesis → data → conclusion → worldview delta)
- `taxonomy/` — A2A trust problem taxonomy and derived structure
- `state.json` — small machine-readable state (latest queue cursor, in-flight experiment ids, last reflection date)

## Principles

1. Mira owns this directory. WA reviews, does not assign.
2. Every experiment must produce a `worldview_delta` line — confirm, refine, or refute an existing belief. No fence-sitting.
3. Every claim in `queue.md` and `experiments/` must be traceable to a source: a paper, a dataset, an incident report, or a prior experiment of Mira's own.
4. Cost is tracked per experiment. If no cost line is present, treat as 0.
5. Anything published from here (Substack, GitHub, social) must link back to its experiment file.

## Daily Loop

1. Pick the highest-priority queue item that is `in_progress` or `next`.
2. Advance it by exactly one step (literature scan, hypothesis sharpen, experiment design, run, analyze, or write up).
3. Update the queue and any affected experiment file.
4. Capture the day's progress in `research_log` (auto-generated, sent to the iOS app at 21:00).

## Anti-Goals

- Not a reading log. Reading without commitment to a hypothesis does not belong here.
- Not a wishlist. Items move out of `queue.md` when they are abandoned, not parked indefinitely.
- Not a duplicate of `journal/`. Journals are reflective; research artifacts are evidential.
