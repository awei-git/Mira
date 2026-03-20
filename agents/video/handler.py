"""Video agent handler — orchestrates the full editing pipeline.

Interactive workflow (via Mira app):
    1. User sends video path → agent runs Phase 1+2, returns screenplay
    2. User discusses / adjusts → agent revises screenplay
    3. User approves ("ok", "开始剪") → agent runs Phase 3+4, returns final video

Standalone:
    python handler.py --input /path/to/videos [--music /path/to/music.mp3] [--output /path/to/output.mp4]

Pipeline phases:
    1. Scene analysis (ffmpeg + Gemini Vision + Whisper)
    2. Screenplay generation (Claude)
    3. Automated editing (ffmpeg)
    4. Music mixing (ffmpeg)
"""
import argparse
import json
import logging
import re
import sys
from pathlib import Path

# Add shared modules to path
_AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_AGENTS_DIR / "shared"))

from sub_agent import claude_think, _get_api_key
from scene_analyzer import analyze_all
from screenplay import generate_screenplay
from editor import generate_edit_command, execute_edit
from music_mixer import mix_music, find_music, auto_select_music, get_duration

log = logging.getLogger("video.handler")

# Signals that user approves the screenplay and wants to proceed to cutting
_APPROVE_PATTERNS = re.compile(
    r'\b(ok|好的?|可以|开始[剪切]|go|cut|proceed|执行|没问题|就这样|lgtm|开剪)\b',
    re.IGNORECASE,
)

# State file tracking pipeline progress
_STATE_FILE = "video_state.json"


def handle(workspace: Path, task_id: str, instruction: str,
           sender: str, thread_id: str, **kwargs) -> str:
    """Handle a video editing task from Mira's task_worker.

    Detects conversation phase from workspace state:
    - No state → initial request, run Phase 1+2, return screenplay
    - State = "screenplay_review" + approval signal → run Phase 3+4
    - State = "screenplay_review" + adjustment → revise screenplay
    - State = "done" → already finished
    """
    workspace.mkdir(parents=True, exist_ok=True)
    state = _load_state(workspace)

    phase = state.get("phase", "")

    if phase == "done":
        return f"视频已经剪辑完成: {state.get('output', '')}"

    if phase == "screenplay_review":
        # User is responding to the screenplay
        if _is_approval(instruction):
            return _run_cut(workspace, state)
        else:
            return _revise_screenplay(workspace, state, instruction)

    # Initial request — need video path
    input_dir = _extract_path(instruction)

    # Also check @file: references (from iOS file picker)
    if not input_dir:
        input_dir = _extract_file_ref(instruction)

    if not input_dir or not input_dir.exists():
        return f"找不到视频目录: {input_dir or '(未指定路径)'}\n\n用法: 发送视频文件夹路径，或用 @@ 选择文件夹"

    music_path = _extract_music_path(instruction)
    transcribe = _wants_transcription(instruction)
    output_dir = workspace / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save config to state
    state.update({
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "music_path": str(music_path) if music_path else None,
        "target_minutes": 4.0,
        "transcribe": True,  # always transcribe — detect dialogue for ducking
    })
    _save_state(workspace, state)

    try:
        return _run_analyze_and_screenplay(workspace, state)
    except Exception as e:
        log.error("Video pipeline failed: %s", e, exc_info=True)
        return f"视频处理失败: {e}"


def _run_analyze_and_screenplay(workspace: Path, state: dict) -> str:
    """Phase 1+2: Analyze footage and generate screenplay, then pause for review."""
    input_dir = Path(state["input_dir"])
    target_minutes = state.get("target_minutes", 4.0)
    transcribe = state.get("transcribe", False)

    gemini_key = _get_api_key("gemini")
    openai_key = _get_api_key("openai")

    if not gemini_key:
        return "需要 Gemini API key 才能分析视频画面 (secrets.yml → api_keys → gemini)"

    # ── Phase 1: Scene Analysis ──
    log.info("=== Phase 1: Scene Analysis ===%s", " (with transcription)" if transcribe else "")
    scene_log_path = workspace / "scene_log.json"

    if scene_log_path.exists():
        log.info("Using existing scene log")
        scene_log = json.loads(scene_log_path.read_text(encoding="utf-8"))
    else:
        scene_log = analyze_all(input_dir, workspace, gemini_key, openai_key,
                                transcribe=transcribe)
        if not scene_log.get("scenes"):
            return "视频分析没有找到有效场景，请检查视频文件"

    total_dur = scene_log.get("total_duration", 0)
    n_scenes = len(scene_log.get("scenes", []))
    log.info("Phase 1 complete: %d scenes from %.0fs footage", n_scenes, total_dur)

    # ── Phase 2: Screenplay ──
    log.info("=== Phase 2: Screenplay ===")
    sp_path = workspace / "screenplay.md"

    if sp_path.exists():
        log.info("Using existing screenplay")
        screenplay = sp_path.read_text(encoding="utf-8")
    else:
        screenplay = generate_screenplay(
            scene_log, workspace,
            target_minutes=target_minutes,
            claude_think_fn=claude_think,
        )
        if not screenplay:
            return "Screenplay 生成失败"

    log.info("Phase 2 complete: screenplay ready for review")

    # Update state — pause here for user review
    state["phase"] = "screenplay_review"
    state["screenplay_version"] = 1
    _save_state(workspace, state)

    return (
        f"分析了 {scene_log.get('video_count', 0)} 个视频 "
        f"({total_dur / 60:.1f} 分钟素材, {n_scenes} 个场景)\n\n"
        f"--- Screenplay ---\n\n"
        f"{screenplay}\n\n"
        f"---\n\n"
        f"觉得怎么样？可以直接告诉我要调整的地方，或者说「ok」开始剪辑。"
    )


def _revise_screenplay(workspace: Path, state: dict, feedback: str) -> str:
    """Revise the screenplay based on user feedback."""
    sp_path = workspace / "screenplay.md"
    scene_log_path = workspace / "scene_log.json"

    if not sp_path.exists():
        return "找不到 screenplay，需要重新生成"

    current_sp = sp_path.read_text(encoding="utf-8")
    scene_log = {}
    if scene_log_path.exists():
        scene_log = json.loads(scene_log_path.read_text(encoding="utf-8"))

    # Use Claude to revise
    prompt = f"""你是一个视频剪辑助手。用户对 screenplay 有修改意见，请根据反馈修改。

## 当前 Screenplay
{current_sp}

## 可用素材（场景列表）
{_format_scenes_brief(scene_log)}

## 用户反馈
{feedback}

## 要求
- 根据用户的具体反馈修改 screenplay
- 保持相同的格式（Markdown，每个片段标注源文件和时间戳）
- 如果用户想加/减内容，调整片段选择
- 输出完整的修改后 screenplay，不要解释"""

    revised = claude_think(prompt, timeout=120, tier="light")
    if not revised:
        return "修改失败，请再说一次你想怎么调整"

    # Save revised version (keep backup)
    version = state.get("screenplay_version", 1) + 1
    backup = workspace / f"screenplay_v{version - 1}.md"
    sp_path.rename(backup)
    sp_path.write_text(revised, encoding="utf-8")

    state["screenplay_version"] = version
    _save_state(workspace, state)

    return (
        f"Screenplay 已更新 (v{version}):\n\n"
        f"--- Screenplay ---\n\n"
        f"{revised}\n\n"
        f"---\n\n"
        f"继续调整，或者说「ok」开始剪辑。"
    )


def _run_cut(workspace: Path, state: dict) -> str:
    """Phase 3+4: Execute the cut and mix music."""
    input_dir = Path(state["input_dir"])
    output_dir = Path(state["output_dir"])
    music_path = Path(state["music_path"]) if state.get("music_path") else None

    sp_path = workspace / "screenplay.md"
    if not sp_path.exists():
        return "找不到 screenplay，无法剪辑"

    screenplay = sp_path.read_text(encoding="utf-8")
    scene_log_path = workspace / "scene_log.json"

    # ── Phase 3: Edit ──
    log.info("=== Phase 3: Automated Edit ===")
    rough_cut = output_dir / "rough_cut.mp4"

    if rough_cut.exists():
        log.info("Using existing rough cut")
    else:
        command = generate_edit_command(
            screenplay, input_dir, workspace, rough_cut,
            claude_think_fn=claude_think,
        )
        if not command:
            return "ffmpeg 命令生成失败"

        success = execute_edit(command, input_dir, workspace)
        if not success:
            return (
                f"剪辑执行失败，但 screenplay 已确认。\n"
                f"Screenplay: {sp_path}\n"
                f"Edit command: {workspace / 'edit_command.sh'}\n"
                f"可以手动检查 edit_command.sh 并修复后重新执行。"
            )

    log.info("Phase 3 complete: rough cut ready")

    # ── Extract speech segments for ducking ──
    speech_segments = []
    if scene_log_path.exists():
        scene_log = json.loads(scene_log_path.read_text(encoding="utf-8"))
        for s in scene_log.get("scenes", []):
            if s.get("type") == "transcript":
                # Extract timestamp from transcript scenes
                speech_segments.append({
                    "start": s.get("timestamp", 0),
                    "end": s.get("timestamp", 0) + 3,  # estimate 3s per segment
                    "text": s.get("description", "").replace("[AUDIO] ", ""),
                })
    if speech_segments:
        log.info("Found %d speech segments for audio ducking", len(speech_segments))

    # Track music source for credits
    music_credit = ""

    # ── Phase 4: Music ──
    final_output = output_dir / "final.mp4"

    if music_path and music_path.exists():
        log.info("=== Phase 4: Music Mix ===")
        success = mix_music(rough_cut, music_path, final_output,
                           speech_segments=speech_segments)
        if not success:
            log.warning("Music mix failed, using rough cut as final")
            final_output = rough_cut
    else:
        # Check for music in a music/ subdir of input
        music_dir = input_dir / "music"
        available = find_music(music_dir) if music_dir.exists() else []
        if available:
            log.info("=== Phase 4: Music Mix (local: %s) ===", available[0].name)
            success = mix_music(rough_cut, available[0], final_output,
                               speech_segments=speech_segments)
            if not success:
                final_output = rough_cut
        else:
            # Auto-download royalty-free music from Incompetech
            log.info("=== Phase 4: Auto Music Selection ===")
            # Determine mood from screenplay
            sp_text = screenplay.lower()
            if any(w in sp_text for w in ["epic", "adventure", "exciting", "energetic"]):
                mood = "adventure"
            elif any(w in sp_text for w in ["cinematic", "dramatic", "intense"]):
                mood = "cinematic"
            elif any(w in sp_text for w in ["playful", "fun", "humorous", "bouncy"]):
                mood = "playful"
            elif any(w in sp_text for w in ["calm", "peaceful", "contemplative", "serene"]):
                mood = "contemplative"
            elif any(w in sp_text for w in ["warm", "tender", "intimate", "gentle"]):
                mood = "warm"
            else:
                mood = "joyful"

            video_dur = get_duration(rough_cut) if rough_cut.exists() else 180
            music_file = auto_select_music(mood, video_dur, workspace / "music")

            if music_file:
                log.info("Auto music: %s (mood: %s)", music_file.name, mood)
                # Find the track title for credits
                from music_mixer import _fetch_catalog
                catalog = _fetch_catalog()
                for t in catalog:
                    if t.get("filename") == music_file.name:
                        music_credit = (
                            f"Music: \"{t.get('title', '').strip()}\" "
                            f"by Kevin MacLeod (incompetech.com), "
                            f"Licensed under CC BY 3.0"
                        )
                        break

                success = mix_music(rough_cut, music_file, final_output,
                                   speech_segments=speech_segments)
                if not success:
                    final_output = rough_cut
            else:
                log.info("No music found, using rough cut as final")
                final_output = rough_cut

    # ── Done ──
    state["phase"] = "done"
    state["output"] = str(final_output)
    _save_state(workspace, state)

    scene_log = {}
    if scene_log_path.exists():
        scene_log = json.loads(scene_log_path.read_text(encoding="utf-8"))

    total_dur = scene_log.get("total_duration", 0)
    n_scenes = len(scene_log.get("scenes", []))

    summary = (
        f"视频剪辑完成！\n\n"
        f"- 输入: {scene_log.get('video_count', 0)} 个视频, "
        f"总计 {total_dur / 60:.1f} 分钟素材\n"
        f"- 分析: {n_scenes} 个场景\n"
        f"- Screenplay: v{state.get('screenplay_version', 1)}\n"
        f"- 输出: {final_output}\n"
    )
    if speech_segments:
        summary += f"- 对话保留: {len(speech_segments)} 段 (自动 ducking)\n"
    if music_credit:
        summary += f"\n**Credits**\n{music_credit}\n"

    # Save credits file alongside output
    if music_credit:
        credits_path = output_dir / "CREDITS.txt"
        credits_path.write_text(music_credit + "\n", encoding="utf-8")

    (workspace / "summary.md").write_text(summary, encoding="utf-8")
    log.info("Pipeline complete: %s", final_output)

    return summary


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
# Helpers
# ---------------------------------------------------------------------------

def _is_approval(text: str) -> bool:
    """Check if the user's message is approving the screenplay."""
    return bool(_APPROVE_PATTERNS.search(text))


def _format_scenes_brief(scene_log: dict) -> str:
    """Format scene log as a brief summary for the revision prompt."""
    scenes = scene_log.get("scenes", [])
    if not scenes:
        return "(无场景数据)"
    lines = []
    for s in scenes[:50]:  # cap at 50
        if s.get("type") == "overall_analysis":
            continue
        ts = s.get("timestamp_str", "?")
        end_ts = s.get("end_timestamp_str", "")
        ts_range = f"{ts}-{end_ts}" if end_ts else ts
        desc = s.get("description", "")[:80]
        cam = s.get("camera_motion", "")
        cam_str = f" [{cam}]" if cam else ""
        lines.append(f"- [{s.get('file', '?')} @ {ts_range}]{cam_str} {desc}")
    if len(scenes) > 50:
        lines.append(f"  ... 还有 {len(scenes) - 50} 个场景")
    return "\n".join(lines)


def _wants_transcription(instruction: str) -> bool:
    """Check if user wants audio transcription (off by default, costs extra)."""
    return bool(re.search(
        r'对话|对白|台词|说话|语音|transcri|dialogue|speech|audio|保留.*声音|保留.*对话',
        instruction, re.IGNORECASE,
    ))


def _extract_path(instruction: str) -> Path | None:
    """Extract a file/directory path from the instruction text."""
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
    """Extract @file: references from iOS file picker attachments."""
    matches = re.findall(r'@file:(\S+)', instruction)
    for ref in matches:
        path = Path(ref).expanduser()
        if path.exists():
            # If it's a file, use its parent directory
            if path.is_file():
                return path.parent
            return path
    return None


def _extract_music_path(instruction: str) -> Path | None:
    """Extract music file path from instruction."""
    m = re.search(r'(?:music|音乐|配乐)[:\s]+["\']?(\S+)["\']?', instruction, re.IGNORECASE)
    if m:
        path = Path(m.group(1)).expanduser()
        if path.exists():
            return path
    return None


# ---------------------------------------------------------------------------
# Standalone CLI (runs all phases without interactive pause)
# ---------------------------------------------------------------------------

def run_pipeline_full(input_dir: Path, work_dir: Path, output_dir: Path,
                      music_path: Path = None,
                      target_minutes: float = 4.0) -> str:
    """Run the full pipeline non-interactively (for CLI use)."""
    state = {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "music_path": str(music_path) if music_path else None,
        "target_minutes": target_minutes,
    }
    _save_state(work_dir, state)

    result = _run_analyze_and_screenplay(work_dir, state)
    print(result)

    # In CLI mode, proceed directly to cutting
    state = _load_state(work_dir)
    if state.get("phase") == "screenplay_review":
        print("\n>>> Proceeding to cut...\n")
        result = _run_cut(work_dir, state)

    return result


def main():
    """Standalone CLI entry point."""
    parser = argparse.ArgumentParser(description="Video editing pipeline")
    parser.add_argument("--input", required=True, help="Directory with video files")
    parser.add_argument("--output", help="Output directory (default: input/output)")
    parser.add_argument("--music", help="Background music file")
    parser.add_argument("--target-minutes", type=float, default=4.0,
                        help="Target output length in minutes")
    parser.add_argument("--work-dir", help="Working directory for intermediate files")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
    )

    input_dir = Path(args.input).expanduser().resolve()
    if not input_dir.exists():
        print(f"Error: {input_dir} does not exist")
        sys.exit(1)

    output_dir = Path(args.output).expanduser() if args.output else input_dir / "output"
    work_dir = Path(args.work_dir).expanduser() if args.work_dir else input_dir / ".video_work"
    music_path = Path(args.music).expanduser() if args.music else None

    result = run_pipeline_full(
        input_dir=input_dir,
        work_dir=work_dir,
        output_dir=output_dir,
        music_path=music_path,
        target_minutes=args.target_minutes,
    )
    print(result)


if __name__ == "__main__":
    main()
