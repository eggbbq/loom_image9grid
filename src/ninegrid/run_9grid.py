"""Bootstrap and run recursive nine-grid optimization without touching PSD export."""
import argparse
from collections import Counter
import os
from pathlib import Path
import subprocess
import sys


_SOURCE_ROOT = Path(__file__).resolve().parents[2]
SOURCE_CHECKOUT = ((_SOURCE_ROOT / "pyproject.toml").is_file() and
                   (_SOURCE_ROOT / "src/ninegrid/run_9grid.py").resolve() == Path(__file__).resolve())
ROOT = _SOURCE_ROOT if SOURCE_CHECKOUT else Path.cwd()

# Keep direct-file launching/bootstrap independent of package installation.
# Spawned workers inherit this source path from the parent interpreter.
if SOURCE_CHECKOUT and __package__ in (None, ""):
    sys.path.insert(0, str(ROOT / "src"))


def ratio(value):
    result = float(value)
    if not 0 <= result <= 1:
        raise argparse.ArgumentTypeError("最小节省率必须在 0～1 之间")
    return result


def workers_value(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("workers 必须大于等于 0")
    return number


def parser():
    result = argparse.ArgumentParser(description="递归处理搜索目录中的全部 PNG，生成九宫格资源并回写 Laya 项目。")
    result.add_argument("--project", type=Path, default=Path("laya-project"),
                        help="Laya 项目；源码模式相对仓库根目录，pip 安装后相对当前工作目录（默认 laya-project）")
    result.add_argument("--search-dir", "--input", dest="search_dirs", type=Path, nargs="+",
                        default=[Path("assets")], help="搜索目录或 PNG，可传多个；相对路径基于 Laya 项目，默认 assets")
    result.add_argument("--output", type=Path, default=Path("assets/ninegrid_auto"),
                        help="输出目录，相对 Laya 项目；与搜索目录相同时自动写回原 PNG")
    result.add_argument("--workers", type=workers_value, default=0, help="计算进程数：0 自动使用 CPU 核心数，1 单进程，或填写如 4 / 8")
    result.add_argument("--min-saving", type=ratio, default=0.20, help="最小纹理面积节省率，默认 0.20")
    result.add_argument("--mode", choices=("strict", "visual"), default="strict",
                        help="strict 逐像素无损；visual 在明确误差预算内近似识别")
    result.add_argument("--max-mean-error", type=float, default=1.0, help="视觉模式预乘 RGBA 平均误差上限，0～255，默认 1")
    result.add_argument("--max-bad-pixel-ratio", type=ratio, default=0.01, help="视觉模式严重误差像素占比上限，默认 1%%")
    result.add_argument("--pixel-error-threshold", type=int, default=32, help="严重误差像素的通道差值阈值，默认 32")
    result.add_argument("--center-size", type=int, default=8, help="视觉模式保留的中段像素数，默认 8，外加双线性保护像素")
    result.add_argument("--edge-tolerance", type=int, default=2, help="视觉模式允许的轮廓边界起伏像素数，默认 2")
    result.add_argument("--axis", choices=("auto", "horizontal", "vertical"), default="auto",
                        help="视觉模式拉伸方向，vertical 即上中下三段；默认自动")
    result.add_argument("--scene-root", type=Path, nargs="+", help="仅扫描指定场景/预制体目录，默认整个 assets")
    result.add_argument("--engine-dir", type=Path,
                        help="Laya 3.4.1 引擎 JS 目录；源码模式相对仓库根目录，pip 安装后相对当前工作目录")
    result.add_argument("--dry-run", action="store_true", help="预演资源生成及引用回写，项目零写入，不启动浏览器")
    modes = result.add_mutually_exclusive_group()
    modes.add_argument("--scan-only", action="store_true", help="仅递归分析，零写入")
    modes.add_argument("--build-only", action="store_true", help="生成 PNG、九宫格参数与报告，不修改场景")
    return result


def ensure_python(root):
    """Only install NineGrid's NumPy/Pillow dependencies, without PSD dependencies."""
    if not SOURCE_CHECKOUT:
        return Path(sys.executable)
    python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        print("创建 Python 虚拟环境…", flush=True)
        subprocess.run([sys.executable, "-m", "venv", str(root / ".venv")], check=True)
    check = subprocess.run([str(python), "-c", "import numpy, PIL"], cwd=root,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if check.returncode:
        print("安装九宫格依赖…", flush=True)
        subprocess.run([str(python), "-m", "pip", "install", "-r", str(root / "src/ninegrid/requirements.txt")],
                       cwd=root, check=True)
    return python


def print_report(report):
    for source in report.get("retiredSources", []):
        print(f"[移除旧记录] {source}：源文件已删除且不再被引用，备份保留。", flush=True)
    for item in report["items"]:
        if item["status"] == "candidate":
            old = "×".join(map(str, item["sourceSize"]))
            new = "×".join(map(str, item["outputSize"]))
            print(f"[可优化] {item['source']}：{old} → {new}，节省 {item['pixelAreaSavedRatio']:.2%}，"
                  f"九宫格 {item['layaSizeGrid']}，安全引用 {item['safeReferences']}", flush=True)
            if item.get("mode") == "visual":
                quality = item["cpuQuality"]
                print(f"  视觉近似/{item['sliceMode']}：平均误差 {quality['meanError']:.3f}/255，"
                      f"严重误差像素 {quality['badPixelRatio']:.3%}；并非逐像素无损。", flush=True)
        else:
            print(f"[跳过] {item['source']}：{item['reason']}", flush=True)
    summary = report["summary"]
    print(f"扫描 {summary['scanned']} 张 PNG，可优化 {summary['candidates']} 张，"
          f"跳过 {summary['scanned'] - summary['candidates']} 张。", flush=True)
    if not summary["candidates"] and report["items"]:
        reasons = Counter(item["reason"] for item in report["items"])
        print("跳过原因：" + "；".join(f"{reason}={count}" for reason, count in sorted(reasons.items())), flush=True)


def preview_references(adapter, report):
    changes = []
    for item in report["items"]:
        if item["status"] != "candidate":
            continue
        uid = item["sourceMetadata"].get("uuid") if item.get("overwriteSource") else adapter.create_meta(item)["uuid"]
        for ref in item["references"]:
            if not ref["eligible"]:
                continue
            old = ref["oldSkin"]
            value = ref.get("newSkin") if item.get("overwriteSource") else "res://" + uid if old.startswith("res://") else (
                item["output"] if old.startswith("assets/") else item["output"][len("assets/"):])
            changes.append({**ref, "newSkin": value, "newSizeGrid": item["layaSizeGrid"]})
    return changes


def run(args, root=ROOT):
    from ninegrid.parallel import ImagePool
    with ImagePool(args.workers, progress=print) as pool:
        print(f"CPU 计算进程：{pool.workers}（自动适配任务数）", flush=True)
        return _run(args, root, pool)


def _run(args, root, pool):
    # Imports happen only after the CLI bootstrap chooses a prepared interpreter.
    from ninegrid.laya.adapter import LayaProjectAdapter
    from ninegrid.pipeline import scan, build, apply
    from ninegrid.verify import verify_project
    from ninegrid.visual import quality_limits

    project = (root / args.project).resolve()
    scope = args.scene_root
    adapter = LayaProjectAdapter(project, scope)
    options = dict(inputs=args.search_dirs, output=args.output, min_saving=args.min_saving, mode=args.mode,
                   quality=quality_limits(args.max_mean_error, args.max_bad_pixel_ratio,
                                          args.pixel_error_threshold, args.edge_tolerance),
                   center_size=args.center_size, axis=args.axis, pool=pool)
    print(f"Laya 项目：{project}", flush=True)
    print("递归搜索：" + "，".join(str(project / directory) for directory in args.search_dirs), flush=True)
    if args.scan_only:
        report = scan(adapter, **options)
        print_report(report)
        return {"status": "scan_only", "report": report}

    result = build(adapter, **options, dry_run=args.dry_run)
    if result["report"]["settings"].get("overwriteSource"):
        print("同目录：原地写回模式，保留 PNG 路径及 UUID；原图备份后，在引擎验证通过时一起提交。", flush=True)
    print_report(result["report"])
    if args.dry_run:
        changes = [] if args.build_only else preview_references(adapter, result["report"])
        for change in changes:
            print(f"[将回写] {change['file']} / {change['nodeId']}：{change['newSkin']}，"
                  f"九宫格 {change['newSizeGrid']}", flush=True)
        print(f"预演完成：{len(result['files'])} 个计划生成文件，{len(changes)} 处引用；项目未写入，"
              "实际回写前仍需通过引擎渲染校验。", flush=True)
        return {**result, "changes": changes}

    if result["runId"]:
        print(f"资源生成事务：{result['runId']}", flush=True)
    # Reload after building so new PNG metadata is present in the UUID index.
    applied = None
    if not args.build_only:
        print("校验渲染并回写安全引用…", flush=True)
        engine = str((root / args.engine_dir).resolve()) if args.engine_dir else None
        applied = apply(LayaProjectAdapter(project, scope), engine_dir=engine, pool=pool)
        count = sum(ref["eligible"] for ref in applied["changes"])
        print(f"本次回写 {count} 处引用。", flush=True)
        if applied["runId"]:
            print(f"引用回写事务：{applied['runId']}", flush=True)
    verified = verify_project(LayaProjectAdapter(project, scope), pool=pool)
    validation = "视觉误差预算" if args.mode == "visual" else "RGBA 无损重建"
    print(f"验证完成：{len(verified['items'])} 张优化图的{validation}及资源引用检查通过。", flush=True)
    print(f"报告：{project / '.ninegrid/report.md'}", flush=True)
    return {"status": "built" if args.build_only else "complete", "build": result,
            "apply": applied, "verify": verified}


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = parser().parse_args(arguments)
    if sys.version_info < (3, 11):
        print("需要 Python 3.11+。", file=sys.stderr)
        return 1
    try:
        project = (ROOT / args.project).resolve()
        if not project.is_dir() or not any(project.glob("*.laya")):
            raise ValueError(f"未找到 Laya 项目：{project}")
        python = ensure_python(ROOT)
        # Direct-file bootstrap also works before the package has been installed.
        if Path(sys.prefix).resolve() != python.parent.parent.resolve():
            return subprocess.call([str(python), str(Path(__file__).resolve()), *arguments], cwd=ROOT)
        run(args)
    except subprocess.CalledProcessError as exc:
        print(f"九宫格执行失败，退出码 {exc.returncode}。", file=sys.stderr)
        return exc.returncode if exc.returncode > 0 else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"九宫格执行失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
