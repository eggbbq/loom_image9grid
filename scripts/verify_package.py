"""Install a built wheel in a clean venv and verify the standalone distribution."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--engine-dir", type=Path)
    parser.add_argument("--playwright-module", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    wheel = args.wheel.resolve(strict=True)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    if args.playwright_module:
        env["NINEGRID_PLAYWRIGHT_MODULE"] = str(args.playwright_module.resolve(strict=True))
    with tempfile.TemporaryDirectory(prefix="loom-image9grid-install-") as temporary:
        base = Path(temporary)
        work = base / "workspace"
        work.mkdir()
        venv = base / "venv"

        def run(command):
            result = subprocess.run(list(map(str, command)), cwd=work, env=env,
                                    capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(f"Command failed: {command}\n{result.stdout}\n{result.stderr}")
            return result

        run([sys.executable, "-m", "venv", venv])
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run([python, "-m", "pip", "install", wheel])
        run([python, "-m", "pip", "check"])

        def cli(name, *arguments):
            executable = python.parent / (name + (".exe" if os.name == "nt" else ""))
            return run([executable, *arguments])

        for name in ("ninegrid", "ninegrid-run", "ninegrid-auto"):
            cli(name, "--help")
        run([python, "-m", "ninegrid", "--help"])
        run([python, "-c", FIXTURE])
        cli("ninegrid", "scan", "--project", "project", "--workers", "2")
        cli("ninegrid-run", "--project", "project", "--search-dir", "assets", "--dry-run", "--workers", "2")
        assert not (work / "project/.ninegrid").exists()
        cli("ninegrid-run", "--project", "project", "--search-dir", "assets", "--build-only", "--workers", "2")
        cli("ninegrid", "verify", "--project", "project", "--workers", "2")
        manifest = json.loads((work / "project/.ninegrid/manifest.json").read_text())
        assert len(manifest["items"]) == 2
        if args.engine_dir:
            cli("ninegrid", "apply", "--project", "project", "--workers", "2",
                "--engine-dir", args.engine_dir.resolve(strict=True))
            proof = json.loads((work / "project/.ninegrid/validation/report.json").read_text())
            assert proof["valid"]
            manifest = json.loads((work / "project/.ninegrid/manifest.json").read_text())
            assert all(item["status"] == "applied" for item in manifest["items"])
        assert not (work / ".venv").exists()
        report = dict(valid=True, wheel=wheel.name, clean_venv=True,
                      public_image_api=True, public_project_api=True, commands=3,
                      cpu_multiprocess_build_verify=True, gpu_verified=bool(args.engine_dir),
                      source_checkout_required=False, psd_dependencies=False)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    return 0


FIXTURE = r'''
import importlib.metadata as metadata
import importlib.util
import json
from pathlib import Path
import sys
import numpy as np
from PIL import Image
import ninegrid
from ninegrid.api import (
    LayaProjectAdapter, choose_candidate, choose_visual_candidate,
    read_rgba, png_bytes, reconstruct_9slice, pixel_diff, quality_limits,
    scan, build, apply, verify_project, rollback,
    optimize_project, pixel_candidates, decode_candidate, reconstruct_candidate,
)
assert Path(ninegrid.__file__).is_relative_to(sys.prefix)
assert Path(ninegrid.__file__).with_name("engine_probe.mjs").is_file()
assert Path(ninegrid.__file__).parent.joinpath("laya/automatic_probe.mjs").is_file()
assert Path(ninegrid.__file__).parent.joinpath("laya/resize_probe.mjs").is_file()
import importlib, pkgutil
for item in pkgutil.walk_packages(ninegrid.__path__, "ninegrid."):
    importlib.import_module(item.name)
assert metadata.version("loom-image9grid") == ninegrid.__version__
assert importlib.util.find_spec("psd2ui") is None
assert importlib.util.find_spec("psd_tools") is None
rng = np.random.default_rng(17)
compact = rng.integers(0, 256, (9, 11, 4), dtype=np.uint8)
rgba = reconstruct_9slice(compact, dict(top=3, right=4, bottom=5, left=6), (60, 40))
candidate, reason = choose_candidate(rgba)
assert candidate is not None, reason
assert pixel_diff(rgba, reconstruct_9slice(candidate.crop(rgba), candidate.margins, candidate.size))["equal"]
assert png_bytes(candidate.crop(rgba)).startswith(b"\x89PNG")
visual, quality = choose_visual_candidate(rgba, center_size=1, limits=quality_limits())
assert visual is not None and quality["accepted"]
options, traits = pixel_candidates(np.full((20, 30, 4), 255, dtype=np.uint8))
assert options
option = options[0]
small = decode_candidate(option).crop(np.full((20, 30, 4), 255, dtype=np.uint8))
assert reconstruct_candidate(small, option["sizeGrid"], (30, 20)).shape == (20, 30, 4)
project = Path("project")
assets = project / "assets"
assets.mkdir(parents=True)
(project / "Demo.laya").write_text(json.dumps({"version": __import__("os").environ.get("NINEGRID_TEST_PROJECT_VERSION", "3.4.1")}))
for index in range(2):
    name = f"panel{index}"
    uid = f"aaaaaaaa-aaaa-4aaa-8aaa-{index:012d}"
    Image.fromarray(rgba).save(assets / (name + ".png"))
    (assets / (name + ".png.meta")).write_text(json.dumps({"uuid":uid,"importer":{"textureType":2,"filterMode":0}}))
    (assets / (name + ".lh")).write_text(json.dumps({"_$ver":1,"_$id":name,"_$type":"GImage","width":60,"height":40,"src":"res://"+uid,"autoSize":False}))
assert np.array_equal(read_rgba(assets / "panel0.png"), rgba)
assert scan(LayaProjectAdapter(project))["summary"]["candidates"] == 2
'''


if __name__ == "__main__":
    raise SystemExit(main())
