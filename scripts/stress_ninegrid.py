"""Generate thousands of UI images and verify strict, visual and automatic grids.

Runs directly from the checkout with NumPy/Pillow; pytest is not required.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from html import escape
import json
import os
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
from PIL import Image
from ninegrid.scan import read_rgba
from tests.stress.patterns import PATTERNS, make_case, reference_render
from tests.stress.checks import check_strict, check_visual, check_automatic
from tests.stress.png_checks import check_png


def run_case(task):
    pattern, variant, seed, visual_every, auto_every, png_every = task
    case = make_case(pattern, variant, seed)
    result = dict(pattern=pattern, variant=variant, seed=seed, caseSeed=case.seed,
                  sourceSize=list(case.image.shape[1::-1]), checks={}, failures=[])
    mode_index = variant//visual_every if visual_every else 0
    center = (1, 8, 2, 16, 32, 64)[mode_index % 6]
    axis = ('auto', 'horizontal', 'vertical')[(mode_index//6) % 3]
    threshold = (0, .2, .5, .9, 1)[variant % 5]
    result['parameters'] = dict(minSaving=threshold, centerSize=center, axis=axis)
    stages = [('strict', lambda: check_strict(case, threshold))]
    if visual_every and variant % visual_every == 0:
        stages.append(('visual', lambda: check_visual(case, center, axis)))
    if auto_every and variant % auto_every == 0:
        stages.append(('automatic', lambda: check_automatic(case)))
    for name, check in stages:
        start = time.perf_counter()
        try:
            result['checks'][name] = dict(check(), valid=True, seconds=time.perf_counter()-start)
        except Exception as error:
            result['failures'].append(dict(stage=name, message=str(error), traceback=traceback.format_exc()))
            result['checks'][name] = dict(valid=False, seconds=time.perf_counter()-start)
    if png_every and variant % png_every == 0:
        try:
            result['checks']['png'] = check_png(case, variant//png_every)
        except Exception as error:
            result['failures'].append(dict(stage='png', message=str(error), traceback=traceback.format_exc()))
            result['checks']['png'] = dict(valid=False)
    return result


def save_evidence(directory, result):
    """Save images and a self-contained browser-readable comparison page."""
    from ninegrid.candidate import choose_candidate
    from ninegrid.visual import choose_visual_candidate
    case = make_case(result['pattern'], result['variant'], result['seed'])
    directory.mkdir(parents=True, exist_ok=True)
    Image.fromarray(case.image).save(directory / 'original.png')
    assert np.array_equal(read_rgba(directory / 'original.png'), case.image)
    panels = [('Original', 'original.png')]
    for mode in ('strict', 'visual'):
        try:
            if mode == 'strict':
                candidate, _ = choose_candidate(case.image, result['parameters']['minSaving'])
            else:
                candidate, _ = choose_visual_candidate(case.image, center_size=result['parameters']['centerSize'],
                                                       axis=result['parameters']['axis'])
            if candidate is None:
                continue
            crop = candidate.crop(case.image)
            restored = reference_render(crop, candidate.margins, candidate.size)
            difference = np.max(np.abs(case.image.astype(np.int16)-restored.astype(np.int16)), axis=2)
            diff = np.zeros_like(case.image)
            diff[..., 0] = np.minimum(difference*8, 255)
            diff[..., 3] = 255
            overlay = Image.fromarray(case.image)
            from PIL import ImageDraw
            draw = ImageDraw.Draw(overlay)
            grid = candidate.margins
            width, height = candidate.size
            for x in (grid['left'], width-grid['right']-1):
                draw.line((x, 0, x, height-1), fill=(255, 0, 255, 255))
            for y in (grid['top'], height-grid['bottom']-1):
                draw.line((0, y, width-1, y), fill=(255, 0, 255, 255))
            overlay.save(directory / f'{mode}-grid.png')
            panels.append((mode+' grid', f'{mode}-grid.png'))
            for label, pixels in (('crop', crop), ('restored', restored), ('diff-x8', diff)):
                filename = f'{mode}-{label}.png'
                Image.fromarray(pixels).save(directory / filename)
                panels.append((mode+' '+label, filename))
        except Exception as error:
            result.setdefault('evidenceErrors', []).append(f'{mode}: {error}')
    (directory / 'case.json').write_text(json.dumps(result, indent=2)+'\n')
    figures = ''.join(f'<figure><figcaption>{escape(title)}</figcaption><img src="{path}"></figure>' for title, path in panels)
    html = '<!doctype html><meta charset="utf-8"><title>Nine-grid case</title>'
    html += '<style>body{font:15px system-ui;margin:24px;background:#eee}main{display:flex;flex-wrap:wrap}figure{margin:8px;padding:12px;background:white}img{max-width:600px;image-rendering:pixelated;background:repeating-conic-gradient(#ddd 0 25%,#fff 0 50%) 0/16px 16px}pre{white-space:pre-wrap}</style>'
    html += f'<h1>{escape(case.name)}</h1><p>Magenta: grid boundaries. Red: raw RGBA difference ×8.</p><main>{figures}</main>'
    html += '<pre>'+escape(json.dumps(result, indent=2))+'</pre>'
    (directory / 'index.html').write_text(html)


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('must be positive')
    return number


def nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError('must be nonnegative')
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variants', type=positive, default=192, help='variants per pattern (default: 192; 13,056 images)')
    parser.add_argument('--seed', type=int, default=20261027)
    parser.add_argument('--pattern', choices=PATTERNS, action='append', help='only selected families; repeatable')
    parser.add_argument('--variant', type=nonnegative, help='reproduce just this variant of each selected pattern')
    parser.add_argument('--visual-every', type=nonnegative, default=8, help='visual check stride; 0 disables')
    parser.add_argument('--auto-every', type=nonnegative, default=64, help='automatic check stride; 0 disables')
    parser.add_argument('--png-every', type=nonnegative, default=16, help='PNG roundtrip stride; 0 disables')
    parser.add_argument('--workers', type=positive, default=min(4, os.cpu_count() or 1))
    parser.add_argument('--output', type=Path, help='new or empty report directory')
    args = parser.parse_args()
    output = args.output or ROOT / 'out/stress' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    if output.exists() and any(output.iterdir()):
        parser.error(f'output directory is not empty: {output}')
    output.mkdir(parents=True, exist_ok=True)
    patterns = args.pattern or PATTERNS
    variants = [args.variant] if args.variant is not None else range(args.variants)
    tasks = [(pattern, variant, args.seed, args.visual_every, args.auto_every, args.png_every)
             for variant in variants for pattern in patterns]
    start = time.perf_counter()
    counts = Counter(images=0, passedImages=0, failedImages=0)
    stages = defaultdict(Counter)
    families = defaultdict(Counter)
    failures, links = [], []
    executor = ProcessPoolExecutor(max_workers=args.workers) if args.workers > 1 else None
    results = executor.map(run_case, tasks, chunksize=4) if executor else map(run_case, tasks)
    last_progress = start
    print(f'{len(tasks)} images, {len(patterns)} families, {args.workers} workers; output={output}', flush=True)
    try:
        with (output / 'cases.jsonl').open('w') as stream:
            for result in results:
                counts['images'] += 1
                families[result['pattern']]['images'] += 1
                failed = bool(result['failures'])
                counts['failedImages' if failed else 'passedImages'] += 1
                families[result['pattern']]['failedImages' if failed else 'passedImages'] += 1
                for name, check in result['checks'].items():
                    stages[name]['checks'] += 1
                    stages[name]['passed' if check['valid'] else 'failed'] += 1
                    if check['valid'] and name != 'png':
                        stages[name]['accepted' if check['accepted'] else 'rejected'] += 1
                        stages[name]['resizeChecks'] += check.get('resizeChecks', 0)
                    if check['valid'] and name == 'png':
                        stages[name]['mode:'+check['storageMode']] += 1
                    for coding, count in check.get('candidateCodings', {}).items():
                        stages[name]['coding:'+coding] += count
                    for decision, count in check.get('decisions', {}).items():
                        stages[name]['decision:'+decision] += count
                if failed:
                    failures.append(result)
                    print(f"FAIL {result['pattern']} variant={result['variant']}: {result['failures'][0]['message']}", flush=True)
                # One representative of each family plus the first 100 failures.
                if result['variant'] == variants[0] or (failed and len(failures) <= 100):
                    directory = output / 'images' / (result['pattern'].replace('/', '-')+f"-{result['variant']:05d}")
                    save_evidence(directory, result)
                    links.append((result['pattern']+f"/{result['variant']}", str(directory.relative_to(output) / 'index.html'), failed))
                stream.write(json.dumps(result)+'\n')
                now = time.perf_counter()
                if now-last_progress >= 5 or counts['images'] == len(tasks):
                    print(f"{counts['images']}/{len(tasks)} images, {counts['failedImages']} failures, {now-start:.1f}s", flush=True)
                    last_progress = now
    finally:
        if executor:
            executor.shutdown(cancel_futures=True)
    report = dict(valid=not failures, seed=args.seed, seconds=round(time.perf_counter()-start, 3),
                  counts=dict(counts), stages={k: dict(v) for k, v in stages.items()},
                  families={k: dict(v) for k, v in families.items()}, failures=failures,
                  configuration={k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
                  scope='CPU generation, geometry, resizing and PNG; real GPU sampling requires tests/test_engine.py')
    (output / 'report.json').write_text(json.dumps(report, indent=2)+'\n')
    html = '<!doctype html><meta charset="utf-8"><title>Nine-grid stress results</title><style>body{font:16px system-ui;margin:32px}li{margin:8px}.fail{color:#c00}pre{white-space:pre-wrap}</style>'
    html += f'<h1>{"PASS" if report["valid"] else "FAIL"}: {counts["images"]} generated images</h1>'
    html += '<pre>'+escape(json.dumps(dict(counts=report['counts'], stages=report['stages'], seconds=report['seconds']), indent=2))+'</pre><ul>'
    html += ''.join(f'<li class="{"fail" if failed else "pass"}"><a href="{path}">{escape(name)}</a>{" FAILED" if failed else ""}</li>' for name, path, failed in links)
    html += '</ul><p>CPU checks only. See report.json and cases.jsonl for full results.</p>'
    (output / 'index.html').write_text(html)
    print(json.dumps(dict(valid=report['valid'], counts=report['counts'], stages=report['stages'], seconds=report['seconds'], report=str(output / 'report.json')), indent=2))
    return 0 if report['valid'] else 1


if __name__ == '__main__':
    # Assertion-based oracles must never silently disappear under python -O.
    if not __debug__:
        raise SystemExit('Run without -O: stress checks require assertions.')
    raise SystemExit(main())
