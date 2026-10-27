from .manifest import read_active_manifest, reconcile_removed_sources
from .paths import inside, sha256, load_json
from .reconstruct import reconstruct_9slice, pixel_diff
from .scan import read_rgba


def verify_item(root, item):
    if item.get("overwriteSource"):
        from .inplace import verify_inplace_item
        return verify_inplace_item(root, item)
    source, output = (inside(root, item[k]) for k in ("source", "output"))
    if sha256(source) != item["sourceSha256"]:
        raise ValueError(f"Stale manifest; original changed: {item['source']}")
    for config in item.get("atlasConfigurations", []):
        if sha256(inside(root, config["file"])) != config["sha256"]:
            raise ValueError(f"Source atlas configuration changed: {config['file']}")
    from .laya.adapter import source_importer_reason, TEXTURE_DEFAULTS
    original_meta_path = inside(root, item["source"] + ".meta")
    original_meta = load_json(original_meta_path) if original_meta_path.exists() else {}
    if "sourceMetadata" in item and original_meta != item["sourceMetadata"]:
        raise ValueError(f"Stale source texture settings: {item['source']}")
    if source_importer_reason(original_meta) or original_meta.get("importer", {}).get("filterMode", 1) != item.get("sourceFilterMode", 1):
        raise ValueError(f"Stale source texture settings: {item['source']}")
    if sha256(output) != item["outputSha256"]:
        raise ValueError(f"Optimized PNG changed: {item['output']}")
    original, optimized = read_rgba(source), read_rgba(output)
    if list(original.shape[1::-1]) != item["sourceSize"] or list(optimized.shape[1::-1]) != item["outputSize"]:
        raise ValueError("Manifest/PNG dimensions disagree")
    reconstructed = reconstruct_9slice(optimized, item["sizeGrid"], item["sourceSize"])
    if item.get("mode") == "visual":
        from .visual import visual_diff, accepted
        result = visual_diff(original, reconstructed, item["qualityLimits"]["pixelErrorThreshold"])
        result.update(mode="visual", accepted=accepted(result, item["qualityLimits"]))
        if not result["accepted"]:
            raise ValueError(f"Visual reconstruction exceeds error budget: {item['source']}: {result}")
    else:
        result = pixel_diff(original, reconstructed)
        if not result["equal"]:
            raise ValueError(f"Pixel reconstruction failed: {item['source']}: {result}")
    meta = load_json(inside(root, item["output"] + ".meta"))
    expected_grid = [item["sizeGrid"][k] for k in ("top", "right", "bottom", "left", "repeat")]
    if meta.get("uuid") != item["outputUuid"] or meta.get("importer", {}).get("sizeGrid") != expected_grid:
        raise ValueError(f"Optimized texture metadata changed: {item['output']}")
    if meta.get("importer", {}).get("filterMode", 1) != item.get("sourceFilterMode", 1):
        raise ValueError(f"Optimized texture sampling changed: {item['output']}")
    settings = meta.get("importer", {})
    if set(settings) - (set(TEXTURE_DEFAULTS) | {"sizeGrid"}) or any(
            settings.get(key, default) != original_meta.get("importer", {}).get(key, default)
            for key, default in TEXTURE_DEFAULTS.items()):
        raise ValueError(f"Optimized texture importer changed: {item['output']}")
    return {"source": item["source"], **result}


def verify_project(adapter, manifest=None, workers=1, pool=None):
    if pool is None:
        from .parallel import ImagePool
        with ImagePool(workers) as pool:
            return verify_project(adapter, manifest, pool=pool)
    from .jobs import verify_image
    root = adapter.project.root
    manifest, retired = (reconcile_removed_sources(adapter, manifest) if manifest is not None
                         else read_active_manifest(adapter))
    results = pool.map(verify_image, [(str(root), i) for i in manifest["items"]], "CPU 重建校验")
    for item in manifest["items"]:
        for ref in item.get("references", []):
            if not ref.get("applied"):
                continue
            node = adapter.project.scenes[ref["file"]]
            for part in ref["pointer"]:
                node = node[part]
            if node.get("_$id") != ref["nodeId"] or node.get(ref["property"]) != ref["newSkin"]:
                raise ValueError(f"Applied reference changed: {ref['file']} {ref['nodeId']}")
            if adapter.project.resolve_reference(node[ref["property"]]) != inside(root, item["output"]):
                raise ValueError("Applied resource cannot be resolved")
            if ref["property"] == "skin" and node.get("sizeGrid") != item["layaSizeGrid"]:
                raise ValueError("Applied node sizeGrid changed")
            if [node.get("width"), node.get("height")] != item["sourceSize"]:
                raise ValueError("Applied display dimensions changed")
    return {"valid": True, "items": results, "retiredSources": retired,
            "engineValidation": "use --engine for GPU comparison"}
