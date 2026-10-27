import pytest

from ninegrid.laya.adapter import LayaProjectAdapter
from ninegrid.pipeline import scan, build
from ninegrid.names import normalize_ignored_names, matches_ignored_name
from .factory import make_project,hashes


@pytest.mark.parametrize('name',['icon_reward.png','ICO_Menu.png','bg_Icon_panel.png','图标_金币.png'])
def test_icon_names_are_not_analyzed_or_cropped(tmp_path,name):
    root=make_project(tmp_path/'project')
    (root/'assets/panel.png').rename(root/'assets'/name)
    (root/'assets/panel.png.meta').rename(root/'assets'/(name+'.meta'))
    before=hashes(root)
    report=scan(LayaProjectAdapter(root),mode='visual',center_size=1)
    assert report['items'][0]['status']=='skipped'
    assert report['items'][0]['reason']=='excluded_icon_name'
    assert hashes(root)==before


def test_icon_group_in_export_path_is_excluded_but_project_name_is_not(tmp_path):
    root=make_project(tmp_path/'icon_project')
    assert scan(LayaProjectAdapter(root),mode='visual')['items'][0]['status']=='candidate'
    folder=root/'assets/ico_rewards';folder.mkdir()
    for name in ('panel.png','panel.png.meta'):(root/'assets'/name).rename(folder/name)
    assert scan(LayaProjectAdapter(root),mode='visual')['items'][0]['reason']=='excluded_icon_name'


@pytest.mark.parametrize('names,excluded', [([],False), (['panel'],True), (['PANEL','图标'],True), (['other'],False)])
def test_custom_policy_flows_through_build_and_scan_workers(tmp_path,names,excluded):
    root=make_project(tmp_path/'project')
    result=build(LayaProjectAdapter(root),mode='visual',center_size=1,workers=2,ignore_names=names)
    assert result['report']['settings']['excludedNameContains']==list(normalize_ignored_names(names))
    report=scan(LayaProjectAdapter(root),mode='visual',center_size=1,workers=2,ignore_names=names)
    assert (report['items'][0]['reason']=='excluded_icon_name')==excluded
    assert bool(result['report']['summary']['candidates']) != excluded


def test_custom_list_replaces_defaults_and_supports_unicode_casefold(tmp_path):
    root=make_project(tmp_path/'project')
    for name in ('panel.png','panel.png.meta'):(root/'assets'/name).rename(root/'assets'/name.replace('panel','ICON_图标'))
    assert scan(LayaProjectAdapter(root),mode='visual',ignore_names=[])['items'][0]['status']=='candidate'
    assert scan(LayaProjectAdapter(root),mode='visual',ignore_names=['panel'])['items'][0]['status']=='candidate'
    assert matches_ignored_name('Straße_图标.png',['STRASSE'])
    assert matches_ignored_name('Straße_图标.png',['图标'])
    assert normalize_ignored_names([' Panel ','PANEL','图标'])==('panel','图标')


@pytest.mark.parametrize('names',[None,'icon',{},[1],[''],['  '],[True],['icon',None]])
def test_bad_name_policies_fail_before_scanning(tmp_path,names):
    root=make_project(tmp_path/'project')
    with pytest.raises(ValueError,match='non-empty strings'):
        scan(LayaProjectAdapter(root),ignore_names=names)
