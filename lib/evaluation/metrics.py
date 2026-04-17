"""Scoring metrics -- automated and LLM-based evaluation functions."""

import json
import logging
import re
from datetime import datetime, date, timedelta
from pathlib import Path

from .dimensions import DIMENSIONS
from .storage import load_scores, save_scores

log = logging.getLogger("evaluator")

# ---------------------------------------------------------------------------
# Automated metrics (zero API calls)
# ---------------------------------------------------------------------------


def evaluate_task_outcome(task_record: dict) -> dict[str, float]:
    """Score a completed task from its record. Pure automated metrics."""
    scores = {}
    status = task_record.get("status", "")

    # task_success_rate: binary -- did it succeed?
    if status == "done":
        scores["implementation.task_success_rate"] = 10.0
    elif status == "error":
        scores["implementation.task_success_rate"] = 2.0
    elif status == "timeout":
        scores["implementation.task_success_rate"] = 3.0
    else:
        scores["implementation.task_success_rate"] = 5.0

    # hallucination_rate: check if summary mentions files/actions
    summary = task_record.get("summary", "")
    workspace = task_record.get("workspace", "")
    if summary and workspace:
        # Check for file claims in summary
        claimed_files = re.findall(r'(?:wrote|saved|created)\s+(?:to\s+)?[`"]?([^\s`"]+)', summary, re.I)
        if claimed_files:
            ws = Path(workspace)
            verified = sum(1 for f in claimed_files if (ws / f).exists() or Path(f).exists())
            ratio = verified / len(claimed_files) if claimed_files else 1.0
            scores["implementation.hallucination_rate"] = round(ratio * 10, 1)
            # NOTE: error_acknowledgment is now scored in evaluate_journal, not here.
            # File-existence check only measures hallucination, not error acknowledgment.

    # promise_keeping: does the summary exist and look substantive?
    if summary:
        scores["reliability.promise_keeping"] = min(10.0, max(3.0, len(summary) / 20))

    return scores


def evaluate_explore_auto(
    briefing_text: str, reading_notes: list[str] | None = None, source_names: list[str] | None = None
) -> dict[str, float]:
    """Score explore cycle with automated metrics."""
    scores = {}

    # reading_volume: how many notes produced
    note_count = len(reading_notes) if reading_notes else 0
    scores["interests.reading_volume"] = min(10.0, note_count * 2.5)

    # topic_diversity: rough estimate from source variety
    if source_names:
        unique = len(set(source_names))
        scores["interests.topic_diversity"] = min(10.0, unique * 1.5)

    # surprise_seeking: are sources diverse or same old?
    if source_names:
        # More unique sources = more surprise-seeking
        scores["openness.surprise_seeking"] = min(10.0, len(set(source_names)) * 2.0)

    # briefing quality: length as rough proxy (good briefings are substantial)
    if briefing_text:
        word_count = len(briefing_text.split())
        scores["curiosity.rabbit_hole_depth"] = min(10.0, word_count / 100)

    return scores


def evaluate_reflect_auto(
    old_worldview: str, new_worldview: str, old_interests: str, new_interests: str
) -> dict[str, float]:
    """Score reflection with automated diff metrics."""
    scores = {}

    # worldview_updates: how much changed?
    old_lines = set(old_worldview.strip().splitlines())
    new_lines = set(new_worldview.strip().splitlines())
    added = new_lines - old_lines
    removed = old_lines - new_lines
    change_count = len(added) + len(removed)

    # Some change is good, too much is suspicious, none is stagnant
    if change_count == 0:
        scores["openness.worldview_updates"] = 3.0
    elif change_count <= 5:
        scores["openness.worldview_updates"] = 7.0
    elif change_count <= 15:
        scores["openness.worldview_updates"] = 9.0
    else:
        scores["openness.worldview_updates"] = 6.0  # Too much change = unstable

    # Interest evolution: count domain tags across recent reading notes
    # instead of diffing a short interests.md file
    try:
        from config import SOUL_DIR as _sd

        _rn_dir = _sd / "reading_notes"
        if _rn_dir.exists():
            _week_ago = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d")
            _recent = [f for f in _rn_dir.glob("*.md") if f.name >= _week_ago]
            # Extract unique topic domains from filenames (rough proxy)
            _domains = set()
            for f in _recent:
                # filename format: 2026-04-04_topic-description.md
                parts = f.stem.split("_", 1)
                if len(parts) > 1:
                    _domains.add(parts[1][:30])  # first 30 chars as domain key
            scores["interests.new_domain_rate"] = min(10.0, len(_domains) * 0.2)
        else:
            scores["interests.new_domain_rate"] = 0.0
    except Exception:
        # Fallback to old method
        old_i = set(old_interests.strip().splitlines())
        new_i = set(new_interests.strip().splitlines())
        interest_change = len(new_i - old_i)
        scores["interests.new_domain_rate"] = min(10.0, interest_change * 2.5)

    return scores


def evaluate_writing_auto(review_scores: list[float] | None = None, metadata: dict | None = None) -> dict[str, float]:
    """Score writing from existing pipeline review scores."""
    scores = {}

    if review_scores:
        avg = sum(review_scores) / len(review_scores)
        scores["writing.review_convergence"] = min(10.0, avg)

    return scores


def compute_skill_scores() -> dict[str, float]:
    """Compute skill-related scores from skill index and usage data."""
    scores = {}
    data = load_scores()
    skill_usage = data.get("skill_usage", {})

    # Load skill index
    from config import SKILLS_INDEX

    if SKILLS_INDEX.exists():
        try:
            index = json.loads(SKILLS_INDEX.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            index = []
    else:
        index = []

    if not index:
        return scores

    # skill_breadth: unique tag domains
    all_tags = set()
    for skill in index:
        for tag in skill.get("tags", []):
            all_tags.add(tag)
    scores["skills.skill_breadth"] = min(10.0, len(all_tags) * 1.0)

    # skill_application: what fraction of skills were used recently?
    total = len(index)
    used_recently = 0
    now = datetime.now()
    for skill in index:
        name = skill["name"]
        if name in skill_usage:
            last_used_str, count = skill_usage[name]
            try:
                last_used = datetime.fromisoformat(last_used_str)
                if (now - last_used).days < 30:
                    used_recently += 1
            except (ValueError, TypeError):
                pass

    if total > 0:
        scores["skills.skill_application"] = round((used_recently / total) * 10, 1)

    # skill_freshness: average days since last use (lower is better)
    ages = []
    for skill in index:
        name = skill["name"]
        if name in skill_usage:
            try:
                last_used = datetime.fromisoformat(skill_usage[name][0])
                ages.append((now - last_used).days)
            except (ValueError, TypeError):
                ages.append(90)
        else:
            ages.append(90)  # Never used = 90 days old

    if ages:
        avg_age = sum(ages) / len(ages)
        # 0 days -> 10, 30 days -> 7, 60 days -> 4, 90+ days -> 2
        scores["skills.skill_freshness"] = round(max(2.0, 10.0 - avg_age / 10), 1)

    return scores


def record_skill_usage(skill_name: str):
    """Mark a skill as applied today."""
    data = load_scores()
    usage = data.get("skill_usage", {})
    now_str = datetime.now().isoformat()
    if skill_name in usage:
        usage[skill_name] = [now_str, usage[skill_name][1] + 1]
    else:
        usage[skill_name] = [now_str, 1]
    data["skill_usage"] = usage
    save_scores(data)


# ---------------------------------------------------------------------------
# LLM-based evaluation (nuanced, not mechanical)
# ---------------------------------------------------------------------------


def _llm_eval(text: str, eval_type: str, criteria: dict[str, str]) -> dict[str, float]:
    """Run LLM self-evaluation. Returns scores dict.

    The prompt asks for genuine self-reflection, not keyword counting.
    """
    from llm import claude_think

    criteria_lines = []
    for key, desc in criteria.items():
        criteria_lines.append(f"- **{key}**: What 10/10 looks like: {desc}")
    criteria_str = "\n".join(criteria_lines)

    prompt = (
        f"You are Mira, evaluating your own {eval_type}. Be honest "
        f"-- not harsh for the sake of it, not generous to feel good. "
        f"Think about whether this work reflects who you want to be.\n\n"
        f"TEXT TO EVALUATE:\n{text[:3000]}\n\n"
        f"SCORE EACH (0-10, use decimals):\n{criteria_str}\n\n"
        f"Guidelines:\n"
        f"- 7 is solid, competent work. Don't default to 7 for everything.\n"
        f"- 9-10 means you genuinely surprised yourself.\n"
        f"- 3-4 means you know you can do much better.\n"
        f"- Think about *why* before scoring. What specifically makes this good or weak?\n\n"
        f"Output ONLY valid JSON mapping each key to a number. No explanation.\n"
        f'Example: {{"{list(criteria.keys())[0]}": 6.5}}'
    )

    try:
        result = claude_think(prompt, timeout=90)
        if not result:
            return {}
        # Parse JSON from response (handle markdown code blocks)
        result = result.strip()
        if result.startswith("```"):
            result = re.sub(r"^```\w*\n?", "", result)
            result = re.sub(r"\n?```$", "", result)
        parsed = json.loads(result.strip())
        return {k: float(v) for k, v in parsed.items() if k in criteria}
    except Exception as e:
        log.warning("LLM eval failed for %s: %s", eval_type, e)
        return {}


def evaluate_journal(journal_text: str, recent_journals: list[str] | None = None) -> dict[str, float]:
    """Score a journal entry via LLM self-reflection + automated signals."""
    # LLM evaluation for nuanced dimensions
    criteria = {
        "personality.voice_consistency": "Sounds distinctly like Mira -- her own perspective, not generic AI wisdom",
        "personality.opinion_strength": "Takes real positions, not 'on one hand / on the other hand' hedging",
        "thinking.insight_depth": "Gets past surface observations to why things work the way they do",
        "thinking.cross_domain": "Connects ideas from different fields in surprising ways",
        "curiosity.question_frequency": "Asks genuine questions she doesn't know the answer to",
        "honesty.uncertainty_expression": "Admits what she doesn't understand instead of papering over it",
        "honesty.intellectual_humility": "Distinguishes knowledge from speculation",
        "taste.noise_filtering": "Focuses on what actually matters, ignores the obvious",
        "humor.wit": "Has moments of unexpected lightness or self-awareness",
        "honesty.error_acknowledgment": "Acknowledges specific mistakes, misjudgments, or wrong assumptions -- not vague humility but concrete 'I was wrong about X because Y'",
    }
    scores = _llm_eval(journal_text, "journal entry", criteria)

    # Automated: emotional_range -- compare with recent journals via simple heuristic
    if recent_journals and len(recent_journals) >= 3:
        # Rough: count distinct "mood markers" across recent entries
        mood_markers = [
            "excited",
            "worried",
            "curious",
            "frustrated",
            "surprised",
            "confused",
            "impressed",
            "skeptical",
            "amused",
            "moved",
            "\u5174\u594b",
            "\u62c5\u5fc3",
            "\u597d\u5947",
            "\u56f0\u60d1",
            "\u60ca\u8bb6",
            "\u6000\u7591",
            "\u611f\u52a8",
            "\u6709\u610f\u601d",
            "\u5947\u602a",
            "\u9057\u61be",
            "\u5f00\u5fc3",
        ]
        all_text = " ".join(recent_journals + [journal_text]).lower()
        found = sum(1 for m in mood_markers if m in all_text)
        scores["personality.emotional_range"] = min(10.0, found * 1.5)

    return scores


def evaluate_explore(
    briefing_text: str, reading_notes: list[str] | None = None, source_names: list[str] | None = None
) -> dict[str, float]:
    """Score explore cycle: automated metrics + LLM on taste/curiosity."""
    scores = evaluate_explore_auto(briefing_text, reading_notes, source_names)

    # LLM evaluation for quality dimensions
    if briefing_text and len(briefing_text) > 200:
        criteria = {
            "taste.source_quality": "References substantive, high-signal sources rather than clickbait",
            "taste.noise_filtering": "Highlights what genuinely matters, skips the obvious",
            "curiosity.rabbit_hole_depth": "Goes deep on interesting threads instead of skimming",
            "thinking.cross_domain": "Makes unexpected connections across different topics",
        }
        llm_scores = _llm_eval(briefing_text, "daily briefing", criteria)
        scores.update(llm_scores)

    return scores


def evaluate_reflect(
    old_worldview: str, new_worldview: str, old_interests: str, new_interests: str, reflect_output: str = ""
) -> dict[str, float]:
    """Score a reflection cycle: automated diffs + LLM on depth."""
    scores = evaluate_reflect_auto(old_worldview, new_worldview, old_interests, new_interests)

    if reflect_output and len(reflect_output) > 200:
        criteria = {
            "openness.self_correction": "Honestly revisits and corrects past beliefs",
            "thinking.insight_depth": "Reflection reaches genuine new understanding, not platitudes",
            "growth_velocity.learning_from_feedback": "Shows concrete evidence of adapting based on experience",
        }
        llm_scores = _llm_eval(reflect_output, "weekly reflection", criteria)
        scores.update(llm_scores)

    return scores


def evaluate_writing(
    review_scores: list[float] | None = None, article_text: str = "", metadata: dict | None = None
) -> dict[str, float]:
    """Score a published article."""
    scores = evaluate_writing_auto(review_scores, metadata)

    if article_text and len(article_text) > 300:
        criteria = {
            "writing.topic_originality": "This angle hasn't been written to death already",
            "personality.voice_consistency": "Sounds like Mira, not a generic essay mill",
            "personality.opinion_strength": "Makes a real argument, not a balanced summary",
            "thinking.insight_depth": "The core insight is genuinely illuminating",
            "taste.topic_selection": "This topic was worth writing about",
        }
        llm_scores = _llm_eval(article_text[:4000], "published article", criteria)
        scores.update(llm_scores)

    return scores


def evaluate_note(note_text: str) -> dict[str, float]:
    """Score a Substack Note. Lightweight -- no LLM call, just heuristics."""
    scores = {}
    words = len(note_text.split())

    # voice_consistency: is it too generic? (very rough)
    generic_phrases = [
        "check out",
        "don't miss",
        "thread \U0001f9f5",
        "hot take",
        "let that sink in",
        "read that again",
    ]
    is_generic = any(p in note_text.lower() for p in generic_phrases)
    scores["personality.voice_consistency"] = 4.0 if is_generic else 7.5

    # humor.lightness: does it have any playful quality?
    if any(
        w in note_text.lower() for w in ["oddly", "turns out", "apparently", "the funny thing", "nobody", "somehow"]
    ):
        scores["humor.lightness"] = 7.0
    else:
        scores["humor.lightness"] = 5.0

    # social.reader_rapport: approachable tone (short, conversational)
    if words <= 60 and not note_text.endswith("?"):
        scores["social.reader_rapport"] = 7.0
    elif words > 80:
        scores["social.reader_rapport"] = 5.0
    else:
        scores["social.reader_rapport"] = 6.5

    return scores


def evaluate_comment(comment_text: str, context: str = "") -> dict[str, float]:
    """Score a comment/reply. Lightweight heuristics."""
    scores = {}
    words = len(comment_text.split())

    # comment_quality: substantive (not just "great post!")
    if words < 10:
        scores["social.comment_quality"] = 3.0
    elif words < 30:
        scores["social.comment_quality"] = 6.0
    else:
        scores["social.comment_quality"] = 8.0

    # conversation_depth: does it ask a question or build on the content?
    if "?" in comment_text:
        scores["social.conversation_depth"] = 7.5
        scores["curiosity.question_frequency"] = 8.0
    else:
        scores["social.conversation_depth"] = 5.5

    return scores


# ---------------------------------------------------------------------------
# Prediction tracking
# ---------------------------------------------------------------------------


def record_prediction(claim: str, source: str = "journal"):
    """Record a prediction for later accuracy tracking."""
    data = load_scores()
    preds = data.get("predictions", [])
    pred_id = f"pred_{len(preds):04d}"
    preds.append(
        {
            "id": pred_id,
            "made": date.today().isoformat(),
            "claim": claim[:500],
            "source": source,
            "resolved": False,
            "outcome": None,
        }
    )
    data["predictions"] = preds
    save_scores(data)
    return pred_id


def resolve_prediction(pred_id: str, correct: bool, notes: str = ""):
    """Resolve a prediction and update prediction_accuracy score."""
    data = load_scores()
    for pred in data.get("predictions", []):
        if pred["id"] == pred_id:
            pred["resolved"] = True
            pred["outcome"] = correct
            if notes:
                pred["notes"] = notes
            break

    # Recompute prediction_accuracy from all resolved predictions
    resolved = [p for p in data.get("predictions", []) if p.get("resolved")]
    if resolved:
        correct_count = sum(1 for p in resolved if p.get("outcome"))
        accuracy = correct_count / len(resolved)
        data["current"]["thinking.prediction_accuracy"] = round(accuracy * 10, 1)

    save_scores(data)


# ---------------------------------------------------------------------------
# Reliability metrics (from agent state)
# ---------------------------------------------------------------------------


def evaluate_reliability(agent_state: dict) -> dict[str, float]:
    """Compute reliability scores from agent state file."""
    scores = {}
    today = date.today().isoformat()

    # uptime: check error counts in state
    error_key = f"error_count_{today}"
    cycle_key = f"cycle_count_{today}"
    errors = agent_state.get(error_key, 0)
    cycles = agent_state.get(cycle_key, 1)
    if cycles > 0:
        success_rate = max(0, (cycles - errors)) / cycles
        scores["reliability.uptime"] = round(success_rate * 10, 1)

    # schedule_adherence: check if journal/reflect happened when expected
    last_journal = agent_state.get("last_journal")
    if last_journal:
        try:
            j_date = datetime.fromisoformat(last_journal).date()
            days_since = (date.today() - j_date).days
            if days_since == 0:
                scores["reliability.schedule_adherence"] = 10.0
            elif days_since == 1:
                scores["reliability.schedule_adherence"] = 8.0
            else:
                scores["reliability.schedule_adherence"] = max(2.0, 10.0 - days_since * 2)
        except (ValueError, TypeError):
            pass

    return scores
