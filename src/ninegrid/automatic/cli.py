"""One-command automatic optimization without per-image settings."""
import argparse,json
from pathlib import Path
from .api import optimize_project

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True,type=Path)
    parser.add_argument('--input',required=True,type=Path,help='One project-relative PNG directory')
    parser.add_argument('--records',type=Path,help='New empty proof directory; default .ninegrid/automatic')
    parser.add_argument('--mode',choices=('visual','strict'),default='visual')
    parser.add_argument('--workers',type=int,default=1)
    parser.add_argument('--engine-dir',type=Path)
    parser.add_argument('--playwright-module',type=Path)
    args=parser.parse_args(argv)
    result=optimize_project(args.project,args.input,records_directory=args.records,mode=args.mode,
        workers=args.workers,engine_dir=args.engine_dir,playwright_module=args.playwright_module)
    print(json.dumps(result['summary'],ensure_ascii=False,indent=2));return 0

if __name__=='__main__':raise SystemExit(main())
