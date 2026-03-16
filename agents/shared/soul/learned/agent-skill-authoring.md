When adding skills to an agent that uses a skills/ directory + index.json pattern:

1. Read existing skills first to match format. Skills are typically pure markdown "strategy documents" — describe WHAT to do and HOW to interpret results, no executable code. This passes security audit trivially.

2. Structure each skill file as:
   - Goal / when to use this skill
   - Step-by-step fetch/parse strategy (describe the URL, selectors, or API fields to target)
   - How to interpret and format results for the agent
   - Edge cases and fallbacks

3. For web scraping skills, prefer:
   - Public JSON APIs (HN: hacker-news.firebaseio.com, GitHub: api.github.com/search, Reddit: reddit.com/r/{sub}/hot.json, arXiv: export.arxiv.org/api/query)
   - Avoid scraping HTML when an API exists
   - Use urllib (stdlib) in any fetcher code to avoid extra dependencies

4. Register each new skill in the agent's index.json (or equivalent skill registry) so the soul_manager / skill loader can discover and inject it. Typical fields: name, file, description, tags.

5. Security audit checklist:
   - Skill markdown files: no shell commands, no code blocks that run, no path traversal
   - Any fetcher code: no eval/exec, validate URLs before fetching, cap response size, handle HTTP errors gracefully
   - No credentials or API keys hardcoded

6. Test discoverability: after updating index.json, verify the count matches expected and the loader picks up the new entries.