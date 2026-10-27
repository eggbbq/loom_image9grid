"""Small, mandatory regression slice of the larger scripts/stress_ninegrid.py suite."""
import pytest

from .stress.patterns import PATTERNS, make_case
from .stress.checks import check_strict, check_visual, check_automatic
from .stress.png_checks import PNG_MODES, check_png


@pytest.mark.parametrize('pattern', PATTERNS)
@pytest.mark.parametrize('variant', (0, 7))
def test_generated_strict(pattern, variant):
    check_strict(make_case(pattern, variant))


@pytest.mark.parametrize('pattern', PATTERNS)
def test_generated_visual(pattern):
    check_visual(make_case(pattern), center_size=1)


@pytest.mark.parametrize('pattern', ('exact/opaque/both', 'exact/binary_alpha/horizontal',
                                     'solid', 'gradient_x', 'rounded', 'internal_gap', 'noise'))
def test_generated_automatic(pattern):
    check_automatic(make_case(pattern))


@pytest.mark.parametrize('mode', range(len(PNG_MODES)), ids=PNG_MODES)
def test_generated_png_storage(mode):
    check_png(make_case('exact/random_rgba/both'), mode)


@pytest.mark.parametrize('axis', ('auto', 'horizontal', 'vertical'))
@pytest.mark.parametrize('center', (1, 8, 16, 32, 64))
def test_generated_visual_controls(axis, center):
    check_visual(make_case('exact/opaque/both', 8), center, axis)


@pytest.mark.parametrize('pattern', ('near_rgb', 'near_alpha', 'gradient_xy', 'noise'))
def test_generated_zero_error_budget(pattern):
    from ninegrid.visual import quality_limits
    check_visual(make_case(pattern), limits=quality_limits(max_mean_error=0, max_bad_pixel_ratio=0))


@pytest.mark.parametrize('variant,center', ((19, 8), (20, 2), (38, 2)))
def test_generated_nearly_circular_pill_may_be_skipped(variant, center):
    check_visual(make_case('pill', variant), center_size=center)


@pytest.mark.parametrize('pattern', ('exact/opaque/both', 'multiple_bands', 'tied_bands'))
def test_generated_saving_threshold_equality(pattern):
    import numpy as np
    from .stress.checks import oracle_band
    case = make_case(pattern)
    height, width = case.image.shape[:2]
    col, row = oracle_band(case.image, 1), oracle_band(case.image, 0)
    ow = width-(col[1]-col[0]-1 if col else 0)
    oh = height-(row[1]-row[0]-1 if row else 0)
    saving = 1-ow*oh/(width*height)
    assert check_strict(case, saving)['accepted']
    assert not check_strict(case, float(np.nextafter(saving, 1)))['accepted']


@pytest.mark.parametrize('pattern,variant', (('pill', 116), ('gradient_x', 127), ('gradient_x', 31)))
def test_generated_automatic_unequal_retained_centers(pattern, variant):
    check_automatic(make_case(pattern, variant))


def test_generated_large_panel_keeps_small_center_ornament():
    check_visual(make_case('center_logo', 127), center_size=8)
