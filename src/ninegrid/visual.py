"""Explicit, bounded visual approximation; strict RGBA detection stays separate."""
from dataclasses import dataclass
import numpy as np

from .reconstruct import reconstruct_9slice
from .scan import repeated_bands


def premultiplied(rgba):
    values = rgba.astype(np.float32)
    values[..., :3] *= values[..., 3:4] / 255
    return values


def visual_diff(original, restored, threshold=32):
    if original.shape != restored.shape:
        return {"equal": False, "reason": "shape_mismatch", "accepted": False}
    delta = np.abs(premultiplied(original)-premultiplied(restored))
    pixels = np.max(delta, axis=2)
    raw = np.abs(original.astype(np.int16)-restored.astype(np.int16))
    changed = np.any(raw != 0, axis=2)
    first = np.argwhere(changed)
    return {"equal": not bool(changed.any()), "differentPixels": int(changed.sum()),
            "firstDifferentPixel": [int(first[0, 1]), int(first[0, 0])] if len(first) else None,
            "meanError": float(delta.mean()), "badPixelRatio": float(np.mean(pixels > threshold)),
            "maxVisualChannelError": float(delta.max()), "maxRawChannelError": int(raw.max()),
            "pixelErrorThreshold": threshold, "metric": "premultiplied_rgba"}


def accepted(diff, limits):
    return ("meanError" in diff and diff["meanError"] <= limits["maxMeanError"] + 1e-6
            and diff["badPixelRatio"] <= limits["maxBadPixelRatio"] + 1e-9)


def _preserves_local_details(original, restored, limits):
    """Do not spend a large image's entire budget on erasing a small feature.

    A dense 8x8 patch of severe differences is a visible detail loss even when
    its area is below the global bad-pixel allowance. Sparse texture noise and
    thin antialiasing differences still use the caller's normal quality budget.
    """
    window = 8
    if min(original.shape[:2]) < window:
        return True
    delta = np.max(np.abs(premultiplied(original)-premultiplied(restored)), axis=2)
    severe = delta > limits['pixelErrorThreshold']
    integral = np.pad(severe.astype(np.int32), ((1, 0), (1, 0))).cumsum(axis=0).cumsum(axis=1)
    counts = (integral[window:, window:] - integral[:-window, window:]
              - integral[window:, :-window] + integral[:-window, :-window])
    return not bool(np.any(counts >= window*window*.75))


def quality_limits(max_mean_error=1.0, max_bad_pixel_ratio=0.01, pixel_error_threshold=32, edge_tolerance=2):
    if not np.isfinite(max_mean_error) or not 0 <= max_mean_error <= 255:
        raise ValueError("max-mean-error must be finite and in [0,255]")
    if not np.isfinite(max_bad_pixel_ratio) or not 0 <= max_bad_pixel_ratio <= 1:
        raise ValueError("max-bad-pixel-ratio must be in [0,1]")
    if type(pixel_error_threshold) is not int or not 0 <= pixel_error_threshold <= 255:
        raise ValueError("pixel-error-threshold must be an integer in [0,255]")
    if type(edge_tolerance) is not int or not 0 <= edge_tolerance <= 8:
        raise ValueError("edge-tolerance must be an integer in [0,8]")
    return {"maxMeanError": max_mean_error, "maxBadPixelRatio": max_bad_pixel_ratio,
            "pixelErrorThreshold": pixel_error_threshold, "edgeTolerancePixels": edge_tolerance}


def _content_span(visible):
    """Allow empty internal vectors, while keeping outer padding fixed."""
    span = np.zeros_like(visible, dtype=bool)
    occupied = np.flatnonzero(visible)
    if occupied.size:
        span[occupied[0]:occupied[-1]+1] = True
    return span


def _bands(values, axis, limits, center_size):
    size = values.shape[axis]
    seeds = sorted(set(np.linspace(0, size-1, min(25, size), dtype=int).tolist() + [size//2]))
    found, expanded, budgeted = {}, {}, {}
    # A fixed 16px floor misses the repeatable interior of thin progress fills.
    minimum = max(center_size+4, min(16, max(4, size//3)), int(size*0.08))
    silhouette = values[..., 3] >= 8
    cross_axis = 1 if axis == 0 else 0
    visible = silhouette.any(axis=cross_axis)
    stretchable = _content_span(visible)
    first = np.argmax(silhouette, axis=cross_axis)
    last = silhouette.shape[cross_axis]-np.argmax(np.flip(silhouette, axis=cross_axis), axis=cross_axis)
    first = np.where(visible, first, -1)
    last = np.where(visible, last, -1)
    for seed in seeds:
        # Stretching an empty outer margin saves pixels but cannot resize the
        # visible artwork. Keep transparent padding in the fixed borders.
        if not stretchable[seed]:
            continue
        reference = values[seed:seed+1] if axis == 0 else values[:, seed:seed+1]
        delta = np.abs(values-reference)
        mean = delta.mean(axis=(1, 2) if axis == 0 else (0, 2))
        severe = (delta.max(axis=2) > limits["pixelErrorThreshold"]).mean(axis=1 if axis == 0 else 0)
        # Compare every full vector to the fixed representative, never chain
        # adjacent near-equalities (which would flatten gradual gradients).
        protected = (stretchable & (visible == visible[seed]) & (mean <= limits["maxMeanError"]*2) &
                     (np.abs(first-first[seed]) <= limits.get("edgeTolerancePixels", 2)) &
                     (np.abs(last-last[seed]) <= limits.get("edgeTolerancePixels", 2)))
        good = protected & (severe <= limits["maxBadPixelRatio"]*2)
        # A short local border imperfection must not split an otherwise useful
        # stretch band. Bridge only bounded gaps that still satisfy the fixed
        # representative's mean/outline checks. The complete reconstructed
        # image must pass the unchanged error budget before acceptance.
        bridged = good.copy()
        transitions = np.flatnonzero(np.diff(np.r_[True, good, True]))
        max_gap = max(2*center_size, int(size*0.04))
        for a, b in transitions.reshape(-1, 2):
            if a > 0 and b < size and b-a <= max_gap and protected[a:b].all():
                bridged[a:b] = True
        # Sparse texture details can exceed the per-vector severe-pixel limit
        # even though replacing the whole band stays inside the image budget.
        # Search around a fixed representative using cumulative error, keeping
        # silhouette/gradient guards and the final reconstruction check intact.
        left, right = seed, seed+1
        while left > 0 and protected[left-1]:
            left -= 1
        while right < size and protected[right]:
            right += 1
        start = left
        mean_sum = severe_sum = 0.0
        for end in range(left, right):
            mean_sum += float(mean[end])
            severe_sum += float(severe[end])
            # Reserve half the error budget for GPU sampling/edge rounding;
            # downsampling can concentrate sparse differences in fewer pixels.
            while start <= end and (mean_sum > limits["maxMeanError"]*size*.5 + 1e-6 or
                                   severe_sum > limits["maxBadPixelRatio"]*size*.5 + 1e-9):
                mean_sum -= float(mean[start])
                severe_sum -= float(severe[start])
                start += 1
            if start <= seed <= end and end+1-start >= minimum:
                key = (start, end+1)
                cost = mean_sum/(end+1-start)
                if key not in budgeted or cost < budgeted[key][1]:
                    budgeted[key] = (seed, cost)
        for mask, collection in ((good, found), (bridged, expanded)):
            left, right = seed, seed+1
            while left > 0 and mask[left-1]:
                left -= 1
            while right < size and mask[right]:
                right += 1
            if right-left >= minimum:
                key = (left, right)
                cost = float(mean[left:right].mean())
                if key not in collection or cost < collection[key][1]:
                    collection[key] = (seed, cost)
    selected = []
    for collection in (budgeted, expanded, found):
        choices = [(a, b, seed, cost) for (a, b), (seed, cost) in collection.items()]
        choices.sort(key=lambda x: (-(x[1]-x[0]), x[3], x[0], x[2]))
        for choice in choices[:4]:
            band = tuple(choice[:3])
            if band not in selected:
                selected.append(band)
    return selected


@dataclass(frozen=True)
class VisualCandidate:
    size: tuple
    column_band: tuple | None
    row_band: tuple | None
    center_size: int = 8

    def _axis(self, band, size):
        if band is None:
            return 0, 0, size, None
        a, b, seed = band
        length = min(self.center_size, b-a-2)
        start = max(a, min(seed-length//2, b-length))
        return a+1, size-b+1, length, (start, start+length)

    @property
    def margins(self):
        w, h = self.size
        left, right, _, _ = self._axis(self.column_band, w)
        top, bottom, _, _ = self._axis(self.row_band, h)
        return dict(top=top, right=right, bottom=bottom, left=left, repeat=0)

    def crop(self, rgba):
        result = rgba
        if self.column_band:
            a, b, _ = self.column_band
            _, _, _, (start, end) = self._axis(self.column_band, self.size[0])
            patch = result[:, start:end]
            result = np.concatenate((result[:, :a], patch[:, :1], patch, patch[:, -1:], result[:, b:]), axis=1)
        if self.row_band:
            a, b, _ = self.row_band
            _, _, _, (start, end) = self._axis(self.row_band, self.size[1])
            patch = result[start:end]
            result = np.concatenate((result[:a], patch[:1], patch, patch[-1:], result[b:]), axis=0)
        return result

    def fields(self):
        w, h = self.size
        left, right, cw, source_x = self._axis(self.column_band, w)
        top, bottom, ch, source_y = self._axis(self.row_band, h)
        ow, oh = left+right+cw, top+bottom+ch
        return {"mode": "visual", "sourceSize": [w, h], "outputSize": [ow, oh],
                "sizeGrid": self.margins,
                "layaSizeGrid": ",".join(str(self.margins[k]) for k in ("top", "right", "bottom", "left", "repeat")),
                "columnBand": list(self.column_band[:2]) if self.column_band else None,
                "rowBand": list(self.row_band[:2]) if self.row_band else None,
                "sourceCenterX": source_x, "sourceCenterY": source_y,
                "samplingGuard": 1, "retainedCenterSize": [cw, ch],
                "removedColumns": {"start": self.column_band[0], "length": w-ow} if self.column_band else None,
                "removedRows": {"start": self.row_band[0], "length": h-oh} if self.row_band else None,
                "sliceMode": "nine_slice" if self.column_band and self.row_band else (
                    "vertical_three_slice" if self.row_band else "horizontal_three_slice"),
                "pixelAreaSavedRatio": 1-ow*oh/(w*h)}


def choose_visual_candidate(rgba, min_saving=0.20, limits=None, center_size=8, axis="auto"):
    limits = limits or quality_limits()
    if type(center_size) is not int or not 1 <= center_size <= 64:
        raise ValueError("center-size must be an integer in [1,64]")
    if axis not in ("auto", "horizontal", "vertical"):
        raise ValueError("Unknown stretch axis")
    h, w = rgba.shape[:2]
    values = premultiplied(rgba)
    columns = _bands(values, 1, limits, center_size) if axis != "vertical" else []
    rows = _bands(values, 0, limits, center_size) if axis != "horizontal" else []
    choices = [VisualCandidate((w, h), column, row, center_size)
               for column in [None, *columns] for row in [None, *rows] if column or row]
    choices.sort(key=lambda c: (-c.fields()["pixelAreaSavedRatio"], c.fields()["layaSizeGrid"]))
    # Preserve exact border steps when an exact crop retains almost all of the
    # available area saving. Approximation of a tiny step can be amplified by
    # Point sampling at smaller scales, despite a low CPU error at full size.
    equal_columns, equal_rows = repeated_bands(rgba)
    visible = rgba[..., 3] >= 8
    def exact_bands(bands, axis):
        occupancy = _content_span(visible.any(axis=1 if axis == 0 else 0))
        return [(a,b,(a+b)//2) for a,b in sorted(bands,key=lambda b:-(b[1]-b[0]))
                if b-a >= center_size+2 and occupancy[a:b].all()][:4]
    exact_columns = exact_bands(equal_columns,1) if axis != 'vertical' else []
    exact_rows = exact_bands(equal_rows,0) if axis != 'horizontal' else []
    exact_choices = [VisualCandidate((w,h),column,row,center_size)
                     for column in [None,*exact_columns] for row in [None,*exact_rows] if column or row]
    exact_choices.sort(key=lambda c:-c.fields()['pixelAreaSavedRatio'])
    best_exact = next((c for c in exact_choices if c.fields()['pixelAreaSavedRatio'] >= min_saving),None)
    for candidate in choices:
        if candidate.fields()["pixelAreaSavedRatio"] < min_saving:
            continue
        restored = reconstruct_9slice(candidate.crop(rgba), candidate.margins, (w, h))
        diff = visual_diff(rgba, restored, limits["pixelErrorThreshold"])
        if accepted(diff, limits) and _preserves_local_details(rgba, restored, limits):
            if best_exact and best_exact.fields()['pixelAreaSavedRatio'] >= .9*candidate.fields()['pixelAreaSavedRatio']:
                exact_diff = visual_diff(rgba,reconstruct_9slice(best_exact.crop(rgba),best_exact.margins,(w,h)),
                                         limits['pixelErrorThreshold'])
                if exact_diff['equal']:
                    return best_exact,{**exact_diff,'accepted':True,'qualityLimits':limits}
            return candidate, {**diff, "accepted": True, "qualityLimits": limits}
    if best_exact:
        diff = visual_diff(rgba,reconstruct_9slice(best_exact.crop(rgba),best_exact.margins,(w,h)),limits['pixelErrorThreshold'])
        if diff['equal']:
            return best_exact,{**diff,'accepted':True,'qualityLimits':limits}
    return None, {"reason": "no_visual_candidate_within_budget", "accepted": False, "qualityLimits": limits}
