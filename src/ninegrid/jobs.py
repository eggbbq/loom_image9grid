"""Pickle-safe, read-only per-image jobs. No scene or source-file mutations."""
import hashlib
from io import BytesIO

import numpy as np
from PIL import Image

from .candidate import Candidate, choose_candidate
from .crop import png_bytes
from .paths import inside, sha256
from .scan import read_rgba
from .visual import VisualCandidate, choose_visual_candidate


def analyze_image(job):
    root, item, options = job
    source = inside(root, item["source"])
    try:
        rgba = read_rgba(source)
        item["sourceSize"] = list(rgba.shape[1::-1])
        if sha256(source) != item["sourceSha256"]:
            raise ValueError("source_changed_during_scan")
        if item["reason"]:
            candidate, reason = None, item["reason"]
        elif options["mode"] == "visual":
            candidate, diff = choose_visual_candidate(rgba, options["minSaving"], options["quality"],
                                                       options["centerSize"], options["axis"])
            reason = diff.get("reason")
            item["cpuQuality"], item["qualityLimits"] = diff, options["quality"]
        else:
            candidate, reason = choose_candidate(rgba, options["minSaving"])
        item["reason"] = reason
        if candidate:
            item.update(candidate.fields(), status="candidate")
    except (OSError, ValueError) as exc:
        item["reason"] = str(exc)
    return item


def candidate_from_fields(item):
    if item.get('geometryOnly') or item.get('candidateCoding') or item.get('sizeGrid',{}).get('repeat')==1:
        from .automatic.codecs import decode_candidate
        return decode_candidate(item)
    return _legacy_candidate_from_fields(item)


def _legacy_candidate_from_fields(item):
    size = tuple(item["sourceSize"])
    if item.get("mode") != "visual":
        return Candidate(size, tuple(item["columnBand"]) if item["columnBand"] else None,
                         tuple(item["rowBand"]) if item["rowBand"] else None)
    bands = []
    for key, center in (("columnBand", "sourceCenterX"), ("rowBand", "sourceCenterY")):
        band = item[key]
        sample = item[center]
        bands.append((*band, sample[0] + (sample[1]-sample[0])//2) if band else None)
    lengths = [item[key][1]-item[key][0] for key in ("sourceCenterX", "sourceCenterY") if item.get(key)]
    # Each axis clips the requested center to its own band's available length.
    # The first axis can therefore retain fewer pixels than the second. Using
    # its length for both axes shrinks the decoded candidate a second time.
    return VisualCandidate(size, *bands, center_size=max(lengths))


def encode_image(job):
    root, item, include_original = job
    source = inside(root, item["source"])
    if sha256(source) != item["sourceSha256"]:
        raise ValueError(f"Source changed during build: {item['source']}")
    rgba = read_rgba(source)
    candidate = candidate_from_fields(item)
    fields = candidate.fields()
    if fields["outputSize"] != item["outputSize"] or fields["sizeGrid"] != item["sizeGrid"]:
        raise ValueError("Stored candidate geometry changed")
    cropped = candidate.crop(rgba)
    content = png_bytes(cropped)
    with Image.open(BytesIO(content)) as encoded:
        if not np.array_equal(cropped, np.asarray(encoded.convert("RGBA"))):
            raise ValueError("PNG roundtrip changed RGBA")
    original = source.read_bytes() if include_original else None
    if (hashlib.sha256(original).hexdigest() if original is not None else sha256(source)) != item["sourceSha256"]:
        raise ValueError(f"Source changed during build: {item['source']}")
    return content, original


def verify_image(job):
    from .verify import verify_item
    return verify_item(*job)


def compare_render(job):
    from .reconstruct import pixel_diff
    from .visual import visual_diff, accepted
    root, record = job
    with Image.open(inside(root, record["before"])) as a, Image.open(inside(root, record["after"])) as b:
        original, optimized = np.asarray(a.convert("RGBA")), np.asarray(b.convert("RGBA"))
    if record.get("mode") == "visual":
        diff = visual_diff(original, optimized, record["qualityLimits"]["pixelErrorThreshold"])
        diff["accepted"] = accepted(diff, record["qualityLimits"])
    else:
        diff = pixel_diff(original, optimized)
        diff["accepted"] = diff["equal"]
    content = None
    if not diff["equal"] and original.shape == optimized.shape:
        delta = np.max(np.abs(original.astype(np.int16)-optimized.astype(np.int16)), axis=2)
        mask = np.where(delta[..., None], [255, 0, 0, 255], [0, 0, 0, 0]).astype(np.uint8)
        content = png_bytes(mask)
    return {**record, **diff}, content
