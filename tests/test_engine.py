"""Optional real GPU tests. Run with NINEGRID_ENGINE_TESTS=1 (no mocks)."""
import json
import os
from pathlib import Path
import uuid

import numpy as np
from PIL import Image
import pytest

from ninegrid.laya.adapter import LayaProjectAdapter
from ninegrid.laya.patch import rollback
from ninegrid.manifest import read_manifest
from ninegrid.paths import load_json
from ninegrid.pipeline import build, apply
from ninegrid.verify import verify_project
from .factory import make_project, panel, hashes


pytestmark = pytest.mark.skipif(os.environ.get("NINEGRID_ENGINE_TESTS") != "1",
                                 reason="Requires actual Laya 3.4.1 JS, Node, Playwright and Chrome")


def add_texture(project, name, rgba, filter_mode):
    assets = project / "assets"
    Image.fromarray(rgba).save(assets / (name + ".png"))
    uid = str(uuid.uuid5(uuid.NAMESPACE_URL, "ninegrid:engine-test:" + name))
    (assets / (name + ".png.meta")).write_text(json.dumps({"uuid": uid, "importer": {
        "textureType": 2, "filterMode": filter_mode}}))
    for kind in ("Image", "GImage"):
        data = load_json(assets / (kind + ".lh"))
        data["_$id"] = kind + "_" + name
        data["width"], data["height"] = rgba.shape[1], rgba.shape[0]
        data["skin" if kind == "Image" else "src"] = "res://" + uid
        (assets / (kind + "_" + name + ".lh")).write_text(json.dumps(data))


def test_real_laya_native_image_and_new_ui_have_identical_pixels_and_rollback(tmp_path):
    project = make_project(tmp_path / "project", panel(600, 300, (16, 16, 16, 16)))
    meta_path = project / "assets/panel.png.meta"
    meta = load_json(meta_path)
    meta["importer"]["filterMode"] = 0  # Preserve explicitly selected Point filtering.
    meta_path.write_text(json.dumps(meta))
    rng = np.random.default_rng(122)
    horizontal = rng.integers(0, 256, (17, 65, 4), dtype=np.uint8)
    horizontal[:, 5:60] = horizontal[:, 5:6]
    vertical = rng.integers(0, 256, (65, 17, 4), dtype=np.uint8)
    vertical[5:60] = vertical[5:6]
    solid = np.full((57, 101, 4), (37, 81, 123, 255), dtype=np.uint8)
    add_texture(project, "horizontal", horizontal, 0)
    add_texture(project, "vertical", vertical, 0)
    add_texture(project, "solid", solid, 1)  # Prove zero grids/1x1 at bilinear sampling too.
    original = {p: h for p, h in hashes(project).items() if p.startswith("assets/")}
    build(LayaProjectAdapter(project))
    result = apply(LayaProjectAdapter(project))
    proof = result["engineValidation"]
    assert proof["valid"] and proof["tolerance"] == 0
    assert len(proof["cases"]) == 8
    assert all(case["differentPixels"] == 0 for case in proof["cases"])
    assert verify_project(LayaProjectAdapter(project))["valid"]
    manifest = read_manifest(project)
    main = next(item for item in manifest["items"] if item["source"] == "assets/panel.png")
    assert main["outputSize"] == [33, 33]
    assert main["layaSizeGrid"] == "16,16,16,16,0"
    # Keep the full screenshots/report in a stable user-reviewable directory.
    evidence = Path(__file__).resolve().parents[1] / "out/validation/ninegrid/gpu-tests"
    import shutil
    shutil.copytree(project / ".ninegrid/validation", evidence, dirs_exist_ok=True)
    (evidence / "apply-result.json").write_text(json.dumps(result, indent=2) + "\n")
    rollback(project, result["runId"])
    after = hashes(project)
    assert all(after[p] == h for p, h in original.items())


def test_bilinear_seams_are_reported_and_block_project_patches(tmp_path):
    project = make_project(tmp_path / "project")  # Default Bilinear filtering.
    build(LayaProjectAdapter(project))
    before = {p: h for p, h in hashes(project).items() if p.startswith("assets/")}
    with pytest.raises(ValueError, match="Engine pixel comparison failed"):
        apply(LayaProjectAdapter(project))
    assert before == {p: h for p, h in hashes(project).items() if p.startswith("assets/")}
    proof = load_json(project / ".ninegrid/validation/report.json")
    assert not proof["valid"] and any(case["differentPixels"] for case in proof["cases"])


@pytest.mark.parametrize("name", ["panel_68.png", "poster_37.png"])
def test_visual_mode_real_user_images_pass_explicit_gpu_budget(tmp_path, name):
    from ninegrid.scan import read_rgba
    fixture = Path(__file__).parent / "fixtures/png" / name
    original = read_rgba(fixture)
    project = make_project(tmp_path / "project", original)
    build(LayaProjectAdapter(project), mode="visual")
    result = apply(LayaProjectAdapter(project))
    proof = result["engineValidation"]
    assert proof["valid"] and proof["tolerance"] == "explicit_per_candidate_visual_budget"
    assert len(proof["cases"]) == 2
    assert all(case["accepted"] and case["mode"] == "visual" for case in proof["cases"])
    assert any(not case["equal"] for case in proof["cases"])
    for case in proof["cases"]:
        with Image.open(project / case["before"]) as capture:
            assert capture.size == (original.shape[1], original.shape[0])
    assert verify_project(LayaProjectAdapter(project))["valid"]
    evidence = Path(__file__).resolve().parents[1] / "out/validation/ninegrid/visual-gpu" / fixture.stem
    import shutil
    shutil.copytree(project / ".ninegrid/validation", evidence, dirs_exist_ok=True)
