"""Layer 3: Strategy Mutation.

Weekly: compare reward trends across two weeks, propose a concrete A/B test
for any declining dimension. Evaluate last week's variant against baseline.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, date

from evolution.config import VARIANT_DIR
from evolution.experience import load_experiences

log = logging.getLogger("mira.evolution")


def propose_strategy_variant(dimension: str = "", user_id: str = "ang") -> dict | None:
    """Analyze reward trends and propose a strategy change for A/B testing.

    Called from weekly reflect. Compares this week vs last week.
    Returns variant dict if a change is warranted, None otherwise.
    """
    this_week = load_experiences(days=7)
    last_week_all = load_experiences(days=14)
    last_week = [e for e in last_week_all if e not in this_week]

    if len(this_week) < 5 or len(last_week) < 5:
        log.info("propose_strategy_variant: not enough data (this=%d, last=%d)",
                 len(this_week), len(last_week))
        return None

    def avg_score(entries, agent_filter=""):
        filtered = [e for e in entries if not agent_filter or e.get("agent") == agent_filter]
        if not filtered:
            return 0.0
        return sum(e.get("score", 0) for e in filtered) / len(filtered)

    # Find dimensions where score is declining
    agents = set(e.get("agent", "") for e in this_week + last_week if e.get("agent"))
    declining = []
    for agent in agents:
        this_avg = avg_score(this_week, agent)
        last_avg = avg_score(last_week, agent)
        if last_avg > 0 and this_avg < last_avg * 0.7:
            declining.append({
                "agent": agent,
                "this_week": round(this_avg, 2),
                "last_week": round(last_avg, 2),
                "drop": round((last_avg - this_avg) / last_avg * 100, 1),
            })

    if not declining and not dimension:
        log.info("propose_strategy_variant: no declining dimensions")
        return None

    decline_text = ""
    if declining:
        decline_text = "## 下降的维度\n" + "\n".join(
            f"- {d['agent']}: {d['last_week']:+.1f} -> {d['this_week']:+.1f} (下降 {d['drop']}%)"
            for d in declining
        )

    top_pos = sorted(this_week, key=lambda x: -x.get("score", 0))[:5]
    top_neg = sorted(this_week, key=lambda x: x.get("score", 0))[:5]

    context_text = "## 本周最好的经验\n" + "\n".join(
        f"- {e['action'][:80]} (score: {e.get('score', 0):+.1f})" for e in top_pos
    )
    context_text += "\n\n## 本周最差的经验\n" + "\n".join(
        f"- {e['action'][:80]} (score: {e.get('score', 0):+.1f})" for e in top_neg
    )

    prompt = f"""你是 Mira, 正在分析自己过去两周的表现趋势.

{decline_text}

{context_text}

请提出一个具体的策略变更建议, 可以在下周 A/B 测试:

## 要求
- 变更必须是具体可执行的 (不是 "写得更好", 而是 "每条 note 开头用一个反直觉的判断")
- 说明什么不变 (对照组) 和什么变 (实验组)
- 预测这个变更的预期效果
- 只提出一个变更, 不要多个

## 输出格式 (JSON)
{{"dimension": "变更的维度", "control": "当前做法", "variant": "新做法", "hypothesis": "预期效果", "metric": "用什么指标衡量", "duration_days": 7}}"""

    try:
        from sub_agent import model_think
        result = model_think(prompt, model_name="omlx", timeout=90)
    except Exception as e:
        log.warning("propose_strategy_variant: LLM call failed: %s", e)
        return None

    if not result:
        return None

    try:
        json_match = re.search(r'\{[^}]+\}', result, re.DOTALL)
        if json_match:
            variant = json.loads(json_match.group())
            variant["proposed_at"] = datetime.now().isoformat(timespec="seconds")
            variant["status"] = "proposed"

            VARIANT_DIR.mkdir(parents=True, exist_ok=True)
            variant_id = f"{date.today().isoformat()}_{variant.get('dimension', 'unknown')[:20]}"
            variant["id"] = variant_id
            variant_path = VARIANT_DIR / f"{variant_id}.json"
            variant_path.write_text(
                json.dumps(variant, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            log.info("propose_strategy_variant: proposed '%s'", variant_id)
            return variant
    except (json.JSONDecodeError, OSError) as e:
        log.warning("propose_strategy_variant: failed to parse/save: %s", e)

    return None


def evaluate_variant(variant_id: str) -> dict | None:
    """Evaluate a previously proposed variant against its baseline.

    Compares experiences during the variant period with the control metric.
    """
    variant_path = VARIANT_DIR / f"{variant_id}.json"
    if not variant_path.exists():
        return None

    try:
        variant = json.loads(variant_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    duration = variant.get("duration_days", 7)
    experiences = load_experiences(days=duration)

    if len(experiences) < 3:
        return {"status": "insufficient_data", "variant_id": variant_id}

    avg_score = sum(e.get("score", 0) for e in experiences) / len(experiences)

    result = {
        "variant_id": variant_id,
        "status": "evaluated",
        "avg_score": round(avg_score, 2),
        "experience_count": len(experiences),
        "evaluated_at": datetime.now().isoformat(timespec="seconds"),
    }

    variant["evaluation"] = result
    variant["status"] = "evaluated"
    try:
        variant_path.write_text(
            json.dumps(variant, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass

    return result
