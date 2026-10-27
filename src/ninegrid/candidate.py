from dataclasses import dataclass
import numpy as np

from .reconstruct import reconstruct_9slice, pixel_diff
from .scan import repeated_bands


@dataclass(frozen=True)
class Candidate:
    size: tuple
    column_band: tuple | None
    row_band: tuple | None

    @property
    def margins(self):
        w, h = self.size
        a, b = self.column_band or (0, w)
        c, d = self.row_band or (0, h)
        return dict(top=c, right=w-b, bottom=h-d, left=a, repeat=0)

    def crop(self, rgba):
        result = rgba
        if self.column_band:
            a, b = self.column_band
            result = np.concatenate((result[:, :a+1], result[:, b:]), axis=1)
        if self.row_band:
            c, d = self.row_band
            result = np.concatenate((result[:c+1], result[d:]), axis=0)
        return result

    def fields(self):
        w, h = self.size
        out_w = w - (self.column_band[1]-self.column_band[0]-1 if self.column_band else 0)
        out_h = h - (self.row_band[1]-self.row_band[0]-1 if self.row_band else 0)
        return {"sourceSize": [w, h], "outputSize": [out_w, out_h], "sizeGrid": self.margins,
                "layaSizeGrid": ",".join(str(self.margins[k]) for k in ("top", "right", "bottom", "left", "repeat")),
                "columnBand": self.column_band, "rowBand": self.row_band,
                "removedColumns": {"start": self.column_band[0]+1, "length": w-out_w} if self.column_band else None,
                "removedRows": {"start": self.row_band[0]+1, "length": h-out_h} if self.row_band else None,
                "pixelAreaSavedRatio": 1 - out_w*out_h/(w*h)}


def choose_candidate(rgba, min_saving=0.20):
    if not 0 <= min_saving <= 1:
        raise ValueError("min-saving must be between 0 and 1")
    h, w = rgba.shape[:2]
    columns, rows = repeated_bands(rgba)
    if not columns and not rows:
        return None, "no_equal_bands"
    # Output area is separable: the longest complete band in each axis is the
    # minimum-area combination. Stable ties use the first band. No image-sized
    # copies are made for the Cartesian product of equivalent candidates.
    column = min(columns, key=lambda b: (-(b[1]-b[0]), b[0])) if columns else None
    row = min(rows, key=lambda b: (-(b[1]-b[0]), b[0])) if rows else None
    candidate = Candidate((w, h), column, row)
    if candidate.fields()["pixelAreaSavedRatio"] < min_saving:
        return None, "below_min_saving"
    restored = reconstruct_9slice(candidate.crop(rgba), candidate.margins, (w, h))
    if not pixel_diff(rgba, restored)["equal"]:
        return None, "reconstruction_mismatch"
    return candidate, None
