"""Actual native widget resize/value/edge-cap validation."""
import json,subprocess,os
from pathlib import Path
import numpy as np
from PIL import Image

def read_json(path):return json.loads(Path(path).read_text(encoding='utf-8'))

def validate_resizing(stage, records, project, *, check_height=False,raise_on_failure=True):
    from ninegrid.jobs import compare_render
    from ninegrid.engine import engine_libraries
    stage,records,project=Path(stage).resolve(),Path(records).resolve(),Path(project).resolve()
    report=read_json(records/'report.json')
    owners={}
    def visit(node,file):
        if node.get('_$type') in {'GProgressBar','GSlider'}:
            for key,axis in (('hBar','horizontal'),('_hBar','horizontal'),('vBar','vertical'),('_vBar','vertical')):
                value=node.get(key)
                ref=value.get('_$ref') if isinstance(value,dict) else None
                if ref:owners[file,ref]=dict(type=node['_$type'],axis=axis,width=node['width'],height=node['height'])
        for child in node.get('_$child',[]):visit(child,file)
    for file in stage.joinpath('assets').rglob('*'):
        if file.suffix in {'.lh','.ls'}:visit(read_json(file),file.relative_to(stage).as_posix())
    cases=[]
    for item in report['items']:
        if item['status']!='candidate':continue
        size=item['sourceSize'];original=records/'originals'/(item['sourceSha256']+'.png')
        from .sampling import effective_sampling
        sampling=item.get('effectiveSampling') or effective_sampling(item.get('sourceMetadata',{}))
        common=dict(name=item['source'],original=str(original),optimized=str(stage/item.get('stagedOutput',item['output'])),sourceSize=size,
                    grid=[item['sizeGrid'][k] for k in ('top','right','bottom','left','repeat')],
                    mode=item.get('mode','strict'),qualityLimits=item.get('qualityLimits'),filterMode=item['sourceFilterMode'],anisoLevel=sampling['anisoLevel'],
                    validationFilterModes=item.get('validationFilterModes',[item['sourceFilterMode']]))
        dynamic=[owners[r['file'],r['nodeId']] for r in item['references'] if (r['file'],r['nodeId']) in owners]
        if dynamic:
            for owner in dynamic:
                for reverse in (False,True):
                    for value in (0,.1,1,2,10,25,50,75,99,100):
                        cases.append(dict(common,owner=owner,reverse=reverse,value=value,capture=[owner['width'],owner['height']]))
        # Dynamic value/reverse cases do not have a static target. Keep them,
        # and additionally exercise independent GImage edge caps for auto trials.
        if not dynamic or check_height:
            for factor in (.5,.8,1,1.2):
                target=[max(1,round(size[0]*factor)),size[1]]
                cases.append(dict(common,target=target,capture=target))
            if check_height:
                for factor in (.8,1.2):
                    target=[size[0],max(1,round(size[1]*factor))]
                    cases.append(dict(common,target=target,capture=target))
        if check_height and (item.get('boundaryChannelJump',0)>1 or item.get('candidateCoding')=='linear'):
            # Test each axis in its own fractional pixel phase. Swapping the
            # texture axes must not hide a collapsed-center sampling failure.
            for axis,margins in ((0,('left','right')),(1,('top','bottom'))):
                extent=sum(item['sizeGrid'][key] for key in margins)
                if not extent:continue
                position=[16.625,16.5] if axis==0 else [16.5,16.625]
                cases.append(dict(common,target=size,capture=size,position=position))
                for delta in sorted({1,2,8,max(1,item.get('outputSize',size)[axis]-extent)}):
                    target=list(size);target[axis]=extent+delta
                    cases.append(dict(common,target=target,capture=target,position=position))
    cases=[dict(case,filterMode=sampling) for case in cases for sampling in case['validationFilterModes']]
    artifacts=records/'resize-validation';artifacts.mkdir()
    from .runtime import project_version,validate_engine_version,engine_environment
    version=project_version(project);libraries=engine_libraries(project,None);validate_engine_version(version,libraries)
    payload=dict(artifacts=str(artifacts),libraries=str(libraries),project=str(stage),version=version)
    engine_records=[]
    # Keep each browser job bounded while retaining every generated case.
    for start in range(0,len(cases),24):
        result=subprocess.run(['node',str(Path(__file__).with_name('resize_probe.mjs'))],input=json.dumps(dict(payload,cases=cases[start:start+24])),
                              capture_output=True,text=True,timeout=180,check=True,env=engine_environment())
        engine_records.extend(json.loads(result.stdout))
    proof=[]
    for record in engine_records:
        comparison,_=compare_render((str(Path(__import__('os').path.commonpath([stage,records]))),record))
        for key in ('before','after'):comparison[key]='resize-validation/'+Path(comparison[key]).name
        proof.append(comparison)
    result=dict(valid=bool(proof) and all(r['accepted'] for r in proof),engine='LayaAir 3.4.2',
                reference='Original texture with the computed nine-slice borders; real native progress/slider value changes',cases=proof)
    (artifacts/'report.json').write_text(json.dumps(result,indent=2)+'\n')
    if not result['valid'] and raise_on_failure:raise ValueError('实际尺寸/进度变化验证失败，未回写项目')
    return result


def verify_fixed_borders(evidence,items,proof,*,tolerance=1,raise_on_failure=True):
    """Compare real resized edge caps with the native original-size rendering."""
    import json
    import numpy as np
    from PIL import Image
    checks=[]
    for item in items:
        constant_field=False
        if item.get('decision')=='constant_pixels' and item.get('cpuExact') and not any(item['sizeGrid'].values()):
            with Image.open(evidence/'originals'/(item['sourceSha256']+'.png')) as source:
                values=np.asarray(source.convert('RGBA'))
                constant_field=bool(np.all(values==values[0,0]))
        cases=[c for c in proof['cases'] if c['source']==item['source'] and c.get('target') is not None]
        for case in cases:
            if case['target']==item['sourceSize']:continue
            reference=next(c for c in cases if c.get('target')==item['sourceSize']
                           and c.get('position')==case.get('position')
                           and c.get('beforeRuntime',{}).get('filterMode')==case.get('beforeRuntime',{}).get('filterMode'))
            with Image.open(evidence/reference['before']) as image:original=np.asarray(image.convert('RGBA')).astype(np.int16)
            with Image.open(evidence/case['after']) as image:resized=np.asarray(image.convert('RGBA')).astype(np.int16)
            grid=item['sizeGrid'];regions=[];contraction=False
            with Image.open(evidence/case['before']) as image:target_original=np.asarray(image.convert('RGBA')).astype(np.int16)
            if case['target'][1]==item['sourceSize'][1]:
                contraction=case['target'][0]<grid['left']+grid['right']
                for edge in ('left','right'):
                    count=(max(1,(round(case['target'][0]*grid[edge]/(grid['left']+grid['right'])) if grid.get('repeat') else (min(grid['left'],case['target'][0]) if edge=='left' else min(grid['right'],max(0,case['target'][0]-grid['left']))))) if contraction else grid[edge])+1
                    if not grid[edge]:continue
                    reference_image=target_original if contraction else original
                    a=reference_image[:,:count] if edge=='left' else reference_image[:,-count:]
                    b=resized[:,:count] if edge=='left' else resized[:,-count:]
                    regions.append(dict(edge=edge,maxRawChannelError=int(np.abs(a-b).max())))
            if case['target'][0]==item['sourceSize'][0]:
                contraction=case['target'][1]<grid['top']+grid['bottom']
                for edge in ('top','bottom'):
                    count=(max(1,(round(case['target'][1]*grid[edge]/(grid['top']+grid['bottom'])) if grid.get('repeat') else (min(grid['top'],case['target'][1]) if edge=='top' else min(grid['bottom'],max(0,case['target'][1]-grid['top']))))) if contraction else grid[edge])+1
                    if not grid[edge]:continue
                    reference_image=target_original if contraction else original
                    a=reference_image[:count] if edge=='top' else reference_image[-count:]
                    b=resized[:count] if edge=='top' else resized[-count:]
                    regions.append(dict(edge=edge,maxRawChannelError=int(np.abs(a-b).max())))
            if not regions:
                regions.append(dict(edge='no_fixed_caps_on_resized_axis',maxRawChannelError=int(np.abs(target_original-resized).max())))
            checks.append(dict(source=item['source'],target=case['target'],regions=regions,constantPixelField=constant_field,
                               capPolicy='native_cap_contraction' if contraction else 'fixed_caps',
                               accepted=(bool(regions) and all(x['maxRawChannelError']<=tolerance for x in regions))
                                        or (constant_field and bool(case['accepted']))))
    result=dict(valid=bool(checks) and all(x['accepted'] for x in checks),
                description='Fixed caps compared to native original-size rendering; below combined border sizes, contracted cap ranges compared to native rendering at the same target size. Screenshot crops retain one pixel of exterior padding.',
                maxRawChannelTolerance=tolerance,cases=checks)
    (evidence/'fixed-border-validation.json').write_text(json.dumps(result,indent=2)+'\n')
    if not result['valid'] and raise_on_failure:raise ValueError('实际缩放后边角区域不保持，未发布')
    return result
