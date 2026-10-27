"""Move failed stretch boundaries inward without changing source pixels or budgets."""
import hashlib
from pathlib import Path

from .jobs import candidate_from_fields, encode_image
from .laya.patch import commit
from .manifest import MANIFEST, markdown_report
from .paths import inside, load_json, sha256, json_bytes
from .scan import read_rgba
from .visual import VisualCandidate, visual_diff, accepted
from .reconstruct import reconstruct_9slice


def inset_failed_candidates(adapter, proof):
    root=adapter.project.root
    manifest=load_json(inside(root,MANIFEST))
    report=load_json(inside(root,'.ninegrid/report.json'))
    failed={Path(case['source']).stem for case in proof.get('cases',[]) if not case.get('accepted')}
    writes={};expected={MANIFEST:sha256(inside(root,MANIFEST))};changed=[]
    for item in manifest['items']:
        if item.get('status')!='built' or not item.get('overwriteSource') or item.get('mode')!='visual' or item['sourceSha256'] not in failed:
            continue
        previous=candidate_from_fields(item)
        def inset(band):
            if band is None:return None
            a,b,seed=band
            if b-a<previous.center_size+4:raise ValueError('No smaller verified stretch interval')
            return a+1,b-1,max(a+1,min(seed,b-2))
        try:candidate=VisualCandidate(previous.size,inset(previous.column_band),inset(previous.row_band),previous.center_size)
        except ValueError:continue
        rgba=read_rgba(inside(root,item['source']))
        diff=visual_diff(rgba,reconstruct_9slice(candidate.crop(rgba),candidate.margins,candidate.size),item['qualityLimits']['pixelErrorThreshold'])
        if not accepted(diff,item['qualityLimits']):continue
        fields=candidate.fields()
        fields.update(cpuQuality={**diff,'accepted':True,'qualityLimits':item['qualityLimits']},
                      engineFallbackInsets=item.get('engineFallbackInsets',0)+1)
        item.update(fields)
        content,_=encode_image((str(root),item,False))
        item['outputSha256']=hashlib.sha256(content).hexdigest()
        item['outputMetadata']['importer']['sizeGrid']=[item['sizeGrid'][k] for k in ('top','right','bottom','left','repeat')]
        expected[item['source']]=item['sourceSha256']
        expected[item['stagedOutput']]=sha256(inside(root,item['stagedOutput']))
        writes[item['stagedOutput']]=content
        for entry in report['items']:
            if entry['source']==item['source']:entry.update(fields)
        changed.append(item['source'])
    if not changed:return []
    writes[MANIFEST]=json_bytes(manifest)
    writes['.ninegrid/report.json']=json_bytes(report)
    writes['.ninegrid/report.md']=markdown_report(report).encode()
    commit(root,writes,'retry_engine_candidate',expected)
    return changed
