from ninegrid.pipeline import build
from ninegrid.laya.adapter import LayaProjectAdapter
from ninegrid.paths import load_json, sha256
from ninegrid.retry import inset_failed_candidates
from ninegrid.verify import verify_project
from .factory import make_project


def test_engine_retry_changes_only_staged_crop_until_verified_apply(tmp_path):
    root=make_project(tmp_path/'project')
    build(LayaProjectAdapter(root),inputs=['assets'],output='assets',mode='visual',center_size=1)
    before=load_json(root/'.ninegrid/manifest.json')['items'][0]
    source_hash=sha256(root/before['source'])
    proof={'valid':False,'cases':[{'source':before['originalBackup'],'accepted':False}]}
    assert inset_failed_candidates(LayaProjectAdapter(root),proof)==[before['source']]
    after=load_json(root/'.ninegrid/manifest.json')['items'][0]
    assert sha256(root/before['source'])==source_hash
    assert after['outputSize'][0]>before['outputSize'][0]
    assert after['engineFallbackInsets']==1
    assert after['qualityLimits']==before['qualityLimits']
    assert verify_project(LayaProjectAdapter(root))['valid']
