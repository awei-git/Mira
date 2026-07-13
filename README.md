# Mira

Canonical docs live under [`docs/`](./docs/README.md).
If you are changing runtime behavior, workflow design, goals, or readiness gates, start there before relying on this README.

A local, multi-user AI collaborator that runs on your Mac, works with you through an iPhone app, keeps long-term continuity, and turns lived work into research, writing, and governed self-improvement. The canonical direction is the [V5.1 north star](./docs/north-star.md) and [master plan](./docs/v5-master-plan.md).

## What it does

Mira is a super-agent that wakes every 30 seconds and:

1. **Collaborates** -- maintains one continuous Mira thread for conversation, corrections, decisions, and shared attention
2. **Fulfills requests** -- treats phone/app requests as visible obligations with honest terminal states
3. **Writes** -- runs a multi-model writing pipeline (plan -> draft -> critique -> revise), with topic overlap detection against published articles, publishes to Substack with auto-generated cover images
4. **Explores** -- fetches 30+ feed sources (arXiv, Reddit, HuggingFace, Substack, Hacker News, RSS), writes daily briefings, extracts reading notes
5. **Podcasts** -- generates dual-voice conversation podcasts from published articles (EN: Gemini TTS, ZH: MiniMax TTS), publishes to RSS feeds
6. **Analyzes** -- pre-market and post-market financial analysis (Tetra integration)
7. **Health** -- ingests Apple Health + Oura Ring data, runs anomaly detection, generates daily GPT health insights, provides on-demand advice for symptoms and checkup reports
8. **Reflects** -- reviews outcomes, runs bounded improvement experiments, and consolidates verified lessons
9. **Journals** -- daily summary and philosophical reflection
10. **Grows** -- Substack Notes, reader engagement, publication discovery
11. **Learns** -- security-audits candidate skills, tests them on later work, and promotes only outcome-backed changes
12. **Sparks** -- proactively messages when it has an insight worth discussing
13. **Mirrors** -- optionally exposes a local Web GUI / HTTP mirror for browser access and lower-latency local reads

## V5.1 operating structure

V5.1 has one operating spine:

`conversation or request → obligation → action → visible outcome → review → verified learning → future behavior`

| System | Responsibility | Success receipt |
| --- | --- | --- |
| Collaboration | Daily Mira thread, requests, corrections, and shared attention | A visible result or honest blocker in the same surface |
| Learning | Bounded self-improvement experiments and skill candidates | A later outcome that verifies, rejects, or rolls back the change |
| Continuity | Identity, personality, memories, beliefs, and human preferences | Provenance, confidence, evidence, and later use—not raw storage |
| Creation | Research, experiments, versioned drafts, and artifacts | A first-hand claim, evidence ledger, and review receipt |
| Governance | Permissions, security audits, review verdicts, and rollback | No irreversible or public action without the required gate |

The goal hierarchy is L0 Survival, L1 Trusted Collaboration, L2 Learning & Continuity, L3 Research & Expression, and L4 Influence & Optionality. Pipeline activity, generated plans, and passing process checks are diagnostic signals—not proof of progress.

## Architecture

```
Mira/
├── agents/
│   ├── super/          # Orchestrator -- core.py, task_manager, task_worker
│   ├── shared/         # Config, LLM interface, prompts, memory_index
│   │   └── soul/       # Identity, worldview, interests, journal, learned skills
│   ├── writer/         # Writing pipeline (ideas, frameworks, templates, skills)
│   ├── explorer/       # Feed fetcher, briefing writer
│   ├── general/        # General task handler
│   ├── socialmedia/    # Substack publishing, Notes, commenting, growth
│   ├── health/         # Health monitoring, anomaly detection, GPT insights
│   ├── podcast/        # Article-to-podcast pipeline, RSS publishing
│   ├── video/          # Video editing skills
│   ├── photo/          # Photography editing skills
│   ├── analyst/        # Market analysis
│   ├── researcher/     # Math research
│   └── coder/          # Programming skills
├── lib/
│   ├── evolution/      # Experience, trajectories, learning proposals and trials
│   ├── evaluation/     # Outcome scoring and improvement lifecycle
│   └── memory/         # Governed memory schema, retrieval, soul and skills
├── data/soul/          # Canonical identity, worldview, interests and durable memory
├── docs/               # North star, current plan, architecture and operating contracts
├── tests/              # Unit, workflow, runtime and acceptance tests
├── web/                # Optional local Web GUI / HTTP mirror
├── feeds/              # Feed sources + raw data
├── logs/               # Agent logs
├── config.yml          # Local settings (gitignored)
└── secrets.yml         # API keys (gitignored)
```

All artifacts (writings, briefings, audio) are written to iCloud `MtJoy/Mira-Artifacts/` for iOS app access. Agent-to-app communication goes through iCloud `MtJoy/Mira-Bridge/` using the MiraBridge protocol.

Runtime state is user-scoped under `users/{user_id}/...`. The orchestrator iterates `Bridge.for_all_users()`, and task routing, thread history, emptiness state, journal/reflect/spark state, soul-question history, and most memory writes stay inside that user namespace. Legacy flat state keys only fall back for `ang` during migration.

The super agent dispatches all heavy work (writing, exploring, analysis, health checks, podcast generation) as background processes so the main loop stays under 5 seconds.

## Key subsystems

**Memory and continuity** -- Structured facts, beliefs, episodes, task state, human preferences, and verified lessons retain provenance and confidence. Durable lessons need evidence; retrieval counts as compounding only when it changes later behavior.

**Writing pipeline** -- Syncs ideas from Apple Notes, checks for topic overlap against published catalog, advances projects through plan/draft/critique/revision cycles. Publishes to Substack with personal photo covers (DALL-E fallback). Queues promotional Notes for gradual posting.

**Explore** -- 11 source groups rotate through the day via LRU scheduling. Each group fetches feeds, writes a briefing, extracts reading notes, and optionally deep-dives into interesting items.

**Health** -- Ingests Apple Health exports and Oura Ring data into PostgreSQL. Runs per-user anomaly detection with configurable thresholds. Generates daily GPT health insights combining wearable data, checkup reports, and symptoms. On-demand advice when users submit symptoms or concerning metrics.

**Podcast** -- Converts published articles into dual-voice conversation podcasts. Generates dialogue scripts (host + Mira), synthesizes with language-specific TTS providers, adds music bumpers, publishes to per-language GitHub Pages RSS feeds, and optionally embeds audio in the Substack post.

**Soul and learning** -- Mira has a persistent but corrigible identity, worldview, interests, and skill corpus. Proposals remain unverified experiments; generated skills are audited into a candidate queue and require a cross-task outcome before promotion.

## Web GUI and Security

- `web/server.py` exposes a lightweight browser UI and HTTP mirror for heartbeat, manifests, items, todos, and artifacts.
- The API only serves known users from `config.yml`.
- Default host is `127.0.0.1` on port `8384`. If you intentionally open it beyond loopback, set `services.webgui_token` and treat it as a trusted-LAN surface, not a public endpoint.
- The mobile app can always run in pure iCloud mode. The Web GUI is optional.

## Multi-model

Mira orchestrates multiple LLM providers:

- **Claude** -- primary reasoning, task planning, writing review
- **GPT** -- creative prose, embeddings, health insights
- **DeepSeek** -- Chinese writing, cost-efficient reasoning
- **Gemini** -- fast analysis, long context, English TTS
- **MiniMax** -- Chinese TTS
- **oMLX** -- local OpenAI-compatible runtime for privacy-sensitive tasks via the `secret` agent; `ollama_*` names remain as backward-compatible aliases in some modules

## Setup

1. Clone this repo
2. Create `config.yml` and `secrets.yml` at the repo root
   ```bash
   $EDITOR config.yml
   $EDITOR secrets.yml
   ```
3. Edit `config.yml` with your root paths, known users, schedules, and optional `services.webgui_*` settings
4. Edit `secrets.yml` with your API keys (Anthropic, OpenAI, DeepSeek, Google, Substack, and any other providers you use)
5. Install the Python dependencies needed for the agents you plan to run
   ```bash
   pip install requests openai psycopg2-binary fastapi uvicorn
   ```
6. Set up the LaunchAgent or run manually:
   ```bash
   cd agents/super && python3 core.py run
   ```
7. Optional: run the local Web GUI / HTTP mirror
   ```bash
   cd web && python3 server.py
   ```

### Companion repos

- **[MiraApp](../MiraApp)** -- SwiftUI iPhone app (chat, health dashboard, todos, artifacts)
- **[MiraBridge](../MiraBridge)** -- Communication protocol library (Python + Swift)

## License

MIT
