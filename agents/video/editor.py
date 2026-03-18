"""Video editor — generate and execute ffmpeg commands from a screenplay.

Phase 3: screenplay.md → ffmpeg filter graph → rough_cut.mp4
"""
import json
import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger("video.editor")


FFMPEG_PROMPT = """You are an expert ffmpeg engineer. Generate a shell script to edit a video
based on this screenplay. Use multiple passes for reliability.

## Screenplay
{screenplay}

## Source files (with durations)
{file_info}

## Working directory
{work_dir}

## Output
{output_path}

## Architecture: Multi-pass pipeline

### Pass 1: Extract and process individual clips
For each clip in the screenplay:
- trim to the specified timestamps (use -ss BEFORE -i for fast seek)
- Apply speed changes if noted:
  - Slow motion: setpts=2.0*PTS, atempo=0.5
  - Speed up: setpts=0.5*PTS, atempo=2.0
  - Speed ramp: split into segments, apply different speeds, concat
  - For smooth slow-mo, use minterpolate=fps=60:mi_mode=mci
- Apply color grading if noted:
  - Warm cinematic: colortemperature=6500,eq=contrast=1.1:brightness=0.02:saturation=1.15
  - Cool: colortemperature=4500,eq=saturation=0.9
  - Vintage: curves=vintage,eq=saturation=0.8:contrast=1.05
  - Natural: no filter needed
  - If a .cube LUT file is specified: lut3d=file=path.cube
- Standardize: scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p
- Save each clip as clip_001.mp4, clip_002.mp4, etc.

### Pass 2: Apply transitions
- For simple cuts: use concat demuxer (fastest, most reliable)
- For crossfade/dissolve: use xfade=transition=fade:duration=0.5:offset=N
- For J-cut (audio leads video): offset audio trim to start earlier
- For L-cut (audio trails): extend audio trim past video end

### Pass 3: Final assembly
- Concatenate all processed clips
- Apply global color correction if specified
- Audio: aac 192k stereo
- Video: -preset medium -crf 18

## Rules
- Use -ss BEFORE -i for input seeking (fast seek)
- Each clip extraction is a separate ffmpeg command
- Use intermediate files in {work_dir}/clips/
- Create a concat_list.txt for the final concatenation
- The script must be self-contained bash, runnable as: bash edit_run.sh
- Include set -e for error handling
- Include mkdir -p for clip directories
- Output ONLY the shell script, no explanation.
- If timestamps are approximate (e.g. "~00:12"), use the nearest second.
"""


def parse_screenplay_clips(screenplay: str) -> list[dict]:
    """Extract clip references from a screenplay markdown.

    Looks for patterns like: **[filename.MOV 00:12-00:28]**
    Returns list of {file, start, end, description, transition}
    """
    clips = []
    # Match **[filename timestamp-timestamp]** or **[filename timestamp]**
    pattern = r'\*\*\[([^\]]+?)\s+(\d{1,2}:\d{2}(?::\d{2})?)\s*-?\s*(\d{1,2}:\d{2}(?::\d{2})?)?\]\*\*\s*(.*?)(?:\n|$)'
    for m in re.finditer(pattern, screenplay):
        filename = m.group(1).strip()
        start = m.group(2)
        end = m.group(3) or ""
        desc = m.group(4).strip()

        # Check for transition note
        transition = "cut"
        trans_match = re.search(r'[Tt]ransition:\s*(\w+)', desc)
        if trans_match:
            transition = trans_match.group(1).lower()

        clips.append({
            "file": filename,
            "start": start,
            "end": end,
            "description": desc,
            "transition": transition,
        })

    if not clips:
        # Fallback: try looser pattern
        pattern2 = r'\[(\S+\.(?:MOV|mp4|mov|MP4|avi|mkv))\s+(\d{1,2}:\d{2}(?::\d{2})?)\s*-?\s*(\d{1,2}:\d{2}(?::\d{2})?)?\]'
        for m in re.finditer(pattern2, screenplay):
            clips.append({
                "file": m.group(1),
                "start": m.group(2),
                "end": m.group(3) or "",
                "description": "",
                "transition": "cut",
            })

    log.info("Parsed %d clips from screenplay", len(clips))
    return clips


def generate_edit_command(screenplay: str, source_dir: Path,
                          work_dir: Path, output_path: Path,
                          claude_think_fn=None) -> str:
    """Use Claude to generate the ffmpeg command from the screenplay.

    Returns the command string (or script content).
    """
    if not claude_think_fn:
        log.error("No LLM function provided")
        return ""

    # Get file info for all source videos
    file_info_lines = []
    for f in sorted(source_dir.iterdir()):
        if f.suffix.lower() in {".mp4", ".mov", ".avi", ".mkv", ".m4v"}:
            # Get duration via ffprobe
            try:
                result = subprocess.run(
                    ["ffprobe", "-v", "quiet", "-show_entries",
                     "format=duration", "-of", "csv=p=0", str(f)],
                    capture_output=True, text=True, timeout=10,
                )
                dur = float(result.stdout.strip() or 0)
            except Exception:
                dur = 0
            file_info_lines.append(f"- {f.name}: {dur:.1f}s ({f.stat().st_size // (1024*1024)}MB)")

    file_info = "\n".join(file_info_lines) if file_info_lines else "No files found"

    prompt = FFMPEG_PROMPT.format(
        screenplay=screenplay,
        file_info=file_info,
        work_dir=str(work_dir),
        output_path=str(output_path),
    )

    log.info("Generating ffmpeg command...")
    command = claude_think_fn(prompt, timeout=120)
    if not command:
        log.error("ffmpeg command generation returned empty")
        return ""

    # Save the command for inspection
    cmd_path = work_dir / "edit_command.sh"
    cmd_path.write_text(f"#!/bin/bash\n# Auto-generated edit command\ncd \"{source_dir}\"\n\n{command}\n",
                        encoding="utf-8")
    cmd_path.chmod(0o755)
    log.info("Edit command saved: %s", cmd_path)

    return command


def execute_edit(command: str, source_dir: Path, work_dir: Path,
                 timeout: int = 600) -> bool:
    """Execute the ffmpeg edit command.

    Returns True if successful.
    """
    if not command.strip():
        log.error("Empty edit command")
        return False

    log.info("Executing edit command (timeout=%ds)...", timeout)

    # If it looks like a multi-line script, run as bash script
    if command.count("\n") > 3 or command.startswith("#!/"):
        script_path = work_dir / "edit_run.sh"
        script_path.write_text(f"#!/bin/bash\nset -e\ncd \"{source_dir}\"\n\n{command}\n",
                               encoding="utf-8")
        script_path.chmod(0o755)
        cmd = ["bash", str(script_path)]
    else:
        # Single command — run directly
        cmd = ["bash", "-c", f"cd \"{source_dir}\" && {command}"]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            log.error("Edit failed (exit %d):\n%s", result.returncode,
                      result.stderr[-500:] if result.stderr else "no stderr")
            # Save error log
            (work_dir / "edit_error.log").write_text(
                result.stderr or "no error output", encoding="utf-8")
            return False

        log.info("Edit completed successfully")
        return True

    except subprocess.TimeoutExpired:
        log.error("Edit timed out after %ds", timeout)
        return False
    except Exception as e:
        log.error("Edit execution error: %s", e)
        return False
