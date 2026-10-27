"""Automatic bounded candidate selection for a staged Laya project."""
from copy import deepcopy
import hashlib,json,shutil
from pathlib import Path
import numpy as np
from PIL import Image
from .candidates import ALGORITHM,GPU_LIMITS,CENTERS,BOUNDARY_INSETS,MAX_COMPRESSED_CANDIDATES,image_traits,pixel_candidates
from ..crop import png_bytes

def optimize_automatically(stage_project,staged,original_project,policy,workers,libraries,version,ignore_names,pool,*,evidence_directory=None,work_directory=None,allowed_component_types=()):
    from ninegrid.laya.adapter import LayaProjectAdapter
    from ninegrid.pipeline import scan
    from ..laya.validation import validate_engine
    from .codecs import candidate_for_record
    from ..laya.resize import verify_fixed_borders
    from ..laya.resize import validate_resizing
    stage_project,staged=Path(stage_project),Path(staged)
    evidence=Path(evidence_directory or staged/'export-records/ninegrid').resolve();(evidence/'originals').mkdir(parents=True,exist_ok=True)
    adapter=LayaProjectAdapter(stage_project,reference_policy='native',allowed_component_types=allowed_component_types)
    validation_scenes=dict(adapter.project.scene_hashes)
    validation_atlases={p.relative_to(stage_project).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in (stage_project/'assets').rglob('*.atlascfg')}
    work=Path(work_directory or stage_project/'.ninegrid-auto').resolve()
    work_relative=work.relative_to(stage_project).as_posix()
    advanced_ignores=[name for name in ignore_names if name not in ('icon','ico','图标')]
    scanned=scan(adapter,inputs=[staged],output=staged,mode='strict',min_saving=0,
                 center_size=8,pool=pool,ignore_names=advanced_ignores)
    states=[];items=[]
    work.mkdir(exist_ok=True)
    for raw in scanned['items']:
        raw=deepcopy(raw);raw['scanReason']=raw.get('reason')
        meta_file=stage_project/(raw['source']+'.meta')
        raw['sourceMetadataSha256']=hashlib.sha256(meta_file.read_bytes()).hexdigest() if meta_file.exists() else None
        from ..laya.sampling import effective_sampling
        raw['effectiveSampling']=effective_sampling(raw['sourceMetadata'])
        if (Path(raw['source']).stem.partition('_')[0] in {'icon','ico','图标'}
                or any(ref['type']=='GLoader' for ref in raw['references'])):
            raw.update(status='skipped',reason='fixed_icon_canvas');items.append(raw);continue
        if raw.get('reason') not in (None,'no_equal_bands','below_min_saving','reconstruction_mismatch'):
            items.append(raw);continue
        if any(not ref['eligible'] for ref in raw['references']):
            raw.update(status='skipped',reason='unsafe_shared_reference');items.append(raw);continue
        path=stage_project/raw['source'];rgba=np.asarray(Image.open(path).convert('RGBA'))
        traits=image_traits(rgba)
        root_coverage=False
        for ref in raw['references']:
            scene=adapter.project.scenes.get(ref['file'],{})
            width,height=scene.get('width',0),scene.get('height',0)
            if width and height and len(ref.get('pointer',[]))==2:
                root_coverage|=rgba.shape[1]>=.8*width and rgba.shape[0]>=.8*height
        if root_coverage and traits['quantizedColors']>32:
            raw.update(status='skipped',reason='detailed_page_background_fixed',traits=traits)
            items.append(raw);continue
        options,traits=pixel_candidates(rgba,policy)
        raw.update(traits=traits,status='skipped',reason=traits.get('reason','no_safe_pixel_region'),attempts=[])
        if not options:items.append(raw);continue
        original=evidence/'originals'/(raw['sourceSha256']+'.png');shutil.copy2(path,original)
        states.append(dict(base=raw,options=options,next=0,rgba=rgba,path=path));items.append(raw)
    accepted=[];accepted_cases=[];resize_cases=[];border_cases=[]
    rounds=0
    while any(state['next']<len(state['options']) and not state.get('accepted') for state in states):
        trial=work/f'attempt-{rounds:02d}';trial.mkdir(parents=True)
        pending=[];by_key={}
        for state in states:
            if state.get('accepted') or state['next']>=len(state['options']):continue
            option=state['options'][state['next']];state['next']+=1
            item=dict(state['base'],**option)
            item.update(status='candidate',qualityLimits=GPU_LIMITS if policy=='visual' else None,
                        originalBackup=str(evidence/'originals'/(item['sourceSha256']+'.png')))
            item['output']=item['source']
            for reference in item['references']:
                reference['newSizeGrid']=item['layaSizeGrid']
            output=trial/(str(len(pending))+'.png')
            if item['geometryOnly']:output.write_bytes(state['path'].read_bytes())
            else:output.write_bytes(png_bytes(candidate_for_record(item).crop(state['rgba'])))
            item['stagedOutput']=output.relative_to(stage_project).as_posix()
            item['candidateKey']=item['source']+':'+str(rounds)
            item['stretchAxes']='both' if item['decision']=='constant_pixels' or item.get('tileEquivalentAxis') else ('horizontal' if not any(item['sizeGrid'][k] for k in ('top','bottom')) else 'both')
            by_key[item['candidateKey']]=(state,item)
            pending.append(dict(item=item,types=sorted({ref['type'] for ref in item['references'] if ref['eligible']}) or ['GImage']))
        if not pending:break
        print(f'  自动九宫格 GPU 第{rounds+1}轮：{len(pending)}个像素候选',flush=True)
        proof=validate_engine(LayaProjectAdapter(stage_project,reference_policy='native',allowed_component_types=allowed_component_types),pending,libraries,version,pool,
                              artifact_directory=trial.relative_to(stage_project).as_posix()+'/validation')
        geometrically_safe=[]
        for key,(state,item) in by_key.items():
            cases=[case for case in proof['cases'] if case.get('candidateKey')==key]
            passed=bool(cases) and all(c['accepted'] and c.get('maxRawChannelError',0)<= (1 if policy=='visual' else 0)
                and c['beforeRuntime']['anisoLevel']==item['effectiveSampling']['anisoLevel']
                and c['afterRuntime']['anisoLevel']==item['effectiveSampling']['anisoLevel'] for c in cases)
            attempt=dict(index=rounds,decision=item['decision'],outputSize=item['outputSize'],sizeGrid=item['sizeGrid'],
                         retainedCenterSize=item.get('retainedCenterSize'),boundaryInset=item.get('boundaryInset',0),tileEquivalentAxis=item.get('tileEquivalentAxis'),gpuAccepted=passed,
                         proof='attempts/'+trial.name+'/validation/report.json')
            state['base']['attempts'].append(attempt)
            if passed:geometrically_safe.append(item)
        if geometrically_safe:
            trial_images=trial/'images';trial_records=trial_images/'export-records/ninegrid';(trial_records/'originals').mkdir(parents=True)
            for item in geometrically_safe:shutil.copy2(stage_project/item['originalBackup'],trial_records/'originals'/Path(item['originalBackup']).name)
            (trial_records/'report.json').write_text(json.dumps(dict(items=geometrically_safe),indent=2))
            resizing=validate_resizing(stage_project,trial_records,original_project,check_height=True,raise_on_failure=False)
            borders=verify_fixed_borders(trial_records,geometrically_safe,resizing,
                       tolerance=1 if policy=='visual' else 0,raise_on_failure=False)
            for item in geometrically_safe:
                state=by_key[item['candidateKey']][0]
                rc=[c for c in resizing['cases'] if c['source']==item['source']]
                bc=[c for c in borders['cases'] if c['source']==item['source']]
                passed=bool(rc) and all(c['accepted'] and c.get('maxRawChannelError',0)<=(1 if policy=='visual' else 0)
                    and c['afterRuntime']['textureSize']==item['outputSize'] for c in rc) and bool(bc) and all(c['accepted'] for c in bc)
                state['base']['attempts'][-1]['resizeAccepted']=passed
                if not passed:continue
                state['accepted']=True;state['base'].update(item,status='candidate',reason=None)
                accepted.append(item)
                accepted_cases.extend(c for c in proof['cases'] if c.get('candidateKey')==item['candidateKey'])
                for case in rc:
                    case=deepcopy(case)
                    for key in ('before','after'):case[key]=(trial_records/case[key]).relative_to(stage_project).as_posix()
                    resize_cases.append(case)
                border_cases.extend(bc)
        rounds+=1
    for state in states:
        if not state.get('accepted'):state['base'].update(status='skipped',reason='all_cpu_gpu_resize_candidates_rejected')
    # Only the export's isolated stage is changed. The outer workflow performs
    # final shared-image audit/resize and publishes all outputs transactionally.
    from ..laya.patch import commit
    from ..paths import json_bytes,sha256
    writes={};expected={};changed_scenes=set()
    for item in accepted:
        source=stage_project/item['source'];content=(stage_project/item['stagedOutput']).read_bytes()
        metadata=deepcopy(item['sourceMetadata']);metadata.setdefault('importer',{})['sizeGrid']=[item['sizeGrid'][k] for k in ('top','right','bottom','left','repeat')]
        writes[item['source']]=content;writes[item['source']+'.meta']=json_bytes(metadata)
        expected[item['source']]=item['sourceSha256']
        expected[item['source']+'.meta']=item.get('sourceMetadataSha256')
        item['finalPngSha256']=hashlib.sha256(content).hexdigest()
        for ref in adapter.for_source(source):
            if ref.node.get('_$type')=='Image' and ref.field=='skin':
                ref.node['sizeGrid']=item['layaSizeGrid'];changed_scenes.add(ref.file)
    for name in changed_scenes:
        writes[name]=json_bytes(adapter.project.scenes[name]);expected[name]=adapter.project.scene_hashes[name]
    transaction=None
    for item in items:
        item['applied']=item['status']=='candidate'
        item['finalOutputSize']=item['outputSize'] if item['applied'] else item['sourceSize']
        item['finalSizeGrid']=item['sizeGrid'] if item['applied'] else item.get('sourceMetadata',{}).get('importer',{}).get('sizeGrid')
        if not item['applied']:
            for reference in item['references']:
                reference['newSizeGrid']=None
    settings=dict(mode=policy,algorithmVersion=ALGORITHM,centerSizes=list(CENTERS),
                  samplingBoundaryInsets=list(BOUNDARY_INSETS),maxCompressedCandidates=MAX_COMPRESSED_CANDIDATES,minSavingRequiredForGrid=False,cpuPixelTolerance=1 if policy=='visual' else 0,cpuMeanErrorLimit=.02,exactCandidatePixelTolerance=0,
                  gpuChannelTolerance=1 if policy=='visual' else 0,
                  excludedNameContains=list(ignore_names),automatic=True)
    report=dict(schemaVersion=2,projectVersion=version,settings=settings,items=items,
                transactionRunId=transaction,summary=dict(scanned=len(items),candidates=len(accepted),compressed=sum(i['outputSize']!=i['sourceSize'] for i in accepted),
                             originalPngGridFallbacks=sum(i['geometryOnly'] for i in accepted),skipped=sum(i['status']!='candidate' for i in items),rounds=rounds))
    # Preserve all rejected screenshots and analyses, without treating them as
    # successful cases. Normalize retained evidence paths into this record set.
    def retain_case(case,folder):
        case=deepcopy(case)
        for key in ('before','after'):
            source=stage_project/case[key]
            target=evidence/folder/(hashlib.sha256(str(source).encode()).hexdigest()[:16]+'-'+Path(source).name)
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
            case[key]=target.relative_to(evidence).as_posix()
        if '/originals/' in case.get('source',''):case['source']='originals/'+Path(case['source']).name
        return case
    accepted_cases=[retain_case(c,'validation') for c in accepted_cases]
    resize_cases=[retain_case(c,'resize-validation') for c in resize_cases]
    for state in states:
        chosen=state['base'].pop('stagedOutput',None)
        if chosen:state['base']['generatedPngProof']=chosen.replace(work_relative+'/','attempts/')
        if state.get('accepted'):state['base']['originalBackup']='originals/'+state['base']['sourceSha256']+'.png'
    def write(path,data):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,indent=2)+'\n')
    write(evidence/'report.json',report)
    write(evidence/'validation/report.json',dict(valid=True,engine='LayaAir '+version,renderer='Chrome WebGL',cases=accepted_cases))
    write(evidence/'resize-validation/report.json',dict(valid=True,engine='LayaAir '+version,cases=resize_cases))
    write(evidence/'fixed-border-validation.json',dict(valid=True,cases=border_cases))
    write(evidence/'compatibility.json',dict(projectVersion=version,engineVersion=version,engineLibraries=str(libraries),adapter=ALGORITHM))
    shutil.copytree(work,evidence/'attempts')
    for file in (evidence/'attempts').rglob('*.json'):
        def normalize(value,key=''):
            if isinstance(value,dict):return {k:normalize(v,k) for k,v in value.items()}
            if isinstance(value,list):return [normalize(v,key) for v in value]
            if isinstance(value,str):
                if value.startswith(work_relative+'/'):return value.replace(work_relative+'/','attempts/',1)
                if key in {'source','originalBackup'} and '/originals/' in value:return 'originals/'+Path(value).name
                if key in {'before','after'} and value.startswith('resize-validation/'):
                    return (file.parent.parent/value).relative_to(evidence).as_posix()
            return value
        file.write_text(json.dumps(normalize(json.loads(file.read_text())),indent=2)+'\n')
    from ..laya.patch import rollback
    try:
        current=LayaProjectAdapter(stage_project,reference_policy='native',allowed_component_types=allowed_component_types).project
        current_atlases={p.relative_to(stage_project).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in (stage_project/'assets').rglob('*.atlascfg')}
        if current.version!=version or current.scene_hashes!=validation_scenes or current_atlases!=validation_atlases:
            raise ValueError('Concurrent scene or atlas modification; source resources were not published')
        expected.update(validation_scenes);expected.update(validation_atlases)
        transaction=commit(stage_project,writes,'automatic_ninegrid',expected,
                           details={'proofDirectory':str(evidence)})
        report['transactionRunId']=transaction
        report['recordsDirectory']=str(evidence)
        write(evidence/'report.json',report)
        write(evidence/'apply-report.json',dict(status='applied' if transaction else 'unchanged',
              automatic=True,images=len(accepted),engineValidationPending=False,transactionRunId=transaction))
    except BaseException:
        if transaction:rollback(stage_project,transaction)
        raise
    try:print('  自动九宫格完成：'+json.dumps(report['summary']),flush=True)
    except BrokenPipeError:pass
    return report
