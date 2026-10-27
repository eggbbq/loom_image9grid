import argparse
import json
import sys

from .laya.adapter import LayaProjectAdapter
from .laya.patch import rollback
from .pipeline import scan, build, apply
from .verify import verify_project


def ratio(value):
    number = float(value)
    if not 0 <= number <= 1:
        raise argparse.ArgumentTypeError("Expected a ratio in [0, 1]")
    return number


def main(argv=None):
    values=list(sys.argv[1:] if argv is None else argv)
    if values and values[0]=='auto':
        from .automatic.cli import main as automatic_main
        return automatic_main(values[1:])
    parser = argparse.ArgumentParser(description="Independent lossless PNG nine-slice optimizer for LayaAir")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("scan", "build", "apply", "verify", "rollback"):
        command = commands.add_parser(name)
        command.add_argument("--project", required=True)
        command.add_argument("--scene-root", action="append", help="Optional project-relative scene scope; repeatable")
        command.add_argument("--workers", type=int, default=0, help="CPU processes: 0 auto, 1 serial")
        command.add_argument("--dry-run", action="store_true", help="Preview only; write no project files")
        if name in ("scan", "build"):
            command.add_argument("--input", action="append", help="Project-relative PNG or directory; default assets")
            command.add_argument("--output", default="assets/ninegrid_auto")
            command.add_argument("--min-saving", type=ratio, default=0.20)
            command.add_argument("--mode", choices=("strict", "visual"), default="strict")
            command.add_argument("--max-mean-error", type=float, default=1.0)
            command.add_argument("--max-bad-pixel-ratio", type=ratio, default=0.01)
            command.add_argument("--pixel-error-threshold", type=int, default=32)
            command.add_argument("--center-size", type=int, default=8)
            command.add_argument("--edge-tolerance", type=int, default=2)
            command.add_argument("--axis", choices=("auto", "horizontal", "vertical"), default="auto")
        if name in ("apply", "verify"):
            command.add_argument("--engine-dir", help="LayaAir 3.4.1 engine JS directory")
        if name == "verify":
            command.add_argument("--engine", action="store_true", help="Also rerun actual Laya/Chrome screenshot comparisons")
        if name == "rollback":
            command.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    try:
        adapter = LayaProjectAdapter(args.project, args.scene_root)
        if args.command == "rollback":
            result = rollback(adapter.project.root, args.run_id, args.dry_run)
        elif args.command in ("scan", "build"):
            from .visual import quality_limits
            function = scan if args.command == "scan" else build
            options = dict(inputs=args.input, output=args.output, min_saving=args.min_saving, mode=args.mode,
                           quality=quality_limits(args.max_mean_error, args.max_bad_pixel_ratio,
                                                  args.pixel_error_threshold, args.edge_tolerance),
                           center_size=args.center_size, axis=args.axis, workers=args.workers)
            if args.command == "build":
                options["dry_run"] = args.dry_run
            result = function(adapter, **options)
        elif args.command == "apply":
            result = apply(adapter, args.dry_run, args.engine_dir, workers=args.workers)
        else:
            result = verify_project(adapter, workers=args.workers)
            if args.engine and not args.dry_run:
                from .engine import validate_items
                from .manifest import read_active_manifest
                manifest = read_active_manifest(adapter)[0]
                entries = [{"item": item, "types": sorted({r["type"] for r in item["references"]
                                                            if r.get("applied") or r.get("eligible")})}
                           for item in manifest["items"]]
                result["engineValidation"] = validate_items(adapter, [e for e in entries if e["types"]], args.engine_dir, workers=args.workers)
                result["valid"] = result["engineValidation"]["valid"]
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 1 if result.get("valid") is False else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
