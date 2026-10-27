"""Independent assertions shared by pytest and the long-running CLI."""
from collections import Counter
import numpy as np

from ninegrid.candidate import choose_candidate
from ninegrid.reconstruct import reconstruct_9slice
from ninegrid.visual import choose_visual_candidate, quality_limits
from .patterns import reference_render


def oracle_band(image, axis):
    """Deliberately simple full-vector loop; no production band search."""
    vectors = np.moveaxis(image, axis, 0)
    runs = []
    start = 0
    for index in range(1, len(vectors)+1):
        if index == len(vectors) or not np.array_equal(vectors[index], vectors[index-1]):
            if index-start > 1:
                runs.append((start, index))
            start = index
    return min(runs, key=lambda pair: (-(pair[1]-pair[0]), pair[0])) if runs else None


def independent_quality(original, restored, threshold):
    def premultiply(image):
        values = image.astype(np.float64)
        values[..., :3] *= values[..., 3:4]/255
        return values
    error = np.abs(premultiply(original)-premultiply(restored))
    return dict(meanError=float(error.mean()), badPixelRatio=float(np.mean(error.max(axis=2) > threshold)),
                maxVisualChannelError=float(error.max()), equal=bool(np.array_equal(original, restored)))


def check_geometry(image, candidate):
    fields = candidate.fields()
    crop = candidate.crop(image)
    height, width = image.shape[:2]
    assert fields["sourceSize"] == [width, height], "wrong source dimensions"
    assert fields["outputSize"] == list(crop.shape[1::-1]), "reported crop dimensions differ"
    grid = fields["sizeGrid"]
    assert all(type(grid[k]) is int and grid[k] >= 0 for k in ("top", "right", "bottom", "left", "repeat"))
    assert crop.shape[1] > grid["left"]+grid["right"], "nonpositive horizontal center"
    assert crop.shape[0] > grid["top"]+grid["bottom"], "nonpositive vertical center"
    assert fields["layaSizeGrid"] == ",".join(str(grid[k]) for k in ("top", "right", "bottom", "left", "repeat"))
    assert abs(fields["pixelAreaSavedRatio"]-(1-crop.shape[0]*crop.shape[1]/(width*height))) < 1e-12
    return crop


def check_strict(case, min_saving=.2):
    image, before = case.image, case.image.copy()
    height, width = image.shape[:2]
    column, row = oracle_band(image, 1), oracle_band(image, 0)
    ow = width-(column[1]-column[0]-1 if column else 0)
    oh = height-(row[1]-row[0]-1 if row else 0)
    saving = 1-ow*oh/(width*height)
    candidate, reason = choose_candidate(image, min_saving=min_saving)
    assert np.array_equal(image, before), "strict detection modified the input"
    if not column and not row:
        assert candidate is None and reason == "no_equal_bands", "accepted an image with no repeated vectors"
        return dict(accepted=False, reason=reason)
    if saving < min_saving:
        assert candidate is None and reason == "below_min_saving", "saving threshold was ignored"
        return dict(accepted=False, reason=reason)
    assert candidate is not None and reason is None, f"missed lossless candidate: {reason}"
    assert candidate.column_band == column and candidate.row_band == row, "wrong optimal band or unstable tie"
    crop = check_geometry(image, candidate)
    assert crop.shape[:2] == (oh, ow), "crop is not minimum area"
    restored = reference_render(crop, candidate.margins, (width, height))
    assert np.array_equal(image, restored), "independent strict roundtrip mismatch"
    assert np.array_equal(restored, reconstruct_9slice(crop, candidate.margins, (width, height)))
    resize_checks = 0
    if case.compact is not None:
        grid = case.grid
        # Enlarge, shrink, and resize the axes independently down to a 1px core.
        sizes = ((width+37, height+19),
                 (max(grid['left']+grid['right']+1, width//2), max(grid['top']+grid['bottom']+1, height//2)),
                 (grid['left']+grid['right']+1, height+7),
                 (width+7, grid['top']+grid['bottom']+1))
        for size in sizes:
            expected = reference_render(case.compact, grid, size)
            actual = reconstruct_9slice(crop, candidate.margins, size)
            assert np.array_equal(actual, expected), f"resized artwork mismatch at {size}"
            resize_checks += 1
    return dict(accepted=True, saving=saving, resizeChecks=resize_checks)


def check_visual(case, center_size=8, axis="auto", limits=None):
    limits = quality_limits() if limits is None else limits
    image, before = case.image, case.image.copy()
    candidate, diff = choose_visual_candidate(image, limits=limits, center_size=center_size, axis=axis)
    assert np.array_equal(image, before), "visual detection modified the input"
    expected = case.visual_expected
    # Exact opaque/visible families have a guaranteed useful repeated interior.
    if case.compact is not None and case.name.startswith("exact/"):
        style = case.name.split("/")[1]
        if style in ("opaque", "soft_alpha", "solid", "bevel", "double_border", "edge_texture"):
            expected = True
    # A large retained center or forced wrong axis may legitimately yield no candidate.
    if center_size > 8 or (axis != "auto" and case.stretch_axes and axis not in case.stretch_axes):
        expected = None
    if axis != "auto" and case.compact is None:
        expected = False if expected is False else None
    if limits["maxMeanError"] == 0 and case.name.split("/")[0] in ("near_rgb", "near_alpha"):
        expected = None
    if expected is not None:
        assert (candidate is not None) == expected, f"visual expectation {expected}, got {diff}"
    if candidate is None:
        assert not diff["accepted"]
        return dict(accepted=False, reason=diff["reason"])
    assert diff["accepted"]
    if axis == "horizontal":
        assert candidate.row_band is None, "forced horizontal search changed vertical geometry"
    if axis == "vertical":
        assert candidate.column_band is None, "forced vertical search changed horizontal geometry"
    crop = check_geometry(image, candidate)
    assert candidate.fields()["pixelAreaSavedRatio"] >= .2, "visual saving threshold was ignored"
    restored = reference_render(crop, candidate.margins, candidate.size)
    assert np.array_equal(restored, reconstruct_9slice(crop, candidate.margins, candidate.size))
    independent = independent_quality(image, restored, limits["pixelErrorThreshold"])
    assert independent['meanError'] <= limits['maxMeanError']+1e-6, "visual mean error exceeded budget"
    assert independent['badPixelRatio'] <= limits['maxBadPixelRatio']+1e-9, "visual bad-pixel ratio exceeded budget"
    for key in ("meanError", "badPixelRatio", "maxVisualChannelError"):
        assert abs(diff[key]-independent[key]) < 1e-4, f"wrong reported metric: {key}"
    assert diff['equal'] == independent['equal']
    # Four fixed corner rectangles must be byte-for-byte unchanged, even in visual mode.
    height, width = image.shape[:2]
    # Visual margins include one synthetic sampling guard on each band edge;
    # only pixels OUTSIDE the proposed bands are declared original fixed art.
    xregions = ((slice(0, candidate.column_band[0]), slice(candidate.column_band[1], width))
                if candidate.column_band else (slice(0, width),))
    yregions = ((slice(0, candidate.row_band[0]), slice(candidate.row_band[1], height))
                if candidate.row_band else (slice(0, height),))
    for ys in yregions:
        for xs in xregions:
            assert np.array_equal(image[ys, xs], restored[ys, xs]), "fixed corner pixels changed"
    if case.protected:
        x0, y0, x1, y1 = case.protected
        assert np.array_equal(image[y0:y1, x0:x1], restored[y0:y1, x0:x1]), "center ornament was destroyed"
    return dict(accepted=True, saving=candidate.fields()['pixelAreaSavedRatio'], **independent)


def check_automatic(case):
    from ninegrid.automatic.candidates import pixel_candidates, MAX_COMPRESSED_CANDIDATES
    from ninegrid.automatic.codecs import decode_candidate, reconstruct_candidate
    image, before = case.image, case.image.copy()
    records, traits = pixel_candidates(image)
    assert np.array_equal(image, before), "automatic detection modified the input"
    assert sum(not r['geometryOnly'] for r in records) <= MAX_COMPRESSED_CANDIDATES
    keys = set()
    for record in records:
        key = (record['geometryOnly'], tuple(record['outputSize']), record['layaSizeGrid'])
        assert key not in keys, "duplicate automatic candidate"
        keys.add(key)
        candidate = decode_candidate(record)
        crop = candidate.crop(image)
        assert list(crop.shape[1::-1]) == record['outputSize']
        assert record['sourceSize'] == list(image.shape[1::-1])
        assert all(type(record['sizeGrid'][k]) is int and record['sizeGrid'][k] >= 0
                   for k in ('top', 'right', 'bottom', 'left', 'repeat'))
        if record.get('candidateCoding') == 'linear':
            restored = reference_render(crop, record['sizeGrid'], tuple(record['sourceSize']), linear=True)
            assert np.array_equal(restored, candidate.restore(crop)), "linear decoding differs from independent tile rendering"
            delta = np.abs(image.astype(np.int16)-restored.astype(np.int16))
            assert delta.max() <= 1 and delta.mean() <= .02, "linear candidate exceeded its raw CPU budget"
        else:
            restored = reference_render(crop, record['sizeGrid'], tuple(record['sourceSize']))
            assert np.array_equal(restored, reconstruct_candidate(crop, record['sizeGrid'], tuple(record['sourceSize'])))
            assert np.array_equal(restored, image), "automatic candidate falsely claims exact CPU proof"
        if record.get('cpuExact'):
            assert np.array_equal(restored, image), "cpuExact record is not exact"
        if record['geometryOnly']:
            assert np.array_equal(crop, image), "geometry-only candidate changed PNG pixels"
    assert np.array_equal(image, before), "automatic crop modified the input"
    return dict(accepted=bool(records), candidates=len(records), traits=traits,
                candidateCodings=dict(Counter(r.get('candidateCoding', 'geometry') for r in records)),
                decisions=dict(Counter(r['decision'] for r in records)))
