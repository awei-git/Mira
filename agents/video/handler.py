"""Video agent handler — orchestrates the full editing pipeline.

Enhanced 6-phase pipeline with see-think-do-review:
    Phase 0: PREPARE  — music beat analysis + content mode detection
    Phase 1: SEE      — per-clip vision analysis (Gemini native video)
    Phase 2: THINK    — taste profile + beat map → edit plan (Claude)
    Phase 3: DO       — per-clip rendering with adaptive color grading (ffmpeg)
    Phase 4: MIX      — music mixing with speech ducking
    Phase 5: REVIEW   — score rough cut, propose fixes, iterate

Interactive workflow (via Mira app):
    1. User sends video path → agent runs Phase 0+1+2, returns screenplay
    2. User discusses / adjusts → agent revises screenplay
    3. User approves → agent runs Phase 3+4+5, auto-iterates if needed

Standalone:
    python handler.py --input /path/to/videos [--music /path/to/music.mp3]
"""
import argparse
import json
import logging
import re
import shutil
import sys
from pathlib import Path

# Add shared modules to path
_AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_AGENTS_DIR.parent / "lib"))

from config import (
    VIDEO_MAX_REVIEW_ITERATIONS, VIDEO_REVIEW_SCORE,
)
from publish.preflight import preflight_check
from llm import claude_think, _get_api_key
from scene_analyzer import analyze_all, analyze_all_v2
from triage import triage_all
from screenplay import generate_screenplay, generate_edit_plan
from editor import (
    execute_edit_plan, re_render_clips, assemble_rough_cut, mix_with_music,
    check_color_continuity,
)
from music_mixer import mix_music, find_music, auto_select_music, get_duration
from beat_analyzer import analyze_beats, summarize_beat_map
from clip_grader import grade_clip, detect_content_mode
from video_reviewer import (
    review_rough_cut, should_iterate, format_review_summary,
)

log = logging.getLogger("video.handler")


def _check_disk_space(output_dir: str, required_gb: float = 2.0) -> bool:
    """Check if enough disk space is available for rendering."""
    try:
        usage = shutil.disk_usage(output_dir)
        free_gb = usage.free / (1024 ** 3)
        if free_gb < required_gb:
            log.error("Insufficient disk space: %.1f GB free, need %.1f GB", free_gb, required_gb)
            return False
        return True
    except Exception as e:
        log.warning("Cannot check disk space: %s", e)
        return True  # Proceed if can't check


def _log_video_failure(step: str, error_msg: str, slug: str = "video"):
    try:
        from ops.failure_log import record_failure
        record_failure(pipeline="video", step=step, slug=slug,
                       error_type="video_agent_error", error_message=error_msg[:500])
    except Exception:
        pass


# Signals that user approves the screenplay
_APPROVE_PATTERNS = re.compile(
    r'\b(ok|好的?|可以|开始[剪切]|go|cut|proceed|执行|没问题|就这样|lgtm|开剪)\b',
    re.IGNORECASE,
)

_STATE_FILE = "video_state.json"
_TASTE_PROFILE_PATH = Path(__file__).parent / "editing_taste_profile.md"
_MAX_REVIEW_ITERATIONS = VIDEO_MAX_REVIEW_ITERATIONS
_REVIEW_THRESHOLD = VIDEO_REVIEW_SCORE


def preflight(workspace: Path, task_id: str, instruction: str,
              sender: str, thread_id: str, **kwargs) -> tuple[bool, str]:
    """Block video jobs that have no resolvable input footage or review state."""
    state = _load_state(workspace)
    phase = state.get("phase", "")
    preflight_text = instruction.strip() or phase or "video task"
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

    if phase == "done":
        return True, ""

    if phase == "screenplay_review":
        input_dir = Path(state.get("input_dir", "")) if state.get("input_dir") else None
        output_dir = Path(state.get("output_dir", "")) if state.get("output_dir") else None
        if not input_dir or not input_dir.exists():
            return False, "PREFLIGHT BLOCKED [video]: review state is missing input footage"
        if not output_dir:
            return False, "PREFLIGHT BLOCKED [video]: review state is missing output directory"
        if _is_approval(instruction) and not _check_disk_space(str(output_dir)):
            return False, "PREFLIGHT BLOCKED [video]: 磁盘空间不足，无法开始渲染"
        return True, ""

    input_dir = _extract_path(instruction) or _extract_file_ref(instruction)
    if not input_dir or not input_dir.exists():
        return False, "PREFLIGHT BLOCKED [video]: 找不到视频目录或文件引用"

    return True, ""


def handle(workspace: Path, task_id: str, instruction: str,
           sender: str, thread_id: str, **kwargs) -> str:
    """Handle a video editing task from Mira's task_worker."""
    workspace.mkdir(parents=True, exist_ok=True)
    state = _load_state(workspace)
    phase = state.get("phase", "")

    if phase == "done":
        return f"视频已经剪辑完成: {state.get('output', '')}"

    if phase == "screenplay_review":
        if _is_approval(instruction):
            return _run_render_and_review(workspace, state)
        else:
            return _revise_screenplay(workspace, state, instruction)

    # Initial request — need video path
    input_dir = _extract_path(instruction)
    if not input_dir:
        input_dir = _extract_file_ref(instruction)

    if not input_dir or not input_dir.exists():
        return f"找不到视频目录: {input_dir or '(未指定路径)'}"

    music_path = _extract_music_path(instruction)
    output_dir = workspace / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    state.update({
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "music_path": str(music_path) if music_path else None,
        "target_minutes": 4.0,
        "transcribe": True,
    })
    _save_state(workspace, state)

    try:
        return _run_prepare_see_think(workspace, state)
    except Exception as e:
        log.error("Video pipeline failed: %s", e, exc_info=True)
        _log_video_failure("pipeline_exception", str(e), slug=task_id)
        return f"视频处理失败: {e}"


# ---------------------------------------------------------------------------
# Phase 0+1+2: PREPARE + SEE + THINK
# ---------------------------------------------------------------------------

def _run_prepare_see_think(workspace: Path, state: dict) -> str:
    """Phase 0+1+2: Beat analysis, scene analysis, edit plan generation."""
    input_dir = Path(state["input_dir"])
    music_path = Path(state["music_path"]) if state.get("music_path") else None
    target_minutes = state.get("target_minutes", 4.0)

    gemini_key = _get_api_key("gemini")
    if not gemini_key:
        return "需要 Gemini API key (secrets.yml → api_keys → gemini)"

    # ── Phase 0: PREPARE (beat analysis) ──
    beat_map = None
    if music_path and music_path.exists():
        log.info("=== Phase 0: Beat Analysis ===")
        beat_map_path = workspace / "beat_map.json"
        if beat_map_path.exists():
            beat_map = json.loads(beat_map_path.read_text())
        else:
            try:
                beat_map = analyze_beats(music_path, workspace)
                log.info("Beat analysis: %.0f BPM, %d phrases",
                         beat_map["tempo"], len(beat_map["phrases"]))
            except Exception as e:
                log.warning("Beat analysis failed: %s", e)
                _log_video_failure("beat_analysis_failed", str(e),
                                   slug=music_path.stem)

    # ── Phase 0.5: TRIAGE (local pre-filter) ──
    log.info("=== Phase 0.5: Triage ===")
    triage_path = workspace / "triage.json"
    if triage_path.exists():
        triage_result = json.loads(triage_path.read_text())
    else:
        triage_result = triage_all(input_dir, workspace)

    kept = triage_result.get("kept", 0)
    rejected = triage_result.get("rejected", 0)
    log.info("Triage: %d kept, %d rejected out of %d",
             kept, rejected, triage_result.get("total", 0))

    # ── Phase 1: SEE (supercut analysis — single Gemini call) ──
    log.info("=== Phase 1: Scene Analysis (v2 supercut) ===")
    scene_log_path = workspace / "scene_log.json"

    if scene_log_path.exists():
        scene_log = json.loads(scene_log_path.read_text())
    else:
        scene_log = analyze_all_v2(triage_result, workspace, gemini_key)
        if not scene_log.get("scenes"):
            # Fallback to per-clip analysis if supercut fails
            log.warning("Supercut analysis failed, falling back to per-clip")
            openai_key = _get_api_key("openai")
            scene_log = analyze_all(input_dir, workspace, gemini_key, openai_key,
                                    transcribe=state.get("transcribe", False))
        if not scene_log.get("scenes"):
            _log_video_failure("scene_analysis_failed", "No valid scenes found in footage",
                               slug=Path(state["input_dir"]).name)
            return "视频分析没有找到有效场景"

    total_dur = scene_log.get("total_duration", 0)
    n_scenes = len(scene_log.get("scenes", []))
    log.info("Phase 1: %d scenes from %.0fs footage", n_scenes, total_dur)

    # Detect content mode from scene analysis
    visual_scenes = [s for s in scene_log.get("scenes", [])
                     if s.get("type") != "transcript"]
    content_mode = detect_content_mode(visual_scenes)
    state["content_mode"] = content_mode
    log.info("Content mode: %s", content_mode)

    # ── Phase 2: THINK (edit plan) ──
    log.info("=== Phase 2: Edit Plan ===")
    taste_profile = _load_taste_profile()

    if beat_map:
        # Enhanced path: use beat-aware edit plan
        screenplay, edit_plan = generate_edit_plan(
            scene_log, beat_map, taste_profile, workspace,
            content_mode=content_mode,
            claude_think_fn=claude_think,
        )
        state["has_edit_plan"] = True
    else:
        # Fallback: traditional screenplay (no music yet)
        screenplay = generate_screenplay(
            scene_log, workspace,
            target_minutes=target_minutes,
            claude_think_fn=claude_think,
        )
        edit_plan = {}
        state["has_edit_plan"] = False

    if not screenplay:
        _log_video_failure("screenplay_failed",
                           f"generate_screenplay/edit_plan returned empty for {n_scenes} scenes",
                           slug=Path(state["input_dir"]).name)
        return "Screenplay 生成失败"

    # Pause for user review
    state["phase"] = "screenplay_review"
    state["screenplay_version"] = 1
    _save_state(workspace, state)

    beat_info = ""
    if beat_map:
        beat_info = f"\n- 配乐: {beat_map['tempo']:.0f} BPM, {len(beat_map['phrases'])} 个 phrase"

    plan_info = ""
    if edit_plan and edit_plan.get("clips"):
        plan_info = f"\n- Edit plan: {len(edit_plan['clips'])} 个 clip"

    return (
        f"分析了 {scene_log.get('video_count', 0)} 个视频 "
        f"({total_dur / 60:.1f} 分钟素材, {n_scenes} 个场景)\n"
        f"- 模式: {content_mode}"
        f"{beat_info}{plan_info}\n\n"
        f"--- Screenplay ---\n\n{screenplay}\n\n---\n\n"
        f"觉得怎么样？可以直接告诉我要调整的地方，或者说「ok」开始剪辑。"
    )


# ---------------------------------------------------------------------------
# Phase 3+4+5: DO + MIX + REVIEW
# ---------------------------------------------------------------------------

def _run_render_and_review(workspace: Path, state: dict) -> str:
    """Phase 3+4+5: Render, mix music, review, and iterate."""
    input_dir = Path(state["input_dir"])
    output_dir = Path(state["output_dir"])
    music_path = Path(state["music_path"]) if state.get("music_path") else None
    content_mode = state.get("content_mode", "family")

    # Pre-render resource check: disk space
    output_dir.mkdir(parents=True, exist_ok=True)
    if not _check_disk_space(str(output_dir)):
        error_msg = "磁盘空间不足 (需要至少 2 GB 可用空间)"
        _log_video_failure("disk_space_check", error_msg,
                           slug=Path(state["input_dir"]).name)
        (workspace / "output.md").write_text(f"# Error\n\n{error_msg}\n")
        return error_msg

    gemini_key = _get_api_key("gemini")

    # Load edit plan if available
    edit_plan_path = workspace / "edit_plan.json"
    edit_plan = {}
    if state.get("has_edit_plan") and edit_plan_path.exists():
        edit_plan = json.loads(edit_plan_path.read_text())

    # ── Phase 3: DO (render) ──
    log.info("=== Phase 3: Render ===")
    final_path = output_dir / "final.mp4"

    if edit_plan and edit_plan.get("clips"):
        # New path: use edit_plan.json with per-clip adaptive grading
        result = execute_edit_plan(
            edit_plan, input_dir, output_dir,
            music_path=music_path or Path(""),
            clip_grader_fn=grade_clip,
        )
        if result:
            final_path = result
        else:
            _log_video_failure("render_failed", "execute_edit_plan returned None",
                               slug=Path(state["input_dir"]).name)
            return "剪辑渲染失败"
    else:
        # Fallback: old pipeline
        return _run_cut_legacy(workspace, state)

    # Check color continuity
    clips_dir = output_dir / "clips"
    if clips_dir.exists():
        continuity_issues = check_color_continuity(clips_dir)
        if continuity_issues:
            log.warning("Color continuity: %d issues", len(continuity_issues))
            state["color_continuity_issues"] = len(continuity_issues)

    log.info("Phase 3+4 complete: %s", final_path)

    # ── Phase 5: REVIEW ──
    if gemini_key and final_path.exists():
        log.info("=== Phase 5: Review ===")
        taste_profile = _load_taste_profile()

        review = review_rough_cut(
            final_path, edit_plan, taste_profile,
            gemini_key, output_dir,
        )

        review_summary = format_review_summary(review)
        log.info("Review: overall %.1f/10", review.get("overall", 0))

        # Iterate if needed
        iteration = 0
        while (should_iterate(review, _REVIEW_THRESHOLD)
               and iteration < _MAX_REVIEW_ITERATIONS):
            iteration += 1
            log.info("=== Review iteration %d ===", iteration)

            fixes = review.get("fix_proposals", [])
            if not fixes:
                _log_video_failure("review_failed",
                                   f"Review iteration {iteration}: no fix proposals returned (score={review.get('overall', 0)})",
                                   slug=Path(state["input_dir"]).name)
                break

            clips_dir = output_dir / "clips"
            re_rendered = re_render_clips(
                fixes, edit_plan, clips_dir, input_dir,
                clip_grader_fn=grade_clip,
            )

            if re_rendered:
                # Re-assemble with updated clips
                clip_paths = sorted(clips_dir.glob("*.mp4"))
                rough = output_dir / "rough_cut.mp4"
                if assemble_rough_cut(clip_paths, rough):
                    if music_path and music_path.exists():
                        beat_map_path = workspace / "beat_map.json"
                        song_dur = 180
                        if beat_map_path.exists():
                            bm = json.loads(beat_map_path.read_text())
                            song_dur = bm.get("duration", 180)
                        new_final = output_dir / f"final_v{iteration + 1}.mp4"
                        mix_with_music(rough, music_path, new_final, song_dur)
                        if new_final.exists():
                            final_path = new_final
                    else:
                        final_path = rough

                # Re-review
                review = review_rough_cut(
                    final_path, edit_plan, taste_profile,
                    gemini_key, output_dir,
                )
                review_summary = format_review_summary(review)
                log.info("Iteration %d: overall %.1f/10",
                         iteration, review.get("overall", 0))
            else:
                _log_video_failure("review_failed",
                                   f"re_render_clips returned empty on iteration {iteration}",
                                   slug=Path(state["input_dir"]).name)
                break

        state["review"] = review
        state["review_iterations"] = iteration
    else:
        review_summary = "(review skipped — no Gemini key)"

    # ── Done ──
    state["phase"] = "done"
    state["output"] = str(final_path)
    _save_state(workspace, state)

    summary = (
        f"视频剪辑完成！\n\n"
        f"- 模式: {content_mode}\n"
        f"- 输出: {final_path}\n"
        f"- 时长: {get_duration(final_path):.0f}s\n\n"
        f"## Review\n{review_summary}\n"
    )

    (workspace / "summary.md").write_text(summary)
    return summary


# ---------------------------------------------------------------------------
# Legacy cut (fallback when no edit_plan.json)
# ---------------------------------------------------------------------------

def _run_cut_legacy(workspace: Path, state: dict) -> str:
    """Phase 3+4 legacy path: LLM-generated ffmpeg script."""
    from editor import parse_screenplay_clips
    input_dir = Path(state["input_dir"])
    output_dir = Path(state["output_dir"])
    music_path = Path(state["music_path"]) if state.get("music_path") else None

    sp_path = workspace / "screenplay.md"
    if not sp_path.exists():
        return "找不到 screenplay"

    screenplay = sp_path.read_text()

    # Use old editor path
    from editor import render_clip, create_title_card, assemble_rough_cut as assemble
    rough_cut = output_dir / "rough_cut.mp4"
    final_output = output_dir / "final.mp4"

    # Try music mixing with old pipeline
    if music_path and music_path.exists():
        success = mix_music(rough_cut, music_path, final_output)
        if success:
            state["phase"] = "done"
            state["output"] = str(final_output)
            _save_state(workspace, state)
            return f"视频剪辑完成: {final_output}"

    state["phase"] = "done"
    state["output"] = str(rough_cut)
    _save_state(workspace, state)
    return f"视频剪辑完成 (无音乐): {rough_cut}"


# ---------------------------------------------------------------------------
# Screenplay revision
# ---------------------------------------------------------------------------

def _revise_screenplay(workspace: Path, state: dict, feedback: str) -> str:
    """Revise the screenplay based on user feedback."""
    sp_path = workspace / "screenplay.md"
    scene_log_path = workspace / "scene_log.json"

    if not sp_path.exists():
        return "找不到 screenplay，需要重新生成"

    current_sp = sp_path.read_text()
    scene_log = {}
    if scene_log_path.exists():
        scene_log = json.loads(scene_log_path.read_text())

    prompt = f"""你是一个视频剪辑助手。用户对 screenplay 有修改意见，请根据反馈修改。

## 当前 Screenplay
{current_sp}

## 可用素材
{_format_scenes_brief(scene_log)}

## 用户反馈
{feedback}

根据反馈修改 screenplay，保持相同格式，输出完整修改版。"""

    revised = claude_think(prompt, timeout=120, tier="light")
    if not revised:
        _log_video_failure("screenplay_revision_failed", "claude_think returned empty for revision")
        return "修改失败，请再试"

    version = state.get("screenplay_version", 1) + 1
    backup = workspace / f"screenplay_v{version - 1}.md"
    sp_path.rename(backup)
    sp_path.write_text(revised)

    state["screenplay_version"] = version
    _save_state(workspace, state)

    return (
        f"Screenplay v{version}:\n\n{revised}\n\n---\n\n"
        f"继续调整，或说「ok」开始剪辑。"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_taste_profile() -> str:
    """Load the editing taste profile."""
    if _TASTE_PROFILE_PATH.exists():
        return _TASTE_PROFILE_PATH.read_text()
    return ""


def _load_state(workspace: Path) -> dict:
    state_path = workspace / _STATE_FILE
    if state_path.exists():
        try:
            return json.loads(state_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(workspace: Path, state: dict):
    (workspace / _STATE_FILE).write_text(
        json.dumps(state, ensure_ascii=False, indent=2))


def _is_approval(text: str) -> bool:
    return bool(_APPROVE_PATTERNS.search(text))


def _format_scenes_brief(scene_log: dict) -> str:
    scenes = scene_log.get("scenes", [])
    if not scenes:
        return "(无场景数据)"
    lines = []
    for s in scenes[:50]:
        if s.get("type") == "overall_analysis":
            continue
        ts = s.get("timestamp_str", "?")
        end_ts = s.get("end_timestamp_str", "")
        ts_range = f"{ts}-{end_ts}" if end_ts else ts
        desc = s.get("description", "")[:80]
        lines.append(f"- [{s.get('file', '?')} @ {ts_range}] {desc}")
    return "\n".join(lines)


def _extract_path(instruction: str) -> Path | None:
    for p in [r'"([^"]+)"', r"'([^']+)'", r'(/\S+)', r'(~/\S+)']:
        m = re.search(p, instruction)
        if m:
            path = Path(m.group(1)).expanduser()
            if path.exists():
                return path
    return None


def _extract_file_ref(instruction: str) -> Path | None:
    for ref in re.findall(r'@file:(\S+)', instruction):
        path = Path(ref).expanduser()
        if path.exists():
            return path.parent if path.is_file() else path
    return None


def _extract_music_path(instruction: str) -> Path | None:
    m = re.search(r'(?:music|音乐|配乐)[:\s]+["\']?(\S+)["\']?',
                  instruction, re.IGNORECASE)
    if m:
        path = Path(m.group(1)).expanduser()
        if path.exists():
            return path
    return None


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def run_pipeline_full(input_dir: Path, work_dir: Path, output_dir: Path,
                      music_path: Path = None,
                      target_minutes: float = 4.0) -> str:
    """Run the full pipeline non-interactively."""
    state = {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "music_path": str(music_path) if music_path else None,
        "target_minutes": target_minutes,
        "transcribe": True,
    }
    _save_state(work_dir, state)

    result = _run_prepare_see_think(work_dir, state)
    print(result)

    state = _load_state(work_dir)
    if state.get("phase") == "screenplay_review":
        print("\n>>> Proceeding to render...\n")
        result = _run_render_and_review(work_dir, state)

    return result


def main():
    parser = argparse.ArgumentParser(description="Video editing pipeline (enhanced)")
    parser.add_argument("--input", required=True, help="Directory with video files")
    parser.add_argument("--output", help="Output directory")
    parser.add_argument("--music", help="Background music file")
    parser.add_argument("--target-minutes", type=float, default=4.0)
    parser.add_argument("--work-dir", help="Working directory")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    input_dir = Path(args.input).expanduser().resolve()
    if not input_dir.exists():
        print(f"Error: {input_dir} does not exist")
        sys.exit(1)

    output_dir = Path(args.output).expanduser() if args.output else input_dir / "output"
    work_dir = Path(args.work_dir).expanduser() if args.work_dir else input_dir / ".video_work"
    music_path = Path(args.music).expanduser() if args.music else None

    result = run_pipeline_full(input_dir, work_dir, output_dir,
                               music_path, args.target_minutes)
    print(result)


if __name__ == "__main__":
    main()
