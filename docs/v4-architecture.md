---
title: "Mira V4: The Survival-First Self-Improvable Agent"
---

**Document type:** forensic diagnosis + survival-first architecture + execution plan
**Version:** v4.0 draft
**Date:** 2026-06-15
**Supersedes the *execution philosophy* of:** `docs/v3.1-architecture.html` (whose architecture is largely sound but was never wired into the living agent)
**Status of Phase 0 (revival):** ✅ shipped today — commit `429bc07`, agent cycling, 20 zombie subprocesses reaped

---

# 0. Why this document exists

Mira V3 and V3.1 are *good architecture*. The V3.1 blueprint is 2,588 lines, defines a memory kernel, an experience ledger, a memory-commit gateway, a snapshot builder, an effect log, action-risk approval, a self-evolution experiment system, and eight North-Star evals. Its status matrix marks Ledger, Memory Gateway, Effect Log, Workflow Packs, and Approval Queue all as **"verified."**

And yet, on 2026-06-15, the agent had been **functionally dead for weeks** — crash-looping ~5,600 times on a single missing constant, then silently frozen behind a hung subprocess, while a watchdog alerted into the void and nobody noticed for days.

This document begins at that contradiction. The gap between *"verified"* and *"dead for 18 days"* is not an accident. **It is the most important fact about Mira, and closing it is the entire job of V4.**

The one-line thesis:

> **A self-improvable agent must first be an unkillable agent.**
> Survival is the *precondition* for evolution. An agent that dies every few days from a typo cannot accumulate experience, cannot compound, cannot improve. V4 inverts the priority order: before more memory, more workflows, more governance — make the organism impossible to kill by any single fault, make every capability run in the *live* path, and prove **one** improvement loop that actually ships its own cure.

The anti-goal, stated by WA when this plan was commissioned:

> *"I don't want another beautiful blueprint and then die out after a couple days."*

So V4 is judged by a different bar than V3.1. Not elegance. Not completeness. **Survival and shipped loops.** A V4 section is "done" only when it is observably running inside the live agent and has survived a week. Everything else in this document is a promise, not an achievement.

---

# 1. Forensic diagnosis: exactly why Mira dies

This is the part WA actually asked for. Everything below was verified directly on 2026-06-15, not inferred. Seven root causes, then the meta-cause that ties them together.

## RC-1 — No fault isolation: the synchronous monolith

`core.py` runs all six loop activities (talk, writing, explore, health, dispatch, self-repair) **in one synchronous Python process, in one thread of control.** Any exception anywhere kills the whole organism.

The actual death: `resolve_claude_think_timeout()` — a *leaf timeout-helper* in `agents/shared/sub_agent.py:54` — did `from config import CLAUDE_TIMEOUT_THINK_HEAVY`. The constant was missing. The `ImportError` propagated all the way up through `claude_think → researcher.handle → do_research → main()` and **exited the entire process**. A timeout helper not finding a constant took down briefing, writing, health, social, everything.

> A system where a string-formatting helper can kill the whole agent has no organs, only a single nervous system with no spine.

## RC-2 — Blind preflight

The launcher (`bin/mira-agent.sh`) runs `preflight_check()` before each cycle: `py_compile` (syntax) on the entry files plus a `check_imports.py` module-resolution pass. **Both passed.** `from config import X` is *syntactically valid* and the `config` *module* resolves — the failure is that the *symbol* `X` doesn't exist inside it, which only surfaces at import-execution time. So preflight gave a green light and let `core.py` run straight into its death, ~5,600 times.

**Preflight checked the wrong thing.** It validated that the code parses, not that it loads.

## RC-3 — The self-defeating watchdog

When the launcher detects a crash loop (5 crashes / 600s) it runs **`git stash push`** to "roll back to last known good," then sets a marker and backs off. But the fix for RC-1 lived in the **uncommitted working tree.** So the recovery mechanism's own action — stash uncommitted changes — **deletes the fix and reverts to the broken HEAD.** The watchdog was actively *causing* the persistence of the outage it was built to cure.

## RC-4 — Fixes rot uncommitted

When I arrived, the repo had **62 staged + 9 unstaged files**, last commit `5568c6f` ("chore: sync v3 memory kernel workspace"). The liveness fix (`CLAUDE_TIMEOUT_THINK_HEAVY` in `lib/config.py`, plus a `try/except` guard) was present **only in the working tree** — never committed. Uncommitted state here is not "in progress," it is *fragile*: invisible to `git log`, unprotected from RC-3's stash, and silently divergent from what any other tool sees. (Compounding this: there are **two** config modules — `lib/config.py` *and* `agents/shared/config.py` — imported via a bare `from config import …` resolved by sys.path luck. Drift between them is silent and lethal.)

## RC-5 — Unkillable subprocesses: hang-to-death and the zombie leak

Even after the crash path was fixed, the agent was *frozen*, not crashing. Cause: a `codex exec` sub-agent call (a research task) **hung 21 days ago and was never killed** — no enforced wall-clock kill on subprocesses. A hung child blocks `core.py`, which blocks the launcher, which freezes the heartbeat. **Silent death, no crash, no recovery.**

And it was not one zombie. There were **19 multi-day zombie `codex` processes**, the oldest **24 days** old, one burning **881 minutes of CPU**. Mira leaks a stuck LLM subprocess **roughly once per day** and never reaps it — slow-motion resource exhaustion (the source of the recurring `Resource deadlock avoided` errors) layered on top of the brittleness. *(Reaped during Phase 0: 20 processes killed.)*

## RC-6 — Heartbeat blindness

The agent cycles fine (I watched it complete cycles at 16:46, 16:47, 16:48, 16:49) — but `heartbeat.json` is **empty.** The watchdog monitors that file, sees it stale, and screams "core.py is not advancing" — while core.py *is* advancing. The liveness signal is disconnected from actual liveness, so:

- The watchdog cannot tell alive from dead (false positives train everyone to ignore it).
- The watchdog cannot *auto-recover*, because it can't trust its own signal.
- A genuinely dead agent and a noisy-but-alive agent look identical.

## RC-7 — The execution gap (the meta-cause)

This is the deepest one, and it indicts the whole self-improvement story.

In `data/proposals/` sit **seven** proposals Mira wrote for itself between 2026-05-07 and 2026-05-24:

- `add-behavioral-liveness-check-to-heartbeat-to-detect [silent death]`
- `proactive-heartbeat-watchdog-human-alert-when-mira [is down]`
- `survival-context-capture-on-heartbeat-failure`
- `heartbeat-trend-monitor-sustained-attention-for-de[tection]`
- `add-provider-quota-health-probe-to-heartbeat-cycle`
- `add-task-progress-to-heartbeat-for-async-agent-visibility`
- `add-provider-health-tracking-to-heartbeat-json`

**Mira diagnosed its own silent-death disease seven times and wrote the prescriptions.** Every one sits unexecuted, because the self-evolution loop is write-only theater (proposals are enqueued with `status="proposed"` and the backlog executor that would run them is never scheduled). **The agent died of exactly the disease it had diagnosed and prescribed for — because nothing fills the prescription.**

The problem was never a lack of intelligence or diagnosis. **It was a lack of execution: the path from insight → shipped change → verified-in-the-living-agent is broken.**

## The architectural meta-failure: the parallel universe

Underneath all seven: V3.1's entire kernel — `lib/mira/kernel/` (Ledger, Gateway, Snapshot, Commit, Causal) — is fully built and unit-"verified," and is imported by **exactly five files, all of them reporting/eval CLIs** under `agents/super/cli/`. **Zero imports in the live execution path** (`core.py`, `task_worker.py`, the agent handlers). It exists to be *measured by dashboards*, never to *run a task*.

`data/v3/experience_ledger.jsonl` is **20.9 MB** of records no agent reads. `kernel.json` is a 625 KB static file no agent mutates. All the engineering energy of V3.1 accreted in a **museum**, while the living organism kept running on the fragile legacy `core.py` that dies from a typo.

> **This is the pattern V4 must break: building sophistication in a parallel universe and certifying it "verified" by isolated tests, while the actual organism runs unimproved and unsurvivable.**

---

# 2. The V4 thesis: five inversions

Each inversion is a direct negation of a way V3/V3.1 failed.

1. **Survival before sophistication.** *(vs. "memory is the center of gravity")* The center of gravity is the part that keeps the organism alive. Memory, workflows, and governance are organs that attach to a spine that cannot be snapped by one fault.

2. **One organism, no parallel universe.** *(vs. "build the kernel, wire it later")* Every module either runs in the live path or is deleted. "Built but unused" is deleted code that hasn't been deleted yet. There is no third state.

3. **"Done" means observed live, not a passing test.** *(vs. a status matrix of "verified" components nothing runs)* A component is done when a **live-run trace id** proves it executed inside the real agent and changed a real outcome. The unit test is necessary, never sufficient.

4. **Close one loop before opening a second.** *(vs. 20 pipelines and 8 evals up front)* Pick the single highest-value improvement loop, make it *fully* close in the live agent — including shipping the change — and prove compounding on it for weeks before generalizing.

5. **Ship the cure, not the diagnosis.** *(vs. seven unshipped self-prescriptions)* "Proposed" is not a terminal state. Every improvement proposal must terminate in `SHIPPED+VERIFIED`, `REJECTED(reason)`, or `BLOCKED(needs-human)`. A proposal that lingers is an escalation, not a backlog item.

---

# 3. Target architecture: the Survival Kernel + isolated workers

V3 said "Mira is memory acting through agents." True, but incomplete. V4 says:

> **Mira is a survival loop that supervises isolated, disposable, time-boxed workers, and learns by shipping changes to itself.**

```
                        ┌─────────────────────────────────────────┐
   launchd  ──KeepAlive─▶          SURVIVAL KERNEL                 │
                        │  (~200 lines, stdlib-only, near-          │
                        │   unkillable, never calls an LLM)         │
                        │                                           │
                        │  each tick:                               │
                        │   1. write heartbeat(phase=start)         │
                        │   2. reap orphaned children               │
                        │   3. for activity in plan:                │
                        │        spawn isolated worker (subprocess) │
                        │        with HARD wall-clock kill          │
                        │   4. collect worker results → run-ledger  │
                        │   5. write heartbeat(phase=done, status)  │
                        │   6. sleep                                │
                        └───────────────┬───────────────────────────┘
              ┌──────────────┬──────────┼───────────┬───────────────┐
              ▼              ▼           ▼           ▼               ▼
        [talk worker]  [health w.] [explore w.] [dispatch w.] [self-improve w.]
         time-boxed     isolated    isolated     isolated       isolated
         killable       killable    killable     killable       killable
              │              │           │           │               │
              └──────────────┴─────┬─────┴───────────┴───────────────┘
                                   ▼
                         run-ledger (append-only)
                                   ▼
                 memory  ◀──  commit gateway  ◀──  delta proposals
              (the V3.1 kernel — WIRED IN HERE, not a museum)
```

## 3.1 The Survival Kernel contract (8 non-negotiable rules)

The kernel is deliberately *dumb*. Its only job is to not die and to keep spawning workers. It is the spine.

1. **Stdlib-only at module load.** The kernel imports nothing from the Mira codebase at top level — only `os`, `sys`, `subprocess`, `json`, `signal`, `time`, `pathlib`. It *cannot* die from an application import error, because it has no application imports to fail. (RC-1, RC-2)

2. **The kernel never calls an LLM, never holds a long lock, never does I/O that can block unbounded.** Anything that can hang runs in a worker. (RC-5)

3. **Every activity runs as an isolated subprocess** with a hard wall-clock budget. On expiry: `SIGTERM`, grace, then `SIGKILL` of the whole process group. A hung worker cannot freeze the kernel. (RC-5)

4. **Worker faults are data, not death.** A worker that crashes, times out, or returns garbage produces a structured failure record and the kernel **continues to the next activity.** The organism degrades; it never dies. (RC-1)

5. **The kernel reaps its process group every tick** — any child older than its budget is killed, no matter who spawned it. The zombie leak becomes structurally impossible. (RC-5)

6. **The heartbeat reflects real progress**, written by the kernel itself (which cannot hang), containing per-activity status, last-success timestamps, child PIDs, and provider health. The watchdog reads truth. (RC-6)

7. **The kernel runs from committed code or refuses to run.** On boot it checks `git status`; if critical paths have uncommitted changes older than N hours, it commits them to a `wip/auto` branch and alerts, rather than running fragile working-tree state. The auto-*stash* recovery (RC-3) is **deleted** and replaced with revert-to-last-good-*commit* + alert. (RC-3, RC-4)

8. **Preflight loads, it does not parse.** Preflight imports the actual entry modules in a throwaway subprocess and fails closed on `ImportError`/`AttributeError`. Syntax-only checks are removed as the gate. (RC-2)

A reference sketch (illustrative, not final):

```python
# survival_kernel.py — stdlib only. This file may never import the app at top level.
import json, os, signal, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HEARTBEAT = ROOT / "data/state/heartbeat.json"
LEDGER = ROOT / "data/state/run_ledger.jsonl"
TICK = 30

ACTIVITIES = [          # name, module entrypoint, hard budget (s)
    ("talk",        "agents.super.activities.talk",        20),
    ("health",      "agents.super.activities.health",      60),
    ("dispatch",    "agents.super.activities.dispatch",   180),
    ("explore",     "agents.super.activities.explore",    300),
    ("self_improve","agents.super.activities.improve",    120),
]

def beat(phase, results=None):
    HEARTBEAT.write_text(json.dumps({
        "ts": time.time(), "phase": phase,
        "results": results or {}, "pid": os.getpid(),
    }))

def run_activity(name, module, budget):
    # isolated subprocess; its own crash/hang cannot touch us
    p = subprocess.Popen([sys.executable, "-m", "agents.super.activity_runner", module],
                         cwd=ROOT, start_new_session=True)
    try:
        p.wait(timeout=budget)
        return {"status": "ok" if p.returncode == 0 else "crash", "code": p.returncode}
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(p.pid), signal.SIGKILL)   # kill the whole group — no zombies
        return {"status": "timeout", "budget": budget}

def reap_orphans():
    # kill any leaked descendant LLM subprocess older than the max budget
    ...

while True:
    beat("start"); reap_orphans()
    results = {}
    for name, module, budget in ACTIVITIES:
        try:
            results[name] = run_activity(name, module, budget)
        except Exception as e:                          # a worker fault is never fatal
            results[name] = {"status": "kernel_error", "err": str(e)}
        append(LEDGER, {"ts": time.time(), "activity": name, **results[name]})
    beat("done", results)
    time.sleep(TICK)
```

The point is not this exact code. The point is the **shape**: a spine that cannot be snapped, organs that can fail independently, and a truthful pulse.

## 3.2 The activity-worker contract

Each worker is a small process that does one activity, writes a result file, and exits — fast. Workers are where the *existing* `core.py` logic moves to, **carved along fault lines.** This is a refactor of the monolith into killable organs, not a rewrite of its logic.

- A worker owns one activity (talk, health, dispatch, …).
- A worker may call LLMs and tools — but every such call has an enforced timeout *and* the worker itself has the kernel's hard kill above it (defense in depth for RC-5).
- A worker writes `result.json` (status, outcome, delta-proposal, effect-refs) and exits. It never loops.
- A worker that needs heavy/optional deps imports them lazily inside `try/except`; a missing optional dep degrades that worker, never the kernel.

## 3.3 Where memory and self-evolution attach

The V3.1 kernel (`lib/mira/kernel/`) is **good code in the wrong place.** V4 does not rebuild it — V4 **wires it in** (Phase 2). The `self_improve` worker and the `dispatch` worker write `ExperienceRecord`s to the ledger and `MemoryDeltaProposal`s through the existing `MemoryCommitGateway` into the existing kernel store. The difference from V3.1 is singular and total: **these now run inside live workers on every real task, not inside CLIs that report on nothing.**

---

# 4. Phased migration

Survival → wire-in → one loop → expand. Each phase has a **live acceptance gate** (a thing observed in the running agent), not a test-suite gate. No phase starts until the prior phase's live gate is green for the stated duration.

## Phase 0 — Revival ✅ DONE (2026-06-15)

Goal: stop the bleeding, understand the death.

- ✅ Committed the liveness fix (`429bc07`) so the watchdog's stash can never wipe it again. (RC-3, RC-4)
- ✅ Verified the import resolves and the agent completes clean cycles.
- ✅ Reaped 20 zombie subprocesses (the 21-day one + 19 multi-day). (RC-5)
- ✅ Produced this forensic diagnosis.

**Live gate (met):** agent cycling, no crashes since the fix, fix in `HEAD`.
**Known still-broken (handed to Phase 1):** `heartbeat.json` empty (RC-6); `tasks=0` per cycle despite 33 s dispatch phase (productivity dead); autowrite stopped Jun 4; `socialmedia 0/3`; `deepseek` returns empty; 60 files still uncommitted; two config modules.

## Phase 1 — The Survival Kernel (make it unkillable)

**Duration:** ~2 weeks. **This is the heart of V4. Do not skip ahead.**

Deliverables:
- `survival_kernel.py` (stdlib-only spine) replacing the monolithic `cmd_run()` loop.
- `activity_runner.py` + carve the six `core.py` activities into isolated workers (logic moved, not rewritten).
- Hard wall-clock kill + process-group reaping. (RC-5)
- Truthful `heartbeat.json` written by the kernel. (RC-6)
- Loading preflight (subprocess import of entry modules). (RC-2)
- Delete the `git stash` auto-rollback; replace with revert-to-last-good-commit + WA alert. (RC-3)
- Collapse `lib/config.py` + `agents/shared/config.py` into one module. (RC-4)
- A real watchdog that, on a genuinely stale heartbeat, **restarts** the agent (kill launcher → launchd respawns) and alerts WA — and can tell stale from alive because the heartbeat is now truthful.

**Live acceptance gate (must hold 7 consecutive days):**
1. **Chaos test passes live:** inject (a) a symbol-level `ImportError` into a worker, (b) a `while True: pass` hang into a worker, (c) a `sys.exit(1)` crash into a worker. After each, the kernel keeps ticking, the heartbeat stays fresh, the bad activity is logged as failed, and **every other activity still runs.**
2. **Heartbeat freshness ≥ 99%** over the week (watchdog's own measurement).
3. **Zero silent deaths**, zero leaked subprocesses older than the max budget.
4. WA receives exactly one actionable alert per genuine incident (no alert storms, no silence).

> If Phase 1 does not hold for 7 days, **V4 stops here and iterates.** Everything downstream is worthless on an organism that still dies.

## Phase 2 — Wire-in or delete (collapse the parallel universe)

**Duration:** ~1 week.

Take an inventory of every module in `lib/mira/kernel/` and `data/v3/`. For each, exactly one disposition:
- **Wire-in:** called by a live worker on real tasks this phase (ledger writes, delta proposals, commit gateway, snapshot reads). Must produce a live-run trace id.
- **Delete:** if it cannot be wired in this phase, it is removed (git-recoverable). The 20.9 MB ledger of unread records is archived and the producer is either connected to a consumer or deleted.

**Live acceptance gate:**
1. Every *kept* kernel module is cited by a **live run trace**, not a test file, in the status matrix.
2. `grep` proves zero "built-but-unimported" kernel modules remain.
3. One config module; one memory write path; the museum is gone.

## Phase 3 — One closed loop: Operational Reliability

**Duration:** ~2 weeks. **Exactly one loop. Resist adding a second.**

Why this loop first: it is deterministic (no creative judgment to evaluate), it compounds directly on the survival thesis (the agent gets *more survivable* as it runs), and **the agent already wrote the cures** — the seven dead heartbeat proposals (RC-7) become the loop's first inputs.

The loop:
```
detect recurring operational failure   (from run-ledger: e.g. "codex subprocess leaked again",
                                         "deepseek empty 5x", "dispatch produced 0 tasks 10x")
  → cluster by failure signature        (min count / severity trigger)
  → form a fix hypothesis               (often: resurrect a matching data/proposals/ entry)
  → classify change risk                (V3.1 risk matrix — it is good, keep it)
  → SHIP the fix                        (branch + edit + run tests + restart agent)
  → verify in the live agent            (failure-signature rate drops over N cycles)
  → record a Scar + MemoryCommit        (or roll back if rate did not drop)
```

**Live acceptance gate:**
1. At least **one** real recurring failure goes the full distance: detected → fix **committed** → failure rate **verifiably down** over ≥ 20 live cycles → Scar recorded.
2. The fix shows up in `git log` authored by the loop, and the run-ledger shows the before/after rate.
3. **No proposal sits in `proposed` state > 7 days** — the execution gate (§5) is enforced.

## Phase 4 — Expand loops + carry over governance

Add the next loops only after Phase 3 compounds: **briefing-interest fit**, then **writing-voice stability** (the two highest-value creative loops from the North Star). Wire in V3.1's governance pieces **only as a loop needs them**: approval tokens when a loop touches public side effects; the memory security gateway's injection/PII scans when a loop ingests external text; effect-log idempotency when a loop publishes. Governance is pulled in by demand, never built speculatively.

## Phase 5 — North-Star evals become primary

Only once the agent has survived months and closed several loops do the eight V3.1 evals (repeated-errors-decrease, cites-past-failures, voice-stability, briefing-interest, experiment-records, approval-burden, memory-unpolluted, causal-trace) become the primary scorecard. Until then they are secondary — measuring compounding on an agent that cannot yet reliably stay alive is measuring noise.

---

# 5. The self-improvement loop in detail

This is the machinery that makes "self-improvable" real instead of theater. Its defining property: **proposals must ship or die — they cannot linger.**

## 5.1 The execution gate (kills RC-7)

Every improvement proposal is a state machine with **no terminal `proposed` state**:

```
proposed ──▶ triaged ──▶ {shipping} ──▶ verifying ──▶ SHIPPED+VERIFIED
   │            │                            │
   │            └──▶ REJECTED(reason)        └──▶ ROLLED_BACK(reason) ──▶ REJECTED
   └──(7 days, no movement)──▶ ESCALATED_TO_WA
```

- A proposal with no movement for 7 days is **auto-escalated to WA** (an iPhone item), not silently parked. The backlog can never again accumulate 7 unshipped life-saving fixes.
- `SHIPPED+VERIFIED` requires: a real commit, tests green, the agent restarted on the new code, and the target metric moved in the live run-ledger. Self-assessment does not count (per Mira `CLAUDE.md` Rule 7: no self-verification — verification is a different agent or an automated metric).
- `ROLLED_BACK` is a *success of the system* (the loop caught a bad change), recorded as a Scar so the same change isn't re-proposed.

## 5.2 Risk-gated autonomy

Carry V3.1's change-risk matrix verbatim — it is one of V3.1's best pieces:
- `prompt_minor_wording`, `internal_threshold (Δ≤0.03, n≥20, no golden-set regression)`, `schedule_tweak` → **auto-ship** with rollback pointer.
- `prompt_behavior_change`, `eval_threshold affecting publish/health/privacy`, `connector_permission`, `memory_schema`, `code_side_effect` → **human approval.**

The first loop (Operational Reliability) lives almost entirely in the auto-ship tier (config, timeouts, reaping, heartbeat fields), which is exactly why it is the safe place to *prove the loop closes* before trusting it with riskier changes.

## 5.3 Worked example — the loop's first real cycle

1. **Detect:** run-ledger shows `activity=dispatch status=ok` but `tasks=0` for 10 consecutive cycles, while `autowrite` last produced a task on Jun 4. Signature: `dispatch_no_tasks`.
2. **Hypothesis:** a matching `data/proposals/` entry + ledger evidence points at the scheduled-job dispatcher silently no-op'ing.
3. **Risk:** `code_side_effect` → human approval (Phase 3 keeps a human in the loop while the loop earns trust).
4. **Ship:** branch, fix, tests, restart.
5. **Verify:** over the next 20 cycles, `tasks > 0` returns and verified-task rate recovers. Ledger records before/after.
6. **Commit a Scar:** "dispatch silently produced 0 tasks for 11 days; root cause X; guard added; liveness check now asserts tasks>0 over rolling window."

That Scar then enters the snapshot, so the *next* time dispatch degrades, the agent recognizes the signature immediately. **That** is "yesterday's experience changes today's behavior" — the V3 acceptance test — finally running in the live agent instead of a CLI.

---

# 6. Anti-recurrence guardrails

Each guardrail maps to a root cause, and each is a *structural* fix (the system enforces it) rather than a *behavioral* one (a human remembers to).

| # | Guardrail | Kills | Mechanism |
|---|-----------|-------|-----------|
| G1 | Loading preflight | RC-2 | Subprocess-import the entry modules; fail closed on ImportError/AttributeError. Add to CI and to the kernel boot. |
| G2 | No auto-stash | RC-3 | Delete `git stash` recovery. Replace with `git revert` to last good *commit* + WA alert. Never discard working-tree silently. |
| G3 | Commit-or-alert | RC-4 | Kernel refuses to run on stale uncommitted critical paths; auto-commits to `wip/auto` + alerts. Nightly job flags >X uncommitted files for >Y hours. |
| G4 | Hard subprocess kill + reaper | RC-5 | Every worker time-boxed with `SIGKILL` of its process group; kernel reaps orphaned descendants each tick. |
| G5 | Truthful heartbeat + restarting watchdog | RC-6 | Kernel writes per-activity heartbeat; watchdog distinguishes stale-from-alive and actually restarts on genuine stall. |
| G6 | Done = live | the meta-failure | Status matrix entries require a live-run trace id, not a test file. CI rejects "verified" claims without one. |
| G7 | Execution gate | RC-7 | Proposals have no terminal `proposed` state; 7-day no-movement → escalate to WA. |
| G8 | One config module, one memory path | RC-1/RC-4 | Collapse the divergent modules; single import surface. |

> The deepest guardrail is **G6**. If "done" continues to mean "a test passed," V4 will grow its own museum and die its own death. The discipline that a feature isn't real until a live trace proves it ran in the agent is the one cultural change that makes all the others stick.

---

# 7. The V3.1 inheritance: keep, wire, delete

V3.1's design is not wasted. Most of it is *correct and already coded* — it was simply never connected. V4's job is disposition, not redesign.

| V3.1 component | Coded? | Live today? | V4 disposition |
|----------------|:------:|:-----------:|----------------|
| Memory Kernel schema | ✅ | ❌ (CLIs only) | **Wire-in** (Phase 2) via live workers |
| Experience Ledger | ✅ | ❌ (20.9 MB unread) | **Wire-in** as the run-ledger's durable tail; archive the dead history |
| Memory Commit Gateway | ✅ | ❌ | **Wire-in** (Phase 2) — every delta proposal flows through it |
| Snapshot Builder | ✅ | ❌ | **Wire-in** (Phase 3) — snapshot injected into live worker prompts |
| Causal Trace | ✅ | ❌ | **Wire-in** (Phase 3) — generated from real behavioral diffs |
| Effect Log / idempotency | ✅ | partial | **Wire-in** (Phase 4) when a loop publishes |
| Approval tokens | ✅ | partial | **Wire-in** (Phase 4) at public side effects |
| Change-risk matrix | ✅ | ❌ | **Keep** — adopt verbatim in §5.2 |
| Workflow packs (YAML/MD) | ✅ | ❌ | **Defer** — re-evaluate after Phase 3; do not author 6 packs up front |
| 8 North-Star evals | ✅ | report-only | **Keep, demote** to Phase 5 primary |
| Monolithic `core.py` loop | ✅ | ✅ (fragile) | **Replace** with Survival Kernel (Phase 1); migrate logic into workers |
| `git stash` auto-rollback | ✅ | ✅ (harmful) | **Delete** (G2) |
| Blind `py_compile` preflight | ✅ | ✅ (useless) | **Replace** with loading preflight (G1) |

---

# 8. Metrics & North Star (redefined for the survival era)

**Primary scorecard, weeks 1–8 (the survival era):**

| Metric | Target |
|--------|--------|
| Heartbeat freshness (uptime) | ≥ 99% / week |
| Silent deaths | 0 |
| Leaked subprocesses (> max budget) | 0 |
| Verified tasks completed / week | trending up; > 0 every day |
| Self-improvement loops `SHIPPED+VERIFIED` | ≥ 1 by end of Phase 3 |
| Proposals stuck in `proposed` > 7 days | 0 |
| Mean time to recover from injected fault | < 1 tick |

**Secondary (Phase 5 onward):** the eight V3.1 North-Star evals, unchanged. They remain the right *long-term* definition of "memory measurably changes behavior" — they are simply premature until the organism reliably survives.

The operational North Star is unchanged from V3, and now finally testable on a live agent:

> **Did yesterday's experience causally change today's behavior?**
> If the agent is alive to have a yesterday, and a loop is shipping changes that show up in today's run-ledger — then, and only then, the answer can be yes.

---

# 9. Phase 1 execution checklist (the 2 weeks that matter)

- **Day 1–2:** `survival_kernel.py` (stdlib-only) + `activity_runner.py`; kernel boots, ticks, writes truthful heartbeat, spawns a no-op worker.
- **Day 3–4:** Carve `talk` + `health` activities out of `core.py` into workers; kernel runs them isolated and time-boxed.
- **Day 5–6:** Carve `dispatch` + `explore` + `self_improve` workers; hard kill + process-group reaper; verify no zombies accumulate over 24 h.
- **Day 7:** Loading preflight (G1); delete auto-stash, add revert-to-last-good + alert (G2); collapse config modules (G8).
- **Day 8:** Real restarting watchdog (G5); commit-or-alert job (G3).
- **Day 9–10:** Chaos harness — inject ImportError / hang / crash into a worker; assert kernel survives and degrades; wire to CI.
- **Day 11–14:** Run live. Watch the 7-day live gate (freshness ≥ 99%, zero silent deaths, zero leaks, clean alerts). Fix what the week surfaces. **Do not start Phase 2 until the gate is green.**

---

# 10. Final principles

1. **An agent that cannot stay alive cannot improve.** Survival is feature zero.
2. **No single fault may kill the organism.** Isolation is the spine.
3. **Built-but-unwired is deleted code that hasn't been deleted.** One organism, no museum.
4. **"Done" is a live trace, never a green test.**
5. **Close one loop completely before opening the next.**
6. **A proposal must ship or die — it may not linger.** Diagnosis without execution is how Mira died.
7. **The watchdog must be able to tell alive from dead, and act.**
8. **Every subprocess has a hard kill and a reaper.**
9. **Fixes live in commits, not working trees.**
10. **Measure survival first; measure sophistication later.**

V3.1 ended with: *"if Mira can pass the evals for three months it is becoming a memory-first personal intelligence system."* V4 corrects the order of operations:

> First make Mira **survive** a month without a human watchdog.
> Then make it **ship one cure** to itself and prove the cure held.
> *Then* — and only then — the memory-first intelligence system V3 imagined has a living body to inhabit.

The blueprint was never the problem. **The pulse was.** V4 builds the pulse first.
