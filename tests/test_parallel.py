import hashlib
import json
import os
from pathlib import Path
import time
import uuid

import numpy as np
from PIL import Image
import pytest

from ninegrid import run_9grid
from ninegrid.jobs import encode_image, candidate_from_fields
from ninegrid.laya.adapter import LayaProjectAdapter
from ninegrid.parallel import ImagePool, worker_count
from ninegrid.pipeline import scan, build, apply
from ninegrid.verify import verify_project
from ninegrid.visual import VisualCandidate
from .factory import make_project, hashes


def pid_job(value):
    time.sleep(0.05)
    return value, os.getpid()


def project_pair(tmp_path):
    projects = []
    for directory in ("serial", "parallel"):
        project = make_project(tmp_path / directory)
        for name in ("a", "b", "c"):
            source = project / "assets/panel.png"
            (source.parent / (name + ".png")).write_bytes(source.read_bytes())
            uid = str(uuid.uuid5(uuid.NAMESPACE_URL, "parallel-test:" + name))
            (source.parent / (name + ".png.meta")).write_text(json.dumps({"uuid": uid, "importer": {"textureType": 2}}))
            node = json.loads((source.parent / "GImage.lh").read_text())
            node["_$id"], node["src"] = name, "res://" + uid
            (source.parent / (name + ".lh")).write_text(json.dumps(node))
        projects.append(project)
    return projects


def test_real_spawn_workers_execute_jobs_in_multiple_processes_and_keep_input_order():
    with ImagePool(2) as pool:
        result = pool.map(pid_job, range(12))
        assert [v for v, _ in result] == list(range(12))
        assert len({pid for _, pid in result}) == 2
        assert os.getpid() not in {pid for _, pid in result}
        executor = pool.executor
        pool.map(pid_job, range(2))
        assert pool.executor is executor


@pytest.mark.parametrize("mode", ["strict", "visual"])
@pytest.mark.parametrize("inplace", [False, True])
def test_parallel_build_and_apply_match_serial_pngs_metadata_manifest_and_scene_results(tmp_path, mode, inplace):
    serial, parallel = project_pair(tmp_path)
    output = "assets" if inplace else "assets/generated"
    results = []
    for project, workers in ((serial, 1), (parallel, 2)):
        with ImagePool(workers) as pool:
            result = build(LayaProjectAdapter(project), inputs=["assets"], output=output, mode=mode, pool=pool)
            results.append(result["report"])
            apply(LayaProjectAdapter(project), engine_validator=lambda *a: {"valid": True, "unitTestStub": True}, pool=pool)
            assert verify_project(LayaProjectAdapter(project), pool=pool)["valid"]
    assert results[0] == results[1]
    def assets(project):
        return {p.relative_to(project).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (project / "assets").rglob("*") if p.is_file()}
    assert assets(serial) == assets(parallel)
    assert (serial / ".ninegrid/manifest.json").read_bytes() == (parallel / ".ninegrid/manifest.json").read_bytes()


def test_worker_error_leaves_all_original_files_untouched(tmp_path):
    project = make_project(tmp_path / "project")
    item = scan(LayaProjectAdapter(project))["items"][0]
    item["sourceSha256"] = "0" * 64
    before = hashes(project)
    with ImagePool(2) as pool, pytest.raises(ValueError, match="Source changed"):
        pool.map(encode_image, [(str(project), item, False)] * 2)
    assert hashes(project) == before


@pytest.mark.parametrize("center", [1, 7, 8, 16, 64])
def test_saved_visual_candidate_reconstructs_exact_same_crop_without_another_search(center):
    rgba = np.random.default_rng(7).integers(0, 256, (180, 300, 4), dtype=np.uint8)
    candidate = VisualCandidate((300, 180), (30, 260, 35), (20, 160, 155), center)
    stored = candidate_from_fields(json.loads(json.dumps(candidate.fields())))
    assert stored.fields() == candidate.fields()
    assert np.array_equal(stored.crop(rgba), candidate.crop(rgba))


def test_encoding_uses_scanned_candidate_without_running_visual_search_again(tmp_path, monkeypatch):
    import ninegrid.visual
    fixture = Path(__file__).parent / "fixtures/png/panel_68.png"
    project = make_project(tmp_path / "project", np.asarray(Image.open(fixture).convert("RGBA")))
    item = scan(LayaProjectAdapter(project), mode="visual")["items"][0]
    monkeypatch.setattr(ninegrid.visual, "choose_visual_candidate", lambda *a: pytest.fail("Repeated search"))
    content, _ = encode_image((str(project), item, False))
    assert content.startswith(b"\x89PNG")


@pytest.mark.parametrize("value", [-1, True, "4", None])
def test_invalid_worker_counts_fail(value):
    with pytest.raises(ValueError):
        worker_count(value)


def test_worker_cli_default_auto_and_serial_override():
    assert run_9grid.parser().parse_args([]).workers == 0
    assert run_9grid.parser().parse_args(["--workers", "1"]).workers == 1


@pytest.mark.skipif(os.environ.get("NINEGRID_ENGINE_TESTS") != "1", reason="Requires actual Laya/Chrome")
def test_real_gpu_render_comparison_also_works_with_reused_process_pool(tmp_path):
    project, _ = project_pair(tmp_path)
    for path in (project / "assets").glob("*.png.meta"):
        meta = json.loads(path.read_text())
        meta["importer"]["filterMode"] = 0
        path.write_text(json.dumps(meta))
    with ImagePool(2) as pool:
        build(LayaProjectAdapter(project), pool=pool)
        result = apply(LayaProjectAdapter(project), pool=pool)
        assert result["engineValidation"]["valid"]
        assert all(case["differentPixels"] == 0 for case in result["engineValidation"]["cases"])
        assert verify_project(LayaProjectAdapter(project), pool=pool)["valid"]
