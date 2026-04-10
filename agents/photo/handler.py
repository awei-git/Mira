from __future__ import annotations

"""Photo editing agent — analyze, learn style, and edit photographs.

Interactive workflow (via Mira app):
    1. User sends photo path → agent analyzes and suggests edits
    2. User discusses / adjusts → agent revises edit plan
    3. User approves → agent applies edits and saves output

Capabilities:
    - Analyze photos (composition, exposure, color, mood) via vision models
    - Learn editing style from reference photos (style_learner)
    - Generate Lightroom XMP presets matching learned style
    - Apply edits directly via Pillow/ImageMagick
    - Batch process folders of images
    - Generate .cube LUTs from style profiles

Pipeline phases:
    1. Analyze (vision model reads the photo)
    2. Plan (suggest edits based on style profile + skills)
    3. Execute (apply edits: direct or export XMP/LUT)
"""
import argparse
import json
import logging
import re
import sys
from pathlib import Path

# Add shared modules to path
_AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_AGENTS_DIR.parent / "lib"))

from llm import claude_think, claude_act, model_think
from config import MIRA_ROOT
from publish.preflight import preflight_check

log = logging.getLogger("photo.handler")


def _log_photo_failure(step: str, error_msg: str, slug: str = "photo"):
    try:
        from ops.failure_log import record_failure
        record_failure(pipeline="photo", step=step, slug=slug,
                       error_type="photo_agent_error", error_message=error_msg[:500])
    except Exception:
        pass


_PHOTO_DIR = Path(__file__).resolve().parent
_SKILLS_DIR = _PHOTO_DIR / "skills"
_STYLE_DIR = _PHOTO_DIR / "styles"
_REFERENCE_DIR = _PHOTO_DIR / "reference"

# Supported image extensions
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".heic", ".webp",
               ".cr2", ".cr3", ".nef", ".arw", ".dng", ".raf", ".rw2", ".orf"}

# Signals that user approves the edit plan
_APPROVE_PATTERNS = re.compile(
    r'\b(ok|好的?|可以|开始|go|proceed|执行|没问题|就这样|lgtm|apply|修吧|改吧)\b',
    re.IGNORECASE,
)

_STATE_FILE = "photo_state.json"


# ---------------------------------------------------------------------------
# Main handler (called by task_worker)
# ---------------------------------------------------------------------------

def preflight(workspace: Path, task_id: str, instruction: str,
              sender: str, thread_id: str, **kwargs) -> tuple[bool, str]:
    """Block photo jobs that have no resolvable image inputs or style context."""
    state = _load_state(workspace)
    phase = state.get("phase", "")
    preflight_text = instruction.strip() or phase or "photo task"
    result = preflight_check(
        "file_write",
        {
            "instruction": preflight_text,
            "path": str(workspace / "output.md"),
            "content": preflight_text,
        },
    )
    if not result.passed:
        return False, result.summary()

    if phase == "edit_review":
        if state.get("images"):
            return True, ""
        return False, "PREFLIGHT BLOCKED [photo]: review state is missing image list"

    if phase == "style_learning":
        ref_dir = _extract_path(instruction)
        if not ref_dir and state.get("reference_dir"):
            ref_dir = Path(state["reference_dir"])
        if not ref_dir:
            ref_dir = _REFERENCE_DIR
        if ref_dir.exists() and _find_images(ref_dir):
            return True, ""
        return False, "PREFLIGHT BLOCKED [photo]: style-learning state is missing reference images"

    intent = _classify_intent(instruction)
    if intent == "generate_preset":
        if _load_active_style():
            return True, ""
        return False, "PREFLIGHT BLOCKED [photo]: 还没有 style profile，不能生成 preset"

    if intent == "learn_style":
        ref_dir = _extract_path(instruction) or _REFERENCE_DIR
        if ref_dir.exists() and _find_images(ref_dir):
            return True, ""
        return False, f"PREFLIGHT BLOCKED [photo]: 在 {ref_dir} 里没找到参考图片"

    images = _extract_images(instruction)
    if intent == "compare":
        if len(images) >= 2:
            return True, ""
        return False, "PREFLIGHT BLOCKED [photo]: 对比模式需要两张图片"

    if images:
        return True, ""
    return False, "PREFLIGHT BLOCKED [photo]: 找不到要处理的图片或目录"


def handle(workspace: Path, task_id: str, instruction: str,
           sender: str, thread_id: str, **kwargs) -> str:
    """Handle a photo editing task from Mira's task_worker."""
    workspace.mkdir(parents=True, exist_ok=True)
    state = _load_state(workspace)
    phase = state.get("phase", "")

    # Resume existing session
    if phase == "done":
        return f"照片已处理完成: {state.get('output', '')}"

    if phase == "edit_review":
        if _is_approval(instruction):
            return _run_edits(workspace, state)
        else:
            return _revise_plan(workspace, state, instruction)

    if phase == "style_learning":
        return _continue_style_learning(workspace, state, instruction)

    # --- Detect intent ---
    intent = _classify_intent(instruction)

    if intent == "learn_style":
        return _start_style_learning(workspace, state, instruction)
    elif intent == "batch":
        return _start_batch(workspace, state, instruction)
    elif intent == "review":
        return _review_photos(workspace, state, instruction)
    elif intent == "compare":
        return _compare_photos(workspace, state, instruction)
    elif intent == "analyze":
        return _analyze_only(workspace, state, instruction)
    elif intent == "generate_preset":
        return _generate_preset(workspace, state, instruction)
    else:
        # Default: analyze + suggest edits
        return _analyze_and_plan(workspace, state, instruction)


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------

_LEARN_HINTS = re.compile(
    r'学习?|learn|style|风格|品味|taste|参考|reference|训练|train',
    re.IGNORECASE,
)
_BATCH_HINTS = re.compile(
    r'批量|batch|所有|all\s+photos?|整个?文件夹|whole\s+folder|每[一张]',
    re.IGNORECASE,
)
_REVIEW_HINTS = re.compile(
    r'打分|评分|score|rate|review|评[价估]|critique|评一下|几分|rating',
    re.IGNORECASE,
)
_COMPARE_HINTS = re.compile(
    r'对比|比较|compare|vs|原图|before.?after|改前|改后',
    re.IGNORECASE,
)
_ANALYZE_HINTS = re.compile(
    r'分析|analyz|看看|怎么样',
    re.IGNORECASE,
)
_PRESET_HINTS = re.compile(
    r'preset|预设|xmp|lut|cube|导出.*风格|export.*style',
    re.IGNORECASE,
)


def _classify_intent(instruction: str) -> str:
    if _LEARN_HINTS.search(instruction):
        return "learn_style"
    if _PRESET_HINTS.search(instruction):
        return "generate_preset"
    if _COMPARE_HINTS.search(instruction):
        return "compare"
    if _REVIEW_HINTS.search(instruction):
        return "review"
    if _BATCH_HINTS.search(instruction):
        return "batch"
    if _ANALYZE_HINTS.search(instruction):
        return "analyze"
    return "edit"


# ---------------------------------------------------------------------------
# Review / Score photos
# ---------------------------------------------------------------------------

def _review_photos(workspace: Path, state: dict, instruction: str) -> str:
    """Score and critique photos."""
    from reviewer import review_photo, review_batch, format_review, format_batch_review

    images = _extract_images(instruction)
    if not images:
        return "找不到要评分的图片。请提供图片路径。"

    # Detect category from instruction
    category = "auto"
    if re.search(r'风景|landscape|scenery|自然|nature', instruction, re.IGNORECASE):
        category = "landscape"
    elif re.search(r'人[像物]|portrait|face|街拍|street', instruction, re.IGNORECASE):
        category = "portrait"

    if len(images) == 1:
        log.info("Reviewing: %s (category=%s)", images[0].name, category)
        review = review_photo(images[0], category)
        output = format_review(review)
    else:
        log.info("Batch reviewing %d photos (category=%s)", len(images), category)
        reviews = review_batch(images[:20], category)
        output = format_batch_review(reviews)

    (workspace / "output.md").write_text(output, encoding="utf-8")
    (workspace / "summary.txt").write_text(output[:300], encoding="utf-8")
    return output


def _compare_photos(workspace: Path, state: dict, instruction: str) -> str:
    """Compare original vs edited versions."""
    from reviewer import compare_versions

    images = _extract_images(instruction)
    if len(images) < 2:
        return "需要两张图来对比——原图和修改后的。请提供两个路径。"

    original, edited = images[0], images[1]
    log.info("Comparing: %s vs %s", original.name, edited.name)

    result = compare_versions(original, edited)

    if "error" in result and "raw_review" not in result:
        _log_photo_failure("compare_failed", f"{original.name} vs {edited.name}: {result['error']}")
        return f"对比失败: {result['error']}"

    # Format output
    lines = [f"## 对比: {original.name} vs {edited.name}\n"]

    orig_score = result.get("original_score", "?")
    edit_score = result.get("edited_score", "?")
    improved = result.get("improvement", False)

    lines.append(f"原图: **{orig_score}/10** → 修改后: **{edit_score}/10** {'↑' if improved else '↓'}\n")

    changes = result.get("changes_detected", [])
    if changes:
        lines.append("**检测到的修改**: " + ", ".join(changes))

    improvements = result.get("improvements", [])
    if improvements:
        lines.append("**改善**: " + " | ".join(improvements))

    regressions = result.get("regressions", [])
    if regressions:
        lines.append("**退步**: " + " | ".join(regressions))

    suggestions = result.get("suggestions", [])
    if suggestions:
        lines.append("**建议**:")
        for s in suggestions:
            lines.append(f"  - {s}")

    summary = result.get("summary", "")
    if summary:
        lines.append(f"\n{summary}")

    output = "\n".join(lines)
    (workspace / "output.md").write_text(output, encoding="utf-8")
    return output


# ---------------------------------------------------------------------------
# Phase 1: Analyze photo(s) via vision model
# ---------------------------------------------------------------------------

def _analyze_photo(image_path: Path, style_profile: dict = None) -> str:
    """Analyze a single photo using Claude vision (via claude_act which can read images)."""
    skills_ctx = _load_skills_context()
    style_ctx = ""
    if style_profile:
        style_ctx = f"\n\n## Style Profile (learned from user's previous edits)\n{json.dumps(style_profile, ensure_ascii=False, indent=2)}"

    prompt = f"""Read and analyze this photograph: {image_path}

{skills_ctx}{style_ctx}

Analyze the photo on these dimensions:
1. **Technical**: Exposure (over/under/correct), white balance, noise, sharpness, dynamic range
2. **Composition**: Rule of thirds, leading lines, balance, negative space, framing, depth
3. **Light**: Quality (hard/soft), direction, color temperature, contrast ratio
4. **Color**: Dominant palette, harmony type, saturation level, color cast issues
5. **Mood/Story**: What emotion does the image convey? What story does it tell?
6. **Suggested edits**: Specific adjustments that would improve the image, with parameter values

For suggested edits, output a JSON block with Lightroom-compatible parameter names:
```json
{{
  "exposure": 0.0,
  "contrast": 0,
  "highlights": 0,
  "shadows": 0,
  "whites": 0,
  "blacks": 0,
  "clarity": 0,
  "dehaze": 0,
  "texture": 0,
  "vibrance": 0,
  "saturation": 0,
  "temperature": 0,
  "tint": 0,
  "sharpness": 0,
  "noise_reduction": 0,
  "vignette": 0,
  "crop_suggestion": null
}}
```

Be specific and opinionated. This user is an experienced photographer — don't sugarcoat."""

    return claude_act(prompt, cwd=image_path.parent, tier="light")


def _analyze_only(workspace: Path, state: dict, instruction: str) -> str:
    """Analyze photos without editing — just provide critique and suggestions."""
    images = _extract_images(instruction)
    if not images:
        return "找不到要分析的图片。请提供图片路径。"

    style_profile = _load_active_style()
    results = []

    for img in images[:10]:  # cap at 10
        log.info("Analyzing: %s", img.name)
        analysis = _analyze_photo(img, style_profile)
        if analysis:
            results.append(f"## {img.name}\n\n{analysis}")

    if not results:
        _log_photo_failure("analysis_failed", f"Vision model returned no results for {len(images)} images")
        return "分析失败——vision model 没返回结果。"

    output = "\n\n---\n\n".join(results)
    (workspace / "output.md").write_text(output, encoding="utf-8")

    summary = f"分析了 {len(results)} 张照片，详细评估见 output.md"
    (workspace / "summary.txt").write_text(summary, encoding="utf-8")
    return output


# ---------------------------------------------------------------------------
# Analyze + Plan edits (interactive)
# ---------------------------------------------------------------------------

def _analyze_and_plan(workspace: Path, state: dict, instruction: str) -> str:
    """Analyze photo(s) and propose an edit plan, then pause for review."""
    images = _extract_images(instruction)
    if not images:
        return "找不到要修的图片。请提供图片路径，比如 '/path/to/photo.jpg' 或一个文件夹路径。"

    style_profile = _load_active_style()

    # Analyze first image (or representative sample for batch)
    sample = images[:3]
    analyses = []
    for img in sample:
        log.info("Analyzing for edit plan: %s", img.name)
        analysis = _analyze_photo(img, style_profile)
        if analysis:
            analyses.append({"file": str(img), "name": img.name, "analysis": analysis})

    if not analyses:
        _log_photo_failure("analysis_failed", f"No analyses returned for {len(sample)} sample images")
        return "分析失败。"

    state["phase"] = "edit_review"
    state["images"] = [str(img) for img in images]
    state["analyses"] = analyses
    _save_state(workspace, state)

    # Format response
    output_parts = []
    for a in analyses:
        output_parts.append(f"### {a['name']}\n\n{a['analysis']}")

    plan_text = "\n\n---\n\n".join(output_parts)

    extra = ""
    if len(images) > len(sample):
        extra = f"\n\n(显示了 {len(sample)}/{len(images)} 张的分析，其余会用相同风格处理)"

    return (
        f"分析了 {len(sample)} 张照片:\n\n"
        f"{plan_text}{extra}\n\n"
        f"---\n\n"
        f"觉得怎么样？可以告诉我要调整的方向，或者说「ok」开始修图。\n"
        f"也可以说「导出preset」生成 Lightroom XMP 预设。"
    )


def _revise_plan(workspace: Path, state: dict, feedback: str) -> str:
    """Revise edit plan based on user feedback."""
    analyses = state.get("analyses", [])
    if not analyses:
        return "找不到分析结果，需要重新分析。"

    style_profile = _load_active_style()
    style_ctx = ""
    if style_profile:
        style_ctx = f"\nStyle profile: {json.dumps(style_profile, ensure_ascii=False)}"

    prompt = f"""你是一个专业修图助手。用户对修图方案有调整意见。

## 当前分析和建议
{json.dumps(analyses, ensure_ascii=False, indent=2)}
{style_ctx}

## 用户反馈
{feedback}

根据用户反馈修改建议的编辑参数。输出修改后的完整分析（保持原格式，包含JSON参数块）。"""

    revised = claude_think(prompt, timeout=120, tier="light")
    if not revised:
        _log_photo_failure("plan_revision_failed", "claude_think returned empty for plan revision")
        return "修改失败，请再说一次你想怎么调整。"

    state["analyses"] = [{"file": a["file"], "name": a["name"], "analysis": revised}
                         for a in analyses]
    _save_state(workspace, state)

    return f"方案已更新:\n\n{revised}\n\n---\n\n继续调整，或者说「ok」开始修图。"


# ---------------------------------------------------------------------------
# Execute edits
# ---------------------------------------------------------------------------

def _run_edits(workspace: Path, state: dict) -> str:
    """Apply the planned edits to all images."""
    from photo_editor import apply_edits, extract_edit_params

    images = [Path(p) for p in state.get("images", [])]
    analyses = state.get("analyses", [])

    if not images:
        return "没有要处理的图片。"

    output_dir = workspace / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Extract edit params from analyses
    params_map = {}
    for a in analyses:
        params = extract_edit_params(a.get("analysis", ""))
        if params:
            params_map[a["file"]] = params

    # If we have fewer analyses than images, use the first analysis as template
    default_params = None
    if params_map:
        default_params = list(params_map.values())[0]

    results = []
    for img in images:
        params = params_map.get(str(img), default_params)
        if not params:
            log.warning("No edit params for %s, skipping", img.name)
            continue

        out_path = output_dir / f"edited_{img.stem}.jpg"
        log.info("Editing: %s → %s", img.name, out_path.name)

        try:
            success = apply_edits(img, out_path, params)
            if success:
                results.append(f"- {img.name} → [edited_{img.stem}.jpg](file://output/edited_{img.stem}.jpg)")
            else:
                results.append(f"- {img.name} → 编辑失败")
        except Exception as e:
            log.error("Edit failed for %s: %s", img.name, e)
            _log_photo_failure("render_failed", f"{img.name}: {e}", slug=img.stem)
            results.append(f"- {img.name} → 错误: {e}")

    state["phase"] = "done"
    state["output"] = str(output_dir)
    _save_state(workspace, state)

    summary = f"修图完成！处理了 {len(results)} 张照片:\n\n" + "\n".join(results)
    summary += f"\n\n输出目录: {output_dir}"

    (workspace / "output.md").write_text(summary, encoding="utf-8")
    (workspace / "summary.txt").write_text(summary[:300], encoding="utf-8")

    return summary


# ---------------------------------------------------------------------------
# Style learning
# ---------------------------------------------------------------------------

def _start_style_learning(workspace: Path, state: dict, instruction: str) -> str:
    """Start learning editing style from reference photos."""
    ref_dir = _extract_path(instruction)
    if not ref_dir:
        ref_dir = _REFERENCE_DIR

    if not ref_dir.exists():
        return (
            f"参考图目录不存在: {ref_dir}\n\n"
            f"请把你修过的成品图放到 `{_REFERENCE_DIR}` 里，\n"
            f"或者指定一个文件夹路径。最好是 RAW + 对应的修后 JPEG。"
        )

    images = _find_images(ref_dir)
    if not images:
        return f"在 {ref_dir} 里没找到图片。"

    state["phase"] = "style_learning"
    state["reference_dir"] = str(ref_dir)
    _save_state(workspace, state)

    # Analyze reference images to extract style
    from style_learner import learn_style

    log.info("Learning style from %d reference images in %s", len(images), ref_dir)
    style_profile = learn_style(images, workspace)

    if not style_profile:
        state["phase"] = ""
        _save_state(workspace, state)
        _log_photo_failure("style_learning_failed", f"learn_style returned empty for {len(images)} reference images")
        return "风格学习失败——分析没返回结果。"

    # Save style profile
    _STYLE_DIR.mkdir(parents=True, exist_ok=True)
    style_name = state.get("style_name", "default")
    style_path = _STYLE_DIR / f"{style_name}.json"
    style_path.write_text(
        json.dumps(style_profile, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    state["phase"] = ""
    state["style_profile"] = style_name
    _save_state(workspace, state)

    summary = _format_style_summary(style_profile)
    output = f"风格学习完成！分析了 {len(images)} 张参考图。\n\n{summary}"
    output += f"\n\nStyle profile 已保存到: {style_path}"
    output += "\n\n之后修图时会自动参考这个风格。"

    (workspace / "output.md").write_text(output, encoding="utf-8")
    return output


def _continue_style_learning(workspace: Path, state: dict, instruction: str) -> str:
    """Continue an in-progress style learning session."""
    state["phase"] = ""
    _save_state(workspace, state)
    return _start_style_learning(workspace, state, instruction)


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

def _start_batch(workspace: Path, state: dict, instruction: str) -> str:
    """Batch process a folder of images."""
    target_dir = _extract_path(instruction)
    if not target_dir:
        return "请提供要批量处理的文件夹路径。"

    images = _find_images(target_dir)
    if not images:
        return f"在 {target_dir} 里没找到图片。"

    style_profile = _load_active_style()
    if not style_profile:
        return (
            "还没有学习过你的修图风格。\n\n"
            "先把你修过的成品图放到参考目录，然后说「学习我的风格」。\n"
            "或者你可以手动指定修图方向。"
        )

    # For batch, analyze a sample then apply uniformly
    state["phase"] = "edit_review"
    state["images"] = [str(img) for img in images]
    state["batch_mode"] = True
    _save_state(workspace, state)

    # Analyze 2-3 representative images
    sample = images[:min(3, len(images))]
    analyses = []
    for img in sample:
        log.info("Batch sample analysis: %s", img.name)
        analysis = _analyze_photo(img, style_profile)
        if analysis:
            analyses.append({"file": str(img), "name": img.name, "analysis": analysis})

    state["analyses"] = analyses
    _save_state(workspace, state)

    plan_text = "\n\n".join(f"### {a['name']}\n{a['analysis']}" for a in analyses)

    return (
        f"找到 {len(images)} 张照片。抽样分析了 {len(sample)} 张:\n\n"
        f"{plan_text}\n\n---\n\n"
        f"这个方向会应用到所有 {len(images)} 张。"
        f"觉得怎么样？说「ok」开始批量处理。"
    )


# ---------------------------------------------------------------------------
# Preset / LUT generation
# ---------------------------------------------------------------------------

def _generate_preset(workspace: Path, state: dict, instruction: str) -> str:
    """Generate a Lightroom XMP preset or .cube LUT from style profile."""
    from photo_editor import generate_xmp_preset, generate_cube_lut

    style_profile = _load_active_style()
    if not style_profile:
        return "还没有风格 profile。先用「学习我的风格」从参考图学习。"

    output_dir = workspace / "presets"
    output_dir.mkdir(parents=True, exist_ok=True)

    results = []

    # Generate XMP preset
    xmp_path = output_dir / "mira_style.xmp"
    if generate_xmp_preset(style_profile, xmp_path):
        results.append(f"- Lightroom XMP 预设: [mira_style.xmp](file://presets/mira_style.xmp)")

    # Generate .cube LUT
    lut_path = output_dir / "mira_style.cube"
    if generate_cube_lut(style_profile, lut_path):
        results.append(f"- 3D LUT: [mira_style.cube](file://presets/mira_style.cube)")

    if not results:
        _log_photo_failure("preset_generation_failed", "Both XMP and LUT generation failed")
        return "预设生成失败。"

    state["phase"] = "done"
    _save_state(workspace, state)

    output = "预设生成完成:\n\n" + "\n".join(results)
    output += "\n\n导入方法:\n"
    output += "- **Lightroom**: File → Import Profiles & Presets → 选择 .xmp 文件\n"
    output += "- **DaVinci Resolve / Premiere**: 导入 .cube LUT\n"

    (workspace / "output.md").write_text(output, encoding="utf-8")
    return output


# ---------------------------------------------------------------------------
# Style profile management
# ---------------------------------------------------------------------------

def _load_active_style() -> dict | None:
    """Load the active style profile."""
    default_path = _STYLE_DIR / "default.json"
    if default_path.exists():
        try:
            return json.loads(default_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return None


def _format_style_summary(profile: dict) -> str:
    """Format a style profile as human-readable summary."""
    parts = []
    if "overall_mood" in profile:
        parts.append(f"**整体风格**: {profile['overall_mood']}")
    if "color_tendency" in profile:
        parts.append(f"**色彩倾向**: {profile['color_tendency']}")
    if "tone_curve" in profile:
        parts.append(f"**调性**: {profile['tone_curve']}")
    if "common_adjustments" in profile:
        adj = profile["common_adjustments"]
        adj_lines = [f"  - {k}: {v}" for k, v in adj.items()]
        parts.append("**常用调整**:\n" + "\n".join(adj_lines))
    if "subjects" in profile:
        parts.append(f"**常见题材**: {', '.join(profile['subjects'])}")
    if "signature_traits" in profile:
        parts.append(f"**标志性特征**: {', '.join(profile['signature_traits'])}")
    return "\n".join(parts) if parts else json.dumps(profile, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Skills context
# ---------------------------------------------------------------------------

def _load_skills_context() -> str:
    """Load relevant photo editing skills for prompt injection."""
    if not _SKILLS_DIR.exists():
        return ""
    skills = []
    for f in sorted(_SKILLS_DIR.glob("*.md")):
        content = f.read_text(encoding="utf-8")
        # Include just the one-liner and techniques summary
        skills.append(f"### {f.stem}\n{content[:500]}")
    if skills:
        return "## Photo Editing Skills\n\n" + "\n\n".join(skills[:5])
    return ""


# ---------------------------------------------------------------------------
# File/path utilities
# ---------------------------------------------------------------------------

def _find_images(directory: Path) -> list[Path]:
    """Find all image files in a directory (non-recursive)."""
    images = []
    if directory.is_file() and directory.suffix.lower() in _IMAGE_EXTS:
        return [directory]
    if not directory.is_dir():
        return []
    for f in sorted(directory.iterdir()):
        if f.is_file() and f.suffix.lower() in _IMAGE_EXTS:
            images.append(f)
    return images


def _extract_images(instruction: str) -> list[Path]:
    """Extract image paths from instruction text."""
    path = _extract_path(instruction)
    if not path:
        # Check for @file: references
        path = _extract_file_ref(instruction)
    if not path:
        return []
    if path.is_file():
        return [path]
    return _find_images(path)


def _extract_path(instruction: str) -> Path | None:
    """Extract a file/directory path from instruction text."""
    patterns = [
        r'"([^"]+)"',
        r"'([^']+)'",
        r'(/\S+)',
        r'(~/\S+)',
    ]
    for p in patterns:
        m = re.search(p, instruction)
        if m:
            path = Path(m.group(1)).expanduser()
            if path.exists():
                return path
    return None


def _extract_file_ref(instruction: str) -> Path | None:
    """Extract @file: references from iOS file picker."""
    matches = re.findall(r'@file:(\S+)', instruction)
    for ref in matches:
        path = Path(ref).expanduser()
        if path.exists():
            return path
    return None


def _is_approval(text: str) -> bool:
    return bool(_APPROVE_PATTERNS.search(text))


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def _load_state(workspace: Path) -> dict:
    state_path = workspace / _STATE_FILE
    if state_path.exists():
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(workspace: Path, state: dict):
    state_path = workspace / _STATE_FILE
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Photo editing agent")
    parser.add_argument("--input", required=True, help="Image file or directory")
    parser.add_argument("--learn", action="store_true", help="Learn style from input images")
    parser.add_argument("--batch", action="store_true", help="Batch process all images")
    parser.add_argument("--preset", action="store_true", help="Generate XMP preset / LUT")
    parser.add_argument("--work-dir", help="Working directory")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        print(f"Error: {input_path} does not exist")
        sys.exit(1)

    work_dir = Path(args.work_dir).expanduser() if args.work_dir else input_path.parent / ".photo_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    if args.learn:
        result = handle(work_dir, "cli", f"学习风格 '{input_path}'", "cli", "cli")
    elif args.preset:
        result = handle(work_dir, "cli", f"导出preset", "cli", "cli")
    elif args.batch:
        result = handle(work_dir, "cli", f"批量修图 '{input_path}'", "cli", "cli")
    else:
        result = handle(work_dir, "cli", f"修图 '{input_path}'", "cli", "cli")
    print(result)


if __name__ == "__main__":
    main()
