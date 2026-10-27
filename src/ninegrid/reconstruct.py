"""Deterministic CPU nine-slice nearest-neighbor reconstruction."""
import numpy as np


def axis_indices(source, target, before, after):
    if any(type(v) is not int or v < 0 for v in (source, target, before, after)):
        raise ValueError("Invalid nine-slice dimensions")
    center = source - before - after
    stretch = target - before - after
    if center < 1 or stretch < 1:
        raise ValueError("Nine-slice center must be positive")
    return np.concatenate((np.arange(before),
                           before + np.arange(stretch, dtype=np.int64) * center // stretch,
                           np.arange(source - after, source)))


def reconstruct_9slice(optimized, margins, original_size):
    width, height = original_size
    top, right, bottom, left = (margins[k] for k in ("top", "right", "bottom", "left"))
    if margins.get("repeat", 0) != 0:
        raise ValueError("Only stretch mode is supported")
    h, w = optimized.shape[:2]
    xs = axis_indices(w, width, left, right)
    ys = axis_indices(h, height, top, bottom)
    return optimized[ys[:, None], xs[None, :]]


def pixel_diff(original, restored):
    if original.shape != restored.shape:
        return {"equal": False, "reason": "shape_mismatch"}
    different = np.any(original != restored, axis=2)
    count = int(different.sum())
    first = np.argwhere(different)
    return {"equal": count == 0, "differentPixels": count,
            "firstDifferentPixel": [int(first[0, 1]), int(first[0, 0])] if count else None}
