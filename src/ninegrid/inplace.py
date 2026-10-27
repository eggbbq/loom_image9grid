"""Stage, validate, and transactionally replace source PNGs without changing UUIDs."""
import copy
import hashlib
import uuid

from .laya.adapter import LayaProjectAdapter
from .laya.patch import commit
from .laya.references import Reference, unsafe_reason
from .manifest import MANIFEST, read_active_manifest, markdown_report
from .paths import inside, relative, sha256, load_json, json_bytes
from .reconstruct import reconstruct_9slice, pixel_diff
from .scan import read_rgba
from .visual import visual_diff, accepted
from .jobs import encode_image, verify_image


def reference_plans(adapter, item):
    root = adapter.project.root
    source = inside(root, item["source"])
    previous = inside(root, item["previousOutput"]) if item.get("previousOutput") else None
    plans = []
    for ref in adapter.references:
        if ref.source not in (source, previous):
            continue
        node = copy.deepcopy(ref.node)
        if ref.source == previous and previous != source:
            old = node[ref.field]
            uid = item["sourceMetadata"].get("uuid")
            if old.startswith("res://") and not uid:
                raise ValueError(f"Missing original UUID: {item['source']}")
            node[ref.field] = "res://" + uid if old.startswith("res://") else (
                item["source"] if old.startswith("assets/") else item["source"][len("assets/"):])
            if node.get("sizeGrid") == item.get("previousGrid"):
                node.pop("sizeGrid")
        virtual = Reference(ref.file, ref.pointer, node, source, ref.field, ref.inherited_conflict)
        record = ref.report(unsafe_reason(virtual, item["sourceSize"]))
        if record["eligible"]:
            record.update(newSkin=node[ref.field], newSizeGrid=item["layaSizeGrid"],
                          gridLocation="node.sizeGrid" if ref.field == "skin" else "texture.importer.sizeGrid")
        plans.append((ref, record))
    return plans


def prepare_report(adapter, report, previous, pool=None):
    # A shared source texture cannot be changed using a partial scene inventory.
    full = LayaProjectAdapter(adapter.project.root)
    if full.project.scene_hashes != adapter.project.scene_hashes:
        raise ValueError("原地写回需要扫描全部场景，请去掉 --scene-root 限制。")
    old = {item["source"]: item for item in previous["items"]}
    checked = [old[i["source"]] for i in report["items"] if i["source"] in old
               and old[i["source"]].get("overwriteSource") and old[i["source"]].get("status") == "applied"]
    pool.map(verify_image, [(str(adapter.project.root), i) for i in checked], "已有结果校验")
    for item in report["items"]:
        prior = old.get(item["source"])
        if prior and prior.get("overwriteSource") and prior.get("status") == "applied":
            item.update(status="skipped", reason="already_optimized_in_place", safeReferences=0)
            continue
        if item["status"] != "candidate":
            continue
        item["overwriteSource"] = True
        if prior and not prior.get("overwriteSource"):
            item.update(previousOutput=prior["output"], previousGrid=prior["layaSizeGrid"])
        plans = reference_plans(adapter, item)
        item["references"] = [record for _, record in plans]
        item["safeReferences"] = sum(record["eligible"] for _, record in plans)
        if any(not record["eligible"] for _, record in plans):
            # Separate outputs may leave unsafe users on the original. Replacing
            # a shared original cannot do that, so skip the entire texture.
            item.update(status="skipped", reason="shared_source_has_unsafe_references", safeReferences=0)
    report["summary"].update(candidates=sum(i["status"] == "candidate" for i in report["items"]),
                             safeReferences=sum(i["safeReferences"] for i in report["items"]))


def build_inplace(adapter, report, dry_run=False, pool=None):
    root = adapter.project.root
    old = read_active_manifest(adapter)[0] if inside(root, MANIFEST).exists() else {"items": []}
    previous = {item["source"]: item for item in old["items"]}
    manifest = {**copy.deepcopy(old), "schemaVersion": 1, "projectVersion": adapter.project.version,
                "settings": report["settings"], "items": []}
    writes, expected = {}, {}
    to_encode = [i for i in report["items"] if i["status"] == "candidate"
                 and not (previous.get(i["source"], {}).get("overwriteSource")
                          and previous.get(i["source"], {}).get("status") == "applied")]
    encoded = dict(zip((i["source"] for i in to_encode),
                       pool.map(encode_image, [(str(root), i, True) for i in to_encode], "生成 PNG")))
    checked = [previous[i["source"]] for i in report["items"] if i["source"] in previous
               and previous[i["source"]].get("overwriteSource") and previous[i["source"]].get("status") == "applied"]
    pool.map(verify_image, [(str(root), i) for i in checked], "保留结果校验")
    for scanned in report["items"]:
        prior = previous.get(scanned["source"])
        if prior and prior.get("overwriteSource") and prior.get("status") == "applied":
            manifest["items"].append(prior)
            continue
        if scanned["status"] != "candidate":
            continue
        item = copy.deepcopy(scanned)
        source = inside(root, item["source"])
        if sha256(source) != item["sourceSha256"]:
            raise ValueError(f"Source changed during build: {item['source']}")
        content, original = encoded[item["source"]]
        meta_path = inside(root, item["source"] + ".meta")
        meta = load_json(meta_path) if meta_path.exists() else {}
        if meta != item["sourceMetadata"]:
            raise ValueError("Source metadata changed during build")
        new_meta = copy.deepcopy(meta)
        new_meta.setdefault("uuid", str(uuid.uuid5(uuid.NAMESPACE_URL, "ninegrid:original:" + item["source"])))
        new_meta.setdefault("importer", {}).setdefault("textureType", 2)
        new_meta["importer"]["sizeGrid"] = [item["sizeGrid"][k] for k in ("top", "right", "bottom", "left", "repeat")]
        item.update(status="built", outputSha256=hashlib.sha256(content).hexdigest(),
                    outputUuid=new_meta["uuid"], outputMetadata=new_meta,
                    originalBackup=f".ninegrid/originals/{item['sourceSha256']}.png",
                    stagedOutput=".ninegrid/staged/" + item["source"][len("assets/"):])
        for name, data in ((item["originalBackup"], original), (item["stagedOutput"], content)):
            path = inside(root, name)
            if path.exists() and name == item["originalBackup"] and sha256(path) != item["sourceSha256"]:
                raise ValueError("Original backup changed")
            if path.exists() and name == item["stagedOutput"] and not (prior and prior.get("overwriteSource")):
                raise ValueError(f"Refusing to overwrite unmanaged staging file: {name}")
            expected[name] = sha256(path) if path.exists() else None
            writes[name] = data
        expected[item["source"]] = item["sourceSha256"]
        expected[item["source"] + ".meta"] = sha256(meta_path) if meta_path.exists() else None
        expected.update({c["file"]: c["sha256"] for c in item.get("atlasConfigurations", [])})
        manifest["items"].append(item)
    selected = {i["source"] for i in manifest["items"]}
    manifest["inplacePendingSources"] = [i["source"] for i in manifest["items"] if i["status"] == "built"]
    if manifest["inplacePendingSources"]:
        manifest.pop("engineValidation", None)
    manifest["items"].extend(item for item in old["items"] if item["source"] not in selected)
    expected[MANIFEST] = sha256(inside(root, MANIFEST)) if inside(root, MANIFEST).exists() else None
    writes.update({MANIFEST: json_bytes(manifest), ".ninegrid/report.json": json_bytes(report),
                   ".ninegrid/report.md": markdown_report(report).encode("utf-8")})
    if dry_run:
        return {"status": "dry_run", "files": sorted(writes), "report": report}
    run_id = commit(root, writes, "build_inplace", expected)
    return {"status": "built", "runId": run_id, "manifest": MANIFEST, "report": report}


def verify_inplace_item(root, item):
    original = inside(root, item["originalBackup"])
    if sha256(original) != item["sourceSha256"]:
        raise ValueError("Original backup hash mismatch")
    applied = item.get("status") == "applied"
    output = inside(root, item["output"] if applied else item["stagedOutput"])
    if sha256(output) != item["outputSha256"]:
        raise ValueError(f"In-place PNG changed: {item['output']}")
    meta_path = inside(root, item["source"] + ".meta")
    meta = load_json(meta_path) if meta_path.exists() else {}
    expected_meta = item["outputMetadata"] if applied else item["sourceMetadata"]
    if meta != expected_meta:
        raise ValueError(f"In-place metadata changed: {item['source']}")
    if not applied and sha256(inside(root, item["source"])) != item["sourceSha256"]:
        raise ValueError(f"Source changed before in-place apply: {item['source']}")
    for config in item.get("atlasConfigurations", []):
        if sha256(inside(root, config["file"])) != config["sha256"]:
            raise ValueError("Source atlas configuration changed")
    rgba, optimized = read_rgba(original), read_rgba(output)
    if list(rgba.shape[1::-1]) != item["sourceSize"] or list(optimized.shape[1::-1]) != item["outputSize"]:
        raise ValueError("In-place PNG dimensions disagree")
    restored = reconstruct_9slice(optimized, item["sizeGrid"], item["sourceSize"])
    if item.get("mode") == "visual":
        result = visual_diff(rgba, restored, item["qualityLimits"]["pixelErrorThreshold"])
        result.update(mode="visual", accepted=accepted(result, item["qualityLimits"]))
    else:
        result = pixel_diff(rgba, restored)
        result["accepted"] = result["equal"]
    if not result["accepted"]:
        raise ValueError("In-place reconstruction exceeds error budget")
    return {"source": item["source"], **result}


def apply_inplace(adapter, manifest, dry_run=False, engine_dir=None, engine_validator=None, retired=None, pool=None):
    from .verify import verify_project
    root = adapter.project.root
    verify_project(adapter, manifest, pool=pool)
    snapshot = copy.deepcopy(adapter.project.scenes)
    expected = {MANIFEST: sha256(inside(root, MANIFEST)), **adapter.project.scene_hashes}
    writes, changes, entries = {}, [], []
    pending = set(manifest.get("inplacePendingSources", [i["source"] for i in manifest["items"]
                                                        if i.get("overwriteSource") and i["status"] == "built"]))
    for item in manifest["items"]:
        if not item.get("overwriteSource") or item["status"] == "applied" or item["source"] not in pending:
            continue
        plans = reference_plans(adapter, item)
        if any(not record["eligible"] for _, record in plans):
            raise ValueError("Shared source acquired an unsafe reference; source PNGs unchanged")
        for ref, record in plans:
            ref.node[ref.field] = record["newSkin"]
            if ref.field == "skin":
                ref.node["sizeGrid"] = item["layaSizeGrid"]
        entries.append({"item": copy.deepcopy(item), "types": sorted({r["type"] for _, r in plans}) or ["GImage"]})
        item["references"] = [{**record, "applied": True} for _, record in plans]
        changes.extend(item["references"])
        source, meta = inside(root, item["source"]), inside(root, item["source"] + ".meta")
        expected[item["source"]] = item["sourceSha256"]
        expected[item["source"] + ".meta"] = sha256(meta) if meta.exists() else None
        expected.update({c["file"]: c["sha256"] for c in item.get("atlasConfigurations", [])})
        writes[item["source"]] = inside(root, item["stagedOutput"]).read_bytes()
        writes[item["source"] + ".meta"] = json_bytes(item["outputMetadata"])
    writes.update({name: json_bytes(data) for name, data in adapter.project.scenes.items() if data != snapshot[name]})
    planned = {"status": "dry_run" if dry_run else "applied", "changes": changes,
               "files": sorted(writes), "engineValidationPending": bool(entries)}
    if retired:
        planned["retiredSources"] = retired
        planned["files"] = sorted(set(planned["files"]) | {MANIFEST})
    if dry_run:
        return planned
    if not entries:
        if retired:
            from .manifest import cleanup_removed_sources
            result = cleanup_removed_sources(adapter)
            return {**planned, **result, "status": "retired_removed_sources"}
        return {**planned, "status": "already_applied_or_no_safe_references", "runId": None}
    if engine_validator is None:
        from .engine import validate_items
        proof = validate_items(adapter, entries, engine_dir, pool)
    else:
        proof = engine_validator(adapter, entries, engine_dir)
    if not proof.get("valid"):
        raise ValueError("Engine pixel comparison failed; original PNGs and scenes unchanged")
    if LayaProjectAdapter(root).project.scene_hashes != adapter.project.scene_hashes:
        raise ValueError("Scene inventory changed during in-place validation")
    pool.map(verify_image, [(str(root), entry["item"]) for entry in entries], "提交前校验")
    for entry in entries:
        item = entry["item"]
        expected[item["stagedOutput"]] = item["outputSha256"]
        expected[item["originalBackup"]] = item["sourceSha256"]
    for item in manifest["items"]:
        if item.get("overwriteSource") and item["status"] == "built" and item["source"] in pending:
            item["status"] = "applied"
    manifest["inplacePendingSources"] = []
    manifest["engineValidation"] = proof
    planned["engineValidationPending"] = False
    writes[MANIFEST] = json_bytes(manifest)
    writes[".ninegrid/apply-report.json"] = json_bytes({**planned, "engineValidation": proof})
    run_id = commit(root, writes, "apply_inplace", expected, {"references": changes})
    return {**planned, "runId": run_id, "engineValidation": proof}
