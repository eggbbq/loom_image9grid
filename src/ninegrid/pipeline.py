import copy
import hashlib

from .manifest import MANIFEST, read_active_manifest, markdown_report
from .paths import inside, relative, sha256, load_json, json_bytes
from .verify import verify_item, verify_project
from .laya.adapter import LayaProjectAdapter, source_importer_reason
from .laya.references import unsafe_reason
from .laya.patch import commit
from .visual import quality_limits
from .parallel import ImagePool
from .jobs import analyze_image, encode_image, verify_image
from .names import DEFAULT_IGNORE_NAMES, normalize_ignored_names, matches_ignored_name


def scan(adapter, inputs=None, output="assets/ninegrid_auto", min_saving=0.20, *,
         mode="strict", quality=None, center_size=8, axis="auto", workers=1, pool=None, ignore_names=DEFAULT_IGNORE_NAMES):
    ignore_names = normalize_ignored_names(ignore_names)
    if pool is None:
        with ImagePool(workers) as pool:
            return scan(adapter, inputs, output, min_saving, mode=mode, quality=quality,
                        center_size=center_size, axis=axis, pool=pool, ignore_names=ignore_names)
    root = adapter.project.root
    output_path = inside(root, output)
    input_names = list(inputs or ["assets"])
    overwrite = any(inside(root, name) == output_path for name in input_names)
    if overwrite and any(not inside(root, name).is_relative_to(output_path) for name in input_names):
        raise ValueError("原地写回时，所有搜索路径须位于相同输出目录内；其他目录请分开运行。")
    if not output_path.is_relative_to(adapter.project.assets) or (output_path == adapter.project.assets and not overwrite):
        raise ValueError("Output must be a subdirectory of project assets")
    if not 0 <= min_saving <= 1:
        raise ValueError("min-saving must be between 0 and 1")
    if mode not in ("strict", "visual"):
        raise ValueError("mode must be strict or visual")
    if type(center_size) is not int or not 1 <= center_size <= 64:
        raise ValueError("center-size must be in [1,64]")
    quality = quality or quality_limits()
    excluded = [] if overwrite else [output_path, inside(root, "assets/ninegrid_auto")]
    old = inside(root, MANIFEST)
    retired = []
    if old.exists():
        old_manifest, retired = read_active_manifest(adapter)
        if not overwrite and not old_manifest["settings"].get("overwriteSource"):
            excluded.append(inside(root, old_manifest["settings"]["output"]))
        if overwrite:
            excluded.extend(inside(root, item["output"]) for item in old_manifest["items"]
                            if not item.get("overwriteSource"))
    files = set()
    for name in input_names:
        path = inside(root, name)
        if not path.exists() or not path.is_relative_to(adapter.project.assets):
            raise ValueError(f"Input must exist inside project assets: {name}")
        if not overwrite and path.is_relative_to(output_path):
            raise ValueError(
                f"搜索路径 {relative(root, path)} 与输出目录 {relative(root, output_path)} 相同，"
                "或位于输出目录内；输出目录会被排除，因此无法扫描。"
                "请将 --output 改为独立目录，例如 assets/ninegrid_auto。"
                "将 --search-dir 与 --output 填为同一目录即可原地写回。")
        if any(path.is_relative_to(directory) for directory in excluded):
            raise ValueError(
                f"搜索路径 {relative(root, path)} 位于已排除的优化输出目录内；"
                "请将 --search-dir 指向原始 PNG 目录，避免重复优化生成图。")
        candidates = [path] if path.is_file() else path.rglob("*")
        for source in candidates:
            if source.suffix.lower() != ".png" or any(source.is_relative_to(p) for p in excluded):
                continue
            files.add(inside(root, source))
    jobs = []
    options = dict(mode=mode, minSaving=min_saving, quality=quality, centerSize=center_size, axis=axis)
    for source in sorted(files):
        name = relative(root, source)
        item = {"source": name, "sourceSha256": sha256(source), "status": "skipped", "reason": None}
        refs = adapter.for_source(source)
        meta_path = inside(root, name + ".meta")
        meta = load_json(meta_path) if meta_path.exists() else {}
        item["sourceMetadata"] = meta
        item["sourceFilterMode"] = meta.get("importer", {}).get("filterMode", 1)
        filter_modes = {item["sourceFilterMode"]}
        reason = ('excluded_icon_name' if matches_ignored_name(name, ignore_names)
                  else source_importer_reason(meta))
        atlas = []
        parent = source.parent
        while parent.is_relative_to(adapter.project.assets):
            for config in sorted(parent.glob("*.atlascfg")):
                config = inside(root, config)
                settings = load_json(config)
                if parent != source.parent and not settings.get("includeSubFolders", False):
                    continue
                atlas.append({"file": relative(root, config), "sha256": sha256(config)})
                if (settings.get("scale", 1) != 1 or settings.get("usePng8") or
                        settings.get("platformDefault", {}).get("format", 0) != 0 or settings.get("allowRotation")):
                    reason = reason or "unsupported_atlas_transform"
                filter_mode = settings.get("filterMode", item["sourceFilterMode"])
                if type(filter_mode) is not int or filter_mode not in (0, 1):
                    reason = reason or "unsupported_atlas_filter"
                else:
                    filter_modes.add(filter_mode)
            parent = parent.parent
        if atlas:
            item["atlasConfigurations"] = atlas
            item["warnings"] = ["source_auto_atlas_present_originals_preserved"]
        item["validationFilterModes"] = sorted(filter_modes)
        item["reason"] = reason
        jobs.append((str(root), item, options))
    items = pool.map(analyze_image, jobs, "图片分析")
    for item in items:
        if item["status"] == "candidate":
            source = inside(root, item["source"])
            item["output"] = item["source"] if overwrite else relative(root, output_path / source.relative_to(adapter.project.assets))
        refs = adapter.for_source(inside(root, item["source"]))
        item["references"] = [ref.report(unsafe_reason(ref, item.get("sourceSize", []))) for ref in refs]
        item["safeReferences"] = sum(ref["eligible"] for ref in item["references"])
    settings = {"output": relative(root, output_path), "inputs": list(map(str, inputs or ["assets"])), "minSaving": min_saving,
                "excludedNameContains": list(ignore_names)}
    if mode == "visual":
        settings.update(mode=mode, algorithmVersion=6, qualityLimits=quality, centerSize=center_size, axis=axis)
    report = {"schemaVersion": 1, "projectVersion": adapter.project.version,
            "settings": settings,
            "items": items,
            "summary": {"scanned": len(items), "candidates": sum(i["status"] == "candidate" for i in items),
                        "safeReferences": sum(i["safeReferences"] for i in items)}}
    if retired:
        report["retiredSources"] = retired
    if overwrite:
        from .inplace import prepare_report
        report["settings"]["overwriteSource"] = True
        prepare_report(adapter, report, old_manifest if old.exists() else {"items": []}, pool=pool)
    return report


def build(adapter, inputs=None, output="assets/ninegrid_auto", min_saving=0.20, dry_run=False, *,
          mode="strict", quality=None, center_size=8, axis="auto", workers=1, pool=None, ignore_names=DEFAULT_IGNORE_NAMES):
    if pool is None:
        with ImagePool(workers) as pool:
            return build(adapter, inputs, output, min_saving, dry_run, mode=mode, quality=quality,
                         center_size=center_size, axis=axis, pool=pool, ignore_names=ignore_names)
    root = adapter.project.root
    report = scan(adapter, inputs, output, min_saving, mode=mode, quality=quality, center_size=center_size, axis=axis, pool=pool, ignore_names=ignore_names)
    if report["settings"].get("overwriteSource"):
        from .inplace import build_inplace
        return build_inplace(adapter, report, dry_run, pool=pool)
    old = read_active_manifest(adapter)[0] if inside(root, MANIFEST).exists() else {"items": []}
    previous = {item["source"]: item for item in old["items"]}
    manifest = {**copy.deepcopy(old), "schemaVersion": 1, "projectVersion": adapter.project.version,
                "settings": report["settings"], "items": []}
    writes, expected = {}, {}
    to_encode = [item for item in report["items"] if item["status"] == "candidate"
                 and not any(r.get("applied") for r in previous.get(item["source"], {}).get("references", []))]
    encoded = dict(zip((item["source"] for item in to_encode),
                       pool.map(encode_image, [(str(root), i, False) for i in to_encode], "生成 PNG")))
    for item in report["items"]:
        prior = previous.get(item["source"])
        if prior and any(r.get("applied") for r in prior.get("references", [])):
            verify_item(root, prior)
            if prior["sourceSha256"] != item["sourceSha256"] or old.get("settings") != report["settings"]:
                raise ValueError("Rollback applied changes before rebuilding with changed sources/settings")
            manifest["items"].append(prior)
            continue
        if item["status"] != "candidate":
            continue
        source = inside(root, item["source"])
        content, _ = encoded[item["source"]]
        meta = adapter.create_meta(item)
        item = copy.deepcopy(item)
        item.update(status="built", outputSha256=hashlib.sha256(content).hexdigest(), outputUuid=meta["uuid"])
        manifest["items"].append(item)
        for name, data in ((item["output"], content), (item["output"] + ".meta", json_bytes(meta))):
            path = inside(root, name)
            expected[name] = sha256(path) if path.exists() else None
            if path.exists() and not prior:
                raise ValueError(f"Refusing to overwrite unmanaged output: {name}")
            if name.endswith(".meta") and path.exists():
                existing = load_json(path)
                # Preserve IDE importer defaults added after the initial build.
                if existing.get("uuid") != meta["uuid"]:
                    raise ValueError(f"Generated UUID changed: {name}")
                existing.setdefault("importer", {})["sizeGrid"] = meta["importer"]["sizeGrid"]
                existing["importer"]["filterMode"] = meta["importer"]["filterMode"]
                data = json_bytes(existing)
            writes[name] = data
        expected[item["source"]] = item["sourceSha256"]
    # Never silently forget an applied image excluded from this scan.
    for name, prior in previous.items():
        if name not in {i["source"] for i in report["items"]} and any(r.get("applied") for r in prior["references"]):
            raise ValueError("Rollback applied changes before changing input scope")
    writes[MANIFEST] = json_bytes(manifest)
    writes[".ninegrid/report.json"] = json_bytes(report)
    writes[".ninegrid/report.md"] = markdown_report(report).encode("utf-8")
    if dry_run:
        return {"status": "dry_run", "files": sorted(writes), "report": report}
    run_id = commit(root, writes, "build", expected)
    return {"status": "built", "runId": run_id, "manifest": MANIFEST, "report": report}


def apply(adapter, dry_run=False, engine_dir=None, engine_validator=None, workers=1, pool=None):
    if pool is None:
        with ImagePool(workers) as pool:
            return apply(adapter, dry_run, engine_dir, engine_validator, pool=pool)
    root = adapter.project.root
    manifest, retired = read_active_manifest(adapter)
    if manifest["settings"].get("overwriteSource"):
        from .inplace import apply_inplace
        return apply_inplace(adapter, manifest, dry_run, engine_dir, engine_validator, retired, pool=pool)
    verify_project(adapter, manifest, pool=pool)
    scene_before = adapter.project.scene_hashes
    expected_state = {MANIFEST: sha256(inside(root, MANIFEST))}
    for item in manifest["items"]:
        for name in (item["source"] + ".meta", item["output"] + ".meta"):
            path = inside(root, name)
            expected_state[name] = sha256(path) if path.exists() else None
        for config in item.get("atlasConfigurations", []):
            expected_state[config["file"]] = config["sha256"]
    original_scenes = copy.deepcopy(adapter.project.scenes)
    changes = []
    engine_items = []
    for item in manifest["items"]:
        records = adapter.patch(item)
        applied = [record for record in records if record["eligible"]]
        if applied:
            engine_items.append({"item": item, "types": sorted({r["type"] for r in applied})})
        changes.extend(records)
        # Previously applied records remain authoritative for verify/rollback.
        retained = [r for r in item["references"] if r.get("applied")]
        item["references"] = retained + [{**record, "applied": record["eligible"]} for record in records]
        if any(r.get("applied") for r in item["references"]):
            item["status"] = "applied"
    writes = {name: json_bytes(data) for name, data in adapter.project.scenes.items() if data != original_scenes[name]}
    planned = {"status": "dry_run" if dry_run else "applied", "changes": changes,
               "files": sorted(writes), "engineValidationPending": bool(engine_items)}
    if retired:
        planned["retiredSources"] = retired
        planned["files"] = sorted(set(planned["files"]) | {MANIFEST})
    if dry_run:
        return planned
    if not writes:
        if retired and not dry_run:
            from .manifest import cleanup_removed_sources
            result = cleanup_removed_sources(adapter)
            return {**planned, **result, "status": "retired_removed_sources"}
        return {**planned, "status": "already_applied_or_no_safe_references", "runId": None}
    # Validate all changed images on the actual Laya runtime before scene commit.
    if engine_validator is None:
        from .engine import validate_items
        proof = validate_items(adapter, engine_items, engine_dir, pool)
    else:
        proof = engine_validator(adapter, engine_items, engine_dir)
    if not proof.get("valid"):
        raise ValueError(f"Engine pixel comparison failed; project scenes unchanged: {proof}")
    manifest["engineValidation"] = proof
    writes[MANIFEST] = json_bytes(manifest)
    writes[".ninegrid/apply-report.json"] = json_bytes({**planned, "engineValidationPending": False,
                                                       "engineValidation": proof})
    expected = {**expected_state, **{name: scene_before[name] for name in writes if name in scene_before}}
    # Recheck source/output pixels after the asynchronous GPU probe.
    pool.map(verify_image, [(str(root), i) for i in manifest["items"]], "提交前校验")
    for item in manifest["items"]:
        expected[item["source"]] = item["sourceSha256"]
        expected[item["output"]] = item["outputSha256"]
    # Every scene patch must still parse before starting the transaction.
    import json
    for name, content in writes.items():
        if name.endswith((".ls", ".lh")):
            json.loads(content)
    run_id = commit(root, writes, "apply", expected, {"references": changes})
    return {**planned, "engineValidationPending": False, "engineValidation": proof, "runId": run_id}
