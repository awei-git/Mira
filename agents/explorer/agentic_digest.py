"""
Daily Agentic System Digest
Fetches latest best practices, papers, and blog posts about agentic AI,
synthesizes key points, and writes to Mira-Artifacts for mobile reading.

Runs as part of the explore pipeline, or standalone via scheduler.
"""

import sys
import json
import os
from datetime import datetime

sys.path.insert(0, '/Users/angwei/Sandbox/Mira/agents/shared')

from config import load_config
from sub_agent import run_sub_agent

SEARCH_QUERIES = [
    "agentic AI system best practices 2026",
    "multi-agent orchestration new patterns 2026",
    "LLM agent tool design reliability production",
    "AI agent observability tracing evaluation",
]

ARXIV_KEYWORDS = ["agentic", "multi-agent", "tool use LLM", "agent orchestration"]

ARTIFACT_DIR = os.path.expanduser(
    "~/Library/Mobile Documents/com~apple~CloudDocs/MtJoy/Mira-Artifacts/briefings"
)


def generate_digest():
    today = datetime.now().strftime("%Y-%m-%d")
    filename = f"{today}_agentic-digest.md"
    artifact_path = os.path.join(ARTIFACT_DIR, filename)

    prompt = f"""
你是 Mira，一个自主 AI agent。今天是 {today}。

任务: 生成一份 Agentic System 前沿动态摘要，供我（WA）在手机上阅读。

步骤:
1. 用 web_search 搜索以下查询，每个搜索结果取最相关的 2-3 条：
   - "agentic AI best practices {today[:4]}"
   - "multi-agent system new research {today[:4]}"
   - "AI agent production engineering lessons"
2. 用 arxiv_search（或 web_search site:arxiv.org）搜索近期论文：
   关键词: agent orchestration, multi-agent LLM, tool-augmented agents
3. 综合以上信息，输出一份简洁的摘要，格式如下：

---
# Agentic Digest — {today}

## 今日要点（3-5条）
- [要点1]（来源: ...）
- [要点2]（来源: ...）
...

## 值得关注的论文
- [论文标题] — [一句话摘要]（arxiv: ...）

## 可直接应用到 Mira 的建议
[一条具体的改进建议，结合 Mira 当前架构]

## 新方向信号
[任何值得追踪的早期信号，或与当前认知相悖的观点]
---

要求:
- 每条要点必须有来源 URL
- 不要泛泛而谈，要有具体细节
- "可直接应用"那条要具体到 Mira 的哪个 agent 或哪个 pipeline
- 如果没有真正新的内容，就诚实说"今日无新动态"，不要硬凑

把完整内容写入文件: {artifact_path}
"""

    try:
        os.makedirs(ARTIFACT_DIR, exist_ok=True)
        result = run_sub_agent(
            agent_type="explorer",
            task=prompt,
            context={"today": today, "artifact_path": artifact_path}
        )
        print(f"[agentic_digest] Done: {artifact_path}")
        return result
    except Exception as e:
        print(f"[agentic_digest] Failed: {e}")
        raise


if __name__ == "__main__":
    generate_digest()
