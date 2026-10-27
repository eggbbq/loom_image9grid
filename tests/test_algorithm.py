from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from ninegrid.candidate import choose_candidate
from ninegrid.reconstruct import reconstruct_9slice, pixel_diff
from ninegrid.scan import equal_bands, read_rgba
from .factory import panel


@pytest.mark.parametrize("size,margins", [
    ((600, 300), (16, 16, 16, 16)),  # T02: exact 33x33 panel
    ((47, 31), (0, 0, 0, 0)),      # T01: 1x1, all zero borders
    ((19, 23), (3, 4, 5, 0)),
    ((2, 2), (0, 0, 0, 0)),
    ((61, 43), (3, 4, 5, 6)),     # T06/T07: partial alpha and hidden RGB
])
def test_exact_reconstruction(size, margins):
    original = panel(*size, margins=margins)
    candidate, reason = choose_candidate(original)
    assert reason is None
    reduced = candidate.crop(original)
    assert np.array_equal(original, reconstruct_9slice(reduced, candidate.margins, size))
    top, right, bottom, left = margins
    assert list(reduced.shape[1::-1]) == [left+right+1, top+bottom+1]
    assert candidate.margins == dict(top=top, right=right, bottom=bottom, left=left, repeat=0)


@pytest.mark.parametrize("axis", [0, 1])
def test_single_axis_compression(axis):
    rng = np.random.default_rng(18)
    original = rng.integers(0, 256, (23, 31, 4), dtype=np.uint8)
    if axis == 1:
        original[:, 6:27] = original[:, 6:7]
    else:
        original[3:18] = original[3:4]
    candidate, reason = choose_candidate(original)
    assert reason is None
    assert (candidate.row_band is None) == (axis == 1)
    assert (candidate.column_band is None) == (axis == 0)
    restored = reconstruct_9slice(candidate.crop(original), candidate.margins, (31, 23))
    assert np.array_equal(original, restored)


def test_noisy_illustration_and_gradient_skip():
    rng = np.random.default_rng(71)
    x, y = np.meshgrid(np.arange(100), np.arange(100))
    gradient = np.stack((x, y, np.ones((100, 100))*128, np.ones((100, 100))*255), axis=2).astype(np.uint8)
    for original in (rng.integers(0, 256, (100, 100, 4), dtype=np.uint8),
                     gradient):
        assert choose_candidate(original)[1] == "no_equal_bands"


def test_hidden_rgb_is_not_ignored():
    image = np.random.default_rng(10).integers(0, 256, (30, 40, 4), dtype=np.uint8)
    image[..., 3] = 0
    assert choose_candidate(image)[1] == "no_equal_bands"


def test_whole_columns_are_compared_not_just_the_center():
    image = np.zeros((40, 60, 4), dtype=np.uint8)
    image[..., 3] = 255
    image[0, :, 0] = np.arange(60)
    image[:, 0, 1] = np.arange(40)
    assert choose_candidate(image)[1] == "no_equal_bands"


def test_short_band_below_threshold():
    image = np.random.default_rng(4).integers(0, 256, (100, 100, 4), dtype=np.uint8)
    image[:, 5] = image[:, 4]
    assert choose_candidate(image)[1] == "below_min_saving"


def test_longest_band_not_first_band_is_selected():
    image = np.random.default_rng(4).integers(0, 256, (30, 100, 4), dtype=np.uint8)
    image[:, 2:7] = image[:, 2:3]
    image[:, 40:90] = image[:, 40:41]
    candidate, _ = choose_candidate(image)
    assert candidate.column_band == (40, 90)


def test_equal_band_link_endpoints_and_degenerate_images():
    assert equal_bands(np.array([True, True, False, True])) == [(0, 3), (3, 5)]
    assert equal_bands(np.array([], dtype=bool)) == []
    for shape in ((1, 1, 4), (1, 30, 4), (30, 1, 4)):
        image = np.ones(shape, dtype=np.uint8)
        candidate, reason = choose_candidate(image)
        if shape == (1, 1, 4):
            assert reason == "no_equal_bands"
        else:
            assert np.array_equal(reconstruct_9slice(candidate.crop(image), candidate.margins,
                                                    (shape[1], shape[0])), image)


def test_corrupted_reconstruction_is_rejected(monkeypatch):
    import ninegrid.candidate as module
    monkeypatch.setattr(module, "reconstruct_9slice", lambda *args: np.zeros((40, 60, 4), dtype=np.uint8))
    assert choose_candidate(panel())[1] == "reconstruction_mismatch"


def test_diff_reports_first_coordinate():
    original = np.zeros((4, 6, 4), dtype=np.uint8)
    changed = original.copy()
    changed[2, 3, 1] = 1
    assert pixel_diff(original, changed) == dict(equal=False, differentPixels=1, firstDifferentPixel=[3, 2])


def test_animated_png_rejected(tmp_path):
    path = tmp_path / "animated.png"
    Image.new("RGBA", (10, 10), "red").save(path, save_all=True,
        append_images=[Image.new("RGBA", (10, 10), "blue")], duration=100, loop=0)
    with pytest.raises(ValueError, match="animated_png"):
        read_rgba(path)
