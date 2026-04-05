# Mira

A personal AI agent system that runs on your Mac, talks to you through an iPhone app, and autonomously explores, writes, publishes, and monitors your health.

## What it does

Mira is a super-agent that wakes every 30 seconds and:

1. **Talks** -- processes messages from the Mira iOS app via iCloud Drive (MiraBridge protocol)
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
├── feeds/              # Feed sources + raw data
├── logs/               # Agent logs
├── config.yml          # Local settings (gitignored)
└── .config/secrets.yml # API keys (gitignored)
```

All artifacts (writings, briefings, audio) are written to iCloud `MtJoy/Mira-Artifacts/` for iOS app access. Agent-to-app communication goes through iCloud `MtJoy/Mira-Bridge/` using the MiraBridge protocol.

The super agent dispatches all heavy work (writing, exploring, analysis, health checks, podcast generation) as background processes so the main loop stays under 5 seconds.

## Key subsystems

**Memory** -- Semantic vector index (SQLite + OpenAI embeddings) with hybrid vector+keyword search and temporal decay. Indexes identity, worldview, memory, interests, journal, reading notes, and skills.

**Writing pipeline** -- Syncs ideas from Apple Notes, checks for topic overlap against published catalog, advances projects through plan/draft/critique/revision cycles. Publishes to Substack with personal photo covers (DALL-E fallback). Queues promotional Notes for gradual posting.

**Explore** -- 11 source groups rotate through the day via LRU scheduling. Each group fetches feeds, writes a briefing, extracts reading notes, and optionally deep-dives into interesting items.

**Health** -- Ingests Apple Health exports and Oura Ring data into PostgreSQL. Runs per-user anomaly detection with configurable thresholds. Generates daily GPT health insights combining wearable data, checkup reports, and symptoms. On-demand advice when users submit symptoms or concerning metrics. Privacy-first: raw health data processed locally via Ollama, only aggregated summaries sent to cloud LLMs for advice.

**Podcast** -- Converts published articles into dual-voice conversation podcasts. Generates dialogue scripts (host + Mira), synthesizes with language-specific TTS providers, adds music bumpers, publishes to per-language GitHub Pages RSS feeds, and optionally embeds audio in the Substack post.

**Soul** -- Mira has persistent identity, worldview, and learned skills that evolve through reflection and experience. 80+ skills across 12 domains are extracted from task trajectories and stored for reuse.

## Multi-model

Mira orchestrates multiple LLM providers:

- **Claude** -- primary reasoning, task planning, writing review
- **GPT** -- creative prose, embeddings, health insights
- **DeepSeek** -- Chinese writing, cost-efficient reasoning
- **Gemini** -- fast analysis, long context, English TTS
- **MiniMax** -- Chinese TTS
- **Ollama** -- local processing for privacy-sensitive data (health)

## Setup

1. Clone this repo
2. Copy config files:
   ```bash
   cp config.example.yml config.yml
   cp .config/secrets.example.yml .config/secrets.yml
   ```
3. Edit `config.yml` with your root path
4. Edit `.config/secrets.yml` with your API keys (Anthropic, OpenAI, DeepSeek, Google, Substack)
5. Install Python dependencies:
   ```bash
   pip install pyyaml requests openai psycopg2-binary
   ```
6. Set up the LaunchAgent or run manually:
   ```bash
   cd agents/super && python3 core.py run
   ```

### Companion repos

- **[MiraApp](../MiraApp)** -- SwiftUI iPhone app (chat, health dashboard, todos, artifacts)
- **[MiraBridge](../MiraBridge)** -- Communication protocol library (Python + Swift)

## License

MIT
