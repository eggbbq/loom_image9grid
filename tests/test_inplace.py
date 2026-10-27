import json
import os
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from ninegrid import run_9grid
from ninegrid.laya.adapter import LayaProjectAdapter
from ninegrid.laya.patch import rollback
from ninegrid.manifest import read_manifest
from ninegrid.paths import load_json, sha256
from ninegrid.pipeline import scan, build, apply
from ninegrid.scan import read_rgba
from ninegrid.verify import verify_project
from .factory import make_project, hashes


def unit_engine(*args):
    return {"valid": True, "unitTestStub": True}


@pytest.fixture
def project(tmp_path):
    return make_project(tmp_path / "project")


def adapter(project):
    return LayaProjectAdapter(project)


@pytest.mark.parametrize("action", ["scan", "dry_run", "build_only"])
def test_same_directory_auto_selects_inplace_without_excluding_inputs(project, action):
    before = hashes(project)
    if action == "scan":
        report = scan(adapter(project), inputs=["assets"], output="assets")
    else:
        result = build(adapter(project), inputs=["assets"], output="assets", dry_run=action == "dry_run")
        report = result["report"]
    assert report["settings"]["overwriteSource"]
    assert report["summary"]["scanned"] == 1 and report["summary"]["candidates"] == 1
    assert report["items"][0]["output"] == "assets/panel.png"
    after = hashes(project)
    assert all(after[p] == h for p, h in before.items())
    if action != "build_only":
        assert after == before


def test_full_inplace_preserves_paths_uuid_dimensions_and_can_rollback(project):
    original = hashes(project)
    uid = load_json(project / "assets/panel.png.meta")["uuid"]
    build_result = build(adapter(project), inputs=["assets"], output="assets")
    result = apply(adapter(project), engine_validator=unit_engine)
    item = read_manifest(project)["items"][0]
    assert item["status"] == "applied" and item["source"] == item["output"]
    assert load_json(project / "assets/panel.png.meta")["uuid"] == uid
    assert load_json(project / "assets/panel.png.meta")["importer"]["sizeGrid"] == [3, 4, 5, 6, 0]
    with Image.open(project / "assets/panel.png") as image:
        assert image.size == (11, 9)
    for name, field in [("Image", "skin"), ("GImage", "src")]:
        node = load_json(project / f"assets/{name}.lh")
        assert node[field] == "res://" + uid and [node["width"], node["height"]] == [60, 40]
    assert sha256(project / item["originalBackup"]) == original["assets/panel.png"]
    assert verify_project(adapter(project))["valid"]
    assert not (project / "assets/ninegrid_auto").exists()
    after_build = hashes(project)
    assert apply(adapter(project), engine_validator=lambda *a: pytest.fail("No repeated engine validation"))["runId"] is None
    assert hashes(project) == after_build
    # Re-running the entire workflow must not shrink an already optimized PNG.
    repeated = build(adapter(project), inputs=["assets"], output="assets")
    assert read_manifest(project)["items"][0]["outputSize"] == [11, 9]
    # The repeated build may refresh the human-readable report. Roll it back
    # before undoing the original apply, as required by transaction ordering.
    if repeated["runId"]:
        rollback(project, repeated["runId"])
    rollback(project, result["runId"])
    assert sha256(project / "assets/panel.png") == original["assets/panel.png"]
    rollback(project, build_result["runId"])
    assert all(hashes(project)[p] == h for p, h in original.items())


def test_failed_gpu_validation_does_not_overwrite_originals(project):
    build(adapter(project), inputs=["assets"], output="assets")
    before = hashes(project)
    with pytest.raises(ValueError, match="original PNGs and scenes unchanged"):
        apply(adapter(project), engine_validator=lambda *a: {"valid": False})
    assert hashes(project) == before


def test_new_input_scope_does_not_apply_previously_staged_images_outside_scope(project):
    original = sha256(project / "assets/panel.png")
    build(adapter(project), inputs=["assets"], output="assets")
    other = project / "assets/other"
    other.mkdir()
    Image.fromarray(np.random.default_rng(51).integers(0, 256, (20, 20, 4), dtype=np.uint8)).save(other / "noise.png")
    build(adapter(project), inputs=["assets/other"], output="assets/other")
    result = apply(adapter(project), engine_validator=lambda *a: pytest.fail("No in-scope candidate"))
    assert result["runId"] is None
    assert sha256(project / "assets/panel.png") == original
    assert read_manifest(project)["items"][0]["status"] == "built"


@pytest.mark.parametrize("kind", ["Image", "GImage"])
def test_unsafe_shared_reference_skips_whole_texture(project, kind):
    path = project / f"assets/{kind}.lh"
    data = load_json(path)
    data["width"] = 999
    path.write_text(json.dumps(data))
    before = hashes(project)
    report = scan(adapter(project), inputs=["assets"], output="assets")
    assert report["items"][0]["reason"] == "shared_source_has_unsafe_references"
    assert report["summary"]["candidates"] == 0
    assert hashes(project) == before


def test_can_transition_previously_redirected_references_back_to_original(project):
    uid = load_json(project / "assets/panel.png.meta")["uuid"]
    build(adapter(project))
    apply(adapter(project), engine_validator=unit_engine)
    assert load_json(project / "assets/GImage.lh")["src"] != "res://" + uid
    report = build(adapter(project), inputs=["assets"], output="assets")["report"]
    assert report["summary"]["candidates"] == 1
    result = apply(adapter(project), engine_validator=unit_engine)
    assert len(result["changes"]) == 2
    assert load_json(project / "assets/GImage.lh")["src"] == "res://" + uid
    assert load_json(project / "assets/Image.lh")["skin"] == "res://" + uid
    assert verify_project(adapter(project))["valid"]


def test_inplace_apply_transaction_failure_restores_png_metadata_and_scenes(project, monkeypatch):
    import ninegrid.laya.patch as transaction
    build(adapter(project), inputs=["assets"], output="assets")
    before = {p: h for p, h in hashes(project).items() if p.startswith("assets/") or p == ".ninegrid/manifest.json"}
    real = transaction.atomic_write
    failed = False

    def write(path, content):
        nonlocal failed
        if Path(path) == project / "assets/panel.png.meta" and not failed:
            failed = True
            raise OSError("injected in-place commit failure")
        return real(path, content)

    monkeypatch.setattr(transaction, "atomic_write", write)
    with pytest.raises(OSError, match="injected"):
        apply(adapter(project), engine_validator=unit_engine)
    assert all(hashes(project)[p] == h for p, h in before.items())


def test_original_or_staging_edit_blocks_apply(project):
    build(adapter(project), inputs=["assets"], output="assets")
    item = read_manifest(project)["items"][0]
    (project / item["stagedOutput"]).write_bytes(b"changed")
    before = hashes(project)
    with pytest.raises(ValueError, match="In-place PNG changed"):
        apply(adapter(project), engine_validator=lambda *a: pytest.fail("Must reject before engine"))
    assert hashes(project) == before


def test_new_unsafe_reference_after_build_blocks_overwrite(project):
    build(adapter(project), inputs=["assets"], output="assets")
    node = load_json(project / "assets/GImage.lh")
    node["_$id"] = "new-unsafe"
    node["autoSize"] = True
    (project / "assets/unsafe.lh").write_text(json.dumps(node))
    before = hashes(project)
    with pytest.raises(ValueError, match="unsafe reference"):
        apply(adapter(project), engine_validator=lambda *a: pytest.fail("Must reject before engine"))
    assert hashes(project) == before


def test_scene_added_during_gpu_validation_prevents_commit(project):
    build(adapter(project), inputs=["assets"], output="assets")
    original = sha256(project / "assets/panel.png")

    def engine(*args):
        node = load_json(project / "assets/GImage.lh")
        (project / "assets/new.lh").write_text(json.dumps(node))
        return unit_engine()

    with pytest.raises(ValueError, match="Scene inventory changed"):
        apply(adapter(project), engine_validator=engine)
    assert sha256(project / "assets/panel.png") == original


def test_runner_same_directory_dry_run_is_zero_write(project):
    before = hashes(project)
    args = run_9grid.parser().parse_args(["--project", str(project), "--search-dir", "assets", "--output", "assets", "--dry-run"])
    result = run_9grid.run(args)
    assert result["report"]["settings"]["overwriteSource"] and len(result["changes"]) == 2
    uid = load_json(project / "assets/panel.png.meta")["uuid"]
    assert all(change["newSkin"] == "res://" + uid for change in result["changes"])
    assert hashes(project) == before


@pytest.mark.skipif(os.environ.get("NINEGRID_ENGINE_TESTS") != "1", reason="Requires actual Laya/Chrome")
@pytest.mark.parametrize("fixture", [None, "panel_68.png", "poster_37.png"])
def test_inplace_actual_laya_gpu_and_rollback(tmp_path, fixture):
    rgba = read_rgba(Path(__file__).parent / "fixtures/png" / fixture) if fixture else None
    project = make_project(tmp_path / "project", rgba)
    if not fixture:
        meta = load_json(project / "assets/panel.png.meta")
        meta["importer"]["filterMode"] = 0
        (project / "assets/panel.png.meta").write_text(json.dumps(meta))
    original = {p: h for p, h in hashes(project).items() if p.startswith("assets/")}
    build(adapter(project), inputs=["assets"], output="assets", mode="visual" if fixture else "strict")
    result = apply(adapter(project))
    assert result["engineValidation"]["valid"]
    assert len(result["engineValidation"]["cases"]) == 2
    assert verify_project(adapter(project))["valid"]
    rollback(project, result["runId"])
    assert all(hashes(project)[p] == h for p, h in original.items())
