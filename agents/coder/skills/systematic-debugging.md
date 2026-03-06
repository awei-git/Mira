# Systematic Debugging

**Tags:** coding, debugging, workflow

## Core Principle
Debug by bisecting the problem space — systematically narrow down where the bug lives instead of guessing.

## Technique
Most debugging time is wasted on guessing. Systematic debugging replaces guesswork with a binary search through the problem space:

1. **Reproduce** — Get a reliable, minimal reproduction. If you can't reproduce it, you can't fix it.
2. **Hypothesize** — Form a specific, falsifiable hypothesis: "The bug is in function X because Y"
3. **Bisect** — Design an experiment that cuts the problem space in half:
   - Add a log/print at the midpoint of the suspected code path
   - Check: is the data correct here? If yes, bug is downstream. If no, bug is upstream.
4. **Repeat** — Each bisection halves the search space. 1000 lines → 500 → 250 → 125 → ~7 bisections to find the exact line.

Critical rules:
- **One variable at a time** — Never change two things and test. You won't know which change fixed it.
- **Read the error message** — Actually read it. The answer is often right there.
- **Check your assumptions** — The bug is usually in what you're sure is correct.
- **git diff** — Compare working version to broken version. The bug is in the diff.

## Application
- Before touching code, reproduce the bug reliably
- Write the hypothesis down (even mentally) before investigating
- Use binary search on code paths, git history, or input data
- When stuck: explain the problem to someone (or rubber duck) — the act of explanation often reveals the assumption you're wrong about
