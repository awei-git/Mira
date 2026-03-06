#!/usr/bin/env python3
"""Task worker — standalone sub-agent process for Mira.

Spawned by TaskManager.dispatch(). Reads a message, loads context,
calls claude_act(), writes output + result JSON.

Usage:
    python task_worker.py --msg-file <path> --workspace <path> --task-id <id> [--thread-id <id>]
"""
import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add shared + sibling agent directories to path
_AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_AGENTS_DIR / "shared"))
sys.path.insert(0, str(_AGENTS_DIR / "writer"))
sys.path.insert(0, str(_AGENTS_DIR / "general"))

import shutil

from config import MIRA_DIR
from soul_manager import load_soul, format_soul, append_memory, save_skill
from sub_agent import claude_act, claude_think
from prompts import respond_prompt
from writing_workflow import run_full_pipeline


log = logging.getLogger("task_worker")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_thread_history(thread_id: str, limit: int = 20) -> str:
    """Load recent messages from a thread for context injection."""
    if not thread_id:
        return ""

    messages = []
    for folder in [MIRA_DIR / "inbox", MIRA_DIR / "outbox"]:
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("thread_id") == thread_id:
                    messages.append(data)
            except (json.JSONDecodeError, OSError):
                continue

    # Sort by timestamp and take recent
    messages.sort(key=lambda m: m.get("timestamp", ""))
    messages = messages[-limit:]

    if not messages:
        return ""

    lines = ["## Recent conversation in this thread\n"]
    for msg in messages:
        sender = msg.get("sender", "?")
        content = msg.get("content", "")
        ts = msg.get("timestamp", "")[:16]
        lines.append(f"**[{ts}] {sender}**: {content}\n")

    return "\n".join(lines)


def load_thread_memory(thread_id: str) -> str:
    """Load per-thread memory if it exists."""
    if not thread_id:
        return ""
    mem_file = MIRA_DIR / "threads" / thread_id / "memory.md"
    if mem_file.exists():
        return mem_file.read_text(encoding="utf-8")
    return ""


def smart_classify(content: str, summary: str = "") -> list[str]:
    """Use LLM to intelligently tag a task. Returns 1-5 short tags."""
    prompt = f"""Given this task request and result, generate 1-5 short tags (each 1-3 words) that classify the task. Tags should be specific and useful for search/filtering. Mix Chinese and English as appropriate. Output ONLY a JSON array of strings, nothing else.

Request: {content[:300]}
Result: {summary[:300] if summary else '(pending)'}

Example output: ["写作", "science-fiction", "自由意志"]"""

    try:
        result = claude_think(prompt, timeout=30)
        if result:
            # Extract JSON array from response
            import re
            match = re.search(r'\[.*?\]', result, re.DOTALL)
            if match:
                tags = json.loads(match.group())
                # Ensure all tags are strings and reasonable length
                return [str(t).strip()[:20] for t in tags if t and str(t).strip()][:5]
    except Exception as e:
        log.warning("Smart classification failed: %s", e)
    return []


def try_extract_skill(task_summary: str, msg_content: str) -> None:
    """Ask Claude to consider extracting a skill from the completed task."""
    if not task_summary or len(task_summary) < 100:
        return

    prompt = f"""Based on this task and its result, is there a reusable skill to extract?

Task request: {msg_content[:500]}

Task result summary: {task_summary[:1000]}

If yes, output EXACTLY in this format:
```
Name: [short skill name]
Description: [one-liner]
Content:
[The full skill — technique, pattern, or method — written in your own words, ready to reuse]
```

If no reusable skill can be extracted, just say "No new skill from this task."
"""
    import re
    result = claude_think(prompt, timeout=60)
    if not result or "no new skill" in result.lower():
        return

    match = re.search(
        r"Name:\s*(.+)\nDescription:\s*(.+)\nContent:\n(.+?)(?:\n```|$)",
        result, re.DOTALL,
    )
    if match:
        name = match.group(1).strip()
        desc = match.group(2).strip()
        content = match.group(3).strip()
        save_skill(name, desc, content)
        append_memory(f"Learned skill from TalkBridge task: {name}")
        log.info("Extracted skill: %s", name)


def main():
    parser = argparse.ArgumentParser(description="TalkBridge task worker")
    parser.add_argument("--msg-file", required=True, help="Path to message JSON")
    parser.add_argument("--workspace", required=True, help="Workspace directory")
    parser.add_argument("--task-id", required=True, help="Task ID")
    parser.add_argument("--thread-id", default="", help="Thread ID for context")
    args = parser.parse_args()

    # Set up logging to workspace
    workspace = Path(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(workspace / "worker.log", encoding="utf-8"),
        ],
    )

    log.info("Worker started: task=%s thread=%s", args.task_id, args.thread_id)

    # Read message
    try:
        msg_data = json.loads(Path(args.msg_file).read_text(encoding="utf-8"))
    except Exception as e:
        log.error("Failed to read message: %s", e)
        _write_result(workspace, args.task_id, "error", f"Failed to read message: {e}")
        sys.exit(1)

    msg_content = msg_data.get("content", "")
    msg_sender = msg_data.get("sender", "unknown")
    thread_id = args.thread_id or msg_data.get("thread_id", "")

    # --- Plan and execute via LLM ---
    plan = _plan_task(msg_content)
    log.info("Plan: %s", plan)

    _execute_plan(plan, workspace, args.task_id, msg_content, msg_sender, thread_id)

    log.info("Worker exiting")


# ---------------------------------------------------------------------------
# LLM-based task planning
# ---------------------------------------------------------------------------

def _plan_task(content: str) -> list[dict]:
    """Use LLM to decompose a request into an ordered list of steps.

    Each step is {"agent": "<name>", "instruction": "<what to do>"}.
    Agents: briefing, writing, publish, general, clarify.
    "clarify" means ask the user for more info (instruction = the question).

    Returns a list of 1+ steps. The output of step N is available to step N+1.
    """
    prompt = f"""You are a task planner. Decompose this user request into ordered execution steps.

Available agents:
- briefing: Fetch feeds and generate a news briefing / summary
- writing: Write or create text content (article, story, essay, post, translation)
- publish: Publish EXISTING content to a platform (Substack, Instagram, Threads)
- general: Answer questions, search, analyze, code, file operations, etc.
- clarify: Ask the user a question to get more information before proceeding

Rules:
- If the request needs content CREATED then PUBLISHED, use writing first then publish.
- If publishing existing content, just use publish.
- Talking ABOUT writing (e.g. "写作技巧") is general, not writing.
- If the request is ambiguous or missing critical info, use clarify.
- Most requests need only 1 step. Use multiple steps only when truly needed.

Output ONLY a JSON array. Each element: {{"agent": "...", "instruction": "..."}}
The instruction should be a clear directive for that agent, in the user's language.

Examples:
- "今天有什么新闻" → [{{"agent": "briefing", "instruction": "生成今日新闻简报"}}]
- "写一篇关于AI的文章" → [{{"agent": "writing", "instruction": "写一篇关于AI的文章"}}]
- "写一个Hello World发到substack" → [{{"agent": "writing", "instruction": "写一篇简短的Hello World文章"}}, {{"agent": "publish", "instruction": "将上一步写好的文章发布到Substack"}}]
- "把自由意志那篇发到substack" → [{{"agent": "publish", "instruction": "将'自由意志'文章发布到Substack"}}]
- "发个东西到substack" → [{{"agent": "clarify", "instruction": "你想发什么内容到Substack？是已有的文章还是需要我先写一篇？"}}]

User request: {content[:500]}

JSON:"""

    import re
    try:
        result = claude_think(prompt, timeout=20)
        if result:
            match = re.search(r'\[.*\]', result, re.DOTALL)
            if match:
                steps = json.loads(match.group())
                # Validate
                valid_agents = {"briefing", "writing", "publish", "general", "clarify"}
                validated = []
                for s in steps:
                    if isinstance(s, dict) and s.get("agent") in valid_agents:
                        validated.append(s)
                if validated:
                    return validated
    except Exception as e:
        log.warning("Planning failed, falling back to general: %s", e)

    return [{"agent": "general", "instruction": content}]


def _execute_plan(plan: list[dict], workspace: Path, task_id: str,
                  content: str, sender: str, thread_id: str):
    """Execute a multi-step plan. Each step's output feeds into the next."""
    prev_output = None
    is_multi = len(plan) > 1

    for i, step in enumerate(plan):
        agent = step["agent"]
        instruction = step["instruction"]
        is_last = (i == len(plan) - 1)
        log.info("Step %d/%d: agent=%s instruction=%s", i+1, len(plan), agent, instruction[:80])

        # If previous step produced output, append it as context
        if prev_output and agent != "clarify":
            instruction = f"{instruction}\n\n--- 上一步的输出 ---\n{prev_output[:3000]}"

        if agent == "clarify":
            (workspace / "output.md").write_text(instruction, encoding="utf-8")
            _write_result(workspace, task_id, "done", instruction,
                          tags=["clarify"])
            return

        elif agent == "briefing":
            _handle_briefing(workspace, task_id, instruction, sender, thread_id)

        elif agent == "writing":
            _handle_writing(workspace, task_id, instruction, sender, thread_id)

        elif agent == "publish":
            _handle_publish(workspace, task_id, instruction, sender, thread_id)

        else:
            _handle_general(workspace, task_id, instruction, sender, thread_id)

        # Check if this step failed (result.json says error)
        result_file = workspace / "result.json"
        if result_file.exists():
            try:
                r = json.loads(result_file.read_text(encoding="utf-8"))
                if r.get("status") == "error":
                    log.error("Step %d/%d failed, aborting plan: %s", i+1, len(plan), r.get("summary", ""))
                    return
            except (json.JSONDecodeError, OSError):
                pass

        # Read output from this step for chaining
        output_file = workspace / "output.md"
        if output_file.exists():
            prev_output = output_file.read_text(encoding="utf-8")

        # For multi-step plans, delete intermediate result.json so next step writes fresh
        if is_multi and not is_last and result_file.exists():
            result_file.unlink()

    log.info("Plan execution complete (%d steps)", len(plan))


# ---------------------------------------------------------------------------
# Briefing handler
# ---------------------------------------------------------------------------


def _handle_briefing(workspace: Path, task_id: str, content: str,
                     sender: str, thread_id: str):
    """Generate a fresh briefing by fetching feeds and running explore pipeline."""
    # Add explorer to path
    sys.path.insert(0, str(_AGENTS_DIR / "explorer"))

    from fetcher import fetch_all
    from config import BRIEFINGS_DIR

    log.info("Fetching feeds for on-demand briefing...")
    items = fetch_all()
    if not items:
        msg = "没有抓到新内容，等下再试试。"
        (workspace / "output.md").write_text(msg, encoding="utf-8")
        _write_result(workspace, task_id, "done", msg, tags=["briefing"])
        return

    soul = load_soul()
    soul_ctx = format_soul(soul)

    # Format feed items
    lines = []
    for i, item in enumerate(items, 1):
        lines.append(f"[{i}] {item.get('source', '?')} | {item.get('title', '?')}")
        if item.get("summary"):
            lines.append(f"    {item['summary'][:200]}")
        if item.get("url"):
            lines.append(f"    {item['url']}")
        lines.append("")
    feed_text = "\n".join(lines)

    from prompts import explore_prompt
    prompt = explore_prompt(soul_ctx, feed_text)
    briefing = claude_think(prompt, timeout=180)

    if not briefing:
        msg = "生成briefing失败了，Claude没返回内容。"
        (workspace / "output.md").write_text(msg, encoding="utf-8")
        _write_result(workspace, task_id, "error", msg, tags=["briefing"])
        return

    # Save to artifacts
    today = datetime.now().strftime("%Y-%m-%d")
    BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)
    briefing_path = BRIEFINGS_DIR / f"{today}.md"
    briefing_path.write_text(briefing, encoding="utf-8")
    log.info("Briefing saved: %s", briefing_path.name)

    # Also copy to mira/artifacts for iOS browsing
    from config import MIRA_DIR
    mira_briefings = MIRA_DIR / "artifacts" / "briefings"
    mira_briefings.mkdir(parents=True, exist_ok=True)
    (mira_briefings / f"{today}.md").write_text(briefing, encoding="utf-8")

    # Write to task output
    (workspace / "output.md").write_text(briefing, encoding="utf-8")

    summary = f"生成了{today}的briefing，基于{len(items)}条feed内容。"
    (workspace / "summary.txt").write_text(summary, encoding="utf-8")
    _write_result(workspace, task_id, "done", summary, tags=["briefing", "explore"])

    append_memory(f"On-demand briefing for {sender}: {len(items)} items")

    if thread_id:
        _update_thread_memory(thread_id, content, summary)


# ---------------------------------------------------------------------------
# Writing handler — quick vs full pipeline
# ---------------------------------------------------------------------------

def _is_quick_write(content: str) -> bool:
    """Detect if this is a short/simple writing request (skip full pipeline)."""
    quick_signals = ["简短", "短", "hello world", "post", "quick", "简单",
                     "随便写", "短文", "一段", "几句"]
    lower = content.lower()
    return any(s in lower for s in quick_signals)


def _handle_writing(workspace: Path, task_id: str, content: str,
                    sender: str, thread_id: str):
    """Route writing requests: quick path for short content, full pipeline for serious work."""
    # Extract a title from the content
    title = content[:30].strip()
    if "写" in title:
        import re
        m = re.search(r"写[一篇个]*(.*?)(?:\s|$)", content[:60])
        if m and m.group(1):
            title = m.group(1).strip()[:30]

    if _is_quick_write(content):
        log.info("Quick write: title='%s'", title)
        _handle_quick_write(workspace, task_id, content, title, sender, thread_id)
    else:
        log.info("Full writing pipeline: title='%s'", title)
        _handle_full_write(workspace, task_id, content, title, sender, thread_id)


def _handle_quick_write(workspace: Path, task_id: str, content: str,
                        title: str, sender: str, thread_id: str):
    """Single-model quick draft — no multi-agent plan/review cycle."""
    soul = load_soul()
    soul_ctx = format_soul(soul)

    prompt = (
        f"你是一个写作助手。以下是你的身份:\n{soul_ctx[:500]}\n\n"
        f"用户请求: {content}\n\n"
        f"请直接写出完整内容（Markdown格式）。不要解释，不要元评论，直接输出文章。"
    )
    text = claude_think(prompt, timeout=120)

    if not text:
        _write_result(workspace, task_id, "error", "Quick write failed: empty output")
        return

    final_text = f"# {title}\n\n{text}"
    (workspace / "output.md").write_text(final_text, encoding="utf-8")

    summary = f"快速写作 '{title}' 完成 (~{len(text)}字)"
    (workspace / "summary.txt").write_text(summary, encoding="utf-8")

    tags = smart_classify(content, summary)
    _write_result(workspace, task_id, "done", summary, tags=tags)

    if thread_id:
        _update_thread_memory(thread_id, content, summary)


def _handle_full_write(workspace: Path, task_id: str, content: str,
                       title: str, sender: str, thread_id: str):
    """Full multi-agent writing pipeline with plan/draft/review cycles."""
    try:
        proj_ws, final_text = run_full_pipeline(title, content)
    except Exception as e:
        log.error("Writing pipeline failed: %s", e)
        _write_result(workspace, task_id, "error", f"Writing pipeline failed: {e}")
        return

    if not final_text:
        _write_result(workspace, task_id, "error", "Writing pipeline produced no output")
        return

    # Copy final.md to task workspace as output.md
    final_file = proj_ws / "final.md"
    if final_file.exists():
        shutil.copy2(final_file, workspace / "output.md")
    else:
        (workspace / "output.md").write_text(final_text, encoding="utf-8")

    # Sync full writing project to mira/artifacts for iOS browsing
    from config import MIRA_DIR
    mira_writings = MIRA_DIR / "artifacts" / "writings" / proj_ws.name
    shutil.copytree(proj_ws, mira_writings, dirs_exist_ok=True)

    # Build summary
    summary = (
        f"写作项目 '{title}' 完成。经过多智能体策划、写作、{5}轮评审。"
        f"\n\n项目文件: {proj_ws}"
        f"\n字数: ~{len(final_text)}字"
    )
    (workspace / "summary.txt").write_text(summary, encoding="utf-8")

    tags = smart_classify(content, summary)
    _write_result(workspace, task_id, "done", summary, tags=tags)
    log.info("Writing task %s completed: %s (tags=%s)", task_id, proj_ws, tags)

    if thread_id:
        _update_thread_memory(thread_id, content, summary)


# ---------------------------------------------------------------------------
# Publish handler
# ---------------------------------------------------------------------------

def _handle_publish(workspace: Path, task_id: str, content: str,
                    sender: str, thread_id: str):
    """Route publish requests to the publisher agent."""
    try:
        # Add publisher dir to path so handler.py can import substack.py
        publisher_dir = str(_AGENTS_DIR / "publisher")
        if publisher_dir not in sys.path:
            sys.path.insert(0, publisher_dir)

        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "publisher_handler", str(_AGENTS_DIR / "publisher" / "handler.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        publish_handle = mod.handle

        log.info("Publishing content for task %s", task_id)
        summary = publish_handle(workspace, task_id, content, sender, thread_id)
    except Exception as e:
        log.error("Publish handler crashed: %s", e)
        _write_result(workspace, task_id, "error", f"发布失败: {e}")
        return

    if summary:
        tags = smart_classify(content, summary)
        tags.append("publish")
        _write_result(workspace, task_id, "done", summary, tags=tags)
        log.info("Publish task %s completed", task_id)

        if thread_id:
            _update_thread_memory(thread_id, content, summary)
    else:
        _write_result(workspace, task_id, "error", "发布失败")
        log.error("Publish task %s failed", task_id)


# ---------------------------------------------------------------------------
# General handler — claude_act
# ---------------------------------------------------------------------------

def _handle_general(workspace: Path, task_id: str, content: str,
                    sender: str, thread_id: str):
    """Handle non-writing requests via the general agent."""
    from handler import handle as general_handle

    thread_history = load_thread_history(thread_id)
    thread_memory = load_thread_memory(thread_id)

    summary = general_handle(
        workspace, task_id, content, sender, thread_id,
        thread_history=thread_history, thread_memory=thread_memory,
    )

    if summary:
        tags = smart_classify(content, summary)
        _write_result(workspace, task_id, "done", summary, tags=tags)
        log.info("Task %s completed successfully", task_id)

        if thread_id:
            _update_thread_memory(thread_id, content, summary)

        try:
            try_extract_skill(summary, content)
        except Exception as e:
            log.warning("Skill extraction failed: %s", e)
    else:
        _write_result(workspace, task_id, "error", "Claude returned empty response")
        log.error("Task %s failed: empty response", task_id)


def _write_result(workspace: Path, task_id: str, status: str, summary: str,
                  tags: list[str] | None = None):
    """Write result JSON for TaskManager to collect."""
    result = {
        "task_id": task_id,
        "status": status,
        "summary": summary,
        "completed_at": _utc_iso(),
    }
    if tags:
        result["tags"] = tags
    (workspace / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _update_thread_memory(thread_id: str, request: str, summary: str):
    """Append task summary to per-thread memory."""
    thread_dir = MIRA_DIR / "threads" / thread_id
    thread_dir.mkdir(parents=True, exist_ok=True)
    mem_file = thread_dir / "memory.md"

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"- [{ts}] Request: {request[:80]} → {summary[:120]}\n"

    if mem_file.exists():
        text = mem_file.read_text(encoding="utf-8")
    else:
        text = "# Thread Memory\n\n"
    text += entry
    mem_file.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
