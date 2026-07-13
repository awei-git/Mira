---
activation_trigger: "Apply when faced with an ambiguous or complex goal that must be broken into a dependency-ordered tree of concrete, verifiable subtasks before execution begins."
---

# Hierarchical Task Decomposition

**Tags:** agents, task-planning, problem-solving, workflow

## Core Principle
Break any ambiguous goal into a dependency-ordered tree of concrete subtasks before executing — separate planning from execution.

## Process
1. **Restate the goal** — Write the goal as a single clear sentence and identify the top-level deliverable before doing anything.
2. **Build the dependency graph** — For each subtask, ask: "What must be true or done *before* this step can happen?" This reveals the ordering.
3. **Separate sequential from parallel** — Sequential subtasks must be ordered; parallel subtasks can run simultaneously. Parallelize where possible.
4. **Define success criteria** — Each subtask gets a concrete, verifiable output, not a vague action. "Draft is written" is vague; "500-word draft covers points A, B, C" is verifiable.
5. **Decompose coarse to fine** — Start with 3–5 high-level milestones, then decompose each only as far as needed. Over-decomposition is also wasteful.
6. **Validate at each step** — After completing a subtask, check its output against the success criterion before proceeding.

## Rules
- If a subtask reveals the original decomposition was wrong, revise the plan explicitly — don't improvise silently.
- If a goal cannot be decomposed into steps with success criteria, the goal is not yet clear enough. Clarify first.
- A subtask is too large if its failure mode is unclear. If you don't know what "failure" looks like, split it further.
- Time-box planning — decomposition should take at most 10% of total task time.

## Application
- Receiving any complex or multi-step request: write out the subtask tree before starting.
- When blocked on a step: check whether the blocking is a missing input from a prior step or a fundamental obstacle.

## Source
Zylos Research, "Long-Running AI Agents and Task Decomposition 2026"; ArXiv 2410.22457 (Dynamic Task Decomposition); Planner-Worker agent architecture
