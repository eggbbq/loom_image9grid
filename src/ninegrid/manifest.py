import copy

from .paths import inside, load_json, sha256, json_bytes


MANIFEST = ".ninegrid/manifest.json"


def read_manifest(root):
    data = load_json(inside(root, MANIFEST))
    if data.get("schemaVersion") != 1 or not isinstance(data.get("items"), list):
        raise ValueError("Unsupported manifest format")
    seen = set()
    for item in data["items"]:
        for key in ("source", "output"):
            path = inside(root, item[key])
            if not path.is_relative_to(inside(root, "assets")):
                raise ValueError("Manifest textures must be project assets")
            if (key, item[key]) in seen:
                raise ValueError("Duplicate manifest path")
            seen.add((key, item[key]))
        if item.get("overwriteSource"):
            if item["source"] != item["output"]:
                raise ValueError("In-place output must be the source PNG")
            for key, directory in (("originalBackup", ".ninegrid/originals"), ("stagedOutput", ".ninegrid/staged")):
                if not inside(root, item[key]).is_relative_to(inside(root, directory)):
                    raise ValueError("Invalid in-place backup/staging path")
        elif item["source"] == item["output"]:
            raise ValueError("Manifest cannot overwrite originals")
    return data


def reconcile_removed_sources(adapter, manifest):
    """Retire deleted, unreferenced resources without resurrecting their files."""
    root = adapter.project.root
    missing = [item for item in manifest["items"] if not inside(root, item["source"]).exists()]
    if not missing:
        return manifest, []
    from .laya.adapter import LayaProjectAdapter
    project = LayaProjectAdapter(root).project  # Check all scenes even for a scoped run.
    values = {}

    def collect(value, file):
        if isinstance(value, dict):
            for child in value.values():
                collect(child, file)
        elif isinstance(value, list):
            for child in value:
                collect(child, file)
        elif isinstance(value, str):
            values.setdefault(value, set()).add(file)

    for file, scene in project.scenes.items():
        collect(scene, file)
    removed = []
    for item in missing:
        paths = {inside(root, item[k]) for k in ("source", "output")}
        aliases = {item[k] for k in ("source", "output")}
        aliases.update(value[len("assets/"):] for value in list(aliases))
        uuids = {item.get("outputUuid"), item.get("sourceMetadata", {}).get("uuid")}
        aliases.update("res://" + value for value in uuids if value)
        referenced = set()
        for alias in aliases & values.keys():
            resolved = project.resolve_reference(alias)
            if resolved is None or resolved in paths:
                referenced.update(values[alias])
        if referenced:
            raise ValueError(f"历史资源已删除但仍被场景引用：{item['source']}；引用文件："
                             + "、".join(sorted(referenced)))
        removed.append(item["source"])
    result = copy.deepcopy(manifest)
    retired = result.setdefault("retiredItems", [])
    for item in result["items"]:
        if item["source"] in removed:
            retired.append({**item, "previousStatus": item["status"], "status": "retired",
                            "retiredReason": "source_removed_unreferenced"})
    result["items"] = [item for item in result["items"] if item["source"] not in removed]
    if "inplacePendingSources" in result:
        result["inplacePendingSources"] = [name for name in result["inplacePendingSources"] if name not in removed]
    return result, removed


def read_active_manifest(adapter):
    return reconcile_removed_sources(adapter, read_manifest(adapter.project.root))


def cleanup_removed_sources(adapter, dry_run=False):
    """Commit only record cleanup; leave PNGs, backups, and pending work intact."""
    from .laya.patch import commit
    root = adapter.project.root
    before = sha256(inside(root, MANIFEST))
    manifest, removed = read_active_manifest(adapter)
    run_id = None
    if removed and not dry_run:
        run_id = commit(root, {MANIFEST: json_bytes(manifest)}, "retire_removed_sources",
                        {MANIFEST: before, **adapter.project.scene_hashes}, {"retiredSources": removed})
    return {"retiredSources": removed, "runId": run_id}


def markdown_report(report):
    lines = ["# NineGrid 优化报告", "", "节省率表示纹理像素面积，非文件大小或 DrawCall。", "",
             "| 图片 | 状态 | 原始尺寸 | 优化尺寸 | 面积节省 | 原因 |",
             "|---|---|---|---|---|---|"]
    for item in report["items"]:
        source = item["source"].replace("|", "\\|")
        size = "×".join(map(str, item.get("sourceSize", [])))
        optimized = "×".join(map(str, item.get("outputSize", [])))
        lines.append(f"| {source} | {item['status']} | {size} | {optimized} | "
                     f"{item.get('pixelAreaSavedRatio', 0):.3%} | {item.get('reason') or ''} |")
    visual = [item for item in report["items"] if item.get("mode") == "visual"]
    if visual:
        lines += ["", "## 视觉近似结果", "", "这是允许像素变化的模式，不宣称逐像素无损。", "",
                  "| 图片 | 拉伸方式 | 平均误差 / 255 | 严重误差像素占比 |",
                  "|---|---|---|---|"]
        for item in visual:
            quality = item["cpuQuality"]
            lines.append(f"| {item['source'].replace('|', ' ')} | {item['sliceMode']} | "
                         f"{quality['meanError']:.4f} | {quality['badPixelRatio']:.4%} |")
    return "\n".join(lines) + "\n"
