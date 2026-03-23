"""Reviewer agents for each specialized Mira agent.

Each reviewer evaluates the text output of its paired agent and returns structured feedback.

Return type for all review_*() functions:
    {
        "passed":      bool,
        "score":       float,   # 0–10
        "issues":      list[str],
        "suggestions": list[str],
    }

Usage — direct:
    from reviewers import review
    result = review("writing", content)

Usage — orchestration handle():
    from reviewers import handle
    handle(workspace, task_id, "review: writing\\n\\n<content>", sender, thread_id)
    # or pass agent_type via metadata kwarg
    handle(workspace, task_id, content, sender, thread_id,
           agent_type="writing", reviewed_content=content)

Install:  copy to agents/shared/reviewers.py  (sits alongside sub_agent.py)
"""
from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import TypedDict

# ---------------------------------------------------------------------------
# Path bootstrap — works whether this file lives in tasks/ or agents/shared/
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_AGENTS_SHARED = Path(__file__).resolve().parent.parent.parent / "Mira" / "agents" / "shared"
# Also try relative: if we're already inside agents/shared/ this is a no-op duplicate
for _candidate in [_HERE, _AGENTS_SHARED, _HERE.parent.parent / "agents" / "shared"]:
    if (_candidate / "sub_agent.py").exists() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))
        break

log = logging.getLogger("reviewers")


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

class ReviewResult(TypedDict):
    passed: bool
    score: float
    issues: list[str]
    suggestions: list[str]


def _empty_result(reason: str = "review failed") -> ReviewResult:
    return {"passed": False, "score": 0.0, "issues": [reason], "suggestions": []}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_JSON_RE = re.compile(r'```(?:json)?\s*\n(.*?)\n```', re.DOTALL)
_JSON_OBJ_RE = re.compile(r'\{.*\}', re.DOTALL)


def _extract_json(text: str) -> dict | None:
    m = _JSON_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = _JSON_OBJ_RE.search(text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None


def _parse_result(raw: str) -> ReviewResult:
    """Parse claude_think output into a ReviewResult dict."""
    if not raw:
        return _empty_result("empty response from model")
    parsed = _extract_json(raw)
    if not parsed:
        return _empty_result("could not parse structured response")
    try:
        score = float(parsed.get("score", 0))
        return ReviewResult(
            passed=bool(parsed.get("passed", score >= 6.0)),
            score=round(min(max(score, 0.0), 10.0), 1),
            issues=list(parsed.get("issues", [])),
            suggestions=list(parsed.get("suggestions", [])),
        )
    except (TypeError, ValueError) as e:
        return _empty_result(f"result parsing error: {e}")


def _think(prompt: str, timeout: int = 120) -> str:
    """Call claude_think from sub_agent. Returns empty string on failure."""
    try:
        from sub_agent import claude_think
        return claude_think(prompt, timeout=timeout, tier="light")
    except ImportError:
        log.error("sub_agent not found — reviewers need agents/shared/ on sys.path")
        return ""
    except Exception as e:
        log.error("claude_think failed in reviewer: %s", e)
        return ""


_SCHEMA = """\
Return ONLY this JSON (no extra text, no markdown outside the fence):
```json
{
  "passed": true,
  "score": 7.5,
  "issues": ["issue 1", "issue 2"],
  "suggestions": ["suggestion 1", "suggestion 2"]
}
```
Rules:
- passed = true if score >= 6.0
- score is 0–10; be honest, not generous (5 = competent but unremarkable)
- issues: concrete problems found (empty list if none)
- suggestions: actionable improvements (2–4 items always)"""


# ---------------------------------------------------------------------------
# Briefing reviewer
# ---------------------------------------------------------------------------

_BRIEFING_CRITERIA = """\
Review criteria for a daily intelligence briefing:
1. KEY_JUDGMENT_FIRST — Does the briefing lead with its most important finding, not with
   background context?
2. FRESHNESS — Are the sources and events genuinely recent (today / this week)?
3. SIGNAL_NOISE — Does it cut noise and surface the handful of things that actually matter?
4. COVERAGE — Are major relevant developments covered, or are obvious things missing?
5. CONFIDENCE_CALIBRATION — Are uncertain claims hedged ("likely", "suggests") vs.
   stated as fact?
6. IMPLICATIONS — Does it go beyond "X happened" to "X matters because..."?
7. SOURCE_DIVERSITY — Does it draw from more than one source / perspective?"""


def review_briefing(content: str, metadata: dict | None = None) -> ReviewResult:
    """Review a daily briefing output for relevance, freshness, and signal quality."""
    prompt = f"""{_BRIEFING_CRITERIA}

## Briefing to review
{content[:6000]}

{_SCHEMA}"""
    raw = _think(prompt, timeout=90)
    return _parse_result(raw)


# ---------------------------------------------------------------------------
# Writing reviewer
# ---------------------------------------------------------------------------

_WRITING_CRITERIA = """\
Review criteria for a written essay / newsletter post:
1. THESIS — Is there a clear, specific, non-obvious central argument?
2. HOOK — Does the opening earn the reader's attention in the first sentence?
3. ARGUMENT_QUALITY — Are claims supported with evidence, not just asserted?
4. VOICE — Does the writing have a distinctive, consistent voice, or does it sound generic?
5. STRUCTURE — Does the piece build logically? Does each paragraph earn its place?
6. ORIGINALITY — Does it say something the reader couldn't find elsewhere, or is it
   a rehash of obvious points?
7. ENDING — Does it close with something memorable, or trail off?
8. PROSE — Is the sentence-level writing clean, varied, and free of padding?"""


def review_writing(content: str, metadata: dict | None = None) -> ReviewResult:
    """Review a piece of writing for voice, argument quality, and structure."""
    prompt = f"""{_WRITING_CRITERIA}

## Writing to review
{content[:8000]}

{_SCHEMA}"""
    raw = _think(prompt, timeout=120)
    return _parse_result(raw)


# ---------------------------------------------------------------------------
# Publish reviewer
# ---------------------------------------------------------------------------

_PUBLISH_CRITERIA = """\
Review criteria for content about to be published to Substack:
1. CONTENT_INTEGRITY — Is this actually a real article? (not an error message, stub,
   or template placeholder)
2. MINIMUM_LENGTH — Is it at least 400 words? Short-form pieces need explicit justification.
3. TITLE — Does the title hook without being clickbait? Is it specific?
4. FORMAT — Proper structure: title, body, closing. No broken markdown.
5. NO_AI_TELLS — No "As an AI...", no meta-references to the writing process,
   no unverified attributed quotes.
6. PLATFORM_FIT — Is the tone and length appropriate for a Substack newsletter?
7. FACTUAL_CLAIMS — Are any bold factual claims hedged or sourced?"""


def review_publish(content: str, metadata: dict | None = None) -> ReviewResult:
    """Review content for publish-readiness (Substack)."""
    prompt = f"""{_PUBLISH_CRITERIA}

## Content to review for publishing
{content[:8000]}

{_SCHEMA}"""
    raw = _think(prompt, timeout=90)
    return _parse_result(raw)


# ---------------------------------------------------------------------------
# Analyst reviewer
# ---------------------------------------------------------------------------

_ANALYST_CRITERIA = """\
Review criteria for a market / analyst response:
1. QUESTION_ANSWERED — Does the response actually address the user's question?
2. DATA_GROUNDING — Are claims backed by specific data points, not vague gestures?
3. LOGICAL_CHAIN — Do conclusions follow from the evidence presented?
4. CONFIDENCE_CALIBRATION — Is uncertainty acknowledged? No false precision.
5. GAPS_FLAGGED — If data was unavailable, is this stated rather than glossed over?
6. ACTIONABILITY — Does the analysis give the user something to act on, or just
   summarize what happened?
7. COUNTER_ARGUMENT — Is there acknowledgment of the strongest case against the conclusion?"""


def review_analyst(content: str, metadata: dict | None = None) -> ReviewResult:
    """Review analyst output for sourcing, logic, and conclusions."""
    prompt = f"""{_ANALYST_CRITERIA}

## Analyst output to review
{content[:6000]}

{_SCHEMA}"""
    raw = _think(prompt, timeout=90)
    return _parse_result(raw)


# ---------------------------------------------------------------------------
# Math reviewer
# ---------------------------------------------------------------------------

_MATH_CRITERIA = """\
Review criteria for mathematical reasoning output:
1. LOGICAL_VALIDITY — Are all inference steps valid? Flag any step that does not
   follow from the previous ones.
2. ASSUMPTIONS_STATED — Are all required assumptions stated explicitly at the start?
3. COMPLETENESS — Are all cases covered? Is there a missing base case, edge case,
   or boundary condition?
4. NOTATION — Is notation consistent and standard? Are ambiguous symbols defined?
5. GAP_IDENTIFICATION — Are any steps hand-wavy or unjustified ("it is clear that…")?
6. COUNTEREXAMPLE_RESISTANCE — Has the claimed result been stress-tested against
   simple cases or known counterexamples?
7. RIGOR_VS_CONJECTURE — Is the output clear about what is proved vs. what is claimed
   without proof?"""


def review_math(content: str, metadata: dict | None = None) -> ReviewResult:
    """Review mathematical reasoning for proof correctness and rigor."""
    prompt = f"""{_MATH_CRITERIA}

## Math output to review
{content[:8000]}

{_SCHEMA}"""
    raw = _think(prompt, timeout=150)
    return _parse_result(raw)


# ---------------------------------------------------------------------------
# Video reviewer (reviews pipeline output / edit plan text, not the video file)
# ---------------------------------------------------------------------------

_VIDEO_CRITERIA = """\
Review criteria for a video editing pipeline output (edit plan, metadata, or decision log):
1. TASK_COMPLETION — Did the agent complete the requested edit or clearly explain why not?
2. CUT_RATIONALE — Are edit decisions (clip selection, cut points) explained?
3. MUSIC_PACING — Are music sync and pacing decisions addressed?
4. OUTPUT_ARTIFACT — Does the output reference a concrete deliverable (file path, render
   status)?
5. STYLE_ADHERENCE — Does the output respect the known editing taste profile?
6. ERROR_HANDLING — Are any failures reported with enough context to diagnose?"""


def review_video(content: str, metadata: dict | None = None) -> ReviewResult:
    """Review video agent pipeline output for completeness and decision quality."""
    prompt = f"""{_VIDEO_CRITERIA}

## Video pipeline output to review
{content[:6000]}

{_SCHEMA}"""
    raw = _think(prompt, timeout=90)
    return _parse_result(raw)


# ---------------------------------------------------------------------------
# Photo reviewer (reviews edit instructions / metadata output, not the image file)
# ---------------------------------------------------------------------------

_PHOTO_CRITERIA = """\
Review criteria for a photo editing agent output (edit instructions, batch result, or metadata):
1. TASK_COMPLETION — Did the agent complete the requested edit or explain why not?
2. EDIT_JUSTIFICATION — Are color/tonal decisions explained with reasoning?
3. STYLE_CONSISTENCY — Do edits align with the established style profile?
4. TECHNICAL_SOUNDNESS — Are exposure, color, and sharpness recommendations technically
   reasonable?
5. ACTIONABILITY — Are the instructions concrete enough to execute without ambiguity?
6. DESTRUCTIVE_RISK — Are any irreversible operations flagged with appropriate warnings?"""


def review_photo(content: str, metadata: dict | None = None) -> ReviewResult:
    """Review photo agent output for edit quality and actionability."""
    prompt = f"""{_PHOTO_CRITERIA}

## Photo pipeline output to review
{content[:6000]}

{_SCHEMA}"""
    raw = _think(prompt, timeout=90)
    return _parse_result(raw)


# ---------------------------------------------------------------------------
# Podcast reviewer
# ---------------------------------------------------------------------------

_PODCAST_CRITERIA = """\
Review criteria for a podcast script (voiceover or two-host dialogue):
1. CONVERSATIONAL_FLOW — Does dialogue feel natural when spoken aloud? No robotic
   phrasing or written-text constructions.
2. DEPTH — Does the script engage substantively with the source material, or skim?
3. HOST_GUEST_DYNAMIC — In dialogue mode: do the two voices sound distinct? Does the
   host ask genuinely probing questions?
4. PACING — Is the script appropriately timed (not too dense, not too sparse)?
5. OPENING_HOOK — Does the episode open with something that earns the listener's attention?
6. FACT_INTEGRITY — Are any factual claims verifiable, or are they asserted without basis?
7. MIRA_VOICE — In Mira-voiced sections: does it sound like Mira (concise, curious,
   direct) rather than a generic AI narrator?"""


def review_podcast(content: str, metadata: dict | None = None) -> ReviewResult:
    """Review a podcast script for conversational quality and content depth."""
    prompt = f"""{_PODCAST_CRITERIA}

## Podcast script to review
{content[:8000]}

{_SCHEMA}"""
    raw = _think(prompt, timeout=120)
    return _parse_result(raw)


# ---------------------------------------------------------------------------
# Secret reviewer — uses local Ollama only (privacy requirement)
# ---------------------------------------------------------------------------

_SECRET_CRITERIA = """\
Review criteria for a private/sensitive task response (local Ollama output):
1. TASK_COMPLETION — Was the user's actual question answered?
2. HELPFULNESS — Is the response substantive and actionable, not vague?
3. APPROPRIATE_DEPTH — Given a sensitive personal topic, is the depth calibrated
   to what was asked?
4. NO_EXTERNAL_REFERENCES — Does the response avoid suggesting cloud services,
   web lookups, or external sharing of the sensitive information?
5. TONE — Is the tone appropriate for a private, sensitive topic?"""


def review_secret(content: str, metadata: dict | None = None) -> ReviewResult:
    """Review secret agent output using local Ollama only (no cloud API)."""
    try:
        from sub_agent import _ollama_call
        from config import OLLAMA_DEFAULT_MODEL
    except ImportError:
        log.error("Cannot import Ollama components for secret reviewer")
        return _empty_result("ollama import failed")

    prompt = f"""{_SECRET_CRITERIA}

## Response to review (private — stays on this machine)
{content[:4000]}

{_SCHEMA}"""

    try:
        raw = _ollama_call(OLLAMA_DEFAULT_MODEL, prompt, timeout=180)
        return _parse_result(raw)
    except Exception as e:
        log.error("Secret reviewer Ollama call failed: %s", e)
        return _empty_result(f"ollama error: {e}")


# ---------------------------------------------------------------------------
# General reviewer
# ---------------------------------------------------------------------------

_GENERAL_CRITERIA = """\
Review criteria for a general-purpose task response:
1. TASK_COMPLETION — Did the response fully address what was asked?
2. ACCURACY — Are the claims factually correct to the best of your knowledge?
3. CONCISENESS — Is the response appropriately sized? Not padded, not truncated?
4. ACTIONABILITY — If the user needs to do something next, is it clear what that is?
5. NO_HALLUCINATION_SIGNALS — Any claims that look suspiciously specific but
   unverified (fake citations, invented URLs, made-up statistics)?
6. LANGUAGE_MATCH — Is the response in the same language as the request?"""


def review_general(content: str, metadata: dict | None = None) -> ReviewResult:
    """Review general agent output for task completion and accuracy."""
    prompt = f"""{_GENERAL_CRITERIA}

## Response to review
{content[:6000]}

{_SCHEMA}"""
    raw = _think(prompt, timeout=90)
    return _parse_result(raw)


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

_REVIEWERS = {
    "briefing":  review_briefing,
    "explorer":  review_briefing,   # explorer writes briefings
    "writing":   review_writing,
    "writer":    review_writing,
    "publish":   review_publish,
    "socialmedia": review_publish,
    "analyst":   review_analyst,
    "math":      review_math,
    "video":     review_video,
    "photo":     review_photo,
    "podcast":   review_podcast,
    "secret":    review_secret,
    "general":   review_general,
}


def review(agent_type: str, content: str,
           metadata: dict | None = None) -> ReviewResult:
    """Dispatch to the correct reviewer for the given agent type.

    Args:
        agent_type: One of briefing, writing, publish, analyst, math, video,
                    photo, podcast, secret, general (and aliases).
        content:    The output text produced by the paired agent.
        metadata:   Optional dict for additional context (e.g. source URLs,
                    task_id, original request).

    Returns:
        ReviewResult with passed, score, issues, suggestions.
    """
    fn = _REVIEWERS.get(agent_type.lower())
    if fn is None:
        log.warning("No reviewer for agent type '%s', using general", agent_type)
        fn = review_general
    log.info("Reviewing %s output (%d chars)", agent_type, len(content))
    result = fn(content, metadata)
    log.info("Review complete: score=%.1f passed=%s issues=%d",
             result["score"], result["passed"], len(result["issues"]))
    return result


# ---------------------------------------------------------------------------
# Orchestration handle() — integrates with task_worker dispatch
# ---------------------------------------------------------------------------

def handle(workspace: Path, task_id: str, content: str,
           sender: str, thread_id: str,
           thread_history: str = "", thread_memory: str = "",
           agent_type: str = "general",
           reviewed_content: str = "") -> str | None:
    """Orchestration entrypoint — follows the same handle() contract as other agents.

    The task content format can be either:
      a) Pass agent_type and reviewed_content as kwargs (preferred from task_worker).
      b) Encode in content as:  "review: <agent_type>\\n\\n<content to review>"

    Writes output.md with full review JSON and returns a summary string.
    """
    # Parse agent_type and content-to-review from free-text if not passed explicitly
    target_content = reviewed_content
    target_type = agent_type

    if not reviewed_content:
        # Try to parse "review: <type>\n\n<content>" format
        m = re.match(r'review:\s*(\w+)\s*\n+(.*)', content, re.DOTALL | re.IGNORECASE)
        if m:
            target_type = m.group(1).strip()
            target_content = m.group(2).strip()
        else:
            # Fall back to reviewing whatever content was passed
            target_content = content
            target_type = "general"

    if not target_content:
        log.error("Reviewer %s: no content to review (task %s)", target_type, task_id)
        return None

    metadata = {
        "task_id": task_id,
        "sender": sender,
        "thread_id": thread_id,
    }

    result = review(target_type, target_content, metadata)

    # Serialize output
    output = {
        "agent_type": target_type,
        "task_id": task_id,
        "review": result,
    }
    output_text = json.dumps(output, ensure_ascii=False, indent=2)
    (workspace / "output.md").write_text(
        f"# Review: {target_type}\n\n```json\n{output_text}\n```",
        encoding="utf-8",
    )

    summary = (
        f"[{target_type} review] score={result['score']}/10 "
        f"passed={result['passed']} "
        f"issues={len(result['issues'])}"
    )
    if result["issues"]:
        summary += " | " + result["issues"][0]
    (workspace / "summary.txt").write_text(summary, encoding="utf-8")

    return summary


# ---------------------------------------------------------------------------
# CLI — python reviewers.py <agent_type> <file_to_review>
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    parser = argparse.ArgumentParser(description="Review a Mira agent output file")
    parser.add_argument("agent_type", choices=list(_REVIEWERS),
                        help="Agent type to review for")
    parser.add_argument("file", help="Path to output file to review")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    content = Path(args.file).read_text(encoding="utf-8")
    result = review(args.agent_type, content)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        status = "PASS" if result["passed"] else "FAIL"
        print(f"\n{status}  score={result['score']}/10\n")
        if result["issues"]:
            print("Issues:")
            for i in result["issues"]:
                print(f"  - {i}")
        if result["suggestions"]:
            print("\nSuggestions:")
            for s in result["suggestions"]:
                print(f"  - {s}")
