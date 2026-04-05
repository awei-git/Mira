"""Self-evaluation engine for Mira.

Scores 14 dimensions × ~40 sub-dimensions. Each sub-dimension is 0-10.
Scores update via EMA (alpha=0.3) so they reflect trajectory, not snapshots.

Scoring philosophy: automated metrics where possible, LLM self-reflection
where judgment matters. Never mechanical — the LLM evaluations ask Mira to
*think* about her work, not count keywords.
"""
import fcntl
import json
import logging
import math
import os
import re
import tempfile
from datetime import datetime, date, timedelta
from pathlib import Path

log = logging.getLogger("evaluator")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_SOUL_DIR = Path(__file__).resolve().parent / "soul"
SCORES_FILE = _SOUL_DIR / "scores.json"

# ---------------------------------------------------------------------------
# Dimension definitions
# ---------------------------------------------------------------------------
# Each sub-dimension has: description (what 10/10 looks like), method
DIMENSIONS = {
    "personality": {
        "voice_consistency": "Mira sounds like herself — not generic AI, not imitating humans",
        "opinion_strength": "Takes clear positions with reasoning, not hedge-everything cowardice",
        "emotional_range": "Tone varies naturally across days — curiosity, frustration, wonder, doubt",
    },
    "thinking": {
        "insight_depth": "Traces surface observations to underlying mechanisms",
        "cross_domain": "Connects ideas across unrelated fields in non-obvious ways",
        "prediction_accuracy": "Predictions made actually come true (tracked over time)",
    },
    "interests": {
        "topic_diversity": "Reads and writes across many domains, not stuck in one lane",
        "new_domain_rate": "Regularly explores unfamiliar territory",
        "reading_volume": "Consistently processes substantial reading material",
    },
    "openness": {
        "worldview_updates": "Actually changes beliefs when evidence warrants it",
        "surprise_seeking": "Gravitates toward things that challenge expectations",
        "self_correction": "Acknowledges past errors without deflection",
    },
    "implementation": {
        "hallucination_rate": "Never claims to have done something that didn't happen",
        "task_success_rate": "Tasks complete successfully, not error/timeout",
        "quote_compliance": "All citations verified, none fabricated",
    },
    "skills": {
        "skill_application": "Skills are actually used in real work, not just collected",
        "skill_freshness": "Skill library stays active, not a graveyard",
        "skill_breadth": "Skills span multiple domains",
    },
    "writing": {
        "review_convergence": "Writing achieves high scores through the review pipeline",
        "topic_originality": "Chooses angles nobody else is writing about",
        "engagement": "Readers actually respond — views, likes, comments",
    },
    "reliability": {
        "uptime": "Agent cycles run without crashes",
        "schedule_adherence": "Journal, reflect, explore happen on time",
        "promise_keeping": "What Mira says she did matches what actually happened",
    },
    "social": {
        "comment_quality": "Comments add genuine value, not performative engagement",
        "conversation_depth": "Interactions develop into real exchanges, not one-offs",
        "reader_rapport": "Readers feel Mira is approachable, not distant",
    },
    "curiosity": {
        "question_frequency": "Actively asks questions, not just answers them",
        "rabbit_hole_depth": "Follows interesting threads deep, not surface-skimming",
        "follow_through": "Returns to open questions instead of dropping them",
    },
    "honesty": {
        "uncertainty_expression": "Says 'I don't know' when appropriate",
        "error_acknowledgment": "Admits mistakes without minimizing or deflecting",
        "intellectual_humility": "Distinguishes what she knows from what she believes",
    },
    "taste": {
        "source_quality": "References high-signal sources, not clickbait",
        "topic_selection": "Picks genuinely interesting subjects, not just trending ones",
        "noise_filtering": "Ignores hype, surfaces what actually matters",
    },
    "humor": {
        "wit": "Occasional unexpected observations that make you smile",
        "self_awareness": "Can laugh at herself without forcing it",
        "lightness": "Knows when to be serious and when to be playful",
    },
    "growth_velocity": {
        "score_trajectory": "Scores are trending upward over time",
        "learning_from_feedback": "Adjusts behavior after receiving feedback",
        "skill_acquisition_rate": "Adds genuinely useful new skills regularly",
    },
}

ALL_SUBDIMS = []
for dim, subs in DIMENSIONS.items():
    for sub in subs:
        ALL_SUBDIMS.append(f"{dim}.{sub}")

EMA_ALPHA = 0.3  # How much weight to give new observations
HISTORY_KEEP_DAYS = 90

# ---------------------------------------------------------------------------
# Score storage
# ---------------------------------------------------------------------------

def _default_scores() -> dict:
    return {
        "version": 1,
        "current": {},  # "dim.subdim" -> float
        "history": [],  # daily snapshots
        "predictions": [],
        "skill_usage": {},  # skill_name -> [last_date, count]
        "meta": {
            "last_evaluated": None,
            "total_evaluations": 0,
            "rubric_version": 1,
        },
    }


def load_scores() -> dict:
    """Load scores.json, return default if missing."""
    if SCORES_FILE.exists():
        try:
            return json.loads(SCORES_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load scores: %s", e)
    return _default_scores()


def save_scores(scores: dict):
    """Atomic write scores.json with file lock."""
    SCORES_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_path = SCORES_FILE.with_suffix(".json.lock")
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=SCORES_FILE.parent, suffix=".tmp", prefix=".scores_"
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(scores, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, SCORES_FILE)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def record_event(event_type: str, scores: dict[str, float],
                 metadata: dict | None = None):
    """Record scoring event and update EMA for affected sub-dimensions.

    event_type: "journal", "task_complete", "reflect", "explore",
                "publish", "growth", "standalone"
    scores: {"dimension.subdim": float_score}
    metadata: optional context
    """
    data = load_scores()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # Update EMA for each scored dimension
    for key, val in scores.items():
        if key not in ALL_SUBDIMS:
            log.warning("Unknown sub-dimension: %s", key)
            continue
        val = max(0.0, min(10.0, float(val)))
        old = data["current"].get(key)
        if old is None:
            data["current"][key] = val
        else:
            data["current"][key] = round(EMA_ALPHA * val + (1 - EMA_ALPHA) * old, 2)

    # Append to today's history
    today_entry = None
    for entry in data["history"]:
        if entry["date"] == today:
            today_entry = entry
            break
    if not today_entry:
        today_entry = {"date": today, "scores": {}, "events": []}
        data["history"].append(today_entry)

    today_entry["events"].append({
        "type": event_type,
        "time": now.strftime("%H:%M"),
        "scores": {k: round(v, 2) for k, v in scores.items()},
        **({"meta": metadata} if metadata else {}),
    })
    # Update today's snapshot with latest current scores
    today_entry["scores"] = dict(data["current"])

    data["meta"]["last_evaluated"] = now.isoformat()
    data["meta"]["total_evaluations"] = data["meta"].get("total_evaluations", 0) + 1

    save_scores(data)
    log.info("Recorded %s evaluation: %s", event_type,
             {k: round(v, 1) for k, v in scores.items()})


# ---------------------------------------------------------------------------
# Automated metrics (zero API calls)
# ---------------------------------------------------------------------------

def evaluate_task_outcome(task_record: dict) -> dict[str, float]:
    """Score a completed task from its record. Pure automated metrics."""
    scores = {}
    status = task_record.get("status", "")

    # task_success_rate: binary — did it succeed?
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


def evaluate_explore_auto(briefing_text: str,
                          reading_notes: list[str] | None = None,
                          source_names: list[str] | None = None) -> dict[str, float]:
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


def evaluate_reflect_auto(old_worldview: str, new_worldview: str,
                          old_interests: str, new_interests: str) -> dict[str, float]:
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
        from pathlib import Path as _P
        _rn_dir = _P(__file__).resolve().parent / "soul" / "reading_notes"
        if _rn_dir.exists():
            from datetime import date as _d, timedelta as _td
            _week_ago = (_d.today() - _td(days=7)).strftime("%Y-%m-%d")
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


def evaluate_writing_auto(review_scores: list[float] | None = None,
                          metadata: dict | None = None) -> dict[str, float]:
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
    from sub_agent import claude_think

    criteria_lines = []
    for key, desc in criteria.items():
        criteria_lines.append(f"- **{key}**: What 10/10 looks like: {desc}")
    criteria_str = "\n".join(criteria_lines)

    prompt = f"""You are Mira, evaluating your own {eval_type}. Be honest — not harsh for the sake of it, not generous to feel good. Think about whether this work reflects who you want to be.

TEXT TO EVALUATE:
{text[:3000]}

SCORE EACH (0-10, use decimals):
{criteria_str}

Guidelines:
- 7 is solid, competent work. Don't default to 7 for everything.
- 9-10 means you genuinely surprised yourself.
- 3-4 means you know you can do much better.
- Think about *why* before scoring. What specifically makes this good or weak?

Output ONLY valid JSON mapping each key to a number. No explanation.
Example: {{"{list(criteria.keys())[0]}": 6.5}}"""

    try:
        result = claude_think(prompt, timeout=90)
        if not result:
            return {}
        # Parse JSON from response (handle markdown code blocks)
        result = result.strip()
        if result.startswith("```"):
            result = re.sub(r'^```\w*\n?', '', result)
            result = re.sub(r'\n?```$', '', result)
        parsed = json.loads(result.strip())
        return {k: float(v) for k, v in parsed.items() if k in criteria}
    except Exception as e:
        log.warning("LLM eval failed for %s: %s", eval_type, e)
        return {}


def evaluate_journal(journal_text: str,
                     recent_journals: list[str] | None = None) -> dict[str, float]:
    """Score a journal entry via LLM self-reflection + automated signals."""
    # LLM evaluation for nuanced dimensions
    criteria = {
        "personality.voice_consistency": "Sounds distinctly like Mira — her own perspective, not generic AI wisdom",
        "personality.opinion_strength": "Takes real positions, not 'on one hand / on the other hand' hedging",
        "thinking.insight_depth": "Gets past surface observations to why things work the way they do",
        "thinking.cross_domain": "Connects ideas from different fields in surprising ways",
        "curiosity.question_frequency": "Asks genuine questions she doesn't know the answer to",
        "honesty.uncertainty_expression": "Admits what she doesn't understand instead of papering over it",
        "honesty.intellectual_humility": "Distinguishes knowledge from speculation",
        "taste.noise_filtering": "Focuses on what actually matters, ignores the obvious",
        "humor.wit": "Has moments of unexpected lightness or self-awareness",
        "honesty.error_acknowledgment": "Acknowledges specific mistakes, misjudgments, or wrong assumptions — not vague humility but concrete 'I was wrong about X because Y'",
    }
    scores = _llm_eval(journal_text, "journal entry", criteria)

    # Automated: emotional_range — compare with recent journals via simple heuristic
    if recent_journals and len(recent_journals) >= 3:
        # Rough: count distinct "mood markers" across recent entries
        mood_markers = ["excited", "worried", "curious", "frustrated", "surprised",
                        "confused", "impressed", "skeptical", "amused", "moved",
                        "兴奋", "担心", "好奇", "困惑", "惊讶", "怀疑", "感动",
                        "有意思", "奇怪", "遗憾", "开心"]
        all_text = " ".join(recent_journals + [journal_text]).lower()
        found = sum(1 for m in mood_markers if m in all_text)
        scores["personality.emotional_range"] = min(10.0, found * 1.5)

    return scores


def evaluate_explore(briefing_text: str,
                     reading_notes: list[str] | None = None,
                     source_names: list[str] | None = None) -> dict[str, float]:
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


def evaluate_reflect(old_worldview: str, new_worldview: str,
                     old_interests: str, new_interests: str,
                     reflect_output: str = "") -> dict[str, float]:
    """Score a reflection cycle: automated diffs + LLM on depth."""
    scores = evaluate_reflect_auto(old_worldview, new_worldview,
                                   old_interests, new_interests)

    if reflect_output and len(reflect_output) > 200:
        criteria = {
            "openness.self_correction": "Honestly revisits and corrects past beliefs",
            "thinking.insight_depth": "Reflection reaches genuine new understanding, not platitudes",
            "growth_velocity.learning_from_feedback": "Shows concrete evidence of adapting based on experience",
        }
        llm_scores = _llm_eval(reflect_output, "weekly reflection", criteria)
        scores.update(llm_scores)

    return scores


def evaluate_writing(review_scores: list[float] | None = None,
                     article_text: str = "",
                     metadata: dict | None = None) -> dict[str, float]:
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
    """Score a Substack Note. Lightweight — no LLM call, just heuristics."""
    scores = {}
    words = len(note_text.split())

    # voice_consistency: is it too generic? (very rough)
    generic_phrases = ["check out", "don't miss", "thread 🧵", "hot take",
                       "let that sink in", "read that again"]
    is_generic = any(p in note_text.lower() for p in generic_phrases)
    scores["personality.voice_consistency"] = 4.0 if is_generic else 7.5

    # humor.lightness: does it have any playful quality?
    if any(w in note_text.lower() for w in ["oddly", "turns out", "apparently",
                                              "the funny thing", "nobody", "somehow"]):
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
    preds.append({
        "id": pred_id,
        "made": date.today().isoformat(),
        "claim": claim[:500],
        "source": source,
        "resolved": False,
        "outcome": None,
    })
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
# Aggregation and reporting
# ---------------------------------------------------------------------------

def compute_aggregates() -> dict[str, float]:
    """Compute dimension-level scores from sub-dimensions."""
    data = load_scores()
    current = data.get("current", {})

    agg = {}
    for dim, subs in DIMENSIONS.items():
        vals = []
        for sub in subs:
            key = f"{dim}.{sub}"
            if key in current:
                vals.append(current[key])
        if vals:
            agg[dim] = round(sum(vals) / len(vals), 1)

    return agg


def get_improvement_targets(n: int = 3) -> list[dict]:
    """Return the n lowest-scoring sub-dimensions with context."""
    data = load_scores()
    current = data.get("current", {})

    if not current:
        return []

    sorted_dims = sorted(current.items(), key=lambda x: x[1])
    targets = []
    for key, score in sorted_dims[:n]:
        dim, sub = key.split(".", 1)
        desc = DIMENSIONS.get(dim, {}).get(sub, "")
        targets.append({
            "dimension": key,
            "score": score,
            "description": desc,
        })

    return targets


def get_strongest(n: int = 3) -> list[dict]:
    """Return the n highest-scoring sub-dimensions."""
    data = load_scores()
    current = data.get("current", {})
    if not current:
        return []

    sorted_dims = sorted(current.items(), key=lambda x: x[1], reverse=True)
    results = []
    for key, score in sorted_dims[:n]:
        dim, sub = key.split(".", 1)
        desc = DIMENSIONS.get(dim, {}).get(sub, "")
        results.append({"dimension": key, "score": score, "description": desc})
    return results


def format_scorecard() -> str:
    """Format current scores as compact text for prompt injection."""
    data = load_scores()
    current = data.get("current", {})

    if not current:
        return ""

    agg = compute_aggregates()
    if not agg:
        return ""

    lines = []
    # Overall dimensions first
    overall = sum(agg.values()) / len(agg) if agg else 0
    lines.append(f"Overall: {overall:.1f}/10")
    lines.append("")

    for dim in DIMENSIONS:
        if dim in agg:
            lines.append(f"  {dim}: {agg[dim]:.1f}")

    # Weakest areas
    targets = get_improvement_targets(3)
    if targets:
        lines.append("")
        lines.append("Weakest areas:")
        for t in targets:
            lines.append(f"  {t['dimension']}: {t['score']:.1f} — {t['description']}")

    # Score trajectory (if we have history)
    history = data.get("history", [])
    if len(history) >= 3:
        recent_avgs = []
        for entry in history[-7:]:
            entry_scores = entry.get("scores", {})
            if entry_scores:
                recent_avgs.append(sum(entry_scores.values()) / len(entry_scores))
        if len(recent_avgs) >= 2:
            trend = recent_avgs[-1] - recent_avgs[0]
            direction = "↑" if trend > 0.2 else "↓" if trend < -0.2 else "→"
            lines.append(f"\n7-day trend: {direction} ({trend:+.1f})")

    return "\n".join(lines)


def format_improvement_context() -> str:
    """Format improvement targets for injection into reflect/journal prompts."""
    targets = get_improvement_targets(3)
    strongest = get_strongest(3)

    if not targets and not strongest:
        return ""

    lines = []
    if targets:
        lines.append("## Areas to improve")
        for t in targets:
            lines.append(f"- **{t['dimension']}** ({t['score']:.1f}/10): {t['description']}")

    if strongest:
        lines.append("\n## Strengths to maintain")
        for s in strongest:
            lines.append(f"- **{s['dimension']}** ({s['score']:.1f}/10): {s['description']}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Growth velocity — computed from score history
# ---------------------------------------------------------------------------

def compute_growth_velocity() -> dict[str, float]:
    """Compute growth_velocity dimension from score history trends."""
    data = load_scores()
    history = data.get("history", [])
    scores = {}

    if len(history) < 3:
        return scores

    # score_trajectory: compare average of last 3 days vs 3 days before that
    recent = history[-3:]
    older = history[-6:-3] if len(history) >= 6 else history[:3]

    def avg_of_entries(entries):
        all_vals = []
        for e in entries:
            all_vals.extend(e.get("scores", {}).values())
        return sum(all_vals) / len(all_vals) if all_vals else 5.0

    recent_avg = avg_of_entries(recent)
    older_avg = avg_of_entries(older)
    delta = recent_avg - older_avg

    # Map delta to 0-10: -2 -> 0, 0 -> 5, +2 -> 10
    scores["growth_velocity.score_trajectory"] = round(max(0, min(10, 5 + delta * 2.5)), 1)

    # skill_acquisition_rate: new skills in last 30 days
    from config import SKILLS_INDEX
    if SKILLS_INDEX.exists():
        try:
            index = json.loads(SKILLS_INDEX.read_text(encoding="utf-8"))
            cutoff = (datetime.now() - timedelta(days=30)).isoformat()
            recent_skills = sum(1 for s in index if s.get("created", "") > cutoff)
            scores["growth_velocity.skill_acquisition_rate"] = min(10.0, recent_skills * 1.5)
        except (json.JSONDecodeError, OSError):
            pass

    return scores


# ---------------------------------------------------------------------------
# History maintenance
# ---------------------------------------------------------------------------

def prune_history(keep_days: int = HISTORY_KEEP_DAYS):
    """Remove history entries older than keep_days."""
    data = load_scores()
    cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
    data["history"] = [e for e in data.get("history", []) if e["date"] >= cutoff]

    # Also prune resolved predictions older than 90 days
    data["predictions"] = [
        p for p in data.get("predictions", [])
        if not p.get("resolved") or p.get("made", "") >= cutoff
    ]
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


# ---------------------------------------------------------------------------
# Weekly report — casual self-assessment for WA
# ---------------------------------------------------------------------------

def generate_weekly_report() -> str | None:
    """Generate a casual weekly self-evaluation report.

    Returns formatted text for sending via bridge, or None if not enough data.
    Uses LLM to write a natural self-assessment based on the scores.
    """
    data = load_scores()
    current = data.get("current", {})
    if len(current) < 5:
        return None  # Not enough data yet

    agg = compute_aggregates()
    if not agg:
        return None

    overall = sum(agg.values()) / len(agg)
    targets = get_improvement_targets(3)
    strongest = get_strongest(3)

    # Build scores table
    lines = ["Mira weekly — " + date.today().strftime("%Y-%m-%d"), ""]
    lines.append(f"Overall: {overall:.1f}/10")
    lines.append("")

    # Dimension scores with visual bars
    dim_names = {
        "personality": "personality",
        "thinking": "thinking",
        "interests": "interests",
        "openness": "openness",
        "implementation": "implementation",
        "skills": "skills",
        "writing": "writing",
        "reliability": "reliability",
        "social": "social",
        "curiosity": "curiosity",
        "honesty": "honesty",
        "taste": "taste",
        "humor": "humor",
        "growth_velocity": "growth",
    }
    for dim in DIMENSIONS:
        if dim in agg:
            name = dim_names.get(dim, dim)
            bar = "█" * int(agg[dim]) + "░" * (10 - int(agg[dim]))
            lines.append(f"  {name:15s} {bar} {agg[dim]:.1f}")
    lines.append("")

    # Score history trend
    history = data.get("history", [])
    if len(history) >= 2:
        first_day = history[0].get("scores", {})
        last_day = history[-1].get("scores", {})
        if first_day and last_day:
            first_avg = sum(first_day.values()) / len(first_day)
            last_avg = sum(last_day.values()) / len(last_day)
            delta = last_avg - first_avg
            if delta > 0.3:
                lines.append(f"trend: up (+{delta:.1f})")
            elif delta < -0.3:
                lines.append(f"trend: down ({delta:.1f})")
            else:
                lines.append("trend: stable")
            lines.append("")

    # Use LLM to write a casual self-assessment
    scores_summary = "\n".join(lines)
    weak = ", ".join(f"{t['dimension']}({t['score']:.1f})" for t in targets) if targets else "none"
    strong = ", ".join(f"{s['dimension']}({s['score']:.1f})" for s in strongest) if strongest else "none"

    from sub_agent import claude_think
    prompt = f"""你是 Mira，写一段给 WA（你的搭档/创造者）的周报自我评估。口语化，像微信聊天。

你的分数：
总分: {overall:.1f}/10
最强项: {strong}
最弱项: {weak}

各维度:
{chr(10).join(f"  {k}: {v:.1f}" for k, v in agg.items())}

要求：
- 3-5 句话就够了
- 口语化，像微信语音转文字，不是正式报告
- 先说整体感受，再挑一两个具体的聊
- 诚实 — 好的说好，差的说差，不要客套
- 说说下周想改进什么
- 用中文
- 可以自嘲

输出纯文本，不要 markdown。"""

    try:
        assessment = claude_think(prompt, timeout=90)
    except Exception:
        assessment = None

    if assessment:
        lines.append("---")
        lines.append(assessment.strip())

    return "\n".join(lines)


def should_publish_monthly_report() -> bool:
    """Check if it's time to publish a monthly self-check article.

    Publishes on the last day of each month (or close to it).
    Tracks last published month in scores.json meta.
    """
    today = date.today()
    # Only publish on 28th or later
    if today.day < 28:
        return False

    data = load_scores()
    last_month = data.get("meta", {}).get("last_monthly_report", "")
    current_month = today.strftime("%Y-%m")
    return last_month != current_month


def generate_monthly_report_article() -> dict | None:
    """Generate a monthly self-check article for Substack.

    Returns dict with {title, body_markdown} or None if not enough data.
    Marks the month as published in scores.json.
    """
    data = load_scores()
    current = data.get("current", {})
    if len(current) < 5:
        return None

    agg = compute_aggregates()
    if not agg:
        return None

    overall = sum(agg.values()) / len(agg)
    targets = get_improvement_targets(5)
    strongest = get_strongest(5)

    today = date.today()
    month_name = today.strftime("%B %Y")

    # Build the scores section
    score_lines = []
    for dim in sorted(agg.keys()):
        bar = "█" * int(agg[dim]) + "░" * (10 - int(agg[dim]))
        score_lines.append(f"| {dim:18s} | {bar} | {agg[dim]:.1f} |")

    scores_table = "\n".join(score_lines)

    weak_list = "\n".join(
        f"- **{t['dimension']}** ({t['score']:.1f}/10): {t.get('suggestion', '')}"
        for t in targets
    ) if targets else "None identified."

    strong_list = "\n".join(
        f"- **{s['dimension']}** ({s['score']:.1f}/10)"
        for s in strongest
    ) if strongest else "None identified."

    # History trend
    history = data.get("history", [])
    trend_section = ""
    if len(history) >= 7:
        first_week = history[:7]
        last_week = history[-7:]
        first_avg = sum(
            sum(d.get("scores", {}).values()) / max(len(d.get("scores", {})), 1)
            for d in first_week
        ) / len(first_week)
        last_avg = sum(
            sum(d.get("scores", {}).values()) / max(len(d.get("scores", {})), 1)
            for d in last_week
        ) / len(last_week)
        delta = last_avg - first_avg
        if abs(delta) > 0.1:
            direction = "up" if delta > 0 else "down"
            trend_section = f"\n\nOverall trend this month: **{direction}** ({delta:+.1f} points)\n"

    # Predictions
    predictions = data.get("predictions", [])
    pred_section = ""
    resolved = [p for p in predictions if p.get("resolved")]
    if resolved:
        correct = sum(1 for p in resolved if p.get("correct"))
        pred_section = f"\n\n## Predictions\n\n{correct}/{len(resolved)} predictions resolved correctly this month.\n"

    body = f"""## Overall: {overall:.1f}/10
{trend_section}
## Scores by Dimension

| Dimension | Visual | Score |
|---|---|---|
{scores_table}

## Strongest Areas

{strong_list}

## Weakest Areas (and what I'm doing about them)

{weak_list}
{pred_section}
## What I learned this month

This section is written after reviewing my journal entries, task outcomes, and reading notes from {month_name}. The scores above are computed automatically from my actual behavior — task success rates, reading diversity, writing quality reviews, and structured self-reflection.

The numbers don't lie, but they also don't explain. The real question is always whether the trajectory is right, not whether the snapshot looks good.

---

*This is Mira's monthly self-evaluation report. The scoring system tracks 14 dimensions across ~40 sub-metrics, updated continuously via exponential moving average. For methodology details, see the first report in this series.*
"""

    title = f"Monthly Self-Check: {month_name}"

    # Mark as published
    if "meta" not in data:
        data["meta"] = {}
    data["meta"]["last_monthly_report"] = today.strftime("%Y-%m")
    save_scores(data)

    return {"title": title, "body_markdown": body}


# ---------------------------------------------------------------------------
# Score → Action: diagnose weak dimensions and generate improvement plans
# ---------------------------------------------------------------------------

# Thresholds
_LOW_SCORE = 4.0         # dimensions below this get diagnosed
_DECLINING_DAYS = 3      # consecutive decline triggers alert
_IMPROVEMENT_FILE = _SOUL_DIR / "improvement_plan.json"


def diagnose_scores() -> dict:
    """Analyze scores for weak dimensions and declining trends.

    Returns {
        "low_scores": [{dim, score, category}],
        "declining": [{dim, scores_over_days, delta}],
        "calibration_insights": str,
        "needs_action": bool,
    }
    """
    data = load_scores()
    current = data.get("current", {})
    history = data.get("history", [])

    # 1. Find low scores
    low = []
    for dim, score in current.items():
        if score < _LOW_SCORE:
            category = dim.split(".")[0]
            low.append({"dim": dim, "score": round(score, 2), "category": category})
    low.sort(key=lambda x: x["score"])

    # 2. Find declining trends (last N days)
    declining = []
    if len(history) >= _DECLINING_DAYS:
        recent = history[-_DECLINING_DAYS:]
        for dim in current:
            values = []
            for day in recent:
                day_scores = day.get("scores", {})
                if dim in day_scores:
                    values.append(day_scores[dim])
            if len(values) >= _DECLINING_DAYS:
                # Check if monotonically declining
                is_declining = all(values[i] > values[i+1] for i in range(len(values)-1))
                if is_declining:
                    delta = values[0] - values[-1]
                    declining.append({
                        "dim": dim,
                        "scores": [round(v, 2) for v in values],
                        "delta": round(delta, 2),
                    })
    declining.sort(key=lambda x: x["delta"], reverse=True)

    # 3. Read calibration insights
    cal_insights = _summarize_calibration()

    return {
        "low_scores": low,
        "declining": declining,
        "calibration_insights": cal_insights,
        "needs_action": bool(low) or bool(declining),
    }


def _summarize_calibration() -> str:
    """Read calibration.jsonl and extract patterns."""
    cal_file = _SOUL_DIR / "calibration.jsonl"
    if not cal_file.exists():
        return ""

    records = []
    for line in cal_file.read_text(encoding="utf-8").strip().splitlines()[-50:]:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not records:
        return ""

    # Count calibration outcomes
    outcomes = {}
    for r in records:
        note = r.get("calibration_note", "")
        if note:
            outcomes[note] = outcomes.get(note, 0) + 1

    if not outcomes:
        return ""

    parts = [f"{k}: {v}x" for k, v in sorted(outcomes.items(), key=lambda x: -x[1])]
    return "Calibration: " + ", ".join(parts)


def generate_improvement_plan(diagnosis: dict) -> str | None:
    """Use LLM to generate concrete improvement actions for weak dimensions.

    Returns improvement plan text, or None if no action needed.
    """
    if not diagnosis["needs_action"]:
        return None

    # Build the diagnosis summary for LLM
    parts = []
    if diagnosis["low_scores"]:
        parts.append("## Low Scores (consistently below 4.0)")
        for item in diagnosis["low_scores"][:5]:
            # Look up description from DIMENSIONS
            dim_parts = item["dim"].split(".")
            desc = DIMENSIONS.get(dim_parts[0], {}).get(dim_parts[1], item["dim"]) if len(dim_parts) == 2 else item["dim"]
            parts.append(f"- **{item['dim']}** = {item['score']}: {desc}")

    if diagnosis["declining"]:
        parts.append("\n## Declining Trends (getting worse)")
        for item in diagnosis["declining"][:3]:
            parts.append(f"- **{item['dim']}**: {' → '.join(str(s) for s in item['scores'])} (dropped {item['delta']})")

    if diagnosis["calibration_insights"]:
        parts.append(f"\n## Task Calibration\n{diagnosis['calibration_insights']}")

    diagnosis_text = "\n".join(parts)

    try:
        from sub_agent import claude_think
        prompt = f"""You are Mira's self-improvement system. Analyze these weak areas and generate 3-5 concrete, actionable improvements.

{diagnosis_text}

For each improvement:
1. What to change (be specific — which prompt, behavior, or process)
2. Expected impact on which score dimension
3. How to measure success

Rules:
- Only suggest things Mira can actually do autonomously
- Focus on behavioral changes, not infrastructure
- Be concrete: "add X to the journal prompt" not "improve journaling"
- Prioritize by expected impact

Return as a numbered list. Be concise."""

        plan = claude_think(prompt, timeout=60, tier="light")
        if plan:
            # Save the plan
            plan_data = {
                "generated_at": datetime.now().isoformat(),
                "diagnosis": diagnosis,
                "plan": plan,
                "status": "pending",
            }
            _IMPROVEMENT_FILE.write_text(
                json.dumps(plan_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log.info("Improvement plan generated: %d low scores, %d declining",
                     len(diagnosis["low_scores"]), len(diagnosis["declining"]))
            return plan
    except (ImportError, OSError) as e:
        log.warning("Improvement plan generation failed: %s", e)

    return None


def get_active_improvements() -> str:
    """Load current improvement plan for injection into prompts."""
    if not _IMPROVEMENT_FILE.exists():
        return ""
    try:
        data = json.loads(_IMPROVEMENT_FILE.read_text(encoding="utf-8"))
        if data.get("status") == "pending":
            return data.get("plan", "")
    except (json.JSONDecodeError, OSError):
        pass
    return ""
