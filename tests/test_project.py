import copy
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from ninegrid.__main__ import main
from ninegrid.laya.adapter import LayaProjectAdapter
from ninegrid.laya.patch import rollback
from ninegrid.manifest import read_manifest
from ninegrid.paths import load_json, sha256
from ninegrid.pipeline import scan, build, apply
from ninegrid.verify import verify_project
from .factory import make_project, hashes


@pytest.fixture
def project(tmp_path):
    return make_project(tmp_path / "project")


def adapter(project):
    return LayaProjectAdapter(project)


def unit_engine_validator(*args):
    # Transaction unit tests isolate GPU checks. test_engine.py uses real Chrome.
    return {"valid": True, "unitTestStub": True}


def test_native_serializer_fixture_has_real_property_diff():
    from .factory import FIXTURES
    before = load_json(FIXTURES / "Image.before.lh")
    after = load_json(FIXTURES / "Image.with-grid.lh")
    assert after.pop("sizeGrid") == "3,4,5,6,0"
    assert after == before
    assert load_json(FIXTURES / "GImage.lh")["autoSize"] is False
    assert len(load_json(FIXTURES / "native-ninegrid.png.meta")["importer"]["sizeGrid"]) == 5


def test_scan_and_all_dry_runs_write_nothing(project):
    before = hashes(project)
    report = scan(adapter(project))
    assert report["summary"] == {"scanned": 1, "candidates": 1, "safeReferences": 2}
    build(adapter(project), dry_run=True)
    assert before == hashes(project)
    build(adapter(project))
    before = hashes(project)
    result = apply(adapter(project), dry_run=True)
    assert len(result["files"]) == 2
    assert len(result["changes"]) == 2
    assert hashes(project) == before


def test_apply_patches_only_native_fields_and_preserves_unknown_properties(project):
    path = project / "assets/Image.lh"
    original = load_json(path)
    original.update(alpha=0.75, anchorX=0.5, visible=False,
                    futureProperty={"keep": [1, 2, "x"]}, **{"_$unused": "untouched"})
    path.write_text(json.dumps(original))
    gimage = load_json(project / "assets/GImage.lh")
    build(adapter(project))
    result = apply(adapter(project), engine_validator=unit_engine_validator)
    assert result["runId"]
    after = load_json(path)
    assert after.pop("sizeGrid") == "3,4,5,6,0"
    assert after.pop("skin") != original.pop("skin")
    assert after == original
    gafter = load_json(project / "assets/GImage.lh")
    assert gafter.pop("src") != gimage.pop("src")
    assert "sizeGrid" not in gafter
    assert gafter == gimage
    assert (project / "assets/panel.png").is_file()
    assert verify_project(adapter(project))["valid"]


@pytest.mark.parametrize("patch,reason", [
    ({"width": 30}, "display_size_differs_from_png"),
    ({"sizeGrid": "1,1,1,1,0"}, "existing_size_grid"),
    ({"useSourceSize": True}, "automatic_source_size"),
    ({"_$comp": [{"_$type": "UserScript"}]}, "dynamic_or_inherited_configuration"),
    ({"rotation": 90}, "transformed_reference"),
    ({"left": 0}, "layout_controlled_size"),
    ({"clipX": 2}, "special_rendering_or_dynamic_skin"),
])
def test_unsafe_references_are_skipped_but_safe_gimage_is_updated(project, patch, reason):
    path = project / "assets/Image.lh"
    before = load_json(path)
    before.update(patch)
    path.write_text(json.dumps(before))
    original = path.read_bytes()
    build(adapter(project))
    result = apply(adapter(project), engine_validator=unit_engine_validator)
    assert path.read_bytes() == original
    assert any(r["reason"] == reason and not r["eligible"] for r in result["changes"])
    assert load_json(project / "assets/GImage.lh")["src"] != "res://" + load_json(project / "assets/panel.png.meta")["uuid"]


def test_auto_sizing_new_ui_is_skipped(project):
    path = project / "assets/GImage.lh"
    node = load_json(path)
    node.pop("autoSize")
    path.write_text(json.dumps(node))
    result = scan(adapter(project))
    assert any(r["reason"] == "automatic_source_size" for r in result["items"][0]["references"])


@pytest.mark.parametrize("bad", ["version", "scene", "no_scenes"])
def test_unknown_project_or_serialization_fails_without_writes(project, bad):
    if bad == "version":
        (project / "Demo.laya").write_text('{"version":"9.0.0"}')
    elif bad == "scene":
        (project / "assets/Image.lh").write_text('{"_$ver":2,"_$type":"Image"}')
    else:
        for p in (project / "assets").glob("*.lh"):
            p.unlink()
    before = hashes(project)
    assert main(["scan", "--project", str(project)]) == 1
    assert hashes(project) == before


def test_build_apply_are_idempotent_and_do_not_chain_optimize(project):
    build(adapter(project))
    first = hashes(project)
    assert build(adapter(project))["runId"] is None
    assert hashes(project) == first
    apply(adapter(project), engine_validator=unit_engine_validator)
    first = hashes(project)
    result = apply(adapter(project), engine_validator=lambda *a: pytest.fail("No second GPU probe for a no-op"))
    assert result["runId"] is None
    assert hashes(project) == first
    assert len(list((project / "assets/ninegrid_auto").rglob("*.png"))) == 1


def test_failed_commit_restores_scenes_and_manifest(project, monkeypatch):
    import ninegrid.laya.patch as module
    build(adapter(project))
    before = hashes(project)
    real_write = module.atomic_write
    failed = False

    def write(path, data):
        nonlocal failed
        if Path(path) == project / "assets/Image.lh" and not failed:
            failed = True
            raise OSError("simulated commit failure")
        return real_write(path, data)

    monkeypatch.setattr(module, "atomic_write", write)
    with pytest.raises(OSError, match="simulated commit failure"):
        apply(adapter(project), engine_validator=unit_engine_validator)
    after = hashes(project)
    for name, value in before.items():
        assert after[name] == value
    assert not (project / ".ninegrid/apply-report.json").exists()
    assert any(load_json(p)["status"] == "failed_rolled_back" for p in
               (project / ".ninegrid/backups").glob("*/transaction.json"))


def test_rollback_restores_every_modified_file_and_can_be_repeated(project):
    build(adapter(project))
    before = hashes(project)
    result = apply(adapter(project), engine_validator=unit_engine_validator)
    dry = hashes(project)
    rollback(project, result["runId"], dry_run=True)
    assert dry == hashes(project)
    rollback(project, result["runId"])
    for name, digest in before.items():
        assert sha256(project / name) == digest
    assert not (project / ".ninegrid/apply-report.json").exists()
    assert rollback(project, result["runId"])["status"] == "already_rolled_back"
    assert verify_project(adapter(project))["valid"]


def test_rollback_refuses_external_edits(project):
    build(adapter(project))
    result = apply(adapter(project), engine_validator=unit_engine_validator)
    (project / "assets/Image.lh").write_text('{"changed":"by user"}')
    before = hashes(project)
    with pytest.raises(ValueError, match="Rollback conflict"):
        rollback(project, result["runId"])
    assert before == hashes(project)


def test_original_source_update_blocks_stale_apply(project):
    build(adapter(project))
    Image.new("RGBA", (60, 40), "blue").save(project / "assets/panel.png")
    before = hashes(project)
    with pytest.raises(ValueError, match="Stale manifest"):
        apply(adapter(project), engine_validator=unit_engine_validator)
    assert hashes(project) == before


def test_bad_reconstruction_and_gpu_failure_prevent_scene_writes(project):
    build(adapter(project))
    before = hashes(project)
    with pytest.raises(ValueError, match="Engine pixel comparison failed"):
        apply(adapter(project), engine_validator=lambda *a: {"valid": False, "differentPixels": 1})
    assert before == hashes(project)
    manifest = read_manifest(project)
    png = project / manifest["items"][0]["output"]
    image = np.array(Image.open(png).convert("RGBA"))
    image[0, 0, 0] ^= 255
    Image.fromarray(image).save(png)
    manifest["items"][0]["outputSha256"] = sha256(png)
    (project / ".ninegrid/manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Pixel reconstruction failed"):
        verify_project(adapter(project))


@pytest.mark.parametrize("output", ["../outside", "/tmp/ninegrid-forbidden"])
def test_path_traversal_is_rejected_without_writes(project, output):
    before = hashes(project)
    with pytest.raises(ValueError):
        build(adapter(project), output=output)
    assert before == hashes(project)


def test_symlink_escape_is_rejected(project, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / "assets/link").symlink_to(outside, target_is_directory=True)
    before = hashes(project)
    with pytest.raises(ValueError):
        build(adapter(project), output="assets/link/generated")
    assert before == hashes(project)


def test_existing_ninegrid_resource_is_skipped(project):
    path = project / "assets/panel.png.meta"
    meta = load_json(path)
    meta["importer"]["sizeGrid"] = [1, 2, 3, 4, 0]
    path.write_text(json.dumps(meta))
    item = scan(adapter(project))["items"][0]
    assert item["status"] == "skipped" and item["reason"] == "existing_texture_size_grid"


def test_native_path_references_are_replaced_exactly(project):
    image = project / "assets/Image.lh"
    data = load_json(image)
    data.update(skin="panel.png", note="panel.png must remain unchanged")
    image.write_text(json.dumps(data))
    gimage = project / "assets/GImage.lh"
    data = load_json(gimage)
    data["src"] = "assets/panel.png"
    gimage.write_text(json.dumps(data))
    build(adapter(project))
    apply(adapter(project), engine_validator=unit_engine_validator)
    assert load_json(image)["skin"] == "ninegrid_auto/panel.png"
    assert load_json(image)["note"] == "panel.png must remain unchanged"
    assert load_json(gimage)["src"] == "assets/ninegrid_auto/panel.png"


def test_source_sampling_changes_block_stale_manifest(project):
    build(adapter(project))
    meta = project / "assets/panel.png.meta"
    data = load_json(meta)
    data["importer"]["sRGB"] = False
    meta.write_text(json.dumps(data))
    before = hashes(project)
    with pytest.raises(ValueError, match="Stale source texture settings"):
        apply(adapter(project), engine_validator=unit_engine_validator)
    assert hashes(project) == before


def test_generated_texture_color_settings_cannot_change_silently(project):
    build(adapter(project))
    path = project / "assets/ninegrid_auto/panel.png.meta"
    data = load_json(path)
    data["importer"]["sRGB"] = False
    path.write_text(json.dumps(data))
    before = hashes(project)
    with pytest.raises(ValueError, match="Optimized texture importer changed"):
        verify_project(adapter(project))
    assert hashes(project) == before


def test_external_scene_edit_between_parse_and_commit_is_not_overwritten(project):
    build(adapter(project))
    parsed = adapter(project)
    path = project / "assets/Image.lh"
    data = load_json(path)
    data["userNote"] = "new external edit"
    path.write_text(json.dumps(data))
    before = hashes(project)
    with pytest.raises(ValueError, match="Concurrent modification"):
        apply(parsed, engine_validator=unit_engine_validator)
    assert hashes(project) == before


def test_auto_atlas_config_does_not_blanket_block_source_png_analysis(project):
    (project / "assets/AutoAtlas.atlascfg").write_text("{}")
    item = scan(adapter(project))["items"][0]
    assert item["status"] == "candidate"
    assert item["warnings"] == ["source_auto_atlas_present_originals_preserved"]
    assert len(item["atlasConfigurations"]) == 1
    assert (project / "assets/AutoAtlas.atlascfg").read_text() == "{}"


def test_unmanaged_output_is_not_overwritten(project):
    target = project / "assets/ninegrid_auto/panel.png"
    target.parent.mkdir()
    target.write_bytes(b"unmanaged")
    before = hashes(project)
    with pytest.raises(ValueError, match="unmanaged"):
        build(adapter(project))
    assert hashes(project) == before
