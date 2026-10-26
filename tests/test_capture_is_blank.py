"""Blank-frame detection keeps its threshold while avoiding a 4K temp array."""

from pathlib import Path

import numpy as np

from lolhex.vision.capture import is_blank


def test_black_solid_and_light_noise_are_blank():
    assert is_blank(np.zeros((80, 120, 3), dtype=np.uint8))
    assert is_blank(np.full((80, 120, 3), 127, dtype=np.uint8))
    rng = np.random.default_rng(112)
    noisy = np.clip(127 + rng.integers(-2, 3, (80, 120, 3)), 0, 255).astype(np.uint8)
    assert noisy.std() < 2.0
    assert is_blank(noisy)


def test_active_synthetic_frame_is_not_blank():
    image = np.full((128, 192, 3), 8, dtype=np.uint8)
    image[::4, ::4] = (220, 180, 40)
    assert is_blank(image) is False


def test_small_strip_uses_full_image_semantics():
    # The only non-background pixel is deliberately between a hypothetical
    # 4x sampling grid; small strips must retain the old full-image decision.
    strip = np.full((16, 40, 3), 100, dtype=np.uint8)
    strip[7, 3] = 255
    assert is_blank(strip) == (float(strip.std()) < 2.0)


def test_real_capture_fixtures_are_non_blank_when_present():
    # The 235 replay harness supplies these 4K captures; keep the test
    # self-contained in normal checkouts where the large files are absent.
    root = Path(__file__).resolve().parents[2] / "evidence"
    shots = sorted(root.glob("235-shot*.png"))
    if not shots:
        return
    from PIL import Image
    for path in shots:
        assert is_blank(np.asarray(Image.open(path))) is False
