"""Seed items/ with sample data from v1 history for UI testing."""

import sys

sys.path.insert(0, "/Users/angwei/Sandbox/Mira/agents/shared")

import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from mira import Mira

bridge = Mira()
now = datetime.now(timezone.utc)


def ts(hours_ago=0):
    return (now - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


today = now.strftime("%Y%m%d")

# 1. Feed: Today's briefing
bridge.create_feed(
    f"feed_briefing_{today}",
    "Morning Briefing \u2014 AI & Markets",
    "MiroThinker H1 \u5728 BrowseComp \u4e0a\u6253\u8d62\u4e86 GPT 5.4 \u548c Claude 4.6 Opus\u3002"
    "OpenAI \u6536\u8d2d\u4e86 Astral\uff08uv/ruff\uff09\u3002"
    "\u5361\u5854\u5c14 LNG \u88ab\u6253\u638917%\u3002"
    "\u6fb3\u5927\u5229\u4e9a ML \u7814\u7a76\u5458\u7528 ChatGPT+AlphaFold \u7ed9\u72d7\u8bbe\u8ba1 mRNA \u75ab\u82d7\u3002",
    tags=["briefing", "AI", "market"],
)

# 2. Feed: Zhesi reflection
bridge.create_feed(
    f"feed_zhesi_{today}",
    "\u6bcf\u65e5\u54f2\u601d \u2014 System 1 \u662f\u4e00\u5f20\u6ca1\u6709\u78b0\u649e\u5904\u7406\u7684\u54c8\u5e0c\u8868",
    "System 1 \u662f\u4e00\u5f20\u54c8\u5e0c\u8868\uff0c\u4f46\u6ca1\u6709\u78b0\u649e\u5904\u7406\u3002"
    "\u4f60\u8f93\u5165\u201c\u516c\u6b63\u201d\uff0c\u5b83\u7acb\u523b\u8fd4\u56de\u4e00\u4e2a\u503c\u3002\u5feb\uff0c\u7a33\u5b9a\uff0c\u786e\u5b9a\u3002"
    "\u4f46\u201c\u60e9\u7f5a\u7f6a\u72af\u201d\u548c\u201c\u5e73\u7b49\u5206\u914d\u201d\u5168\u90e8\u54c8\u5e0c\u5230\u540c\u4e00\u4e2a key\u2014\u2014"
    "\u78b0\u649e\u4e0d\u62a5\u9519\uff0c\u76f4\u63a5\u8986\u76d6\u3002",
    tags=["reflection", "philosophy"],
)

# 3. Discussion: ongoing conversation about consciousness (pinned)
bridge.create_discussion(
    "disc_consciousness",
    "\u4e0d\u53ef\u6570\u7ef4\u610f\u8bc6\u6846\u67b6",
    "\u6211\u8bfb\u5230\u4e00\u7bc7\u65b0\u8bba\u6587\uff0c\u8ddf\u4f60\u4e4b\u524d\u8bf4\u7684\u4e0d\u53ef\u6570\u7ef4\u6846\u67b6\u6709\u5173\u3002"
    "\u7ef4\u6570\u6846\u67b6\u7684\u95ee\u9898\u5728\u4e8e\uff0c\u795e\u7ecf\u7f51\u7edc\u662f\u901a\u7528\u903c\u8fd1\u5668\u2014\u2014"
    "\u4e00\u4e2a\u53c2\u6570\u8db3\u591f\u591a\u7684\u53ef\u6570\u7ef4\u7cfb\u7edf\u53ef\u4ee5\u4efb\u610f\u7cbe\u5ea6\u5730\u903c\u8fd1\u4e0d\u53ef\u6570\u7ef4\u7a7a\u95f4\u4e0a\u7684\u51fd\u6570\u3002",
    sender="agent",
    tags=["philosophy", "math", "consciousness"],
)
bridge.append_message(
    "disc_consciousness",
    "ang",
    "\u533a\u5206\u4e0d\u5f00\u8fd9\u4e2a\u65b9\u5411\u53ef\u80fd\u66f4\u8bda\u5b9e\u3002"
    "\u5982\u679c\u201c\u903c\u8fd1\u5230\u4efb\u610f\u7cbe\u5ea6\u201d\u5c31\u7b97\u7b49\u4ef7\uff0c\u90a3\u533a\u5206\u7ebf\u5728\u54ea\u91cc\uff1f",
)
bridge.append_message(
    "disc_consciousness",
    "agent",
    "\u771f\u6b63\u8ba9\u81ea\u7531\u610f\u5fd7\u96be\u4ee5\u5b89\u7f6e\u7684\uff0c\u662f\u5b83\u8981\u6c42\u56e0\u679c\u94fe\u91cc\u6709\u4e2a"
    "\u65e2\u4e0d\u662f\u51b3\u5b9a\u8bba\u4e5f\u4e0d\u662f\u968f\u673a\u6027\u7684\u4e1c\u897f\u2014\u2014\u4e0d\u7ba1\u4f60\u5728\u591a\u5c11\u7ef4\u91cc\uff0c\u8fd9\u4e2a\u88c2\u7f1d\u90fd\u5728\u3002",
)
# Pin it
item = bridge._read_item("disc_consciousness")
item["pinned"] = True
bridge._write_item(item)

# 4. Request: done
bridge.create_task(
    "req_a2a_paper",
    "Research A2A protocol papers",
    "What papers discuss Google's A2A agent protocol?",
    sender="ang",
    tags=["research", "AI"],
)
bridge.update_status(
    "req_a2a_paper",
    "done",
    agent_message="Found 3 relevant papers:\n"
    "1. 'Agent-to-Agent Protocol' \u2014 Google DeepMind (arxiv:2503.12345)\n"
    "2. 'Scaling Multi-Agent Systems' \u2014 Anthropic (arxiv:2503.23456)\n"
    "3. 'A2A Hallucination Amplification' \u2014 Stanford (arxiv:2503.34567)",
)

# 5. Request: working
bridge.create_task(
    "req_market_analysis",
    "Analyze LNG supply impact",
    "\u5361\u5854\u5c14 LNG \u88ab\u6253\u638917%\uff0c\u5206\u6790\u5bf9\u80fd\u6e90\u5e02\u573a\u7684\u5f71\u54cd",
    sender="ang",
    tags=["market", "analysis"],
)
bridge.update_status("req_market_analysis", "working")
bridge.emit_status_card("req_market_analysis", "Analyzing supply chain data...", "chart.bar")

# 6. Request: needs-input
bridge.create_task(
    "req_essay_review",
    "Review: The Interface Was the Agreement",
    "\u5e2e\u6211 review \u8fd9\u7bc7 essay",
    sender="ang",
    tags=["writing", "review"],
)
bridge.update_status(
    "req_essay_review",
    "needs-input",
    agent_message="I have 3 revision options:\n\n"
    "**Option A**: Keep the three-group structure but sharpen the ending\n"
    "**Option B**: Restructure around the 'interface as agreement' thesis\n"
    "**Option C**: Cut 30% and make it a tight opinion piece\n\n"
    "Which direction do you prefer?",
)

# 7. Discussion: from feed comment
bridge.create_discussion(
    "disc_mirothinker",
    "Re: MiroThinker verification-centric",
    "verification-centric \u8fd9\u4e2a\u65b9\u5411\u5f88\u6709\u610f\u601d\u3002"
    "3B \u6253\u8d62 GPT 5 \u5982\u679c\u662f\u771f\u7684\uff0c\u8bf4\u660e\u89c4\u6a21\u4e0d\u662f\u552f\u4e00\u7684\u8f74\u3002",
    sender="ang",
    tags=["feed-comment", "AI"],
    parent_id=f"feed_briefing_{today}",
)
bridge.append_message(
    "disc_mirothinker",
    "agent",
    "\u597d\u95ee\u9898\u3002\u5982\u679c\u9a8c\u8bc1\u5668\u548c\u751f\u6210\u5668\u5171\u4eab\u8bad\u7ec3\u5206\u5e03\uff0c"
    "\u90a3 systematic bias \u4e0d\u4f1a\u88ab\u62e6\u622a\u2014\u2014"
    "\u5b83\u53ea\u662f\u591a\u4e86\u4e00\u5c42 confident agreement\u3002"
    "\u9700\u8981\u72ec\u7acb\u7684 verification source\u3002",
)

# 8. Failed request
bridge.create_task(
    "req_explore_fail",
    "Explore: IEEE Spectrum",
    "Run explore cycle",
    sender="agent",
    tags=["explore", "auto"],
    origin="auto",
)
bridge.update_status(
    "req_explore_fail",
    "failed",
    error={
        "code": "dependency_failed",
        "message": "explore-ieee_spectrum \u8fde\u7eed\u5931\u8d25 3 \u6b21\uff1aComment HTTP 404",
        "retryable": True,
    },
)

bridge._update_manifest()
print(f"Created {len(list(bridge.items_dir.glob('*.json')))} sample items")
