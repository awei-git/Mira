"""Surfer browser engine — Playwright-based browser automation.

Provides high-level browser primitives that the surfer agent handler
orchestrates via LLM planning. Runs headless Chromium by default.

Capabilities beyond web_browser.py:
- JavaScript-rendered pages (SPAs, dynamic content)
- Form filling, clicking, scrolling
- Screenshots for visual grounding
- Multi-tab sessions
- Cookie/auth persistence
"""
import base64
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_AGENTS_DIR = Path(__file__).resolve().parent.parent
if str(_AGENTS_DIR.parent / "lib") not in sys.path:
    sys.path.insert(0, str(_AGENTS_DIR.parent / "lib"))

from config import (
    BROWSER_DEFAULT_TIMEOUT_MS, BROWSER_NETWORKIDLE_TIMEOUT_MS,
    BROWSER_DOMCONTENTLOADED_TIMEOUT_MS, BROWSER_SCROLL_WAIT_MS,
    BROWSER_TYPING_DELAY_MS,
)

log = logging.getLogger("surfer.browser")


@dataclass
class BrowserResult:
    url: str = ""
    title: str = ""
    text: str = ""
    screenshot_b64: str = ""
    error: str = ""
    links: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.text.strip())

    def summary(self, max_chars: int = 4000) -> str:
        t = self.text.strip()
        if len(t) <= max_chars:
            return t
        return t[:max_chars] + "\n\n[... truncated]"


class BrowserSession:
    """Manages a Playwright browser session with persistent state."""

    def __init__(self, headless: bool = True, timeout: int = BROWSER_DEFAULT_TIMEOUT_MS,
                 screenshots_dir: Optional[Path] = None):
        self._headless = headless
        self._timeout = timeout
        self._screenshots_dir = screenshots_dir
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._screenshot_count = 0

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()

    def start(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self._headless)
        self._context = self._browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        self._page = self._context.new_page()
        self._page.set_default_timeout(self._timeout)
        log.info("Browser session started (headless=%s)", self._headless)

    def close(self):
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()
        self._browser = None
        self._pw = None
        self._page = None
        self._context = None
        log.info("Browser session closed")

    def _ensure_page(self):
        if not self._page:
            raise RuntimeError("Browser session not started. Call start() first.")

    def goto(self, url: str, wait_until: str = "domcontentloaded") -> BrowserResult:
        self._ensure_page()
        try:
            self._page.goto(url, wait_until=wait_until, timeout=self._timeout)
            self._page.wait_for_load_state("networkidle", timeout=BROWSER_NETWORKIDLE_TIMEOUT_MS)
        except Exception:
            pass  # networkidle timeout is non-fatal
        return self._snapshot()

    def click(self, selector: str) -> BrowserResult:
        self._ensure_page()
        try:
            self._page.click(selector, timeout=self._timeout)
            self._page.wait_for_load_state("domcontentloaded", timeout=BROWSER_DOMCONTENTLOADED_TIMEOUT_MS)
        except Exception as e:
            return BrowserResult(error=f"Click failed on '{selector}': {e}")
        return self._snapshot()

    def fill(self, selector: str, value: str) -> BrowserResult:
        self._ensure_page()
        try:
            self._page.fill(selector, value, timeout=self._timeout)
        except Exception as e:
            return BrowserResult(error=f"Fill failed on '{selector}': {e}")
        return self._snapshot()

    def type_text(self, selector: str, text: str, delay: int = BROWSER_TYPING_DELAY_MS) -> BrowserResult:
        """Type text character by character (useful for search boxes with autocomplete)."""
        self._ensure_page()
        try:
            self._page.click(selector, timeout=self._timeout)
            self._page.keyboard.type(text, delay=delay)
        except Exception as e:
            return BrowserResult(error=f"Type failed on '{selector}': {e}")
        return self._snapshot()

    def press(self, key: str) -> BrowserResult:
        self._ensure_page()
        try:
            self._page.keyboard.press(key)
            self._page.wait_for_load_state("domcontentloaded", timeout=BROWSER_DOMCONTENTLOADED_TIMEOUT_MS)
        except Exception:
            pass
        return self._snapshot()

    def scroll(self, direction: str = "down", amount: int = 500) -> BrowserResult:
        self._ensure_page()
        delta = amount if direction == "down" else -amount
        self._page.mouse.wheel(0, delta)
        self._page.wait_for_timeout(BROWSER_SCROLL_WAIT_MS)
        return self._snapshot()

    def select(self, selector: str, value: str) -> BrowserResult:
        self._ensure_page()
        try:
            self._page.select_option(selector, value, timeout=self._timeout)
        except Exception as e:
            return BrowserResult(error=f"Select failed on '{selector}': {e}")
        return self._snapshot()

    def wait_for(self, selector: str, timeout: int = BROWSER_NETWORKIDLE_TIMEOUT_MS) -> BrowserResult:
        self._ensure_page()
        try:
            self._page.wait_for_selector(selector, timeout=timeout)
        except Exception as e:
            return BrowserResult(error=f"Wait failed for '{selector}': {e}")
        return self._snapshot()

    def screenshot(self, full_page: bool = False) -> str:
        """Take screenshot, return base64 PNG. Optionally save to disk."""
        self._ensure_page()
        raw = self._page.screenshot(full_page=full_page)
        b64 = base64.b64encode(raw).decode()
        if self._screenshots_dir:
            self._screenshot_count += 1
            path = self._screenshots_dir / f"screenshot_{self._screenshot_count:03d}.png"
            path.write_bytes(raw)
            log.info("Screenshot saved: %s", path)
        return b64

    def evaluate(self, js_code: str) -> str:
        """Run JavaScript in the page context and return the result as string."""
        self._ensure_page()
        try:
            result = self._page.evaluate(js_code)
            return json.dumps(result, ensure_ascii=False, default=str) if result is not None else ""
        except Exception as e:
            return f"[JS Error: {e}]"

    def get_page_text(self) -> str:
        """Extract readable text from the current page."""
        self._ensure_page()
        try:
            text = self._page.evaluate("""() => {
                const remove = ['script', 'style', 'noscript', 'svg', 'iframe'];
                remove.forEach(tag => {
                    document.querySelectorAll(tag).forEach(el => el.remove());
                });
                return document.body.innerText;
            }""")
            return _clean_text(text or "")
        except Exception as e:
            log.warning("get_page_text failed: %s", e)
            return ""

    def get_links(self, max_links: int = 50) -> list[dict]:
        """Extract visible links from the current page."""
        self._ensure_page()
        try:
            links = self._page.evaluate(f"""() => {{
                const anchors = Array.from(document.querySelectorAll('a[href]'));
                return anchors
                    .filter(a => a.offsetParent !== null && a.textContent.trim())
                    .slice(0, {max_links})
                    .map(a => ({{
                        text: a.textContent.trim().substring(0, 100),
                        href: a.href,
                    }}));
            }}""")
            return links or []
        except Exception:
            return []

    def get_form_fields(self) -> list[dict]:
        """Extract visible form fields from the current page."""
        self._ensure_page()
        try:
            fields = self._page.evaluate("""() => {
                const inputs = Array.from(document.querySelectorAll(
                    'input, textarea, select, button[type="submit"]'
                ));
                return inputs
                    .filter(el => el.offsetParent !== null)
                    .slice(0, 30)
                    .map(el => ({
                        tag: el.tagName.toLowerCase(),
                        type: el.type || '',
                        name: el.name || '',
                        id: el.id || '',
                        placeholder: el.placeholder || '',
                        value: el.value || '',
                        label: el.labels?.[0]?.textContent?.trim() || '',
                        selector: el.id ? '#' + el.id
                                 : el.name ? `[name="${el.name}"]`
                                 : '',
                    }));
            }""")
            return fields or []
        except Exception:
            return []

    def _snapshot(self) -> BrowserResult:
        """Capture current page state."""
        self._ensure_page()
        try:
            url = self._page.url
            title = self._page.title()
            text = self.get_page_text()
            links = self.get_links(max_links=30)
            return BrowserResult(
                url=url, title=title, text=text, links=links,
            )
        except Exception as e:
            return BrowserResult(error=f"Snapshot failed: {e}")


def _clean_text(text: str) -> str:
    """Collapse whitespace, remove excessive blank lines."""
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        stripped = " ".join(line.split())
        if stripped:
            cleaned.append(stripped)
        elif cleaned and cleaned[-1] != "":
            cleaned.append("")
    result = "\n".join(cleaned).strip()
    return re.sub(r"\n{3,}", "\n\n", result)
