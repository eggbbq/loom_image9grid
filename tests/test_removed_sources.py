import copy
import json
import shutil
import uuid

import pytest

from ninegrid.laya.adapter import LayaProjectAdapter
from ninegrid.manifest import MANIFEST, read_manifest, read_active_manifest, cleanup_removed_sources
from ninegrid.paths import load_json, sha256
from ninegrid.pipeline import scan, build, apply
from ninegrid.verify import verify_project
from .factory import make_project, hashes


def unit_engine(*args):
    return {"valid": True, "unitTestStub": True}


def adapter(project):
    return LayaProjectAdapter(project)


@pytest.fixture(params=[False, True], ids=["separate-output", "inplace"])
def bundle(tmp_path, request):
    project = make_project(tmp_path / "project")
    old = project / "assets/old"
    old.mkdir()
    for path in list((project / "assets").iterdir()):
        if path.is_file():
            path.rename(old / path.name)
    node = load_json(old / "GImage.lh")
    original = (old / "panel.png").read_bytes()
    build(adapter(project), inputs=["assets/old"], output="assets/old" if request.param else "assets/generated")
    apply(adapter(project), engine_validator=unit_engine)
    new = project / "assets/new"
    new.mkdir()
    (new / "panel.png").write_bytes(original)
    uid = str(uuid.uuid4())
    (new / "panel.png.meta").write_text(json.dumps({"uuid": uid, "importer": {"textureType": 2}}))
    node["_$id"], node["src"] = "new-panel", "res://" + uid
    (new / "GImage.lh").write_text(json.dumps(node))
    return project, old, node


def test_removed_bundle_is_retired_without_writing_during_scan_or_verify(bundle):
    project, old, _ = bundle
    shutil.rmtree(old)
    before = hashes(project)
    report = scan(adapter(project), inputs=["assets/new"], output="assets/new")
    assert report["retiredSources"] == ["assets/old/panel.png"]
    assert report["summary"]["scanned"] == 1
    verified = verify_project(adapter(project))
    assert verified["valid"] and verified["items"] == []
    assert verified["retiredSources"] == ["assets/old/panel.png"]
    build(adapter(project), inputs=["assets/new"], output="assets/new", dry_run=True)
    assert hashes(project) == before


def test_rebuild_drops_removed_bundle_keeps_backups_and_processes_current_bundle(bundle):
    project, old, _ = bundle
    old_manifest = read_manifest(project)
    old_item = copy.deepcopy(old_manifest["items"][0])
    backups = {p: h for p, h in hashes(project).items() if p.startswith(".ninegrid/backups/")}
    shutil.rmtree(old)
    build(adapter(project), inputs=["assets/new"], output="assets/new")
    manifest = read_manifest(project)
    assert [i["source"] for i in manifest["items"]] == ["assets/new/panel.png"]
    assert manifest["retiredItems"][0]["source"] == old_item["source"]
    assert manifest["retiredItems"][0]["retiredReason"] == "source_removed_unreferenced"
    result = apply(adapter(project), engine_validator=unit_engine)
    assert result["runId"] and verify_project(adapter(project))["valid"]
    assert not old.exists()
    assert all(hashes(project)[p] == h for p, h in backups.items())


def test_apply_handles_removed_history_after_new_images_were_already_staged(bundle):
    project, old, _ = bundle
    build(adapter(project), inputs=["assets/new"], output="assets/new")
    staged = read_manifest(project)
    assert len(staged["items"]) == 2
    shutil.rmtree(old)
    result = apply(adapter(project), engine_validator=unit_engine)
    assert result["runId"]
    manifest = read_manifest(project)
    assert len(manifest["items"]) == 1 and manifest["items"][0]["status"] == "applied"
    assert manifest["retiredItems"][0]["source"] == "assets/old/panel.png"
    assert not old.exists()


def test_apply_with_only_retired_history_cleans_manifest_without_rendering(bundle):
    project, old, _ = bundle
    shutil.rmtree(old)
    result = apply(adapter(project), engine_validator=lambda *a: pytest.fail("No active images"))
    assert result["status"] == "retired_removed_sources" and result["runId"]
    assert read_manifest(project)["items"] == []
    assert not old.exists()


@pytest.mark.parametrize("reference", ["uuid", "asset-path", "relative-path"])
def test_missing_but_still_referenced_resource_gives_actionable_error(bundle, reference):
    project, old, node = bundle
    item = read_manifest(project)["items"][0]
    dangling = copy.deepcopy(node)
    dangling["_$id"] = "dangling"
    dangling["src"] = ("res://" + item["sourceMetadata"]["uuid"] if reference == "uuid" else
                       item["source"] if reference == "asset-path" else item["source"][len("assets/"):])
    (project / "assets/dangling.lh").write_text(json.dumps(dangling))
    shutil.rmtree(old)
    before = hashes(project)
    with pytest.raises(ValueError, match="已删除但仍被场景引用"):
        read_active_manifest(adapter(project))
    assert hashes(project) == before


def test_record_cleanup_preserves_pending_sources_and_is_idempotent(bundle):
    project, old, _ = bundle
    build(adapter(project), inputs=["assets/new"], output="assets/new")
    pending = read_manifest(project)["inplacePendingSources"]
    shutil.rmtree(old)
    before = sha256(project / MANIFEST)
    preview = cleanup_removed_sources(adapter(project), dry_run=True)
    assert preview["retiredSources"] == ["assets/old/panel.png"]
    assert sha256(project / MANIFEST) == before
    result = cleanup_removed_sources(adapter(project))
    assert result["runId"] and read_manifest(project)["inplacePendingSources"] == pending
    assert cleanup_removed_sources(adapter(project))["runId"] is None
