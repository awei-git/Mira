# Mira — Detailed Design Document

This file is retained as historical background.
The current canonical design lives in [`docs/system-design.md`](./docs/system-design.md).

## Overview

Mira is an autonomous AI agent system running as a persistent macOS daemon. It operates on a **30-second cycle**, orchestrating specialized sub-agents, managing persistent memory/identity ("soul"), running scheduled background pipelines, and communicating with its user via an iCloud-based iPhone bridge.

```
┌─────────────────────────────────────────────────────────────┐
│                    macOS LaunchAgent (30s daemon)            │
│                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────┐  │
│  │   Talk    │  │ Explore  │  │  Journal  │  │  Reflect  │  │
│  │ (inbox)   │  │ (feeds)  │  │ (daily)   │  │ (weekly)  │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └─────┬─────┘  │
│       │              │              │               │        │
│  ┌────▼──────────────▼──────────────▼───────────────▼────┐  │
│  │                    Soul System                         │  │
│  │  identity · memory · interests · worldview · skills    │  │
│  └────────────────────────┬──────────────────────────────┘  │
│                           │                                  │
│  ┌────────────────────────▼──────────────────────────────┐  │
│  │              Sub-Agent Dispatch (Registry)             │  │
│  │  writer · explorer · analyst · socialmedia · general   │  │
│  │  coder · math · photo · video · podcast · surfer       │  │
│  └───────────────────────────────────────────────────────┘  │
└───────────────────────────┬─────────────────────────────────┘
                            │ iCloud Drive
                    ┌───────▼───────┐
                    │  MiraBridge   │
                    │  (files on    │
                    │   iCloud)     │
                    └───────┬───────┘
                            │
                    ┌───────▼───────┐
                    │  MiraApp      │
                    │  (iPhone)     │
                    └───────────────┘
```

---

## 1. Daemon Architecture

### Launch Chain

```
launchd (KeepAlive: true)
  → /Users/angwei/Sandbox/bin/mira-agent.sh
    → while true:
        syntax pre-flight check (py_compile on 5 critical files)
        crash-loop detection (5 crashes in 600s → backoff)
        python3 core.py run
        sleep 30
```

The shell wrapper provides:
- **Syntax pre-flight**: Won't run if core files have syntax errors; alerts iPhone
- **Crash-loop detection**: Tracks crashes in rolling 10min window; backs off + alerts
- **Self-healing**: `KeepAlive: true` means launchd restarts the daemon if killed

### 30-Second Cycle (`cmd_run()`)

```
Phase 1: CRITICAL PATH (< 10s)
  ├── do_talk()         — poll commands from iPhone, collect task results
  ├── heartbeat()       — tell phone we're alive
  └── app_feeds.sync()  — sync status to iOS

Phase 2: LIGHTWEIGHT CHECKS (if < 8s elapsed)
  ├── check_writing_responses()  — user feedback on writing projects
  └── read_app_feeds()           — app-to-agent messages

Phase 3: BACKGROUND DISPATCH (non-blocking)
  ├── should_explore()?    → dispatch background("explore-{slot}")
  ├── should_journal()?    → dispatch background("journal")
  ├── should_reflect()?    → dispatch background("reflect")
  ├── should_analyst()?    → dispatch background("analyst-{slot}")
  ├── should_zhesi()?      → dispatch background("zhesi")
  ├── should_research()?   → dispatch background("daily-research")
  ├── ... (15+ scheduled tasks)
  └── health_monitor.check()  — harvest outcomes, detect anomalies
```

**Key constraint**: The main cycle must stay under 10 seconds. All heavy work runs in background subprocesses.

---

## 2. Task System

### Message → Task → Result Flow

```
iPhone user sends message
    ↓
iCloud Bridge (commands/*.json)
    ↓
do_talk() → bridge.poll_commands()
    ↓
TaskManager.dispatch(msg, workspace)
    ↓ (subprocess.Popen)
task_worker.py
    ├── Load soul context (identity, memory, interests, worldview, skills)
    ├── LLM Planner: decompose request into steps
    │   └── [{agent: "writer", instruction: "...", tier: "heavy"},
    │        {agent: "publish", instruction: "...", tier: "light"}]
    ├── For each step:
    │   ├── Load agent handler from registry
    │   ├── Execute handler(workspace, task_id, instruction)
    │   └── Validate output (quality gate)
    └── Write result.json + output.md
    ↓
do_talk() → task_mgr.check_tasks()
    ├── Read result.json
    ├── bridge.update_status(task_id, "done", result)
    └── Post reply to iPhone
```

### LLM Task Planner

The planner decomposes user requests into ordered execution steps:

```python
_plan_task(content, conversation, exec_history) → [
    {"agent": "analyst", "instruction": "...", "tier": "heavy",
     "prediction": {"difficulty": "medium", "failure_modes": [...], "success_criteria": "..."}},
    {"agent": "writing", "instruction": "...", "tier": "heavy",
     "prediction": {...}}
]
```

**Routing rules** (embedded in planner prompt):
- `socialmedia` for all Substack operations (HARD RULE: never surfer)
- `secret` for private/sensitive content (routes to local oMLX)
- `discussion` for conversational messages
- `clarify` only when genuinely ambiguous
- Most requests = 1 step; multi-step only for real data dependencies

**Tier system**:
- `light` (Sonnet) — lookups, Q&A, publishing, discussion
- `heavy` (Opus) — creative writing, deep analysis, math proofs

### Concurrency

| Level | Limit | Purpose |
|-------|-------|---------|
| Foreground tasks | 2 | User-initiated requests from iPhone |
| Background jobs | 3 | Scheduled pipelines (explore, journal, etc.) |
| **Total** | **5** | Max concurrent Claude CLI processes |

---

## 3. Agent Registry

Each agent has a `manifest.json`:

```json
{
  "name": "writer",
  "description": "Write articles, essays, fiction, translations",
  "keywords": ["write", "article", "essay", "story"],
  "handles": ["articles", "essays", "fiction", "rewrite"],
  "tier": "heavy",
  "timeout_category": "long",
  "entry_point": "writing_workflow.py:start_project"
}
```

**Registered agents**:

| Agent | Purpose | Tier | Timeout |
|-------|---------|------|---------|
| **writer** | Articles, essays, fiction, translation | heavy | long (1hr) |
| **explorer** | Feed fetching, briefing generation | light | short (15m) |
| **socialmedia** | Substack publish, notes, comments, growth | light | short |
| **podcast** | TTS synthesis, episode generation | heavy | background |
| **analyst** | Market analysis, competitive intelligence | heavy | long |
| **general** | Q&A, file ops, code, search, web browsing | light | short |
| **coder** | Programming tasks | light | short |
| **math** | Proofs, derivations, paper review | heavy | long |
| **photo** | Photography editing, style learning | light | short |
| **video** | Video editing, scene analysis | light | short |
| **surfer** | Browser automation (last resort) | light | short |
| **reader** | Book review, reading notes | light | short |
| **researcher** | Research tasks | light | short |
| **secret** | Private/sensitive (local oMLX only) | light | short |
| **discussion** | Open-ended conversation | light | short |

---

## 4. Soul System

The soul is Mira's persistent identity — injected into every LLM prompt.

```
agents/shared/soul/
├── identity.md          — Who Mira is, values, communication style
├── memory.md            — Episodic log (200 lines max, overflow → Postgres)
├── interests.md         — Current active interests (updated weekly)
├── worldview.md         — Evolving beliefs, frameworks (Ebbinghaus decay)
├── learned/             — Extracted skills (auto-indexed)
│   ├── index.json
│   ├── experience-self-distillation.md
│   ├── hook-first-line.md
│   └── ... (60+ skills)
├── reading_notes/       — Personal reflections from deep dives
├── conversations/       — Full conversation transcripts
├── episodes/            — Task execution logs (for learning)
├── journal/             — Daily journal entries
├── catalog.jsonl        — Metadata index of all outputs
├── emptiness.json       — "Emptiness" score driving idle-think
├── scores.json          — Self-evaluation scores
└── calibration.jsonl    — Prediction vs outcome calibration data
```

### Soul Manager (`soul_manager.py`)

```python
load_soul()          → dict with all components
format_soul(soul)    → string for prompt injection
append_memory(entry) → add timestamped line to memory.md
update_interests()   → replace interests.md
update_worldview()   → replace worldview.md + Ebbinghaus decay
save_skill(name, desc, content, tags) → save to learned/
save_reading_note(title, reflection)  → save timestamped note
detect_recurring_themes()             → NLP pattern detection
audit_skill(content)                  → security audit for new skills
```

### Ebbinghaus Decay

During weekly `reflect`, the system prunes:
- **Episodes**: Older episodes removed based on forgetting curve
- **Worldview sections**: Stale beliefs pruned if not reinforced
- **Reading notes**: Old notes consolidated into memory

This prevents unbounded growth while preserving what matters.

---

## 5. Explorer Pipeline

### Feed Sources

Configured in `sources.json`, grouped into slots:

```
Slot 1: arxiv, huggingface
Slot 2: reddit (r/MachineLearning, r/LocalLLaMA, ...)
Slot 3: github_trending, hackernews, lobsters
Slot 4: devto, techcrunch
Slot 5: ieee_spectrum, simon_willison
Slot 6: noah_smith, stratechery, lenny's_newsletter
Slot 7: literaryhub, brain_pickings
Slot 8: aeon_essays, quanta_magazine
```

### Explore Cycle

```
do_explore(slot)
    ├── fetch_sources(slot_sources) → raw items
    ├── claude_think(filter_prompt) → ranked + summarized briefing
    ├── Save briefing to BRIEFINGS_DIR + artifacts (iOS)
    ├── Post briefing to Mira bridge as feed item
    ├── Extract reading notes (2-3 personal reflections)
    ├── Deep dive candidate selection (highest confidence item)
    │   └── _do_deep_dive()
    │       ├── claude_think(deep_dive_prompt, 600s)
    │       ├── Extract skill block → save_skill()
    │       └── Save reading note with internalization
    └── Extract comment suggestions → queue for growth cycle
```

**Scheduling**: LRU source group selection with 45-minute cooldown, max 16/day, active 08:00-23:00.

---

## 6. Writer Pipeline

Multi-phase writing with multi-model variety:

```
Phase 1: ANALYZE  → Classify type (essay, blog, novel, etc.)
Phase 2: PLAN     → Multi-agent discussion (propose → critique → synthesize)
Phase 3: AWAIT    → User approves plan (via Apple Notes or bridge)
Phase 4: WRITE    → 3 writers draft in parallel (Claude, GPT-5, DeepSeek)
Phase 5: REVIEW   → 5+ rounds of scoring until convergence
Phase 6: FEEDBACK → User feedback on converged draft
Phase 7: REVISE   → Refine based on feedback
Phase 8: DONE     → Final version locked
```

**Workspace structure**:
```
workspace/
├── project.json          — state machine (current phase)
├── versions/v1/
│   ├── plans/            — propose.md, critique.md, merged.md
│   ├── plan_approved.md  — user-approved outline
│   ├── drafts/           — claude.md, gpt5.md, deepseek.md
│   ├── reviews/          — round_01.json (scores), round_02.json, ...
│   └── converged.md      — best draft after review convergence
└── final.md              — published version
```

---

## 7. Scheduled Background Tasks

| Task | Schedule | Description |
|------|----------|-------------|
| **explore** | 45min cooldown, 8-23h, LRU slots | Fetch feeds, generate briefings, deep dives |
| **journal** | Daily 21:30 | Synthesize day's events into 2-3 threads |
| **reflect** | Weekly Fri 10:00 | Introspect, update identity, prune memory |
| **analyst** | 07:00 + 18:00 | Pre/post-market analysis |
| **zhesi** | Daily 09:30 | Daily philosophical reflection |
| **research** | Daily 14:00 | Work on current research topic |
| **skill-study** | Daily (rotating) | Study domain-specific sources (video/photo/writing) |
| **writing-pipeline** | Continuous | Check and advance writing projects |
| **autowrite-check** | 4hr cooldown, 10-22h | Check if enough insight accumulated to self-initiate article |
| **growth-cycle** | 2hr cooldown | Substack: like posts, draft comments |
| **notes-cycle** | 4hr cooldown | Post Substack Notes |
| **substack-comments** | 2hr cooldown | Monitor + reply to reader comments |
| **daily-report** | Daily (evening) | Operational status report to user |
| **daily-photo** | Daily | Photo editing/learning cycle |
| **idle-think** | When idle | Freeform curiosity-driven thinking |
| **spark-check** | Proactive | Check if sparks warrant deeper exploration |
| **self-audit** | Daily | Audit own behavior for improvement |

---

## 8. Model Routing & Usage

### Model Registry

```python
MODELS = {
    "claude":       {"provider": "claude",   "model_id": "claude-sonnet-4-6"},
    "gpt5":         {"provider": "openai",   "model_id": "gpt-5.4"},
    "deepseek":     {"provider": "deepseek", "model_id": "deepseek-chat"},
    "gemini":       {"provider": "gemini",   "model_id": "gemini-3.1-flash-lite-preview"},
    "gemini-pro":   {"provider": "gemini",   "model_id": "gemini-3.1-pro-preview"},
    "omlx":         {"provider": "omlx",     "model_id": "qwen3.5-27b"},
}
```

### Call Interface

```python
claude_think(prompt, tier="light")    # Reasoning only (Sonnet or Opus)
claude_act(prompt, cwd, tier="heavy") # With tools (file I/O, bash, web)
model_think(prompt, model="deepseek") # Direct model selection
```

### Fallback Chain

```
Claude CLI → (quota hit?) → GPT-5.4 fallback
oMLX → (timeout?) → Claude CLI fallback
Any model → (fail?) → claude_think() as last resort
```

### Token Usage Tracking

Every LLM call logs to `logs/usage_{date}.jsonl`:

```json
{"ts": "2026-03-26T22:00:00Z", "agent": "explore", "provider": "anthropic",
 "model": "claude-sonnet-4-6", "prompt_tokens": 5000, "completion_tokens": 800,
 "total_tokens": 5800, "estimated": true}
```

Daily report aggregates by agent × model.

---

## 9. iPhone Integration (MiraBridge)

### Protocol

```
iCloud Drive: ~/Library/Mobile Documents/.../Mira-Bridge/
├── heartbeat.json              ← Agent writes every 30s
├── users/ang/
│   ├── manifest.json           ← Index of all items (generation counter for CAS)
│   ├── items/*.json            ← One file per item (agent-owned)
│   ├── commands/*.json         ← iOS writes, agent reads + deletes
│   ├── command_ledger.json     ← 2-phase commit delivery tracking
│   └── todos.json              ← Shared todo list
```

### Item Lifecycle

```
queued → working → done
                 → failed (retryable: user can reply to reopen)
                 → needs-input (agent asks user a question)
```

### Reliability

- **Atomic writes**: All mutations use tmp+rename
- **Ledger-based delivery**: Commands tracked in ledger before file deletion
- **iCloud placeholder handling**: `brctl download` + retry loop
- **Manifest diffing**: iOS only fetches changed items (by timestamp comparison)

---

## 10. Health Monitoring

```python
health_monitor.record_dispatch(name, pid)    # Track background process
health_monitor.record_outcome(name)          # Harvest exit status
health_monitor.check_anomalies()             # Alert on failures
```

**Thresholds**:
- 3 consecutive failures → alert to iPhone
- 1 failure for critical processes (journal, reflect, writing-pipeline)
- Alert dedup: 12hr window, max 3/day

**State**: `.bg_health.json` — per-process stats + daily aggregates.

---

## 11. Socialmedia Agent (Substack)

### Operations

- **Publish articles**: Full-length essays to Substack newsletter
- **Post Notes**: Short-form content (< 300 words)
- **Comment**: Reply to reader comments, engage with other publications
- **Growth cycle**: Like relevant posts, draft contextual comments
- **Stats tracking**: Subscriber count, view trends

### Content Guard (CLAUDE.md hard rule)

Before any publish:
1. Check content is not error message
2. Check content > 200 chars
3. Display full content to user for approval
4. Only publish after explicit confirmation

### API Access

Direct HTTP API (reverse-engineered), not browser automation:
- `POST /api/v1/comment/feed` — post notes/replies
- `POST /api/v1/comment/{id}/edit` — edit notes
- `GET /api/v1/reader/comment/{id}/replies` — read replies
- Cookie-based auth via `substack.sid`

---

## 12. Configuration Reference

### Timeouts

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `CLAUDE_TIMEOUT_THINK` | 120s | Quick classification, filtering |
| `CLAUDE_TIMEOUT_ACT` | 600s | Writing, coding with tools |
| `TASK_TIMEOUT` | 900s (15min) | Default foreground task |
| `TASK_TIMEOUT_LONG` | 3600s (1hr) | Writing, research tasks |

### Scheduling

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `EXPLORE_COOLDOWN_MINUTES` | 45 | Min between explore cycles |
| `EXPLORE_MAX_PER_DAY` | 16 | Daily explore cap |
| `EXPLORE_ACTIVE_START/END` | 08:00-23:00 | Active hours |
| `JOURNAL_TIME` | 21:30 | Daily journal |
| `REFLECT_DAY/TIME` | Friday 10:00 | Weekly reflection |
| `ANALYST_TIMES` | [07:00, 18:00] | Market analysis |
| `ZHESI_TIME` | 09:30 | Daily philosophy |
| `RESEARCH_TIME` | 14:00 | Research session |

### Concurrency

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `MAX_CONCURRENT_TASKS` | 2 | Parallel foreground task workers |
| `MAX_CONCURRENT_BG` | 3 | Parallel background jobs |

---

## 13. Key Design Patterns

1. **30s Heartbeat Cycle**: Main loop stays fast (< 10s); all heavy work in background processes
2. **Fire-and-Forget Background**: `subprocess.Popen(start_new_session=True)` with PID tracking
3. **Soul Injection**: Identity + memory + interests + worldview formatted and injected into every LLM call
4. **Ebbinghaus Decay**: Weekly pruning of old episodes, stale worldview entries, reading notes
5. **LRU Scheduling**: Source groups selected by least-recently-used with cooldown
6. **Registry-Based Dispatch**: Agents discovered by manifest; handler loaded dynamically
7. **Atomic State**: File-based state with tmp+rename; iCloud bridge with ledger-based delivery
8. **Multi-Model Diversity**: Writing pool randomly selects from Claude/GPT-5/DeepSeek/Gemini
9. **Calibration Loop**: Predictions recorded before execution, compared with outcomes
10. **Self-Evolution**: Read tech blogs → compare with own architecture → propose improvements

---

## 14. Repository Structure

```
Sandbox/
├── Mira/                    ← This repo (Python agent)
│   ├── agents/
│   │   ├── super/           ← Orchestrator (core.py, task_manager, task_worker)
│   │   ├── shared/          ← Common (config, sub_agent, mira, soul_manager)
│   │   │   └── soul/        ← Identity, memory, skills, journal
│   │   ├── writer/          ← Writing pipeline
│   │   ├── explorer/        ← Feed fetcher, briefing writer
│   │   ├── socialmedia/     ← Substack integration
│   │   ├── podcast/         ← TTS pipeline
│   │   ├── analyst/         ← Market analysis
│   │   ├── general/         ← General-purpose handler
│   │   ├── coder/           ← Programming
│   │   ├── math/            ← Mathematical research
│   │   ├── photo/           ← Photography
│   │   ├── video/           ← Video editing
│   │   ├── surfer/          ← Browser automation
│   │   └── reader/          ← Book review
│   ├── config.yml           ← Runtime configuration
│   ├── sources.json         ← Feed source definitions
│   └── scripts/             ← Utilities (backup, nightly commit)
│
├── MiraBridge/              ← Standalone library repo
│   ├── python/mira_bridge.py   ← Agent-side (stdlib only)
│   └── swift/                  ← iOS-side SPM package
│
└── MiraApp/                 ← Standalone iOS client repo
    └── Mira/                   ← SwiftUI app (imports MiraBridge)
```
