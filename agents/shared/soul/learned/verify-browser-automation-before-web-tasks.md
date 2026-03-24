# verify-browser-automation-before-web-tasks

Check that browser automation dependencies are installed before attempting web scraping tasks

**Source**: Extracted from task failure (2026-03-23)
**Tags**: browser-automation, error-recovery, dependency-check, web-tasks

---

Before executing any task that requires browser automation or web scraping, verify that the required modules/dependencies are available in the environment.

**Rule:** When a task requires visiting a website to retrieve dynamic content, first check if the necessary browser automation tools (`browser`, `playwright`, `selenium`, `puppeteer`, etc.) are installed and importable. Do not assume they are available.

**Fallback chain when browser automation is unavailable:**
1. Use `WebFetch` or `WebSearch` tools if the content is accessible via HTTP GET (static pages, public APIs).
2. Use `WebSearch` to find deal aggregators, cached pages, or relevant listings.
3. Clearly inform the user that browser automation is unavailable and offer alternatives (manual URL, different tool, install instructions).

**Anti-pattern:** Attempting to `import browser` or similar without checking availability, failing silently, then waiting for the user to ask for a fix instead of proactively diagnosing and recovering.

**What should have happened:** Upon receiving `No module named 'browser'`, the agent should have immediately (a) recognized this as a missing dependency, (b) attempted fallback via WebFetch/WebSearch to browse bhphotovideo.com deals, and (c) reported what it could and could not do — rather than surfacing a raw error and stalling until the user pushed multiple times.
