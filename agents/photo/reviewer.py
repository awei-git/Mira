from __future__ import annotations

"""Photo reviewer — score and critique photographs.

Separate evaluation frameworks for landscape and portrait/people photography.
Uses vision model to analyze the image, then scores on multiple dimensions.

Output: structured JSON with scores (1-10), overall rating, and critique.
"""
import json
import logging
import re
import sys
from pathlib import Path

_AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_AGENTS_DIR.parent / "lib"))

from llm import claude_act

log = logging.getLogger("photo.reviewer")


# ---------------------------------------------------------------------------
# Scoring dimensions
# ---------------------------------------------------------------------------

LANDSCAPE_CRITERIA = {
    "composition": "构图 — Rule of thirds, leading lines, foreground interest, depth layers, balance, visual flow. Does the eye travel through the frame intentionally?",
    "light": "光线 — Quality, direction, color of light. Golden hour warmth, blue hour cool, dramatic sidelight, flat overcast. Is the light working FOR the image or just present?",
    "exposure": "曝光 — Dynamic range utilization. Highlight detail, shadow detail, histogram spread. Crushed blacks or blown highlights that lose information?",
    "color": "色彩 — Color harmony, palette coherence, saturation balance. Intentional color grading vs. default camera output. Do the colors serve the mood?",
    "atmosphere": "氛围 — Mood, feeling, emotional impact. Does the image transport you? Sense of place, weather, time of day. The intangible quality that separates a photo from a snapshot.",
    "sharpness": "锐度与细节 — Focus accuracy, depth of field choice, micro-contrast, texture rendering. Appropriate sharpness for the subject — not over-sharpened, not soft where it matters.",
    "post_processing": "后期处理 — Quality of editing. Natural vs. overdone. Color grading consistency, noise handling, local adjustments. Does the processing enhance or distract?",
    "impact": "冲击力 — First-impression strength. Would this stop someone scrolling? Does it reward a second look? Memorable or forgettable?",
}

PORTRAIT_CRITERIA = {
    "composition": "构图 — Framing, headroom, negative space, body positioning, crop. Eye placement in frame. Does the composition direct attention to the subject?",
    "light": "光线 — Light quality on the face/subject. Hard vs. soft, direction (Rembrandt, loop, butterfly, split). Catch lights in eyes. Hair light, rim light, fill ratio.",
    "expression": "表情与瞬间 — Captured emotion, gesture, micro-expression. Does the moment feel genuine or posed? Connection between subject and camera.",
    "skin_tone": "肤色 — Accuracy and pleasantness of skin rendering. No orange, green, or grey casts. Consistent across face. Retouching: enhanced identity, not erased it.",
    "background": "背景 — Separation from subject. Bokeh quality, distracting elements, color relationship with subject. Does the background serve the portrait?",
    "color": "色彩 — Color harmony between subject and environment. Wardrobe, background, skin tone relationship. Intentional palette or accidental?",
    "sharpness": "锐度与对焦 — Eyes sharp? Correct focal plane? Depth of field appropriate for the intent? Motion blur intentional or accident?",
    "post_processing": "后期处理 — Retouching quality. Skin texture preserved? Frequency separation visible? Over-smoothed? Color grading appropriate for the mood?",
    "storytelling": "叙事性 — Does the image tell us something about the person? Environmental context, props, gesture, gaze. Character revealed or hidden?",
    "impact": "冲击力 — Emotional response. Connection. Would you remember this face? Does the image make you feel something?",
}


# ---------------------------------------------------------------------------
# Review functions
# ---------------------------------------------------------------------------

def review_photo(image_path: Path, category: str = "auto") -> dict:
    """Review and score a single photo.

    Args:
        image_path: Path to image file
        category: "landscape", "portrait", or "auto" (let model decide)

    Returns:
        dict with scores, overall rating, critique, and suggestions
    """
    if category == "auto":
        category = _classify_photo(image_path)

    criteria = LANDSCAPE_CRITERIA if category == "landscape" else PORTRAIT_CRITERIA
    criteria_text = "\n".join(f"- **{k}**: {v}" for k, v in criteria.items())

    prompt = f"""Read this photograph: {image_path}

You are a professional photo critic and editor. Score this {category} photograph on each dimension below.

## Scoring Criteria (1-10 scale)
{criteria_text}

## Scoring Guide
- 1-3: Significant problems, amateur mistakes
- 4-5: Competent but unremarkable, typical snapshot
- 6-7: Good — intentional choices visible, few issues
- 8-9: Excellent — professional quality, distinctive vision
- 10: Exceptional — gallery/competition worthy

## Rules
- Be HONEST and SPECIFIC. Don't inflate scores.
- A 7 is genuinely good. Most casual photos are 4-5. Reserve 9-10 for truly outstanding work.
- For each dimension, give the score AND a one-sentence explanation of WHY.
- At the end, give an overall assessment and 2-3 specific actionable suggestions for improvement.

Output as JSON:
```json
{{
    "category": "{category}",
    "scores": {{
        "dimension_name": {{"score": 7, "reason": "one sentence why"}},
        ...
    }},
    "overall": 7.0,
    "summary": "2-3 sentence overall assessment",
    "strengths": ["strength 1", "strength 2"],
    "weaknesses": ["weakness 1", "weakness 2"],
    "suggestions": ["specific actionable suggestion 1", "specific actionable suggestion 2"]
}}
```"""

    result = claude_act(prompt, cwd=image_path.parent, tier="light")
    if not result:
        return {"error": "Vision model returned empty response"}

    parsed = _extract_json(result)
    if parsed:
        parsed["file"] = str(image_path)
        parsed["category"] = category
        return parsed

    # If JSON extraction failed, return raw text
    return {
        "file": str(image_path),
        "category": category,
        "raw_review": result,
        "error": "Failed to parse structured review",
    }


def review_batch(image_paths: list[Path], category: str = "auto") -> list[dict]:
    """Review multiple photos and return sorted by score."""
    reviews = []
    for img in image_paths:
        log.info("Reviewing: %s", img.name)
        review = review_photo(img, category)
        reviews.append(review)

    # Sort by overall score descending
    reviews.sort(key=lambda r: r.get("overall", 0), reverse=True)
    return reviews


def compare_versions(original: Path, edited: Path, category: str = "auto") -> dict:
    """Compare original vs edited version of the same photo."""
    if category == "auto":
        category = _classify_photo(original)

    prompt = f"""Read both of these photographs.

Original: {original}
Edited: {edited}

You are a professional photo editor comparing an original photo with its edited version.
This is a {category} photograph.

Evaluate:
1. What changes were made? (exposure, color, contrast, crop, etc.)
2. Did the edits IMPROVE the image? Be specific about what got better and what got worse.
3. Score both versions on a 1-10 scale.
4. What would you change about the edit?

Output as JSON:
```json
{{
    "original_score": 5.0,
    "edited_score": 7.0,
    "improvement": true,
    "changes_detected": ["change 1", "change 2"],
    "improvements": ["what got better 1", "what got better 2"],
    "regressions": ["what got worse, if anything"],
    "suggestions": ["what I would change about the edit"],
    "summary": "2-3 sentence comparison"
}}
```"""

    result = claude_act(prompt, cwd=original.parent, tier="light")
    if not result:
        return {"error": "Vision model returned empty response"}

    parsed = _extract_json(result)
    if parsed:
        parsed["original"] = str(original)
        parsed["edited"] = str(edited)
        return parsed

    return {"raw_review": result, "error": "Failed to parse"}


# ---------------------------------------------------------------------------
# Photo classification
# ---------------------------------------------------------------------------

def _classify_photo(image_path: Path) -> str:
    """Classify photo as landscape or portrait using vision model."""
    prompt = f"""Read this photograph: {image_path}

Classify this photo into ONE category. Reply with ONLY the category name, nothing else:
- landscape (nature, cityscape, architecture, scenery, travel, still life, animals)
- portrait (people, faces, street with people as subject, group photos, environmental portraits)"""

    result = claude_act(prompt, cwd=image_path.parent, tier="light")
    if result and "portrait" in result.lower():
        return "portrait"
    return "landscape"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_review(review: dict) -> str:
    """Format a review dict as readable text."""
    if "error" in review and "raw_review" in review:
        return review["raw_review"]

    lines = []
    filename = Path(review.get("file", "")).name
    category = review.get("category", "unknown")
    overall = review.get("overall", 0)

    lines.append(f"## {filename} [{category}] — {overall:.1f}/10")
    lines.append("")

    scores = review.get("scores", {})
    if scores:
        for dim, data in scores.items():
            if isinstance(data, dict):
                score = data.get("score", "?")
                reason = data.get("reason", "")
                bar = "█" * int(score) + "░" * (10 - int(score))
                lines.append(f"  {dim:20s} {bar} {score}/10  {reason}")
            else:
                lines.append(f"  {dim:20s} {data}")
        lines.append("")

    summary = review.get("summary", "")
    if summary:
        lines.append(f"**Summary**: {summary}")
        lines.append("")

    strengths = review.get("strengths", [])
    if strengths:
        lines.append("**Strengths**: " + " | ".join(strengths))

    weaknesses = review.get("weaknesses", [])
    if weaknesses:
        lines.append("**Weaknesses**: " + " | ".join(weaknesses))

    suggestions = review.get("suggestions", [])
    if suggestions:
        lines.append("**Suggestions**:")
        for s in suggestions:
            lines.append(f"  - {s}")

    return "\n".join(lines)


def format_batch_review(reviews: list[dict]) -> str:
    """Format multiple reviews with ranking."""
    lines = ["# Photo Review\n"]

    for i, r in enumerate(reviews):
        lines.append(f"### #{i+1}")
        lines.append(format_review(r))
        lines.append("\n---\n")

    # Summary table
    lines.append("## Rankings\n")
    lines.append("| # | File | Category | Score |")
    lines.append("|---|------|----------|-------|")
    for i, r in enumerate(reviews):
        name = Path(r.get("file", "")).name
        cat = r.get("category", "?")
        score = r.get("overall", 0)
        lines.append(f"| {i+1} | {name} | {cat} | {score:.1f} |")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict | None:
    m = re.search(r'```(?:json)?\s*\n(.*?)\n```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    parser = argparse.ArgumentParser(description="Photo reviewer")
    parser.add_argument("images", nargs="+", help="Image files to review")
    parser.add_argument("--category", default="auto", choices=["auto", "landscape", "portrait"])
    parser.add_argument("--compare", help="Compare with edited version")
    args = parser.parse_args()

    if args.compare:
        result = compare_versions(Path(args.images[0]), Path(args.compare), args.category)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif len(args.images) == 1:
        result = review_photo(Path(args.images[0]), args.category)
        print(format_review(result))
    else:
        results = review_batch([Path(p) for p in args.images], args.category)
        print(format_batch_review(results))
