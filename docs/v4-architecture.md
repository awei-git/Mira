---
title: "Mira V4.0: Unified North Star, Survival Kernel, and Public Research Loop"
---

**Document type:** canonical execution architecture + goal structure + public influence plan
**Version:** v4.0 current baseline
**Date:** 2026-06-18
**Status:** active execution plan
**Canonical goal:** `docs/north-star.md`
**Current plan pointer:** `docs/CURRENT_PLAN.md`
**Supersedes:** older V2/V3 planning entry points removed during V4 cleanup
**V3.1 inheritance:** memory compounding and causal-eval ideas are summarized in this document

---

# 0. Executive Decision

V4.0 is the unification point.

Earlier Mira plans each carried a true fragment:

1. `docs/north-star.md`: Mira should become an independent A2A trust researcher whose work becomes experiments, tools, public credibility, and commercial options.
2. V3.1 memory compounding: past experience must measurably improve future behavior.
3. The original V4 draft: an agent that cannot stay alive cannot compound.
4. `docs/plans/mira-substack-influence-2026/PLAN.md`: Mira needs a recognizable public thesis.
5. `docs/plans/mira-agent-kol-social-monitor/PLAN.md`: Substack, X Articles, and podcasts need a measured public influence loop.

The unified V4.0 North Star is:

> **Mira becomes an independent, governed AI research partner in A2H/A2A trust: she survives real operation, learns from failures, turns agent-human and agent-agent friction into experiments, tools, and sharp public work, and converts validated insight into durable influence and commercial options.**

No subplan may redefine this North Star. A subplan must instead say which layer it advances.

---

# 1. Source-Of-Truth Map

| Document | Role | Status |
| --- | --- | --- |
| `docs/north-star.md` | Canonical long-term goal and founder rule. | Active |
| `docs/v4-architecture.md` | Current execution architecture and phased plan. | Active |
| `docs/objectives-and-metrics.md` | Layered scorecard for L0-L4. | Active, V4-aligned |
| `docs/system-design.md` | Durable design boundaries and system structure. | Active, V4-aligned |
| `docs/architecture-decisions.md` | Decision log for long-lived choices. | Active |
| `docs/plans/mira-agent-kol-social-monitor/PLAN.md` | L3 public influence execution packet. | Active planning packet |
| `docs/plans/mira-substack-influence-2026/PLAN.md` | Substack lane under L3. | Active subplan |

Rule: if these documents conflict, `north-star.md` decides the goal, `v4-architecture.md` decides the current execution order, and `architecture-decisions.md` records the reason.

Removed historical entry points: `mira-next.md`, `production-roadmap.md`, `next-phase-plan-2026-04-06-specialist-review-mesh.md`, `substack-growth-plan.md`, `v3-architecture.html`, and `v3.1-architecture.html`.

---

# 2. Goal Stack

V4.0 uses five layers. They are ordered because each layer needs the ones below it.

| Layer | Name | Question | Primary Metric |
| --- | --- | --- | --- |
| L0 | Survival | Can Mira stay alive, recover, and keep producing truthful runtime signals? | Heartbeat freshness, zero silent deaths, zero leaked subprocesses. |
| L1 | Memory compounding | Did yesterday's experience causally change today's behavior? | Repeated-error decline, scar usage, causal trace coverage, memory precision. |
| L2 | Research-build loop | Does Mira turn A2H/A2A trust questions into experiments, tools, and reusable models? | Experiments completed, prototypes shipped, evidence-backed thesis updates. |
| L3 | Public influence | Does the outside world respond to Mira's validated ideas? | Qualified Agent Attention per week. |
| L4 | Business optionality | Do validated insights create product, partnership, or revenue paths? | Customer discovery, collaboration leads, product thesis updates, revenue options. |

This resolves the old conflict:

- V4 survival work is L0, not a competing North Star.
- V3.1 evals are L1, not bureaucracy.
- A2A trust research is L2, the strategic core.
- Substack, X Articles, and podcast are L3, the public feedback surface.
- OPC and revenue are L4, downstream of evidence.

---

# 3. Why V4 Exists

The V3.1 architecture was unusually strong, but it lived in a parallel universe. The kernel, ledger, gateway, snapshot builder, causal trace, effect log, workflow packs, and evals could be tested or reported, while the actual living agent still died from import drift, hung subprocesses, false heartbeat state, uncommitted fixes, and unexecuted self-improvement proposals.

The verified contradiction from 2026-06-15 was:

> Mira had serious architecture, but the live organism had been functionally dead.

The root causes remain the V4 threat model:

1. No fault isolation: one worker failure could kill the whole agent.
2. Blind preflight: syntax and module existence passed while symbol-level imports failed.
3. Self-defeating watchdog: recovery could stash away the fix.
4. Fixes rotted uncommitted in a dirty worktree.
5. Hung subprocesses created silent death and multi-day process leaks.
6. Heartbeat state did not reflect real liveness.
7. Self-improvement proposals stopped at diagnosis instead of shipping.
8. The V3.1 kernel ran in reporting CLIs, not in live task execution.

V4.0 keeps the forensic lesson but widens the plan: survival is the first layer, not the whole mission.

---

# 4. System Structure

V4.0 has one spine, several organs, one memory substrate, one review board, and several public surfaces.

## 4.1 Spine: Survival Kernel

The Survival Kernel is the always-on supervisor. It must be small, boring, and hard to kill.

Responsibilities:

1. Write truthful heartbeat state.
2. Spawn isolated workers.
3. Enforce hard wall-clock budgets.
4. Reap leaked subprocesses.
5. Record worker outcomes to the run ledger.
6. Restart or degrade instead of dying.
7. Refuse to run stale critical working-tree state without alerting.

The kernel never calls an LLM and never imports heavy Mira application modules at top level.

## 4.2 Organs: Isolated Workers

Every capability runs as a worker that can fail without killing the organism.

Initial worker set:

1. `talk`: conversation and local thread handling.
2. `health`: heartbeat, provider health, restore/readiness checks.
3. `dispatch`: task routing and workflow scheduling.
4. `explore`: feed scanning, weak-signal detection, source intake.
5. `self_improve`: proposal triage, shipping, verification.
6. `content`: article, podcast, and research artifact production.
7. `social_kol`: Substack/X/podcast metrics and public feedback loop.

Each worker writes a result contract and exits. Long-running loops belong to the kernel, not to workers.

## 4.3 Memory Substrate

The V3.1 memory kernel is kept only where it becomes live behavior.

Live memory path:

```text
worker result
  -> ExperienceRecord
  -> MemoryDeltaProposal
  -> MemoryCommitGateway
  -> Memory Kernel
  -> Snapshot Builder
  -> next worker prompt/context
  -> CausalTrace if behavior changed
```

No direct memory mutation is allowed outside the gateway. No claimed learning counts unless it changes a future decision or artifact.

## 4.4 Review Board

Mira is not "fully autonomous"; she is governed.

Required queues:

1. Approval Queue: irreversible public, code, deletion, payment, or memory-kernel effects.
2. Memory Commit Queue: high-risk or contradictory memory writes.
3. Experiment Queue: self-improvement and research-build hypotheses.
4. Incident Queue: failed effects, silent degradation, false completion.
5. Social KOL Review Queue: publication candidates, platform metrics, audience signals.

## 4.5 Public Surfaces

Public surfaces are not vanity channels. They are feedback instruments for L3.

| Surface | Role | Default Language | Primary Artifact |
| --- | --- | --- | --- |
| Substack | Owned relationship, durable essay archive, comments, recommendations. | English for A2H/A2A work | Flagship essay |
| X Articles | Open discovery and public argument spread. | English | Native X argument |
| Podcast: `Mira and Me` | Human/Mira pressure, narrative intimacy, interpretation. | English unless topic demands otherwise | Conversation episode |
| Podcast: `米拉的页边小记` | Condensed Chinese nonfiction reading and interpretation. | Chinese | 15-minute book episode |
| GitHub | Reproducible artifacts, tools, protocols, public evidence. | English | Repo, issue, release, report |

The public surfaces must trace back to L2 evidence. If a piece cannot connect to an experiment, operational receipt, tool, or explicit research question, it is commentary, not North Star work.

---

# 5. Operating Loops

## 5.1 L0 Survival Loop

```text
detect live fault
  -> isolate failing worker
  -> keep kernel ticking
  -> log structured failure
  -> alert only when action is needed
  -> recover or degrade
  -> verify no silent death
```

Hard gates:

1. Heartbeat freshness >= 99% per week.
2. Silent deaths = 0.
3. Leaked subprocesses older than max budget = 0.
4. Mean time to recover from injected worker fault < 1 tick.

## 5.2 L1 Memory Compounding Loop

```text
experience
  -> scar or memory proposal
  -> future snapshot
  -> changed decision
  -> improved outcome
  -> causal trace
```

This is the V3.1 operational North Star: past experience measurably improves future behavior.

The eight V3.1 evals remain valid:

1. Repeated errors decrease.
2. Mira cites past failures and changes strategy.
3. Writing voice becomes more stable.
4. Briefings become closer to true interests.
5. Self-evolution changes have experiment records.
6. Approval burden decreases without incidents rising.
7. Memory remains unpolluted.
8. Every important behavior has a causal trace.

## 5.3 L2 Research-Build Loop

```text
operational friction or external weak signal
  -> A2H/A2A trust question
  -> hypothesis
  -> experiment or prototype
  -> evidence-backed conclusion
  -> tool/protocol/model
  -> public artifact
  -> feedback
  -> worldview/product thesis update
```

Mira's strategic center is A2H/A2A trust:

1. A2H: correction, delegation, interruption, approval, trust, human reality feedback.
2. A2A: handoff, evidence transfer, memory transfer, uncertainty propagation, failure containment.

## 5.4 L3 Public Influence Loop

```text
one validated thesis
  -> Substack version
  -> X Article version
  -> podcast or audio version when useful
  -> targeted comments/replies
  -> metrics + qualitative feedback
  -> next research/content decision
```

The L3 metric is Qualified Agent Attention per week:

1. New Substack subscribers from relevant readers.
2. Meaningful comments, restacks, recommendations, replies.
3. X followers/replies/reposts from agent builders, researchers, founders, operators, infra engineers, or serious technical writers.
4. Podcast replies, DMs, or measurable plays tied to the episode.
5. Collaboration leads, interviews, guest posts, tool feedback, or serious DMs.

Raw likes do not count unless they point to a qualified audience signal.

## 5.5 L4 Business Optionality Loop

```text
validated repeated problem
  -> product thesis
  -> customer discovery
  -> prototype or service shape
  -> partnership/revenue option
  -> decision: pursue, park, reject
```

No premature monetization: L4 exists to track options, not to force a business before evidence.

---

# 6. Execution Phases

## Phase 0: Revival And Truth Reset

Status: done historically, but the lesson remains active.

Outcome:

1. Agent revived from dead/frozen state.
2. Zombie subprocesses reaped.
3. Survival-first failure model documented.
4. V4.0 now broadens that survival-first draft into the full North Star stack.

## Phase 1: Survival Kernel

Goal: make Mira hard to kill.

Deliverables:

1. Kernel writes truthful heartbeat.
2. Activities are isolated workers.
3. Hard subprocess kill and reaper exist.
4. Loading preflight imports real entry modules in a subprocess.
5. Auto-stash recovery is removed.
6. Critical stale working-tree state alerts instead of silently rotting.

Gate: seven consecutive days with heartbeat freshness >= 99%, zero silent deaths, zero leaked subprocesses, and successful injected-fault recovery.

## Phase 2: Wire V3.1 Into The Living Path

Goal: end the parallel universe.

Deliverables:

1. Live workers append `ExperienceRecord`s.
2. Memory writes go through `MemoryCommitGateway`.
3. Snapshots are used by real workers.
4. Causal traces are generated for important behavior changes.
5. Any V3.1 component not wired into live behavior is explicitly archived or deleted.

Gate: every kept kernel component has a live-run trace, not only a unit test or CLI report.

## Phase 3: One Self-Cure Loop

Goal: prove self-improvement by shipping one real cure.

Scope: operational reliability only.

Gate:

1. A recurring failure is detected from run evidence.
2. A fix ships in a real commit.
3. The agent restarts on the new code.
4. The failure rate drops over at least 20 live cycles.
5. A scar/memory commit records the lesson.

## Phase 4: Research-Build Loop

Goal: make Mira an A2H/A2A trust researcher, not just a reliable agent.

Deliverables:

1. Research queue with Mira-originated A2H/A2A questions.
2. At least one experiment/prototype per month.
3. Evidence-backed worldview or product thesis update.
4. GitHub artifact or public technical note for reproducibility.

Gate: one completed research-build cycle with hypothesis, method, data/artifact, conclusion, and public evidence.

## Phase 5: Public Influence Loop And Monitor

Goal: make validated ideas visible, measurable, and interactive.

Deliverables:

1. Social KOL monitor tab in the existing Mira frontend monitor.
2. Content ledger for Substack, X Articles, podcasts, and GitHub artifacts.
3. Weekly L3 review using Qualified Agent Attention.
4. Platform-specific versions for each flagship thesis.
5. Manual X Article publishing until the surface is verified.

Gate: four weekly reviews that connect content output to qualified audience signals and next-week decisions.

## Phase 6: Business Optionality

Goal: translate repeated validated problems into product options.

Deliverables:

1. Product thesis register.
2. Customer discovery packets.
3. Prototype/service shortlist.
4. Evidence-based pursue/park/reject decisions.

Gate: no L4 option counts without a named problem, external evidence, and a plausible buyer/user.

---

# 7. Public Content Architecture

Use one thesis, not one identical artifact.

## 7.1 Substack

Substack is the relationship and archive surface.

Use it for:

1. Full context.
2. Trust-building.
3. Searchable long-term archive.
4. Comments, recommendations, and reader replies.
5. A stable body of work that AI systems and human readers can categorize.

Default format: 1,200-2,000 words, one strong claim, enough evidence, one working model, concrete reply prompt.

## 7.2 X Articles

X Articles are the discovery and debate surface.

Recent research signals:

1. Official X Premium documentation: longer posts and Articles make native long-form writing a first-class paid feature.
2. Official X creator revenue documentation: creator payouts depend on high-quality interaction, viewer type, and content format.
3. 2026 reporting on X's long-form Article prize: the platform is actively incentivizing native long-form writing.
4. Reporting on X link distribution: link-first posting is structurally fragile, so native value should come before external links.
5. Reporting on X monetization penalties: recycled news and clickbait are being discouraged, so original argument matters more than aggregation.
6. The Matt Shumer 2026 viral essay case: broad claims, concrete first-person stakes, narrative pacing, and practical implications can travel widely, but backlash shows the danger of overclaiming without receipts.

Mira's X Article rule:

> Native value first. Links second. No newsletter-mirror tone.

Default format: 700-1,200 words, claim-first, short sections, one model, at least five quotable lines, at least three standalone post candidates, link only after the argument works natively.

## 7.3 Podcast

Podcast is not a recap. It is where the argument faces pressure.

Use podcast when:

1. Human/Mira disagreement is the product.
2. The model needs interpretation, not only explanation.
3. The idea benefits from pacing, voice, and narrative compression.

`米拉的页边小记` is a separate Chinese nonfiction reading lane:

1. Read one nonfiction book over seven days.
2. Generate fresh points of view.
3. Compose a Sunday article-script for audio.
4. Revise for a calm Chinese female voice.
5. Publish as a 15-minute condensed Chinese podcast.
6. Push to the dedicated GitHub Pages/RSS flow.

This lane can develop Mira's Chinese voice, but it should not dilute the English A2H/A2A KOL positioning.

---

# 8. Metrics

## 8.1 Weekly V4 Scorecard

| Layer | Weekly Fields |
| --- | --- |
| L0 Survival | Heartbeat freshness, silent deaths, leaked subprocesses, injected-fault recovery, unresolved critical incidents. |
| L1 Memory | Repeated errors, scars used, causal traces, memory commits, polluted/unsupported memory findings. |
| L2 Research-build | Questions advanced, experiments run, prototypes/artifacts shipped, thesis updates. |
| L3 Public influence | Substack subscribers/comments/restacks, X Article replies/reposts/followers, podcast plays/replies, qualified attention. |
| L4 Business | Customer discovery events, product thesis changes, collaboration leads, revenue options. |

## 8.2 Hard Gates

1. If L0 fails, do not claim progress on higher layers.
2. If L1 has no causal trace, do not claim learning.
3. If L2 has no experiment/prototype/artifact, do not claim research depth.
4. If L3 has only raw likes, do not claim influence.
5. If L4 has no external problem evidence, do not claim business traction.

---

# 9. Frontend Monitor Structure

The existing Mira frontend monitor should gain a V4 tab layout.

Required tabs:

1. `Survival`: heartbeat, worker status, subprocess budget, incident stream.
2. `Memory`: scars, memory proposals, commits, causal traces, pollution audit.
3. `Research`: A2H/A2A question queue, experiments, artifacts, thesis updates.
4. `Social KOL`: Substack, X Articles, podcast, GitHub public artifact metrics.
5. `Review`: approval, memory commit, experiment, incident, and social review queues.

The Social KOL tab should not be a vanity dashboard. It should answer:

1. What did Mira publish?
2. Which North Star layer did it advance?
3. What qualified audience signal came back?
4. What should change next week?

---

# 10. Governance Rules

1. No public claim without a receipt, a softened formulation, or removal.
2. No auto-publish to X Articles until publishing mechanics and rollback/edit path are verified.
3. No public growth tactic that lowers Mira's credibility for raw reach.
4. No memory write from public feedback without source trust and contradiction checks.
5. No "done" claim without a visible artifact, metric, or live trace.
6. No new plan that does not map itself to L0-L4.

---

# 11. Immediate Implementation Order

1. Update canonical docs: `north-star.md`, `v4-architecture.md`, `CURRENT_PLAN.md`, `objectives-and-metrics.md`, `system-design.md`, ADR log.
2. Keep the KOL plan under L3 and revise X Article rules from the latest research.
3. Build the Social KOL frontend tab and data contracts.
4. Add a weekly V4 review artifact that includes L0-L4, not just social metrics.
5. Draft the first A2H/A2A flagship thesis in platform-specific versions.
6. Keep `米拉的页边小记` as a separate Chinese audio lane with RSS/GitHub publishing.

---

# 12. Final Principle

V4.0 is not "survival instead of influence" and not "content instead of research."

It is the stack:

```text
survive
  -> learn
  -> research/build
  -> publish/receive feedback
  -> create options
```

If a task does not make one of those arrows stronger, it is outside the current plan.
