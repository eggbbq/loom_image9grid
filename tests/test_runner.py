import json
from pathlib import Path
import shutil
import subprocess

import numpy as np
from PIL import Image
import pytest

from ninegrid import run_9grid
from ninegrid.manifest import read_manifest
from .factory import hashes, make_project


@pytest.fixture
def project(tmp_path):
    root = make_project(tmp_path / "project")
    nested = root / "assets/ui/backgrounds"
    nested.mkdir(parents=True)
    for name in ("panel.png", "panel.png.meta"):
        (root / "assets" / name).rename(nested / name)
    icons = root / "assets/ui/deep/icons"
    icons.mkdir(parents=True)
    Image.fromarray(np.random.default_rng(3).integers(0, 256, (20, 20, 4), dtype=np.uint8)).save(icons / "noise.PNG")
    Image.new("RGBA", (30, 30), "red").save(root / "assets/outside-search.png")
    return root


def options(project, *extra):
    return run_9grid.parser().parse_args(["--project", str(project), "--search-dir", "assets/ui", *extra])


def test_runner_scans_all_subdirectories_and_honors_scope(project, capsys):
    before = hashes(project)
    result = run_9grid.run(options(project, "--scan-only"))
    report = result["report"]
    assert report["summary"]["scanned"] == 2
    assert {item["source"] for item in report["items"]} == {
        "assets/ui/backgrounds/panel.png", "assets/ui/deep/icons/noise.PNG"}
    assert hashes(project) == before
    assert "扫描 2 张 PNG" in capsys.readouterr().out


def test_runner_dry_run_previews_refs_without_a_prior_manifest_or_browser(project, monkeypatch):
    import ninegrid.pipeline as pipeline
    monkeypatch.setattr(pipeline, "apply", lambda *a, **k: pytest.fail("Dry run must not call apply"))
    before = hashes(project)
    result = run_9grid.run(options(project, "--dry-run"))
    assert len(result["changes"]) == 2
    assert all(change["newSizeGrid"] == "3,4,5,6,0" for change in result["changes"])
    assert hashes(project) == before
    assert not (project / ".ninegrid").exists()
    assert not (project / "assets/ninegrid_auto").exists()


def test_runner_build_only_verifies_pngs_and_keeps_all_scenes(project):
    before = {p: h for p, h in hashes(project).items() if p.endswith(".lh")}
    result = run_9grid.run(options(project, "--build-only"))
    assert result["status"] == "built" and result["apply"] is None
    assert result["verify"]["valid"]
    after = hashes(project)
    assert all(after[p] == digest for p, digest in before.items())
    assert (project / "assets/ninegrid_auto/ui/backgrounds/panel.png").is_file()
    assert (project / ".ninegrid/report.md").is_file()


def test_runner_full_workflow_reloads_metadata_and_applies_refs(project, monkeypatch):
    import ninegrid.engine as engine
    monkeypatch.setattr(engine, "validate_items", lambda *a: {"valid": True, "unitTestStub": True})
    result = run_9grid.run(options(project))
    assert result["status"] == "complete" and result["verify"]["valid"]
    assert len([r for r in result["apply"]["changes"] if r["eligible"]]) == 2
    manifest = read_manifest(project)
    uid = manifest["items"][0]["outputUuid"]
    assert json.loads((project / "assets/GImage.lh").read_text())["src"] == "res://" + uid
    assert json.loads((project / "assets/Image.lh").read_text())["sizeGrid"] == "3,4,5,6,0"
    again = run_9grid.run(options(project))
    assert again["apply"]["runId"] is None
    assert len(list((project / "assets/ninegrid_auto").rglob("*.png"))) == 1


def test_runner_paths_are_relative_to_script_and_project_not_cwd(project, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path.parent)
    args = options(Path("project"), "--scan-only")
    result = run_9grid.run(args, root=tmp_path)
    assert result["report"]["summary"]["scanned"] == 2


def test_runner_multiple_search_dirs_and_last_argument_override(project):
    args = run_9grid.parser().parse_args(["--search-dir", "assets/missing", "--project", str(project),
                                         "--search-dir", "assets/ui/backgrounds", "assets/ui/deep", "--scan-only"])
    result = run_9grid.run(args)
    assert result["report"]["summary"]["scanned"] == 2


def test_runner_rejects_directory_outside_project_before_writing(project, tmp_path):
    before = hashes(project)
    with pytest.raises(ValueError, match="escapes project"):
        run_9grid.run(options(project, "--search-dir", str(tmp_path), "--build-only"))
    assert before == hashes(project)


@pytest.mark.parametrize("search,output", [
    ("assets/ui/backgrounds", "assets/ui"),
    ("assets/ui/backgrounds/panel.png", "assets/ui"),
])
@pytest.mark.parametrize("mode", ["--scan-only", "--build-only", "--dry-run"])
def test_runner_reports_search_inside_output_instead_of_silently_scanning_zero(project, search, output, mode):
    before = hashes(project)
    with pytest.raises(ValueError, match="填为同一目录即可原地写回"):
        run_9grid.run(options(project, "--search-dir", search, "--output", output, mode))
    assert before == hashes(project)


def test_output_inside_search_is_allowed_and_generated_pngs_are_excluded(project):
    args = options(project, "--output", "assets/ui/optimized", "--build-only")
    run_9grid.run(args)
    before = hashes(project)
    report = run_9grid.run(options(project, "--output", "assets/ui/optimized", "--scan-only"))["report"]
    assert report["summary"]["scanned"] == 2
    assert all(not item["source"].startswith("assets/ui/optimized/") for item in report["items"])
    assert hashes(project) == before


def test_historical_output_cannot_be_explicitly_scanned_as_a_source(project):
    run_9grid.run(options(project, "--output", "assets/generated", "--build-only"))
    before = hashes(project)
    with pytest.raises(ValueError, match="已排除的优化输出目录"):
        run_9grid.run(options(project, "--search-dir", "assets/generated", "--scan-only"))
    assert hashes(project) == before


def test_shell_entry_bootstraps_from_another_working_directory(project, tmp_path):
    script = run_9grid.ROOT / "run_9grid.sh"
    result = subprocess.run(["bash", str(script), "--project", str(project), "--search-dir", "assets/ui",
                             "--output", "assets/ninegrid_auto", "--dry-run"],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "扫描 2 张 PNG" in result.stdout
    assert not (project / ".ninegrid").exists()


@pytest.mark.parametrize("value", ["nan", "inf", "-0.1", "1.1"])
def test_runner_rejects_invalid_saving_threshold(value):
    with pytest.raises(SystemExit):
        run_9grid.parser().parse_args(["--min-saving", value])
