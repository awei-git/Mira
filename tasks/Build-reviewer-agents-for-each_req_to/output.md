Done. Files created:

- [reviewers.py](file://reviewers.py) — main module with all 10 reviewer agents
- [output.md](file://output.md) — full documentation with criteria, usage, and installation notes
- [summary.txt](file://summary.txt)

**What was built:**

`reviewers.py` contains `review_briefing`, `review_writing`, `review_publish`, `review_analyst`, `review_math`, `review_video`, `review_photo`, `review_podcast`, `review_secret`, and `review_general`, all dispatched via `review(agent_type, content)`.

Key design decisions:
- All reviewers use `claude_think` (text evaluation, no tools) — fast and cheap
- **`review_secret` uses local Ollama only** — preserves the privacy guarantee of the paired agent; zero cloud API calls
- **`review_video` and `review_photo` review pipeline text output** (edit plans, instructions, metadata), not the media files themselves — the existing `video/video_reviewer.py` and `photo/reviewer.py` already handle media-level review via Gemini vision
- The `handle()` function follows the exact same contract as every other Mira agent handler, so it drops cleanly into `task_worker.py` dispatch
- To install: `cp reviewers.py ~/Sandbox/Mira/agents/shared/reviewers.py`