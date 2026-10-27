"""Real-engine pose validation for the automatic candidate API."""
from pathlib import Path
from .runtime import validate_engine_version

def validate_engine(adapter, entries, libraries, version, pool,*,artifact_directory='.ninegrid/validation'):
    """Use NineGrid's original render cases and pixel comparisons on 3.4.2."""
    import os
    import subprocess
    from ninegrid import engine
    from ninegrid.jobs import compare_render
    from ninegrid.paths import inside, load_json, atomic_write, json_bytes
    validate_engine_version(version,libraries)
    root = adapter.project.root
    directory = inside(root, artifact_directory)
    directory.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).with_name('automatic_probe.mjs')
    cases = []
    for entry in entries:
        item = entry['item']
        original = item.get('originalBackup', item['source'])
        output = item.get('stagedOutput', item['output']) if item.get('status') != 'applied' else item['output']
        meta_path = inside(root, item['source'] + '.meta')
        meta = load_json(meta_path) if meta_path.exists() else {}
        from .sampling import effective_sampling
        sampling=item.get('effectiveSampling') or effective_sampling(meta)
        for filter_mode in item.get('validationFilterModes', [meta.get('importer', {}).get('filterMode', 1)]):
            for kind in entry['types']:
                for pose, scale, x, y in (('normal', 1, 0, 0), ('subpixel', 1, .5, .25),
                                          ('down-scale', .8, .5, .25), ('up-scale', 1.2, .5, .25)):
                    # Avoid exact texel-boundary ties with Point sampling:
                    # float UV rounding can select either adjacent texel even
                    # when the image is losslessly represented. Linear keeps
                    # its half-pixel cases; Point still tests fractional poses.
                    if filter_mode == 0 and x:
                        x = .375
                    cases.append(dict(source=str((root/Path(original)).resolve()), output=str(inside(root, output)),
                        candidateKey=item.get('candidateKey',item['source']),
                        type=kind, sourceSize=item['sourceSize'], sizeGrid=item['layaSizeGrid'],anisoLevel=sampling['anisoLevel'],
                        filterMode=filter_mode, mode=item.get('mode', 'strict'),
                        qualityLimits=item.get('qualityLimits'), pose=pose, renderScale=scale, offsetX=x, offsetY=y))
    payload = dict(project=str(root), libraries=str(libraries), artifacts=str(directory), cases=cases,version=version)
    from .runtime import engine_environment
    env = engine_environment()
    result = subprocess.run(['node', str(script)], input=json.dumps(payload), capture_output=True,
                            text=True, timeout=180, env=env)
    if result.returncode:
        raise ValueError('LayaAir 引擎验证失败：' + result.stderr[-3000:])
    records = json.loads(result.stdout)
    stats = []
    for stat, diff in pool.map(compare_render, [(str(root), record) for record in records], '引擎截图比较'):
        if diff is not None:
            atomic_write(inside(root, stat['after'] + '.diff.png'), diff)
        stats.append(stat)
    proof = dict(valid=all(s['accepted'] for s in stats) and len(stats) == len(cases),
                 engine='LayaAir ' + version, renderer='Chrome WebGL', cases=stats)
    atomic_write(directory / 'report.json', json_bytes(proof))
    return proof
