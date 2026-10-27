"""Opt-in actual Laya 3.4.2 tests for the installed/public automatic API."""
import os,json,hashlib,subprocess,sys,shutil
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw
import pytest
from ninegrid.automatic import optimize_project,pixel_candidates
from ninegrid.laya.resize import validate_resizing,verify_fixed_borders

pytestmark=pytest.mark.skipif(os.environ.get('NINEGRID_ENGINE_TESTS')!='1',reason='Actual Laya/Chrome test')


def project_fixture(folder):
    project=folder/'native-client';assets=project/'assets/surfaces';assets.mkdir(parents=True)
    (project/'Native.laya').write_text('{"version":"3.4.2"}')
    png=assets/'unrelated-rounded.png';image=Image.new('RGBA',(119,83))
    ImageDraw.Draw(image).rounded_rectangle((0,0,118,82),radius=12,fill='#7090b0')
    image.save(png);Path(str(png)+'.meta').write_text(json.dumps({'uuid':'rounded-source','importer':{'textureType':2}}))
    children=[{'_$id':'new','_$type':'GImage','name':'new','src':'res://rounded-source','width':119,'height':83,'autoSize':False},
              {'_$id':'old','_$type':'Image','name':'old','skin':'res://rounded-source','width':119,'height':83,'useSourceSize':False}]
    (project/'assets/Native.lh').write_text(json.dumps({'_$ver':1,'_$id':'root','_$type':'GWidget','width':119,'height':83,'_$child':children}))
    return project,png


def test_public_api_and_cli_default_repeat_without_presets(tmp_path):
    project,png=project_fixture(tmp_path)
    first=optimize_project(project,'assets/surfaces',playwright_module=os.environ.get('NINEGRID_PLAYWRIGHT_MODULE'))
    assert first['summary']['compressed']==1
    assert Image.open(png).width<119 and Image.open(png).height<83
    assert json.loads(Path(str(png)+'.meta').read_text())['uuid']=='rounded-source'
    scene=json.loads((project/'assets/Native.lh').read_text())
    assert scene['_$child'][1]['sizeGrid'].endswith(',1')
    records=Path(first['recordsDirectory']);proof=json.loads((records/'validation/report.json').read_text())
    assert {c['type'] for c in proof['cases']}=={'Image','GImage'}
    assert all(c['accepted'] and c['maxRawChannelError']<=1 and c['afterRuntime']['anisoLevel']==1 for c in proof['cases'])
    before={p:p.read_bytes() for p in (project/'assets').rglob('*') if p.is_file()}
    second=optimize_project(project,'assets/surfaces')
    assert second['recordsDirectory']!=first['recordsDirectory'] and second['summary']['candidates']==0
    result=subprocess.run([sys.executable,'-m','ninegrid','auto','--project',str(project),'--input','assets/surfaces'],cwd=tmp_path,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert {p:p.read_bytes() for p in (project/'assets').rglob('*') if p.is_file()}==before


def test_strict_does_not_admit_bounded_candidates(tmp_path):
    project,png=project_fixture(tmp_path)
    report=optimize_project(project,'assets/surfaces',mode='strict')
    assert report['settings']['cpuPixelTolerance']==0
    assert not any(i.get('candidateCoding')=='linear' for i in report['items'])
    proof=json.loads((Path(report['recordsDirectory'])/'validation/report.json').read_text())
    assert all(c['mode']=='strict' and c['equal'] for c in proof['cases'])


def test_native_progress_slider_values_and_independent_caps(tmp_path):
    project=tmp_path/'ranges';(project/'assets').mkdir(parents=True);(project/'Ranges.laya').write_text('{"version":"3.4.2"}')
    records=tmp_path/'range-records';(records/'originals').mkdir(parents=True)
    image=Image.new('RGBA',(139,51));ImageDraw.Draw(image).rounded_rectangle((0,0,138,50),radius=9,fill='#304e77',outline='#aaccee',width=3)
    png=project/'assets/fill.png';image.save(png);sha=hashlib.sha256(png.read_bytes()).hexdigest();shutil.copy2(png,records/'originals'/(sha+'.png'))
    choice=next(c for c in pixel_candidates(np.asarray(image))[0] if c['geometryOnly'])
    owners=[];references=[]
    for kind,axis in [('GProgressBar','horizontal'),('GProgressBar','vertical'),('GSlider','horizontal'),('GSlider','vertical')]:
        uid=kind+'-'+axis;references.append({'file':'assets/Ranges.lh','nodeId':uid+'-fill'})
        owners.append({'_$id':uid,'_$type':kind,'width':139,'height':51,'_hBar' if axis=='horizontal' else '_vBar':{'_$ref':uid+'-fill'},'_$child':[{'_$id':uid+'-fill','_$type':'GImage','width':139,'height':51}]})
    (project/'assets/Ranges.lh').write_text(json.dumps({'_$type':'GWidget','_$child':owners}))
    item=dict(choice,status='candidate',source='assets/fill.png',output='assets/fill.png',sourceSha256=sha,sourceFilterMode=0,references=references,sourceMetadata={'importer':{'textureType':2}},qualityLimits={'maxMeanError':.02,'maxBadPixelRatio':0,'pixelErrorThreshold':1,'edgeTolerancePixels':0})
    (records/'report.json').write_text(json.dumps({'items':[item]}))
    proof=validate_resizing(project,records,project,check_height=True)
    assert proof['valid'] and sum(c.get('target') is None for c in proof['cases'])==80
    assert verify_fixed_borders(records,[item],proof)['valid']
