"""Video editor — generate and execute ffmpeg commands from a screenplay.

Phase 3: screenplay.md → ffmpeg filter graph → rough_cut.mp4
"""
import json
import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger("video.editor")


FFMPEG_PROMPT = """You are an expert ffmpeg engineer. Generate a complete ffmpeg command
to edit a travel video based on this screenplay.

## Screenplay
{screenplay}

## Source files (with durations)
{file_info}

## Working directory
{work_dir}

## Output
{output_path}

## Requirements

1. Generate a SINGLE ffmpeg command that produces the final video
2. Use filter_complex for all operations
3. For each clip in the screenplay:
   - trim to the specified timestamps (use trim/atrim + setpts/asetpts)
   - Apply any noted transitions (xfade for crossfade/dissolve/fade)
4. Standardize all clips:
   - Resolution: 1920x1080 (scale + pad if needed)
   - Frame rate: 30fps
   - Pixel format: yuv420p
   - Audio: aac 192k stereo
5. Use -preset medium -crf 18 for quality
6. If a clip's timestamps are approximate (e.g. "~00:12"), use the nearest second

## Important
- Each input file needs its own -i flag
- Keep the filter_complex as simple as possible
- Use concat demuxer approach if filter_complex gets too complex
- Test each trim range is within the file's duration
- Output ONLY the ffmpeg command, nothing else. No explanation.
- If the command is too complex for a single filter_complex, output a shell script
  that runs multiple ffmpeg passes and concatenates at the end.
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
