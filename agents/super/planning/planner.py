"""Task planner — LLM-based task decomposition and output synthesis.

Extracted from task_worker.py. Contains:
- _load_super_skills: loads orchestration skill files for planner prompt
- _plan_task: main planner that calls Claude to decompose tasks
- _synthesize_outputs: synthesizes multi-step outputs into coherent response
"""
from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

# Add shared + sibling agent directories to path
_AGENTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(_AGENTS_DIR / "shared") not in sys.path:
    sys.path.insert(0, str(_AGENTS_DIR / "shared"))

from config import MIRA_DIR, MIRA_ROOT
from sub_agent import claude_think
from planning.plan_schema import validate_plan_step as _validate_plan_step

log = logging.getLogger("task_worker")

# ---------------------------------------------------------------------------
# Super-agent skill loader
# ---------------------------------------------------------------------------

_SUPER_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
_SUPER_SKILLS_INDEX = _SUPER_SKILLS_DIR / "index.json"


def _load_super_skills(task_content: str = "") -> str:
    """Load super-agent orchestration skills, filtered by relevance to the task.

    If task_content is provided, only loads skills whose tags or description
    overlap with task keywords. Always loads 'task-routing' and 'intent-inference'
    as baseline skills. Falls back to loading all skills if no filtering match.
    """
    if not _SUPER_SKILLS_INDEX.exists():
        return ""
    try:
        index = json.loads(_SUPER_SKILLS_INDEX.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""

    # Always-load skills (core orchestration)
    _ALWAYS_LOAD = {"task-routing-intelligence", "intent-inference"}

    if task_content:
        lower = task_content.lower()
        selected = []
        for entry in index:
            fname = entry.get("file", "")
            tags = entry.get("tags", [])
            desc = entry.get("description", "").lower()
            # Always-load skills
            if any(al in fname for al in _ALWAYS_LOAD):
                selected.append(entry)
                continue
            # Check if any tag or description keyword appears in task
            tag_match = any(t.lower() in lower for t in tags)
            desc_words = set(desc.split())
            content_words = set(lower.split())
            desc_match = len(desc_words & content_words) >= 2
            # Multi-step detection
            multi_kw = ["步", "step", "然后", "先", "再", "pipeline", "多步"]
            multi_match = "multi-step" in fname and any(k in lower for k in multi_kw)
            # Synthesis detection
            synth_kw = ["综合", "synthesize", "merge", "combine", "汇总"]
            synth_match = "synthesis" in fname and any(k in lower for k in synth_kw)

            if tag_match or desc_match or multi_match or synth_match:
                selected.append(entry)

        # Fallback: if filtering matched nothing beyond always-load, load all
        if len(selected) <= len(_ALWAYS_LOAD):
            selected = index
    else:
        selected = index

    sections = []
    for entry in selected:
        skill_file = _SUPER_SKILLS_DIR / entry.get("file", "")
        if skill_file.exists():
            try:
                sections.append(skill_file.read_text(encoding="utf-8").strip())
            except OSError:
                pass
    return "\n\n---\n\n".join(sections)


# ---------------------------------------------------------------------------
# LLM-based task planning
# ---------------------------------------------------------------------------

def _plan_task(content: str, conversation: str = "", exec_history: str = "",
               prior_context: str = "", allowed_agents: list[str] | None = None,
               content_filter: bool = False) -> list[dict]:
    """Use LLM to decompose a request into an ordered list of steps.

    Each step is {"agent": "<name>", "instruction": "<what to do>"}.
    Agents: briefing, writing, publish, general, clarify.
    "clarify" means ask the user for more info (instruction = the question).

    Returns a list of 1+ steps. The output of step N is available to step N+1.
    """
    conversation_context = ""
    context_parts = []
    if prior_context:
        context_parts.append(f"## Prior context from memory\n{prior_context}")
    if exec_history:
        context_parts.append(exec_history)
    if conversation:
        context_parts.append(f"""
IMPORTANT: This is a FOLLOW-UP message in an ongoing conversation. Read the history carefully.
If the user's intent is clear from context, DO NOT use clarify — just execute the task.
Only use clarify if the request is genuinely ambiguous even with the conversation history.
If a previous round already produced content, reference it in your plan (e.g. use publish to publish existing output).

{conversation}""")
    if context_parts:
        conversation_context = "\n\n".join(context_parts) + f"\n\n---\nLatest message from user: {content[:500]}"
    else:
        conversation_context = f"User request: {content[:500]}"

    super_skills = _load_super_skills(content)
    skills_section = f"\n\n## Orchestration Skills\n{super_skills}\n" if super_skills else ""

    # Calibration feedback: learn from past task outcomes
    cal_section = ""
    try:
        from evaluator import diagnose_scores
        diag = diagnose_scores()
        cal = diag.get("calibration_insights", "")
        if cal:
            cal_section = f"\n\n## Past Task Calibration\n{cal}\nUse this to estimate difficulty more accurately.\n"
    except (ImportError, OSError):
        pass

    # Build available agents list from registry (single source of truth)
    from agent_registry import get_registry
    _registry = get_registry()
    _all_agent_descs = {}
    for _name in _registry.list_agents():
        _m = _registry.get_manifest(_name)
        if _m:
            _handles = ", ".join(_m.handles) if _m.handles else _m.description
            _all_agent_descs[_name] = _handles
    # clarify is a virtual agent (not in registry)
    _all_agent_descs["clarify"] = "Ask the user a question ONLY if the request is genuinely ambiguous and cannot be inferred"
    if allowed_agents:
        # Always include clarify + discussion as fallbacks
        agent_filter = set(allowed_agents) | {"clarify", "discussion"}
        filtered = {k: v for k, v in _all_agent_descs.items() if k in agent_filter}
    else:
        filtered = _all_agent_descs
    agent_lines = "\n".join(f"- {name}: {desc}" for name, desc in filtered.items())

    content_filter_rule = ""
    if content_filter:
        content_filter_rule = """
- CONTENT FILTER ACTIVE: This is a child user. Only provide age-appropriate, safe, educational content.
  Never discuss violence, drugs, alcohol, sexual content, or anything dangerous. Redirect inappropriate requests to something positive."""

    prompt = f"""You are a task planner and orchestrator. Decompose this user request into ordered execution steps.{skills_section}{cal_section}

## Available Agents
{agent_lines}

## Rules
- Apply the routing, intent-inference, and instruction-crafting skills above to produce the best plan.
- Most requests need only 1 step. Use multiple steps only when data dependencies genuinely require it.
- Write instructions tailored to each agent — not just a copy of the user's words.
- Match instruction language to the user's language.
- NEVER ask for confirmation before starting. AVOID clarify unless truly impossible to infer.
- Prefer specialized agents over general-purpose ones. surfer (browser) is a last resort — only use it when no other agent can handle the task.
- HARD RULE: Any task mentioning Substack (notes, comments, links, engagement, subscribers) MUST use socialmedia, NEVER surfer. Surfer cannot authenticate to Substack.{content_filter_rule}

## Model Tier Selection
Each step must include a "tier" field:
- "light" (Sonnet, fast) — simple lookups, straightforward tasks, browsing, Q&A, discussion, scheduling, publishing, clarification
- "heavy" (Opus, best quality) — complex writing (essays, stories), deep analysis, math proofs, multi-step reasoning, creative work, anything requiring nuanced judgment

Default to "light". Only use "heavy" when the task genuinely requires deeper thinking. Most tasks are "light".

## Output
Output ONLY a JSON array. Each element:
{{"agent": "...", "instruction": "...", "tier": "light|heavy", "prediction": {{"difficulty": "easy|medium|hard", "failure_modes": ["..."], "success_criteria": "..."}}}}

The "prediction" block is REQUIRED on every step. It captures your expectation before execution — used for calibration.
- difficulty: how hard you expect this step to be
- failure_modes: 1-3 specific ways this step could fail
- success_criteria: one sentence describing what "done" looks like

## Examples
- "今天有什么新闻" → [{{"agent": "explorer", "instruction": "生成今日新闻简报", "tier": "light", "prediction": {{"difficulty": "easy", "failure_modes": ["feed fetch timeout"], "success_criteria": "briefing with 5+ items returned"}}}}]
- "写一篇关于AI的文章" → [{{"agent": "writer", "instruction": "写一篇600-800字的Substack文章，探讨AI的某个具体有趣角度，有独特观点", "tier": "heavy"}}]
- "写一个Hello World发到substack" → [{{"agent": "writer", "instruction": "写一篇简短的Hello World文章", "tier": "light"}}, {{"agent": "socialmedia", "instruction": "将上一步写好的文章发布到Substack", "tier": "light"}}]
- "分析一下AI agent市场" → [{{"agent": "analyst", "instruction": "分析2026年AI agent市场的竞争格局：主要玩家、市场份额估算、战略差异化点和近期趋势", "tier": "heavy"}}]
- "帮我去bhphotos上看看有什么好deal" → [{{"agent": "surfer", "instruction": "打开bhphotovideo.com的deals页面，提取当前打折商品", "tier": "light"}}]
- "帮我去这个网站填个表单" → [{{"agent": "surfer", "instruction": "打开指定网站，找到表单并填写", "tier": "light"}}]
- "每天早上9点给我发briefing" → [{{"agent": "general", "instruction": "用scheduler模块创建一个每天9点运行的定时任务", "tier": "light"}}]
- "你觉得AI会取代程序员吗" → [{{"agent": "discussion", "instruction": "用户想讨论AI是否会取代程序员", "tier": "heavy"}}]
- "今天怎么样" → [{{"agent": "discussion", "instruction": "用户在打招呼", "tier": "light"}}]
- "把自由意志那篇发到substack" → [{{"agent": "socialmedia", "instruction": "将'自由意志'文章发布到Substack", "tier": "light"}}]
- "帮我算一下税" → [{{"agent": "secret", "instruction": "帮用户计算税务（隐私模式，本地处理）", "tier": "light"}}]
- "帮我修这张照片" → [{{"agent": "photo", "instruction": "分析并修图", "tier": "light"}}]
- "证明这个定理" → [{{"agent": "researcher", "instruction": "证明用户给出的定理", "tier": "heavy"}}]

{conversation_context}

JSON:"""

    try:
        result = claude_think(prompt, timeout=20)
        if result:
            match = re.search(r'\[.*\]', result, re.DOTALL)
            if match:
                steps = json.loads(match.group())
                # Validate against schema
                from agent_registry import get_registry
                valid_agents = get_registry().get_valid_agents() | {"clarify"}  # clarify is special, not in registry
                validated = []
                for s in steps:
                    clean = _validate_plan_step(s, valid_agents)
                    if clean:
                        validated.append(clean)
                    else:
                        log.warning("Plan step failed schema validation: %s", s)
                if validated:
                    return validated
    except Exception as e:
        log.warning("Planning failed, falling back to general: %s", e)

    return [{"agent": "general", "instruction": content}]


def _synthesize_outputs(original_request: str, plan: list[dict],
                        final_output: str) -> str:
    """Synthesize the final output of a multi-step plan into a coherent response.

    Only called for multi-step plans where the last step's raw output
    may benefit from integration with the original request context.
    Skips synthesis if the last agent was publish (nothing to synthesize).
    """
    last_agent = plan[-1].get("agent", "")
    # No synthesis needed for publish/clarify — the output is the result
    if last_agent in ("publish", "clarify"):
        return ""

    # Also skip if the output is already short/clean (single-step feel)
    if len(final_output) < 200:
        return ""

    super_skills = _load_super_skills()
    synthesis_skill = ""
    if super_skills:
        # Extract just the Response Synthesis section for efficiency
        for block in super_skills.split("---"):
            if "Response Synthesis" in block:
                synthesis_skill = block.strip()
                break

    steps_summary = "; ".join(
        f"{s['agent']}: {s['instruction'][:60]}" for s in plan
    )

    prompt = f"""You are synthesizing the output of a multi-step agent plan into a single coherent response.

{synthesis_skill}

## Original user request
{original_request[:500]}

## Steps executed
{steps_summary}

## Final step output (the most complete output)
{final_output[:4000]}

## Your task
Apply the Response Synthesis skill: integrate this output into the clearest, most direct answer to the original request.
- Lead with what matters most
- Remove redundancy
- Add connective tissue between sections if needed
- Match the user's language
- Do NOT add meta-commentary like "I have completed the following steps..."

Synthesized response:"""

    result = claude_think(prompt, timeout=120, tier="light")
    return result or ""
