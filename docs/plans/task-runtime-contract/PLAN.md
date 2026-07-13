# Mira Task Runtime Contract Refactor

- **Date**: 2026-05-01
- **Status**: planning
- **Owner**: Default User + Mira
- **Scope**: Mira super-agent task orchestration, routing, verification, workflow durability, and eventing
- **Related plan**: `docs/plans/api-control-plane-refactor/PLAN.md`

## 1. Executive Summary

Mira's current task runtime can accept app-submitted work, start workers, and update status, but it does not yet have a hard contract for what a task is, who owns it, what proof is required before `done`, and how a crash resumes or fails.

The highest-priority fix is not a new agent framework. It is a strict runtime contract:

1. A task has a canonical state machine.
2. A task has a type.
3. A task has an owner and execution plan.
4. A terminal status has evidence.
5. `done` is impossible unless type-aware verification passes.
6. Worker output is projected back to the app/API as user-visible state.
7. Failures are classified, retryable when appropriate, and never silently hidden.

This plan should make Mira honest before making Mira smarter.

## 2. Current Diagnosis

### 2.1 Status Lies

Today `done` can mean "the worker process ended without crashing", not "the user got what they asked for".

Examples observed:

- X/Twitter reply tasks marked `done` even when the reply was never posted because the browser was not authenticated.
- Market analysis marked `failed` even when the diagnostic content was directionally correct.
- App-submitted smoke tasks could reach terminal runtime state before the final response was projected back into the API item.

The runtime needs a verification gate between worker completion and user-facing `done`.

### 2.2 Routing Is Brittle

Routing still depends too much on keyword matching and legacy handler paths. A task can be routed to an incapable agent, or fall through to a generic path that cannot safely complete effectful work.

Examples:

- EPUB/book requests routed toward coder-style preflight instead of a writer/book workflow.
- Specialized tasks falling back to `general`, then failing because `general` has no safe tool path for the requested effect.

The router should produce a typed, auditable plan. It should block when no capable route exists instead of degrading silently.

### 2.3 Worker Crashes Are Too Terminal

Import errors, handler signature drift, missing preflight, and browser auth failures can push tasks into terminal-looking states without a meaningful recovery path.

The runtime needs:

- failure classes
- retry eligibility
- retry ceilings
- resume from checkpoint where possible
- clear app-visible messages when blocked

### 2.4 Multi-Step Work Is Not Durable Enough

`plan_executor.py` has step-level structure, but a long task is still too easy to lose on crash, timeout, PID confusion, or partial output.

A durable workflow should checkpoint each meaningful step. Restarting the agent should resume from the last completed step instead of starting from scratch or leaving the app stuck in `working`.

### 2.5 Background Coordination Is Fragile

The current system still has PID files, periodic reaping, ad hoc heartbeats, file-based state, and scheduled jobs that are not represented as one coherent event stream.

This makes debugging forensic. It also makes live status too dependent on the most recent projection rather than the actual stream of runtime facts.

### 2.6 Orchestration Is Sprawled

Task orchestration logic is spread across:

- `agents/super/core.py`
- `agents/super/talk.py`
- `agents/super/task_manager.py`
- `agents/super/task_worker.py`
- `agents/super/plan_executor.py`
- `agents/super/handlers_legacy.py`
- workflow modules and scheduled-job helpers

There is no single authoritative place that defines: "when a request arrives, this is the contract Mira follows."

## 3. Design Principle

The central invariant:

**A task is not done because an agent ran. A task is done only when the expected outcome is verified.**

Everything else follows from that.

## 4. Reference Patterns To Steal

| Framework | Useful Pattern | Avoid |
|---|---|---|
| LangGraph | Explicit state graph, checkpoints, interrupts, durable task state | Pulling in the full LangChain dependency tree |
| Hermes / Nous Research | Structured tool-use loop; explicit plan before action | Copying their exact XML/function-call format |
| Claude Agent SDK | Subagents as tools with machine-readable descriptions, tools, and model constraints | Assuming a single-process runtime is enough |
| CrewAI | Declared process styles: sequential, hierarchical, consensual | Heavy role-play abstractions |
| Temporal / Durable Workflows | `await activity()` semantics, resume from last completed step | Running a separate Temporal service for a single-Mac system |

The best fit for Mira is not wholesale adoption. It is borrowing:

- LangGraph/Temporal's durable state and checkpointing pattern
- Hermes/Claude Agent SDK's explicit routing plan and tool contract
- a small local implementation that fits Mira's single-user, single-Mac runtime

## 5. Proposed Architecture

### 5.1 Canonical Task State Machine

Introduce one task state machine used by app-submitted tasks, scheduled tasks, and worker-subtasks.

Canonical states:

```text
queued
  -> routing
  -> planned
  -> running
  -> verifying
  -> done

queued/routing/planned/running/verifying
  -> blocked
  -> needs-input
  -> failed
  -> cancelled
  -> timeout
```

Rules:

- `done` requires a passing `VerificationResult`.
- `failed`, `blocked`, `timeout`, `cancelled`, and `needs-input` require an app-visible reason.
- `running` requires an active worker or resumable workflow checkpoint.
- `queued` means dispatchable work exists.
- `blocked` means the runtime cannot continue without environment, permissions, credentials, missing tool, or unavailable input.
- `needs-input` means the user can unblock the task by replying.

### 5.2 Task Type

Every task gets a `TaskType`.

Initial task types:

| Task type | Examples | Verifier |
|---|---|---|
| `general_answer` | normal questions, explanations | final text exists and addresses request |
| `health_query` | sleep data, readiness, health metrics | local-only policy respected; requested metrics present |
| `market_report` | EOD market analysis, portfolio/risk | required sections and data timestamps present |
| `epub_build` | reading notes to EPUB/book artifact | EPUB file exists, opens, metadata/content checks pass |
| `social_draft` | draft a reply, ask user to approve | draft exists; terminal state should usually be `needs-input`, not `done` |
| `social_publish` | post to X/Substack/Bluesky | platform confirms post URL/id; auth failure becomes `blocked` |
| `code_change` | code edit/refactor | tests or compile checks pass, diff exists |
| `artifact_generation` | PDF, audio, images, reports | artifact exists and passes basic integrity check |

Task type should be stored in Postgres control state and mirrored into task records.

### 5.3 Verification Result

Add a structured verification object:

```json
{
  "status": "passed",
  "task_type": "epub_build",
  "checks": [
    {"name": "artifact_exists", "status": "passed", "detail": "book.epub"},
    {"name": "epub_opens", "status": "passed", "detail": "metadata parsed"}
  ],
  "evidence": {
    "artifact_path": "/path/to/book.epub",
    "summary": "Created EPUB with 12 chapters"
  }
}
```

Allowed verification statuses:

- `passed`
- `failed`
- `blocked`
- `not_applicable`

Runtime rule:

```text
worker_result.status == done
AND verification.status == passed
=> task.status = done
```

Anything else becomes `failed`, `blocked`, or `needs-input`.

### 5.4 Router Contract

Replace brittle direct keyword routing with a structured router output.

Target shape:

```json
{
  "task_type": "epub_build",
  "intent": "convert_reading_notes_to_epub",
  "confidence": 0.87,
  "route": {
    "primary_agent": "writer",
    "workflow": "book_to_epub"
  },
  "plan": [
    {"step": "collect_source_notes", "agent": "writer", "tool": "collect_reading_notes"},
    {"step": "de_ai_edit", "agent": "writer", "tool": "de_ai_pass"},
    {"step": "compile_epub", "agent": "writer", "tool": "compile_epub"},
    {"step": "verify_epub", "agent": "writer", "tool": "verify_epub"}
  ],
  "blocked_if_missing": ["source_notes", "epub_builder"],
  "verification": {"type": "epub_build"}
}
```

Rules:

- Router output must validate before dispatch.
- No effectful task may fall back to `general` by default.
- If no capable route exists, the task becomes `blocked` with a clear reason.
- The router may be deterministic for obvious task types and LLM-assisted for ambiguous tasks.

### 5.5 Durable Workflow Shape

Longer task families should become workflows with explicit checkpoints:

```python
@workflow
async def habermas_epub_task(ctx, request):
    chapters = await ctx.step("collect_chapters", collect_reading_notes, request)
    edited = await ctx.step("de_ai_each", de_ai_pass_chapters, chapters)
    epub = await ctx.step("build_epub", compile_epub, edited)
    verification = await ctx.step("verify_epub", verify_epub_structure, epub)
    return ctx.done(epub, verification)
```

Each `ctx.step()` persists:

- step id
- input hash
- output reference
- status
- started/completed timestamps
- error if any

On restart:

- completed steps are reused
- failed retryable steps can retry
- non-retryable blocked steps surface to the app

### 5.6 Event Bus

Use the existing Postgres direction from the API control-plane refactor. Do not add SQLite for this.

Required event categories:

- `task.created`
- `task.routed`
- `task.planned`
- `task.dispatched`
- `task.running`
- `task.heartbeat`
- `task.verifying`
- `task.done`
- `task.failed`
- `task.blocked`
- `task.needs_input`
- `workflow.step_started`
- `workflow.step_completed`
- `workflow.step_failed`
- `agent.background_started`
- `agent.background_finished`
- `external.activity_received`

The app should subscribe to task events. Runtime debugging should use the same event stream.

## 6. Recommended Phasing

### Phase 1: Task Contract And Verification Gates

Goal: make task status honest without rewriting the full runtime.

Deliverables:

- `TaskStateMachine`
- `TaskType`
- `VerificationResult`
- type-aware verifier registry
- terminal transition guard: no `done` without passing verification
- app/API projection of final message, summary, verification, artifacts, and failure class

Initial verifiers:

- `general_answer`
- `health_query`
- `market_report`
- `epub_build`
- `social_draft`
- `social_publish`

Acceptance criteria:

- A social publish task without a confirmed post URL cannot become `done`.
- A social draft task becomes `needs-input`, not worker-consuming `queued`.
- An EPUB task cannot become `done` unless the EPUB exists and passes integrity checks.
- A health query that violates local-only policy becomes `blocked`, not ambiguous failure.
- App-visible status and final message match runtime truth.

Estimated effort: about 1 week.

### Phase 2: Structured Router Contract

Goal: replace brittle routing with typed plans.

Deliverables:

- router input schema
- router output schema
- deterministic pre-router for obvious cases
- LLM-assisted router for ambiguous requests
- validation before dispatch
- no generic fallback for effectful tasks

Acceptance criteria:

- Habermas EPUB-style tasks route to a book/EPUB workflow, not coder preflight.
- Publish tasks route to social publish only when credentials/tooling are available.
- Unsupported task types become `blocked` with a clear missing-capability message.

Estimated effort: 1-2 weeks after Phase 1.

### Phase 3: Durable Workflow Engine

Goal: make multi-step tasks resumable.

Deliverables:

- minimal local workflow runner
- step checkpoint table
- `ctx.step()` abstraction
- resume from last completed step
- workflow-level retries and retry ceilings
- migration of high-value task families

First workflows to migrate:

- EPUB/book workflows
- social publishing workflows
- market report workflows
- app-submitted research/writing workflows

Acceptance criteria:

- Killing the worker mid-workflow resumes from the last completed step.
- Step outputs survive agent restart.
- Failed steps surface exact retry/block reason.

Estimated effort: 3-4 weeks.

### Phase 4: Postgres Event Bus And Reactors

Goal: replace ad hoc polling/reaping/status files with one event stream.

Deliverables:

- append-only event log
- event subscribers/reactors
- heartbeat derived from events
- activity inbox as reactor
- scheduled jobs as scheduled events
- replay/debug command

Acceptance criteria:

- `mira replay <task_id>` can reconstruct the workflow trace.
- App task list updates from events, not stale file cache.
- No active task truth depends on iCloud item JSON.

Estimated effort: 2-3 weeks after API control plane stabilizes.

## 7. Explicit Non-Goals

Do not adopt these wholesale:

- CrewAI
- AutoGen
- full LangGraph dependency stack
- a full Temporal service

Do not make every scheduled job go through an LLM planner.

The preferred runtime is two-tier:

- deterministic runtime for cron/scheduled/system work
- LLM planner/router for ambiguous user requests
- strict verifier layer for both

## 8. Wild Ideas To Revisit Later

### 8.1 Mira-As-A-Hermes-Agent

Reimplement the super-agent as a single tool-using LLM loop where tools are `dispatch_to_writer`, `dispatch_to_socialmedia`, `query_control_db`, `read_inbox`, and similar.

Potential upside:

- aligns with Mira's "thinking agent" identity
- simpler conceptual model for ad hoc requests

Risks:

- latency
- cost
- non-determinism
- worse debugging for routine work

This should not replace deterministic scheduled work.

### 8.2 Speculative Execution Plus Verification

For high-value ambiguous requests, run two or three candidate plans in parallel and let verifiers choose the successful output.

Potential upside:

- faster time-to-good-answer
- better for ambiguous writing/research tasks

Risks:

- higher cost
- more resource contention
- more complicated cancellation

### 8.3 Trace Replay Debug Mode

Every workflow run should produce a replayable trace.

Target command:

```bash
mira replay <task_id>
```

This should reconstruct:

- router decision
- plan
- workflow steps
- worker outputs
- verifier results
- final projection

## 9. Open Questions

1. Should verifier logic live inside each agent package, or in a central `agents/super/verification` module?
2. Should `TaskType` be assigned by deterministic rules first, then confirmed by LLM router?
3. Should `social_draft` ever become `done`, or should it always be `needs-input` until user dismisses it?
4. Which artifact checks are required for EPUB, PDF, audio, and code tasks?
5. Should scheduled jobs use the same state machine as user tasks, or a parallel `JobStateMachine` with shared terminal rules?
6. How aggressive should automatic retry be before surfacing a failure?
7. Should app-visible `failed` distinguish `failed`, `blocked`, and `timeout`, or keep mapping them together while showing structured detail?

## 10. Immediate Next Step

Build Phase 1 as the next implementation unit:

1. Define `TaskStateMachine`, `TaskType`, and `VerificationResult`.
2. Add a verifier registry.
3. Wire terminal transitions through the verifier.
4. Add initial verifiers for `general_answer`, `health_query`, `market_report`, `epub_build`, `social_draft`, and `social_publish`.
5. Update API projection so the app always sees:
   - current canonical status
   - final user-visible message
   - verification result
   - artifact paths
   - failure class and retryability

Success means Mira stops claiming success without proof.
