import json
import os
from pathlib import Path
import uuid

import numpy as np
from PIL import Image

from ninegrid.reconstruct import reconstruct_9slice


FIXTURES = Path(__file__).parent / "fixtures/laya"


def panel(width=60, height=40, margins=(3, 4, 5, 6), seed=17):
    top, right, bottom, left = margins
    rng = np.random.default_rng(seed)
    compact = rng.integers(0, 256, (top+bottom+1, left+right+1, 4), dtype=np.uint8)
    # Includes invisible RGB and antialiased edges; no alpha normalization.
    return reconstruct_9slice(compact, dict(top=top, right=right, bottom=bottom, left=left), (width, height))


def make_project(path, rgba=None):
    path = Path(path)
    assets = path / "assets"
    assets.mkdir(parents=True)
    (path / "Demo.laya").write_text(json.dumps({"version": os.environ.get("NINEGRID_TEST_PROJECT_VERSION", "3.4.1")}) + "\n")
    rgba = panel() if rgba is None else rgba
    Image.fromarray(rgba).save(assets / "panel.png")
    texture_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "ninegrid:fixture:panel"))
    (assets / "panel.png.meta").write_text(json.dumps({"uuid": texture_id, "importer": {"textureType": 2}}))
    for original, name in (("Image.before.lh", "Image.lh"), ("GImage.lh", "GImage.lh")):
        node = json.loads((FIXTURES / original).read_text())
        node["width"], node["height"] = rgba.shape[1], rgba.shape[0]
        node["skin" if node["_$type"] == "Image" else "src"] = "res://" + texture_id
        (assets / name).write_text(json.dumps(node, indent=2) + "\n")
    return path


def hashes(root):
    import hashlib
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}
