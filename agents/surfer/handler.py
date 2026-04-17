"""Surfer agent — browser automation via Playwright + LLM planning.

Routes browser tasks through an LLM planning loop:
1. LLM sees the task + current page state
2. LLM decides next action (goto, click, fill, scroll, etc.)
3. Execute action, capture result
4. Repeat until task is done or max steps reached

This is fundamentally different from web_browser.py which just fetches
static HTML. Surfer can handle JS-rendered SPAs, fill forms, click
through multi-step flows, and take screenshots for visual grounding.
"""

import json
import logging
import sys
from pathlib import Path

_SURFER_DIR = Path(__file__).resolve().parent
_AGENTS_DIR = _SURFER_DIR.parent
sys.path.insert(0, str(_SURFER_DIR))
sys.path.insert(0, str(_AGENTS_DIR.parent / "lib"))

from config import (
    MIRA_DIR,
    SURFER_MAX_STEPS,
    SURFER_STEP_TIMEOUT,
    SURFER_LLM_TIMEOUT,
    SURFER_EXTRACTION_TIMEOUT,
)
from memory.soul import load_soul, format_soul
from llm import claude_think

log = logging.getLogger("surfer_agent")

# Available browser actions the LLM can choose from
_ACTIONS_SPEC = """
Available actions (output ONE as JSON):

{"action": "goto", "url": "https://..."}
  Navigate to a URL.

{"action": "click", "selector": "CSS selector"}
  Click an element. Use selectors like: button:has-text("Submit"), #login-btn, a[href="/about"]

{"action": "fill", "selector": "CSS selector", "value": "text to fill"}
  Fill a form field (clears first, then types).

{"action": "type", "selector": "CSS selector", "text": "text to type", "delay": 50}
  Type character-by-character (for autocomplete/search boxes). delay in ms.

{"action": "press", "key": "Enter"}
  Press a key (Enter, Tab, Escape, ArrowDown, etc.)

{"action": "scroll", "direction": "down", "amount": 500}
  Scroll the page (direction: up/down, amount in pixels).

{"action": "select", "selector": "CSS selector", "value": "option value"}
  Select an option from a dropdown.

{"action": "wait", "selector": "CSS selector", "timeout": 5000}
  Wait for an element to appear.

{"action": "screenshot"}
  Take a screenshot of the current page (returned as description, not image).

{"action": "evaluate", "code": "document.querySelector(...).textContent"}
  Run JavaScript and get the result.

{"action": "extract", "instruction": "what to extract from the current page"}
  Extract specific information from the current page text.

{"action": "done", "result": "The final answer / result of the task"}
  Task is complete. Include the result.

{"action": "fail", "reason": "Why the task cannot be completed"}
  Task cannot be completed.
"""


def handle(workspace: Path, task_id: str, content: str, sender: str, thread_id: str) -> str | None:
    """Handle a browser automation request. Returns output text or None."""
    from browser import BrowserSession

    screenshots_dir = workspace / "screenshots"
    screenshots_dir.mkdir(exist_ok=True)

    soul = load_soul()
    soul_ctx = format_soul(soul)

    log.info("Starting surfer session for task %s: %s", task_id, content[:80])

    with BrowserSession(headless=True, screenshots_dir=screenshots_dir) as browser:
        page_state = "No page loaded yet."
        history = []
        result = None

        for step in range(SURFER_MAX_STEPS):
            # Build the planning prompt
            prompt = _build_step_prompt(
                task=content,
                soul_ctx=soul_ctx,
                page_state=page_state,
                history=history,
                step=step + 1,
                max_steps=SURFER_MAX_STEPS,
            )

            # Ask LLM what to do next
            llm_response = claude_think(prompt, timeout=SURFER_LLM_TIMEOUT)
            if not llm_response:
                log.warning("Step %d: LLM returned empty response", step + 1)
                continue

            # Parse the action
            action = _parse_action(llm_response)
            if not action:
                log.warning("Step %d: Could not parse action from: %s", step + 1, llm_response[:200])
                history.append({"step": step + 1, "error": "Could not parse action"})
                continue

            action_type = action.get("action", "")
            log.info(
                "Step %d: %s %s",
                step + 1,
                action_type,
                json.dumps({k: v for k, v in action.items() if k != "action"}, ensure_ascii=False)[:100],
            )

            # Handle terminal actions
            if action_type == "done":
                result = action.get("result", "Task completed.")
                history.append({"step": step + 1, "action": "done"})
                break
            elif action_type == "fail":
                result = f"Task failed: {action.get('reason', 'Unknown reason')}"
                history.append({"step": step + 1, "action": "fail", "reason": action.get("reason", "")})
                break

            # Execute browser action
            browser_result = _execute_action(browser, action)
            history.append(
                {
                    "step": step + 1,
                    "action": action_type,
                    "detail": {k: str(v)[:80] for k, v in action.items() if k != "action"},
                    "ok": browser_result.ok if hasattr(browser_result, "ok") else not browser_result.error,
                    "error": browser_result.error if browser_result.error else None,
                }
            )

            # Update page state for next iteration
            if action_type == "extract":
                # For extract, use LLM to pull specific info from page text
                page_text = browser.get_page_text()
                extraction = _extract_info(page_text, action.get("instruction", ""))
                page_state = _format_page_state(browser_result, extraction=extraction)
            elif action_type == "screenshot":
                page_state = _format_page_state(browser_result, screenshot_taken=True)
            else:
                page_state = _format_page_state(browser_result)

        if result is None:
            result = f"Reached max steps ({SURFER_MAX_STEPS}) without completing. Last page: {page_state[:500]}"

    # Write output
    output = _format_output(content, result, history)
    (workspace / "output.md").write_text(output, encoding="utf-8")
    log.info("Surfer task %s completed in %d steps", task_id, len(history))
    return result[:500]


def _build_step_prompt(
    task: str, soul_ctx: str, page_state: str, history: list[dict], step: int, max_steps: int
) -> str:
    history_text = ""
    if history:
        recent = history[-8:]  # keep context manageable
        lines = []
        for h in recent:
            s = f"Step {h['step']}: {h.get('action', '?')}"
            if h.get("detail"):
                s += f" {h['detail']}"
            if h.get("error"):
                s += f" [ERROR: {h['error']}]"
            lines.append(s)
        history_text = "\n".join(lines)

    return f"""{soul_ctx}

You are Mira's browser automation agent. You control a real Chromium browser.

## Task
{task}

## Current page state
{page_state[:6000]}

## Action history
{history_text if history_text else "(No actions taken yet)"}

## Step {step}/{max_steps}

{_ACTIONS_SPEC}

Think about what to do next to accomplish the task, then output exactly ONE action as JSON.
If the task is complete, use {{"action": "done", "result": "..."}}.
Be efficient — minimize steps. If you can see the answer on the page, extract it and finish.

Your action (JSON only):"""


def _parse_action(response: str) -> dict | None:
    import re

    # Try to find JSON in the response
    match = re.search(r"\{[^{}]*\}", response, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Try the whole response as JSON
    try:
        return json.loads(response.strip())
    except json.JSONDecodeError:
        pass

    return None


def _execute_action(browser, action: dict):
    """Execute a browser action and return the result."""
    from browser import BrowserResult

    act = action.get("action", "")

    try:
        if act == "goto":
            return browser.goto(action["url"])
        elif act == "click":
            return browser.click(action["selector"])
        elif act == "fill":
            return browser.fill(action["selector"], action["value"])
        elif act == "type":
            return browser.type_text(action["selector"], action["text"], delay=action.get("delay", 50))
        elif act == "press":
            return browser.press(action["key"])
        elif act == "scroll":
            return browser.scroll(action.get("direction", "down"), action.get("amount", 500))
        elif act == "select":
            return browser.select(action["selector"], action["value"])
        elif act == "wait":
            return browser.wait_for(action["selector"], timeout=action.get("timeout", 10000))
        elif act == "screenshot":
            b64 = browser.screenshot()
            return BrowserResult(
                url=browser._page.url if browser._page else "",
                title=browser._page.title() if browser._page else "",
                text=f"[Screenshot taken ({len(b64)} bytes b64)]",
            )
        elif act == "evaluate":
            result = browser.evaluate(action["code"])
            return BrowserResult(
                url=browser._page.url if browser._page else "",
                title=browser._page.title() if browser._page else "",
                text=f"JS result: {result[:2000]}",
            )
        else:
            return BrowserResult(error=f"Unknown action: {act}")
    except Exception as e:
        return BrowserResult(error=f"{act} failed: {e}")


def _extract_info(page_text: str, instruction: str) -> str:
    """Use LLM to extract specific information from page text."""
    prompt = f"""Extract the following from this page content:

Instruction: {instruction}

Page content:
{page_text[:8000]}

Extract ONLY the requested information. Be concise and precise."""

    result = claude_think(prompt, timeout=SURFER_EXTRACTION_TIMEOUT)
    return result or "[Extraction failed]"


def _format_page_state(result, extraction: str = "", screenshot_taken: bool = False) -> str:
    parts = []
    if result.error:
        parts.append(f"ERROR: {result.error}")
    parts.append(f"URL: {result.url}")
    parts.append(f"Title: {result.title}")

    if extraction:
        parts.append(f"\nExtracted info:\n{extraction}")
    elif result.text:
        # Truncate page text to keep prompt manageable
        text = result.text[:4000]
        parts.append(f"\nPage text (first 4000 chars):\n{text}")

    if result.links:
        link_lines = [f"  [{l['text'][:50]}]({l['href']})" for l in result.links[:20]]
        parts.append(f"\nLinks:\n" + "\n".join(link_lines))

    if screenshot_taken:
        parts.append("\n[Screenshot captured and saved]")

    return "\n".join(parts)


def _format_output(task: str, result: str, history: list[dict]) -> str:
    steps_summary = []
    for h in history:
        line = f"- Step {h['step']}: {h.get('action', '?')}"
        if h.get("error"):
            line += f" (error: {h['error']})"
        steps_summary.append(line)

    return f"""# Surfer Task Result

## Task
{task}

## Result
{result}

## Steps taken ({len(history)})
{chr(10).join(steps_summary)}
"""
