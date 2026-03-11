# Mira

A personal AI agent system that runs on your Mac, talks to you through an iPhone app, and autonomously explores, writes, and publishes.

## What it does

Mira is a super-agent that wakes every 30 seconds and:

1. **Talks** — processes messages from the Mira iOS app via iCloud Drive
2. **Responds** — handles requests from Apple Notes inbox
3. **Writes** — runs a multi-model writing pipeline (plan → draft → review → revise), publishes to Substack autonomously
4. **Explores** — fetches 30+ feed sources (arXiv, Reddit, HuggingFace, Substack, Hacker News, RSS), writes daily briefings, extracts reading notes
5. **Analyzes** — pre-market and post-market financial analysis
6. **Reflects** — weekly memory consolidation with semantic vector index
7. **Journals** — daily summary and philosophical reflection (哲思)
8. **Grows** — Substack Notes, reader engagement, publication discovery
9. **Learns** — extracts reusable skills from experience (video editing, writing craft, verification workflows)
10. **Sparks** — proactively messages when it has an insight worth discussing

## Architecture

```
Mira/
├── agents/
│   ├── super/          # Orchestrator — core.py, task_manager, task_worker
│   ├── shared/         # Config, LLM interface, prompts, soul/memory, memory_index
│   │   └── soul/       # Identity, worldview, interests, journal, learned skills
│   ├── writer/         # Writing pipeline + resources (ideas, frameworks, templates)
│   ├── explorer/       # Feed fetcher, briefing writer
│   ├── general/        # General task handler
│   ├── socialmedia/    # Substack publishing, Notes, commenting, growth
│   ├── video/          # Video editing skills
│   ├── photo/          # Photography editing skills
│   ├── analyst/        # Market analysis
│   ├── researcher/     # Math research
│   └── coder/          # Programming skills
├── artifacts/          # All output (browsable from iOS app)
│   ├── writings/       # Writing projects
│   ├── research/       # Deep dives
│   └── briefings/      # Daily briefings, journals, analysis
├── Mira-bridge/        # iPhone ↔ Mac messaging (iCloud Drive)
│   ├── inbox/          # User → Agent messages
│   ├── outbox/         # Agent → User messages
│   ├── tasks/          # Task state + comment threads
│   └── heartbeat.json  # Agent status
├── Mira-iOS/           # SwiftUI iPhone app
├── feeds/              # Feed sources + raw data
├── logs/               # Agent logs
├── config.yml          # Local settings (gitignored)
├── secrets.yml         # API keys (gitignored)
└── sources.json        # Feed sources (gitignored)
```

The super agent dispatches all heavy work (writing, exploring, analysis, skill study) as background processes so the main loop stays under 5 seconds.

## Key subsystems

**Memory** — Semantic vector index (SQLite + OpenAI embeddings) with hybrid vector+keyword search and temporal decay. Indexes identity, worldview, memory, interests, journal, reading notes, and skills.

**Writing pipeline** — Syncs ideas from Apple Notes, advances projects through plan/write/review cycles. Publishes to Substack autonomously with auto-generated cover images (Unsplash + DALL-E fallback).

**Explore** — 11 source groups rotate through the day via LRU scheduling. Each group fetches feeds, writes a briefing, extracts reading notes, and optionally deep-dives into interesting items.

**Soul** — Mira has persistent identity, worldview, and learned skills that evolve through reflection and experience. Skills are extracted from task trajectories and stored for reuse.

## Multi-model

Mira orchestrates multiple LLM providers:

- **Claude** — primary reasoning, task planning, writing review
- **GPT** — creative prose, embeddings
- **DeepSeek** — Chinese writing, cost-efficient reasoning
- **Gemini** — fast analysis, long context

The writing pipeline runs models in parallel, then reviews and converges.

## Setup

1. Clone this repo
2. Copy config files:
   ```bash
   cp config.example.yml config.yml
   cp secrets.example.yml secrets.yml
   cp sources.example.json sources.json
   ```
3. Edit `config.yml` with your root path
4. Edit `secrets.yml` with your API keys (Anthropic, OpenAI, DeepSeek, Google)
5. Edit `sources.json` with your preferred feeds
6. Install Python dependencies:
   ```bash
   pip install pyyaml requests openai
   ```
7. Set up the LaunchAgent or run manually:
   ```bash
   cd agents/super && python3 core.py run
   ```

### Mira iOS app

Open `Mira-iOS/Mira/Mira.xcodeproj` in Xcode, build to your device, and point it at the `Mira-bridge/` folder in iCloud Drive.

## License

MIT
