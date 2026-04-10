"""Video reviewer — score a rough cut and propose targeted fixes.

Phase 5 of the enhanced video pipeline:
  rough_cut.mp4 → Gemini native video review → review.json

Scores on 8 dimensions and returns specific per-clip fix proposals
for iterative refinement without full re-render.
"""
import json
import logging
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

_AGENTS_DIR = Path(__file__).resolve().parent.parent
if str(_AGENTS_DIR.parent / "lib") not in sys.path:
    sys.path.insert(0, str(_AGENTS_DIR.parent / "lib"))

from config import (
    GEMINI_VIDEO_MODEL, GEMINI_VIDEO_REVIEWER_MAX_TOKENS,
    GEMINI_REVIEWER_TEMPERATURE,
)

log = logging.getLogger("video.reviewer")

# Reuse upload infrastructure from scene_analyzer
from scene_analyzer import (
    _upload_to_file_api,
    _MIME_TYPES,
)

_REVIEW_PROMPT = """You are an expert video editor reviewing a finished edit.

Watch this video carefully. Score it on each dimension (1-10 scale):

1. **pacing** — Does the rhythm feel right? Varied or monotonous? Do fast/slow sections serve the story?
2. **color_consistency** — Do adjacent clips match? Any jarring color jumps between shots?
3. **narrative_arc** — Does it tell a story? Clear opening, build, climax, close?
4. **music_sync** — Do cuts land on musical beats or phrases? Does visual energy match audio energy?
5. **transitions** — Appropriate transition choices? Too many hard cuts or too many effects?
6. **clip_selection** — Is the best footage used? Any filler, blurry, or low-quality shots?
7. **emotional_impact** — Does it make you feel something? Does it build to a meaningful moment?
8. **overall_polish** — Professional feel? Consistent grade? Clean titles?

Scoring guide:
- 1-3: Significant problems
- 4-5: Competent but issues
- 6-7: Good, intentional choices visible
- 8-9: Excellent, professional quality
- 10: Exceptional

{taste_context}

Also identify specific clips that should be fixed. For each problem, specify:
- Which clip (by rough timecode in the edit)
- What the issue is
- How to fix it (re-grade, replace, re-time, remove)

Return ONLY this JSON:
{{
  "scores": {{
    "pacing": {{"score": N, "reason": "..."}},
    "color_consistency": {{"score": N, "reason": "..."}},
    "narrative_arc": {{"score": N, "reason": "..."}},
    "music_sync": {{"score": N, "reason": "..."}},
    "transitions": {{"score": N, "reason": "..."}},
    "clip_selection": {{"score": N, "reason": "..."}},
    "emotional_impact": {{"score": N, "reason": "..."}},
    "overall_polish": {{"score": N, "reason": "..."}}
  }},
  "overall": N.N,
  "summary": "2-3 sentence overall assessment",
  "strengths": ["...", "..."],
  "weaknesses": ["...", "..."],
  "fix_proposals": [
    {{
      "timecode": "M:SS",
      "clip_idx_approx": N,
      "issue": "description of the problem",
      "type": "re-grade|replace|re-time|remove",
      "fix": "specific fix instruction"
    }}
  ]
}}"""


def review_rough_cut(video_path: Path, edit_plan: dict,
                     taste_profile: str, api_key: str,
                     work_dir: Path = None) -> dict:
    """Review a rough cut video using Gemini native video analysis.

    Args:
        video_path: Path to the video to review
        edit_plan: The edit plan used to create this video (for context)
        taste_profile: Full text of editing_taste_profile.md
        api_key: Gemini API key
        work_dir: Where to save review.json

    Returns:
        dict with scores, overall, summary, strengths, weaknesses, fix_proposals
    """
    if not video_path.exists():
        log.error("Video not found: %s", video_path)
        return _empty_review("Video file not found")

    file_size = video_path.stat().st_size
    if file_size > 2 * 1024 * 1024 * 1024:  # 2GB safety limit for review
        log.warning("Video too large for review upload: %.1f GB",
                    file_size / (1024**3))
        return _empty_review("Video too large for review")

    # Upload video
    file_uri = _upload_to_file_api(video_path, api_key)
    if not file_uri:
        log.warning("Upload failed for review, returning empty review")
        return _empty_review("Upload failed")

    # Build taste context
    taste_ctx = ""
    if taste_profile:
        taste_ctx = (
            "The editor's style preferences (use to judge taste_alignment):\n"
            f"{taste_profile[:2000]}"  # truncate if too long
        )

    prompt = _REVIEW_PROMPT.format(taste_context=taste_ctx)

    # Call Gemini
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_VIDEO_MODEL}:generateContent?key={api_key}"
    )

    payload = {
        "contents": [{
            "role": "user",
            "parts": [
                {
                    "file_data": {
                        "file_uri": file_uri,
                        "mime_type": "video/mp4",
                    }
                },
                {"text": prompt},
            ],
        }],
        "generationConfig": {
            "maxOutputTokens": GEMINI_VIDEO_REVIEWER_MAX_TOKENS,
            "temperature": GEMINI_REVIEWER_TEMPERATURE,
        },
    }

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint, data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.error("Gemini review call failed: %s", e)
        return _empty_review(f"API error: {e}")

    # Parse response
    review = _parse_review_response(result)

    # Save
    if work_dir:
        out_path = work_dir / "review.json"
        out_path.write_text(json.dumps(review, indent=2, ensure_ascii=False))
        log.info("Review saved: %s (overall: %.1f)", out_path, review.get("overall", 0))

    return review


def _parse_review_response(result: dict) -> dict:
    """Extract review JSON from Gemini response."""
    try:
        text = result["candidates"][0]["content"]["parts"][0]["text"]
        # Strip markdown code fences
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        review = json.loads(text)

        # Ensure overall score exists
        if "overall" not in review and "scores" in review:
            scores = [s["score"] for s in review["scores"].values()
                      if isinstance(s, dict) and "score" in s]
            review["overall"] = round(sum(scores) / len(scores), 1) if scores else 5.0

        return review
    except (KeyError, json.JSONDecodeError, IndexError) as e:
        log.error("Failed to parse review response: %s", e)
        return _empty_review("Parse error")


def _empty_review(reason: str) -> dict:
    """Return a neutral review when analysis fails."""
    return {
        "scores": {dim: {"score": 5, "reason": reason}
                   for dim in ["pacing", "color_consistency", "narrative_arc",
                               "music_sync", "transitions", "clip_selection",
                               "emotional_impact", "overall_polish"]},
        "overall": 5.0,
        "summary": f"Review unavailable: {reason}",
        "strengths": [],
        "weaknesses": [],
        "fix_proposals": [],
    }


def should_iterate(review: dict, threshold: float = 7.0) -> bool:
    """Determine if the edit needs iteration based on review scores."""
    return review.get("overall", 0) < threshold


def format_review_summary(review: dict) -> str:
    """Create a human-readable review summary."""
    lines = [f"Overall: {review.get('overall', '?')}/10"]
    lines.append(review.get("summary", ""))

    if review.get("scores"):
        lines.append("\nScores:")
        for dim, info in review["scores"].items():
            if isinstance(info, dict):
                lines.append(f"  {dim}: {info.get('score', '?')}/10 — {info.get('reason', '')}")

    if review.get("strengths"):
        lines.append("\nStrengths: " + ", ".join(review["strengths"]))
    if review.get("weaknesses"):
        lines.append("Weaknesses: " + ", ".join(review["weaknesses"]))

    fixes = review.get("fix_proposals", [])
    if fixes:
        lines.append(f"\nFix proposals ({len(fixes)}):")
        for f in fixes:
            lines.append(f"  [{f.get('timecode', '?')}] {f.get('issue', '')} → {f.get('fix', '')}")

    return "\n".join(lines)
