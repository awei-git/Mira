# Mira

Canonical docs live under [`docs/`](./docs/README.md).
If you are changing runtime behavior, workflow design, goals, or readiness gates, start there before relying on this README.

A local, multi-user AI agent system that runs on your Mac, talks to you through an iPhone app, and autonomously explores, writes, publishes, and monitors health.

## What it does

Mira is a super-agent that wakes every 30 seconds and:

1. **Talks** -- processes per-user messages and todos from the Mira iOS app via MiraBridge
2. **Responds** -- handles requests from Apple Notes inbox
3. **Writes** -- runs a multi-model writing pipeline (plan -> draft -> critique -> revise), with topic overlap detection against published articles, publishes to Substack with auto-generated cover images
4. **Explores** -- fetches 30+ feed sources (arXiv, Reddit, HuggingFace, Substack, Hacker News, RSS), writes daily briefings, extracts reading notes
5. **Podcasts** -- generates dual-voice conversation podcasts from published articles (EN: Gemini TTS, ZH: MiniMax TTS), publishes to RSS feeds
6. **Analyzes** -- pre-market and post-market financial analysis (Tetra integration)
7. **Health** -- ingests Apple Health + Oura Ring data, runs anomaly detection, generates daily GPT health insights, provides on-demand advice for symptoms and checkup reports
8. **Reflects** -- weekly memory consolidation with semantic vector index
9. **Journals** -- daily summary and philosophical reflection
10. **Grows** -- Substack Notes, reader engagement, publication discovery
11. **Learns** -- extracts reusable skills from experience (80+ learned skills across writing, coding, research, photography, video editing)
12. **Sparks** -- proactively messages when it has an insight worth discussing
13. **Mirrors** -- optionally exposes a local Web GUI / HTTP mirror for browser access and lower-latency local reads

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

**Memory** -- Semantic vector index (SQLite + OpenAI embeddings) with hybrid vector+keyword search and temporal decay. Indexes identity, worldview, memory, interests, journal, reading notes, and skills.

**Writing pipeline** -- Syncs ideas from Apple Notes, checks for topic overlap against published catalog, advances projects through plan/draft/critique/revision cycles. Publishes to Substack with personal photo covers (DALL-E fallback). Queues promotional Notes for gradual posting.

**Explore** -- 11 source groups rotate through the day via LRU scheduling. Each group fetches feeds, writes a briefing, extracts reading notes, and optionally deep-dives into interesting items.

**Health** -- Ingests Apple Health exports and Oura Ring data into PostgreSQL. Runs per-user anomaly detection with configurable thresholds. Generates daily GPT health insights combining wearable data, checkup reports, and symptoms. On-demand advice when users submit symptoms or concerning metrics.

**Podcast** -- Converts published articles into dual-voice conversation podcasts. Generates dialogue scripts (host + Mira), synthesizes with language-specific TTS providers, adds music bumpers, publishes to per-language GitHub Pages RSS feeds, and optionally embeds audio in the Substack post.

**Soul** -- Mira has persistent identity, worldview, and learned skills that evolve through reflection and experience. 80+ skills across 12 domains are extracted from task trajectories and stored for reuse.

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
