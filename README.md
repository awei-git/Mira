# Mira

A personal AI agent system that runs on your Mac, talks to you through an iPhone app, and orchestrates multiple AI models for writing, research, exploration, and publishing.

## What it does

Mira is a super-agent that wakes every 60 seconds and:

1. **Talks** — processes messages from the Mira iOS app via iCloud Drive
2. **Responds** — handles requests from Apple Notes inbox
3. **Writes** — runs a multi-model writing pipeline (plan → draft → review → revise)
4. **Explores** — fetches feeds (arXiv, Reddit, HuggingFace, RSS), writes daily briefings, extracts skills
5. **Reflects** — weekly memory consolidation
6. **Journals** — daily summary

## Architecture

```
Mira/
├── agents/
│   ├── super/          # Orchestrator — core.py, task_manager, task_worker
│   ├── shared/         # Config, LLM interface, prompts, soul/memory
│   ├── writer/         # Writing pipeline + resources
│   ├── explorer/       # Feed fetcher
│   ├── publisher/      # Substack publishing
│   └── general/        # General task handler
├── Mira-bridge/        # iPhone ↔ Mac messaging (iCloud Drive)
├── Mira-iOS/           # SwiftUI iPhone app
├── config.yml          # Your local settings (gitignored)
├── secrets.yml         # API keys (gitignored)
└── sources.json        # Feed sources (gitignored)
```

The super agent dispatches long-running tasks (writing, publishing, research) as background processes so the main loop stays under 5 seconds.

## Multi-model

Mira orchestrates multiple LLM providers:

- **Claude** — primary reasoning, task planning
- **GPT** — creative prose, natural language
- **DeepSeek** — Chinese writing, cost-efficient reasoning
- **Gemini** — fast analysis, long context

The writing pipeline runs all models in parallel, then reviews and converges.

## Setup

1. Clone this repo
2. Copy config files:
   ```bash
   cp config.example.yml config.yml
   cp secrets.example.yml secrets.yml
   cp sources.example.json sources.json
   ```
3. Edit `config.yml` with your root path
4. Edit `secrets.yml` with your API keys
5. Edit `sources.json` with your preferred feeds
6. Install Python dependencies:
   ```bash
   pip install pyyaml
   ```
7. Install [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) (used for some agent calls)
8. Set up the LaunchAgent (see `docs/launchagent.md`) or run manually:
   ```bash
   cd agents/super && python3 core.py run
   ```

### Mira iOS app

Open `Mira-iOS/Mira/Mira.xcodeproj` in Xcode, build to your device, and point it at the `Mira-bridge/` folder in iCloud Drive.

## License

MIT
