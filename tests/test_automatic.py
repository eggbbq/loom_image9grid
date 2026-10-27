"""Generic public API/codec/transaction tests; real GPU coverage is separate."""
from pathlib import Path
import hashlib,json
import numpy as np
import pytest
from PIL import Image,ImageDraw

from ninegrid.automatic import pixel_candidates,decode_candidate,reconstruct_candidate,optimize_project
from ninegrid.laya.sampling import effective_sampling


def fixture(project,*,icon=False):
    images=project/'assets/textures';images.mkdir(parents=True)
    (project/'Demo.laya').write_text('{"version":"3.4.2"}')
    png=images/('icon_test.png' if icon else 'surface.png');Image.new('RGBA',(48,24),'#405080').save(png)
    Path(str(png)+'.meta').write_text(json.dumps({'uuid':'surface-id','importer':{'textureType':2}}))
    scene=project/'assets/Demo.lh';scene.write_text(json.dumps({'_$ver':1,'_$id':'root','_$type':'GWidget','width':48,'height':24,'_$child':[
        {'_$id':'image','_$type':'GImage','src':'res://surface-id','width':48,'height':24,'autoSize':False}]}))
    libraries=project/'engine/libs';libraries.mkdir(parents=True)
    for name in ('laya.core.js','laya.webgl_2D.js','laya.ui.js','laya.ui2.js'):
        (libraries/name).write_text("LayaEnv.version = '3.4.2';")
    return png,scene


def mock_proofs(monkeypatch):
    # Only used to inject I/O/concurrency faults, never claimed as GPU evidence.
    def pose(adapter,entries,libraries,version,pool,*,artifact_directory):
        directory=adapter.project.root/artifact_directory;directory.mkdir(parents=True)
        image=directory/'capture.png';Image.new('RGB',(2,2),'black').save(image)
        cases=[dict(candidateKey=e['item']['candidateKey'],source=e['item']['originalBackup'],accepted=True,
             before=image.relative_to(adapter.project.root).as_posix(),after=image.relative_to(adapter.project.root).as_posix(),
             maxRawChannelError=0,beforeRuntime={'anisoLevel':1},afterRuntime={'anisoLevel':1}) for e in entries]
        (directory/'report.json').write_text(json.dumps({'valid':True,'cases':cases}));return {'valid':True,'cases':cases}
    def resize(stage,records,project,**kwargs):
        data=json.loads((records/'report.json').read_text());d=records/'resize-validation';d.mkdir()
        Image.new('RGB',(2,2),'black').save(d/'capture.png')
        return {'valid':True,'cases':[dict(source=i['source'],accepted=True,maxRawChannelError=0,
            before='resize-validation/capture.png',after='resize-validation/capture.png',afterRuntime={'textureSize':i['outputSize']}) for i in data['items']]}
    def caps(records,items,proof,**kwargs):return {'valid':True,'cases':[dict(source=i['source'],accepted=True) for i in items]}
    monkeypatch.setattr('ninegrid.laya.validation.validate_engine',pose)
    monkeypatch.setattr('ninegrid.laya.resize.validate_resizing',resize)
    monkeypatch.setattr('ninegrid.laya.resize.verify_fixed_borders',caps)


def identities(project):return {p.relative_to(project):p.read_bytes() for p in (project/'assets').rglob('*') if p.is_file()}


def test_exact_opaque_tile_and_legacy_decoding():
    image=Image.new('RGBA',(131,79));ImageDraw.Draw(image).rounded_rectangle((0,0,130,78),radius=12,fill='#708090')
    rgba=np.asarray(image);options,_=pixel_candidates(rgba)
    choice=next(c for c in options if c.get('tileEquivalentAxis')=='both')
    candidate=decode_candidate(choice)
    assert np.array_equal(reconstruct_candidate(candidate.crop(rgba),candidate.margins,(131,79)),rgba)
    assert len([c for c in options if not c['geometryOnly']])<=8
    legacy=dict(choice,candidateCoding='guarded',mode='strict')
    assert np.array_equal(decode_candidate(legacy).crop(rgba),candidate.crop(rgba))
    fallback=dict(geometryOnly=True,sourceSize=[131,79],outputSize=[131,79],sizeGrid=dict(top=12,right=12,bottom=12,left=12,repeat=0))
    assert np.array_equal(decode_candidate(fallback).crop(rgba),rgba)


def test_linear_explicit_cpu_proof_and_strict_exclusion():
    rgba=np.empty((64,256,4),dtype=np.uint8)
    for x in range(256):
        for y in range(64):rgba[y,x]=[60+x//16,20+2*y,130+(x%2 if y==0 else 0),255]
    options,_=pixel_candidates(rgba)
    linear=next(c for c in options if c.get('candidateCoding')=='linear')
    candidate=decode_candidate(linear);difference=np.abs(candidate.restore(candidate.crop(rgba)).astype(int)-rgba)
    assert difference.max()<=1 and difference.mean()<=.02
    assert not any(c.get('candidateCoding')=='linear' for c in pixel_candidates(rgba,'strict')[0])
    assert effective_sampling({'importer':{'textureType':2,'anisoLevel':4}})['anisoLevel']==1


def test_proof_preparation_failure_does_not_publish(tmp_path,monkeypatch):
    project=tmp_path/'project';fixture(project);before=identities(project);mock_proofs(monkeypatch)
    real=__import__('shutil').copytree
    def fail(src,dst,*args,**kwargs):
        if Path(dst).name=='attempts':raise OSError('Proof archive failure')
        return real(src,dst,*args,**kwargs)
    monkeypatch.setattr('ninegrid.automatic.workflow.shutil.copytree',fail)
    with pytest.raises(OSError,match='Proof archive'):optimize_project(project,'assets/textures')
    assert identities(project)==before


def test_postcommit_report_failure_rolls_back_assets(tmp_path,monkeypatch):
    project=tmp_path/'project';fixture(project);before=identities(project);mock_proofs(monkeypatch)
    real=Path.write_text;once=[]
    def fail(path,*args,**kwargs):
        if path.name=='apply-report.json' and not once:once.append(True);raise OSError('Final report failure')
        return real(path,*args,**kwargs)
    monkeypatch.setattr(Path,'write_text',fail)
    with pytest.raises(OSError,match='Final report'):optimize_project(project,'assets/textures')
    assert once and identities(project)==before


def test_concurrent_consumer_edit_preserved_without_publishing(tmp_path,monkeypatch):
    project=tmp_path/'project';png,scene=fixture(project);before=identities(project);mock_proofs(monkeypatch)
    from ninegrid.laya.patch import commit
    edited=scene.read_text()+'\n'
    def mutate_then_commit(*args,**kwargs):scene.write_text(edited);return commit(*args,**kwargs)
    monkeypatch.setattr('ninegrid.laya.patch.commit',mutate_then_commit)
    with pytest.raises(ValueError,match='Concurrent'):optimize_project(project,'assets/textures')
    assert png.read_bytes()==before[png.relative_to(project)] and scene.read_text()==edited


def test_two_default_calls_get_unique_records_and_skip_existing(tmp_path,monkeypatch):
    project=tmp_path/'project';fixture(project);mock_proofs(monkeypatch)
    first=optimize_project(project,'assets/textures');before=identities(project)
    second=optimize_project(project,'assets/textures')
    assert first['recordsDirectory']!=second['recordsDirectory']
    assert second['summary']['candidates']==0 and identities(project)==before


def test_original_restore_reads_legacy_and_geometry_records(tmp_path):
    from ninegrid.automatic import restore_original
    from ninegrid.candidate import choose_candidate
    records=tmp_path/'records';(records/'originals').mkdir(parents=True)
    rgba=np.full((24,48,4),[70,100,140,255],dtype=np.uint8);rgba[3:-3,3:-3]=[120,180,60,255]
    source=tmp_path/'original.png';Image.fromarray(rgba).save(source);checksum=hashlib.sha256(source.read_bytes()).hexdigest()
    (records/'originals'/(checksum+'.png')).write_bytes(source.read_bytes())
    candidate,_=choose_candidate(rgba);image=tmp_path/'surface.png';Image.fromarray(candidate.crop(rgba)).save(image)
    grid=[candidate.margins[k] for k in ('top','right','bottom','left','repeat')]
    metadata={'uuid':'legacy','importer':{'sizeGrid':grid}}
    record=dict(candidate.fields(),sourceSha256=checksum,sourceMetadata={'uuid':'legacy'})
    (records/'report.json').write_text(json.dumps({'items':[record]}))
    assert restore_original(image,metadata,[records])==source.read_bytes()
    geometry=dict(record,geometryOnly=True,outputSize=[48,24]);Image.fromarray(rgba).save(image)
    (records/'report.json').write_text(json.dumps({'items':[geometry]}))
    assert restore_original(image,metadata,[records])==source.read_bytes()
    edited=rgba.copy();edited[0,0]=[255,0,0,255];Image.fromarray(edited).save(image)
    with pytest.raises(ValueError,match='No verified original'):restore_original(image,metadata,[records])

@pytest.mark.parametrize('kind',('atlas','new_scene'))
def test_validation_dependency_inventory_change_prevents_publish(tmp_path,monkeypatch,kind):
    project=tmp_path/'project';png,_=fixture(project);before=png.read_bytes();mock_proofs(monkeypatch)
    from ninegrid.laya import adapter
    original=adapter.LayaProjectAdapter;calls=0
    def changed_adapter(*args,**kwargs):
        nonlocal calls
        calls+=1
        if calls==2:
            if kind=='atlas':(project/'assets/Changed.atlascfg').write_text('{}')
            else:(project/'assets/Added.lh').write_text(json.dumps({'_$ver':1,'_$id':'added','_$type':'GWidget','width':48,'height':24}))
        return original(*args,**kwargs)
    monkeypatch.setattr(adapter,'LayaProjectAdapter',changed_adapter)
    with pytest.raises(ValueError,match='Concurrent scene or atlas'):
        optimize_project(project,'assets/textures',records_directory=tmp_path/'records')
    assert png.read_bytes()==before
    assert (project/'assets/Changed.atlascfg' if kind=='atlas' else project/'assets/Added.lh').exists()
