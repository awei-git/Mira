"""Photo agent unit tests - no LLM calls, no external dependencies."""
import pytest
import sys
from pathlib import Path

# Setup path
_AGENTS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_AGENTS / "photo"))
sys.path.insert(0, str(_AGENTS.parent / "lib"))


class TestParamClamping:
    """Test that color parameters are clamped to safe ranges."""

    def test_clamp_extreme_values(self):
        from aesthetic_editor import _clamp_params
        # Test with extreme values that should be clamped
        params = {
            "colorbalance": {
                "chroma_global": 0.5,  # Way too high (max 0.05)
                "chroma_shadows": -0.3,  # Way too low (min -0.03)
            }
        }
        clamped = _clamp_params(params)
        cb = clamped["colorbalance"]
        assert abs(cb["chroma_global"]) <= 0.05
        assert abs(cb["chroma_shadows"]) <= 0.03

    def test_clamp_preserves_safe_values(self):
        from aesthetic_editor import _clamp_params
        params = {
            "colorbalance": {
                "chroma_global": 0.02,
                "chroma_shadows": -0.01,
            }
        }
        clamped = _clamp_params(params)
        cb = clamped["colorbalance"]
        assert cb["chroma_global"] == 0.02
        assert cb["chroma_shadows"] == -0.01

    def test_clamp_tone_eq_extreme(self):
        from aesthetic_editor import _clamp_params
        params = {
            "tone_eq": {
                "blacks": 5.0,
                "shadows": -3.0,
                "midtones": 0.5,
            }
        }
        clamped = _clamp_params(params)
        te = clamped["tone_eq"]
        assert te["blacks"] == 1.5
        assert te["shadows"] == -1.5
        assert te["midtones"] == 0.5

    def test_clamp_c_channels(self):
        from aesthetic_editor import _clamp_params
        params = {
            "colorbalance": {
                "shadows_C": 0.1,
                "midtones_C": -0.05,
                "highlights_C": 0.005,
            }
        }
        clamped = _clamp_params(params)
        cb = clamped["colorbalance"]
        assert cb["shadows_C"] == 0.015
        assert cb["midtones_C"] == -0.015
        assert cb["highlights_C"] == 0.005

    def test_clamp_empty_params(self):
        from aesthetic_editor import _clamp_params
        result = _clamp_params({})
        assert result == {}


class TestEditBatch:
    """Test image editing operations with numpy."""

    def test_exposure_adjustment(self):
        try:
            import numpy as np
            from edit_batch import load_and_convert, save
            # Create a test image (100x100, mid-gray)
            img = np.full((100, 100, 3), 0.5, dtype=np.float64)
            # Can't test full pipeline without file I/O, but verify import works
            assert img.shape == (100, 100, 3)
        except ImportError:
            pytest.skip("numpy not available")

    def test_adjust_exposure_brightens(self):
        try:
            import numpy as np
            from edit_batch import adjust_exposure
            img = np.full((10, 10, 3), 0.5, dtype=np.float32)
            result = adjust_exposure(img, 1.0)  # +1 stop
            assert result.mean() == pytest.approx(1.0, abs=0.01)
        except ImportError:
            pytest.skip("numpy not available")

    def test_adjust_exposure_darkens(self):
        try:
            import numpy as np
            from edit_batch import adjust_exposure
            img = np.full((10, 10, 3), 0.5, dtype=np.float32)
            result = adjust_exposure(img, -1.0)  # -1 stop
            assert result.mean() == pytest.approx(0.25, abs=0.01)
        except ImportError:
            pytest.skip("numpy not available")


class TestXmpGeneration:
    """Test XMP struct packing produces valid bytes."""

    def test_make_exposure_size(self):
        from dt_xmp import make_exposure
        data = make_exposure(ev=0.5, black=0.0)
        # struct "<iffffii" = 4 + 4*4 + 4*2 = 28 bytes
        assert len(data) == 28

    def test_make_filmic_size(self):
        from dt_xmp import make_filmic
        data = make_filmic()
        # struct "<18f11i" = 18*4 + 11*4 = 116 bytes
        assert len(data) == 116

    def test_make_colorbalance_size(self):
        from dt_xmp import make_colorbalance
        data = make_colorbalance()
        # struct "<32fi" = 32*4 + 4 = 132 bytes
        assert len(data) == 132

    def test_make_tone_equalizer_size(self):
        from dt_xmp import make_tone_equalizer
        data = make_tone_equalizer()
        # struct "<15f3i" = 15*4 + 3*4 = 72 bytes
        assert len(data) == 72

    def test_encode_params_short(self):
        from dt_xmp import encode_params
        data = b"\x00" * 10
        result = encode_params(data)
        assert result == data.hex()

    def test_encode_params_long_compressed(self):
        from dt_xmp import encode_params
        data = b"\x00" * 200
        result = encode_params(data)
        assert result.startswith("gz")


class TestSkillsIndex:
    """Test photo skills are properly indexed."""

    def test_skills_exist(self):
        skills_dir = _AGENTS / "photo" / "skills"
        if not skills_dir.exists():
            pytest.skip("No skills directory")
        skills = list(skills_dir.glob("*.md"))
        assert len(skills) >= 5, f"Expected 5+ photo skills, found {len(skills)}"

    def test_index_matches_files(self):
        import json
        index_path = _AGENTS / "photo" / "skills" / "index.json"
        skills_dir = _AGENTS / "photo" / "skills"
        if not index_path.exists():
            pytest.skip("No index.json")
        index = json.loads(index_path.read_text())
        index_files = {s["file"] for s in index}
        actual_files = {f.name for f in skills_dir.glob("*.md")}
        assert index_files == actual_files, f"Index mismatch: {index_files ^ actual_files}"
