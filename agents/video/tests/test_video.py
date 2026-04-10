"""Video agent unit tests - no LLM calls, no external media files."""
import pytest
import sys
import json
from pathlib import Path

_AGENTS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_AGENTS / "video"))
sys.path.insert(0, str(_AGENTS.parent / "lib"))


class TestClipGrader:
    """Test deterministic color grading presets."""

    def test_all_presets_have_valid_ffmpeg(self):
        from clip_grader import GRADE_PRESETS
        for key, preset in GRADE_PRESETS.items():
            assert isinstance(preset, str), f"Preset {key} is not a string"
            assert "eq=" in preset or "colorbalance=" in preset, \
                f"Preset {key} missing eq or colorbalance filter"

    def test_grade_clip_returns_string(self):
        from clip_grader import grade_clip
        analysis = {
            "lighting_type": "golden_hour",
            "color_temperature_est": "warm",
        }
        result = grade_clip(analysis, content_mode="travel")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_grade_clip_unknown_lighting_falls_back(self):
        from clip_grader import grade_clip
        analysis = {"lighting_type": "alien_sun"}
        result = grade_clip(analysis, content_mode="family")
        assert isinstance(result, str)
        assert "eq=" in result or "colorbalance=" in result

    def test_content_mode_detection(self):
        from clip_grader import detect_content_mode
        # Family signals
        family_scenes = [
            {"subjects": "child playing", "mood": "intimate"},
            {"subjects": "people laughing", "mood": "warm"},
        ]
        assert detect_content_mode(family_scenes) == "family"

        # Travel signals
        travel_scenes = [
            {"subjects": "landscape", "mood": "epic"},
            {"subjects": "architecture", "mood": "adventure"},
            {"subjects": "scenic vista", "mood": "energetic"},
        ]
        assert detect_content_mode(travel_scenes) == "travel"

    def test_content_mode_empty(self):
        from clip_grader import detect_content_mode
        assert detect_content_mode([]) == "family"


class TestTriage:
    """Test clip quality scoring logic."""

    def test_quality_score_range(self):
        """Quality scores should be 0-5."""
        # Can't test full triage without ffprobe, but verify the scoring logic
        from triage import _MIN_DURATION, _MIN_BRIGHTNESS, _MAX_BRIGHTNESS, _MIN_BLUR_SCORE
        assert _MIN_DURATION > 0
        assert _MIN_BRIGHTNESS >= 0
        assert _MAX_BRIGHTNESS <= 255
        assert _MIN_BLUR_SCORE > 0

    def test_video_extensions(self):
        from triage import VIDEO_EXTS
        assert ".mp4" in VIDEO_EXTS
        assert ".mov" in VIDEO_EXTS
        assert ".mkv" in VIDEO_EXTS

    def test_thresholds_sensible(self):
        from triage import _MIN_DURATION, _MIN_BRIGHTNESS, _MAX_BRIGHTNESS, _MIN_BLUR_SCORE
        assert _MIN_BRIGHTNESS < _MAX_BRIGHTNESS
        assert _MIN_DURATION < 60  # shouldn't reject clips under a minute


class TestBeatAnalyzer:
    """Test beat analysis helpers."""

    def test_detect_sections_empty(self):
        from beat_analyzer import _detect_sections
        sections = _detect_sections([], 120.0)
        assert len(sections) == 1
        assert sections[0]["type"] == "full"

    def test_detect_sections_short(self):
        from beat_analyzer import _detect_sections
        curve = [{"time": i * 2.0, "energy": 0.5} for i in range(3)]
        sections = _detect_sections(curve, 6.0)
        assert len(sections) == 1
        assert sections[0]["type"] == "full"

    def test_detect_sections_normal(self):
        from beat_analyzer import _detect_sections
        # 16 energy points over 60 seconds
        curve = [{"time": i * 4.0, "energy": 0.3 + 0.04 * i} for i in range(16)]
        sections = _detect_sections(curve, 60.0)
        assert len(sections) == 4
        types = [s["type"] for s in sections]
        assert types == ["intro", "build", "peak", "outro"]

    def test_summarize_beat_map(self):
        from beat_analyzer import summarize_beat_map
        beat_map = {
            "tempo": 120.0,
            "duration": 180.0,
            "beats": [0.5 * i for i in range(360)],
            "phrases": [2.0 * i for i in range(90)],
            "sections": [
                {"start": 0, "end": 45, "type": "intro", "energy": 0.3},
                {"start": 45, "end": 90, "type": "build", "energy": 0.5},
                {"start": 90, "end": 135, "type": "peak", "energy": 0.9},
                {"start": 135, "end": 180, "type": "outro", "energy": 0.4},
            ],
        }
        summary = summarize_beat_map(beat_map)
        assert "120" in summary
        assert "BPM" in summary
        assert "intro" in summary
        assert "peak" in summary


class TestSkillsIndex:
    """Test video skills are properly indexed."""

    def test_skills_exist(self):
        skills_dir = _AGENTS / "video" / "skills"
        skills = list(skills_dir.glob("*.md"))
        assert len(skills) >= 10, f"Expected 10+ video skills, found {len(skills)}"

    def test_index_matches_files(self):
        index_path = _AGENTS / "video" / "skills" / "index.json"
        skills_dir = _AGENTS / "video" / "skills"
        if not index_path.exists():
            pytest.skip("No index.json")
        index = json.loads(index_path.read_text())
        index_files = {s["file"] for s in index}
        actual_files = {f.name for f in skills_dir.glob("*.md")}
        assert index_files == actual_files, f"Index mismatch: {index_files ^ actual_files}"
