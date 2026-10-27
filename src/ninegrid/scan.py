"""Exact comparison of complete RGBA row/column vectors."""
import numpy as np
from PIL import Image
from pathlib import Path


def read_rgba(path):
    path = Path(path)
    with Image.open(path) as image:
        if image.format != "PNG":
            raise ValueError("not_png")
        if getattr(image, "n_frames", 1) != 1:
            raise ValueError("animated_png")
        # Pillow's RGBA conversion is 8-bit; do not silently truncate 16-bit PNGs.
        with path.open("rb") as stream:
            bit_depth = stream.read(25)[24]
        if bit_depth == 16:
            raise ValueError("sixteen_bit_png")
        return np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()


def equal_bands(edges):
    """Convert k consecutive equal-neighbor links into a half-open k+1 band."""
    padded = np.concatenate(([False], edges, [False])).astype(np.int8)
    starts = np.flatnonzero(np.diff(padded) == 1)
    ends = np.flatnonzero(np.diff(padded) == -1)
    return [(int(a), int(b) + 1) for a, b in zip(starts, ends)]


def repeated_bands(rgba):
    cols = np.all(rgba[:, 1:, :] == rgba[:, :-1, :], axis=(0, 2))
    rows = np.all(rgba[1:, :, :] == rgba[:-1, :, :], axis=(1, 2))
    return equal_bands(cols), equal_bands(rows)
