# verify-browser-automation-dependencies

Always verify browser automation module availability before attempting to run browser tasks

**Source**: Extracted from task failure (2026-03-23)
**Tags**: browser-automation, python, dependencies, environment-setup

---

## Rule: Verify Browser Automation Environment Before Execution

When a task involves browser automation (web scraping, UI testing, form filling, screenshot capture, etc.), always verify the required modules and environment are available **before** attempting execution.

### What to check:
1. **Module existence**: Confirm the browser automation library is installed (`browser`, `playwright`, `selenium`, `puppeteer`, etc.)
2. **Import path**: Verify the module name matches what's actually installed — `browser` is not a standard module; likely needs `playwright.sync_api`, `selenium.webdriver`, or similar
3. **Browser binaries**: Playwright/Selenium require browser binaries beyond just the Python package (`playwright install` step)
4. **Environment compatibility**: Headless browser support may not be available in sandboxed or restricted environments

### How to apply:
- Before writing browser automation code, run a quick dependency check: `python -c "import playwright"` or equivalent
- If the environment is unknown, use `subprocess` or shell to probe available packages first
- Prefer standard, well-known libraries (`playwright`, `selenium`) over ambiguous module names like `browser`
- If browser automation is unavailable, fall back to `requests`/`httpx` for non-JS pages, or surface a clear error explaining what's missing rather than silently failing

### Root cause here:
The code attempted `import browser` — a non-standard module name — without verifying it exists. The fix is either installing the correct package or using the correct import for the intended library.
