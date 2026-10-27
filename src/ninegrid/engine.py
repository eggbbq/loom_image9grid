"""Real Laya/Chrome screenshots, kept separate from CPU reconstruction."""
import json
import os
from pathlib import Path
import shutil
import subprocess

from .paths import inside, load_json, relative, atomic_write, json_bytes
from .parallel import ImagePool
from .jobs import compare_render


def engine_libraries(root, explicit=None):
    choices = [explicit, os.environ.get("NINEGRID_ENGINE_DIR"), root / "engine/libs",
               Path("/Applications/LayaAirIDE.app/Contents/Resources/engine/libs")]
    for value in choices:
        if value and all((Path(value) / name).is_file() for name in
                         ("laya.core.js", "laya.webgl_2D.js", "laya.ui.js", "laya.ui2.js")):
            return Path(value).resolve()
        if explicit:
            break
    raise ValueError("Laya 3.4.1 / 3.4.2 libraries unavailable; pass --engine-dir with core/webgl_2D/ui/ui2 JS libraries")


def validate_items(adapter, entries, engine_dir=None, pool=None, workers=1):
    if pool is None:
        with ImagePool(workers) as pool:
            return validate_items(adapter, entries, engine_dir, pool)
    root = adapter.project.root
    if not entries:
        return {"valid": True, "cases": [], "reason": "no_changed_references"}
    node = shutil.which("node")
    if node is None:
        raise ValueError("Engine verification requires Node.js and Playwright/Chrome")
    libraries = engine_libraries(root, engine_dir)
    from .laya.runtime import validate_engine_version
    validate_engine_version(adapter.project.version,libraries)
    directory = inside(root, ".ninegrid/validation")
    directory.mkdir(parents=True, exist_ok=True)
    cases = []
    for entry in entries:
        item = entry["item"]
        source_name = item.get("originalBackup", item["source"])
        output_name = item.get("stagedOutput", item["output"]) if item.get("status") != "applied" else item["output"]
        meta = load_json(inside(root, item["source"] + ".meta")) if inside(root, item["source"] + ".meta").exists() else {}
        filter_mode = meta.get("importer", {}).get("filterMode", 1)
        for sampling_mode in item.get("validationFilterModes", [filter_mode]):
            for kind in entry["types"]:
                cases.append({"source": str(inside(root, source_name)),
                              "output": str(inside(root, output_name)), "type": kind,
                              "sourceSize": item["sourceSize"], "sizeGrid": item["layaSizeGrid"],
                              "filterMode": sampling_mode, "mode": item.get("mode", "strict"),
                              "qualityLimits": item.get("qualityLimits")})
    payload = {"project": str(root), "libraries": str(libraries), "artifacts": str(directory), "cases": cases,"version":adapter.project.version}
    script = Path(__file__).with_name("engine_probe.mjs")
    try:
        result = subprocess.run([node, str(script)], input=json.dumps(payload), capture_output=True,
                                text=True, timeout=180)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Laya engine validation timed out; scenes were not modified") from exc
    if result.returncode:
        raise ValueError(f"Laya engine validation unavailable/failed: {result.stderr[-3000:]}")
    records = json.loads(result.stdout)
    stats = []
    comparisons = pool.map(compare_render, [(str(root), record) for record in records], "渲染截图比较")
    for stat, diff_png in comparisons:
        if diff_png is not None:
            atomic_write(inside(root, stat["after"] + ".diff.png"), diff_png)
        stats.append(stat)
    proof = {"valid": all(s["accepted"] for s in stats) and len(stats) == len(cases),
             "engine": "LayaAir "+adapter.project.version, "renderer": "Chrome WebGL",
             "tolerance": "explicit_per_candidate_visual_budget" if any(c["mode"] == "visual" for c in cases) else 0,
             "cases": stats}
    atomic_write(directory / "report.json", json_bytes(proof))
    return proof
