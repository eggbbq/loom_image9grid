import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from ninegrid.candidate import choose_candidate
from ninegrid.laya.adapter import LayaProjectAdapter
from ninegrid.paths import load_json, sha256
from ninegrid.pipeline import build, scan, apply
from ninegrid.reconstruct import reconstruct_9slice
from ninegrid.scan import read_rgba
from ninegrid.verify import verify_project
from ninegrid.visual import choose_visual_candidate, visual_diff, accepted, quality_limits, _bands, premultiplied
from .factory import make_project, hashes


FIXTURES = Path(__file__).parent / "fixtures/png"


@pytest.mark.parametrize('width,height,padding', [(80,80,0), (113,67,7), (64,109,4)])
def test_disconnected_corners_stretch_internal_transparency_but_keep_outer_padding(width,height,padding):
    image = np.zeros((height+2*padding, width+2*padding, 4), dtype=np.uint8)
    for x in (padding, padding+width-9):
        for y in (padding, padding+height-9):
            image[y:y+3,x:x+9] = [255,210,15,255]
            image[y:y+9,x:x+3] = [255,210,15,255]
    candidate, diff = choose_visual_candidate(image, center_size=1)
    assert candidate is not None and diff['equal']
    assert candidate.column_band and candidate.row_band
    assert candidate.column_band[0] >= padding+9
    assert candidate.column_band[1] <= padding+width-9
    assert candidate.row_band[0] >= padding+9
    assert candidate.row_band[1] <= padding+height-9
    assert candidate.fields()['pixelAreaSavedRatio'] > .65
    assert np.array_equal(image,reconstruct_9slice(candidate.crop(image),candidate.margins,candidate.size))
    # Exercise approximate search independently of the exact-band fallback.
    for axis in (0,1):
        bands=_bands(premultiplied(image),axis,quality_limits(),1)
        assert any(not image[a:b,:,3].any() if axis==0 else not image[:,a:b,3].any()
                   for a,b,_ in bands)


def test_outer_transparency_is_not_a_stretch_band_and_blank_images_have_no_candidate():
    image = np.zeros((80,80,4),dtype=np.uint8)
    # A small unique patch is surrounded by large empty margins.
    image[35:45,35:45] = np.random.default_rng(43).integers(8,256,(10,10,4),dtype=np.uint8)
    assert choose_visual_candidate(image,center_size=1)[0] is None
    assert choose_visual_candidate(np.zeros_like(image),center_size=1)[0] is None


def test_internal_transparency_with_nonrepeating_invisible_rgb_uses_visual_search():
    image=np.random.default_rng(12).integers(0,256,(83,97,4),dtype=np.uint8)
    image[...,3]=0
    for y in (0,75):
        for x in (0,89):image[y:y+8,x:x+8]=[255,220,25,255]
    candidate,diff=choose_visual_candidate(image,center_size=1)
    assert candidate is not None and candidate.column_band and candidate.row_band
    assert diff['maxVisualChannelError']==0 and not diff['equal']
    restored=reconstruct_9slice(candidate.crop(image),candidate.margins,candidate.size)
    assert np.array_equal(premultiplied(image),premultiplied(restored))


def test_solid_content_still_supports_both_axes():
    image = np.full((47,71,4),255,dtype=np.uint8)
    candidate,diff=choose_visual_candidate(image,center_size=1)
    assert candidate is not None and diff['equal']
    assert candidate.fields()['outputSize'] == [3,3]


def test_ornamented_header_uses_visible_horizontal_band_not_empty_bottom_padding():
    image = read_rgba(FIXTURES / 'task_header_original.png')
    candidate, diff = choose_visual_candidate(image, center_size=1)
    assert candidate is not None and accepted(diff, quality_limits())
    fields = candidate.fields()
    assert fields['outputSize'][0] < image.shape[1]*.6
    assert fields['outputSize'][1] == image.shape[0]
    assert fields['sliceMode'] == 'horizontal_three_slice'
    assert fields['sizeGrid']['left'] > 100 and fields['sizeGrid']['right'] > 100
    restored = reconstruct_9slice(candidate.crop(image), candidate.margins, candidate.size)
    a, b, _ = candidate.column_band
    assert np.array_equal(restored[:, :a], image[:, :a])
    assert np.array_equal(restored[:, b:], image[:, b:])


def test_thin_progress_fill_repeats_both_axes_without_losing_highlight():
    image = read_rgba(FIXTURES / 'task_progress_fill_original.png')
    candidate, diff = choose_visual_candidate(image, center_size=1)
    assert candidate is not None and diff['equal']
    assert candidate.fields()['outputSize'][0] < 16
    assert candidate.fields()['outputSize'][1] < 16
    assert candidate.fields()['sliceMode'] == 'nine_slice'
    assert np.array_equal(image, reconstruct_9slice(candidate.crop(image), candidate.margins, candidate.size))


@pytest.mark.parametrize('name',['ui_button_original.png','ui_input_original.png'])
def test_exact_border_steps_win_when_most_area_saving_is_retained(name):
    image=read_rgba(FIXTURES/name)
    candidate,diff=choose_visual_candidate(image,center_size=1)
    assert candidate is not None and diff['equal']
    assert candidate.fields()['pixelAreaSavedRatio'] > .9


@pytest.mark.parametrize("name", ["panel_68.png", "poster_37.png"])
def test_real_user_images_are_visual_candidates_but_not_strictly_lossless(name):
    original = read_rgba(FIXTURES / name)
    assert choose_candidate(original)[0] is None
    candidate, diff = choose_visual_candidate(original)
    assert candidate is not None and diff["accepted"] and not diff["equal"]
    assert accepted(diff, quality_limits())
    restored = reconstruct_9slice(candidate.crop(original), candidate.margins, candidate.size)
    assert not np.array_equal(original, restored)
    fields = candidate.fields()
    if name == "poster_37.png":
        assert fields["sliceMode"] == "vertical_three_slice"
        assert fields["outputSize"][0] == original.shape[1]
        assert fields["pixelAreaSavedRatio"] > 0.40
        start, end = fields["rowBand"]
        assert start >= 380 and end <= 900
        assert np.array_equal(original[:start], restored[:start])
        assert np.array_equal(original[end:], restored[end:])
    else:
        assert fields["sliceMode"] == "nine_slice"
        # A small border imperfection around the middle must not prevent a
        # stretch band spanning both halves of this real panel.
        assert fields["outputSize"][0] < 100
        assert fields["pixelAreaSavedRatio"] > 0.90
        assert fields["columnBand"][0] < 50 and fields["columnBand"][1] > 525
        a, b = fields["columnBand"]
        c, d = fields["rowBand"]
        for ys in (slice(None, c), slice(d, None)):
            for xs in (slice(None, a), slice(b, None)):
                assert np.array_equal(original[ys, xs], restored[ys, xs])


def test_visual_three_slice_can_be_explicitly_selected():
    image = read_rgba(FIXTURES / "panel_68.png")
    candidate, diff = choose_visual_candidate(image, axis="vertical")
    assert diff["accepted"] and candidate.column_band is None
    assert candidate.fields()["outputSize"][0] == image.shape[1]


def test_gradients_do_not_chain_near_equal_neighbor_vectors():
    x, y = np.meshgrid(np.linspace(0, 255, 300), np.linspace(0, 255, 200))
    image = np.stack((x, y, np.ones_like(x)*75, np.ones_like(x)*255), axis=2).astype(np.uint8)
    candidate, diff = choose_visual_candidate(image)
    assert candidate is None and not diff["accepted"]


def test_alpha_silhouette_protects_small_edge_ornaments():
    image = np.zeros((400, 400, 4), dtype=np.uint8)
    image[:, 40:360] = [75, 43, 43, 255]
    image[:120, :2] = [255, 180, 100, 255]
    candidate, _ = choose_visual_candidate(image, axis="vertical")
    assert candidate is not None
    assert candidate.row_band[0] >= 120
    restored = reconstruct_9slice(candidate.crop(image), candidate.margins, candidate.size)
    assert np.array_equal(image[:120], restored[:120])


def test_visual_budget_accounts_for_alpha_but_not_invisible_rgb():
    original = np.zeros((4, 4, 4), dtype=np.uint8)
    invisible = original.copy()
    invisible[..., :3] = 255
    diff = visual_diff(original, invisible)
    assert not diff["equal"] and diff["meanError"] == 0 and diff["maxRawChannelError"] == 255
    opaque = invisible.copy()
    opaque[..., 3] = 255
    assert not accepted(visual_diff(original, opaque), quality_limits())


def test_visual_pipeline_writes_explicit_quality_policy_and_verifies_it(tmp_path):
    project = make_project(tmp_path / "project", read_rgba(FIXTURES / "panel_68.png"))
    build(LayaProjectAdapter(project), mode="visual")
    manifest = load_json(project / ".ninegrid/manifest.json")
    item = manifest["items"][0]
    assert item["mode"] == "visual" and item["qualityLimits"]["maxMeanError"] == 1
    assert not item["cpuQuality"]["equal"]
    result = apply(LayaProjectAdapter(project), engine_validator=lambda *a: {"valid": True, "unitTestStub": True})
    assert result["runId"]
    verified = verify_project(LayaProjectAdapter(project))
    assert verified["valid"] and verified["items"][0]["accepted"]
    assert not verified["items"][0]["equal"]


def test_visual_verify_refuses_corrupted_pixels_even_if_file_hash_is_updated(tmp_path):
    project = make_project(tmp_path / "project", read_rgba(FIXTURES / "panel_68.png"))
    build(LayaProjectAdapter(project), mode="visual")
    manifest_path = project / ".ninegrid/manifest.json"
    manifest = load_json(manifest_path)
    item = manifest["items"][0]
    output = project / item["output"]
    Image.new("RGBA", tuple(item["outputSize"]), "white").save(output)
    item["outputSha256"] = sha256(output)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Visual reconstruction exceeds error budget"):
        verify_project(LayaProjectAdapter(project))


def test_applied_older_algorithm_is_not_silently_reused_after_upgrade(tmp_path):
    project = make_project(tmp_path / "project", read_rgba(FIXTURES / "panel_68.png"))
    build(LayaProjectAdapter(project), mode="visual")
    apply(LayaProjectAdapter(project), engine_validator=lambda *a: {"valid": True, "unitTestStub": True})
    path = project / ".ninegrid/manifest.json"
    manifest = load_json(path)
    manifest["settings"].pop("algorithmVersion")
    path.write_text(json.dumps(manifest))
    before = hashes(project)
    with pytest.raises(ValueError, match="Rollback applied changes"):
        build(LayaProjectAdapter(project), mode="visual")
    assert hashes(project) == before


def test_atlas_configuration_warns_without_blocking_real_visual_candidate(tmp_path):
    project = make_project(tmp_path / "project", read_rgba(FIXTURES / "poster_37.png"))
    path = project / "assets/AutoAtlas.atlascfg"
    path.write_text('{"includeSubFolders":true,"scale":1,"filterMode":1}')
    before = hashes(project)
    report = scan(LayaProjectAdapter(project), mode="visual")
    assert report["items"][0]["status"] == "candidate"
    assert report["items"][0]["warnings"]
    assert hashes(project) == before


def test_unsupported_atlas_resize_remains_blocked(tmp_path):
    project = make_project(tmp_path / "project", read_rgba(FIXTURES / "panel_68.png"))
    (project / "assets/AutoAtlas.atlascfg").write_text('{"scale":0.5}')
    report = scan(LayaProjectAdapter(project), mode="visual")
    assert report["items"][0]["reason"] == "unsupported_atlas_transform"


def test_atlas_filter_override_requires_both_runtime_sampling_modes(tmp_path):
    project = make_project(tmp_path / 'project', read_rgba(FIXTURES / 'panel_68.png'))
    (project / 'assets/AutoAtlas.atlascfg').write_text('{"includeSubFolders":true,"filterMode":0}')
    item = scan(LayaProjectAdapter(project), mode='visual')['items'][0]
    assert item['status'] == 'candidate'
    assert item['validationFilterModes'] == [0, 1]


@pytest.mark.parametrize("kwargs", [{"max_mean_error": float('nan')}, {"max_bad_pixel_ratio": 1.1},
                                    {"pixel_error_threshold": 256}, {"edge_tolerance": -1}])
def test_invalid_visual_budgets_are_rejected(kwargs):
    with pytest.raises(ValueError):
        quality_limits(**kwargs)
