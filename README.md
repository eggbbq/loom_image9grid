# loom-image9grid

独立的 PNG 三段／九宫格优化 Python 库，支持严格无损和视觉近似，并提供 LayaAir 3.4.1／3.4.2 工程集成。

- **发行包名**：`loom-image9grid`
- **Python 导入名**：`ninegrid`，公共 API 位于 `ninegrid.api`
- **命令**：`ninegrid`、`ninegrid-run`、`ninegrid-auto`
- **环境**：Python 3.11+、NumPy、Pillow；不依赖 psd2ui、PSD 或 Photoshop

## 安装

从独立仓库安装：

```bash
python -m pip install "git+https://github.com/eggbbq/loom_image9grid.git"
```

本地开发：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

本库与旧版 `psd2ui` 中内置的 `ninegrid` 使用相同导入名和命令。请安装在独立虚拟环境中，避免两个发行包覆盖同名文件。

## 作为图片算法库使用

图片算法不需要 Laya 工程、Node.js 或浏览器。输入为 `(height, width, 4)` 的 `uint8` RGBA NumPy 数组。

```python
from pathlib import Path
from ninegrid.api import choose_candidate, png_bytes, read_rgba, reconstruct_9slice, pixel_diff

rgba = read_rgba("panel.png")
candidate, reason = choose_candidate(rgba, min_saving=0.20)
if candidate is not None:
    optimized = candidate.crop(rgba)
    Path("panel.small.png").write_bytes(png_bytes(optimized))
    print(candidate.fields())  # 原尺寸、优化尺寸、边距和纹理面积节省率
    restored = reconstruct_9slice(optimized, candidate.margins, candidate.size)
    assert pixel_diff(rgba, restored)["equal"]
else:
    print("跳过：", reason)
```

视觉近似模式：

```python
from ninegrid.api import choose_visual_candidate, quality_limits, read_rgba

candidate, quality = choose_visual_candidate(
    read_rgba("panel.png"),
    min_saving=0.20,
    limits=quality_limits(max_mean_error=1.0, max_bad_pixel_ratio=0.01),
    center_size=8,
    axis="auto",  # auto / horizontal / vertical
)
if candidate is not None:
    print(candidate.fields(), quality)
```

`candidate.size` 为 `(width, height)`；边距顺序为 top、right、bottom、left、repeat，repeat 固定为 0。严格模式要求完整 RGBA 无差异；视觉模式比较预乘 Alpha 后的误差，不能称为逐像素无损。算法只返回候选和像素数组，保存 PNG 和引擎九宫格参数由调用方决定。

## 作为 Laya 工程库使用

```python
from ninegrid.api import LayaProjectAdapter, scan, build, verify_project

project = "/absolute/path/to/laya-project"
report = scan(LayaProjectAdapter(project), inputs=["assets/ui"], mode="visual")
print(report["summary"])

# 另存优化 PNG、meta 和报告；不修改场景。
result = build(
    LayaProjectAdapter(project),
    inputs=["assets/ui"],
    output="assets/ninegrid_auto",
    mode="visual",
)
assert verify_project(LayaProjectAdapter(project))["valid"]
```

`ninegrid.api` 同时提供 `apply(adapter, ...)` 和 `rollback(project_root, run_id, dry_run=False)`。build 后应新建 adapter，使资源索引包含新生成的 meta；apply 在实际引擎验证通过后才提交。API 的 `workers` 默认 1；使用多进程时在 `if __name__ == "__main__":` 内调用。

## 自动优化 API

新增模块随库一并保留，可自动枚举候选、检查真实引擎效果和缩放边框，再原地提交：

```python
from ninegrid.api import optimize_project

# 需要引擎、Playwright 和浏览器；records_directory 须为空目录。
result = optimize_project(
    "/absolute/path/to/laya-project", "assets/ui",
    records_directory="/absolute/path/to/new-proof-directory",
    mode="visual", workers=1,
    engine_dir="/absolute/path/to/engine/libs",
)
```

也可运行 `ninegrid-auto --project /path/to/project --input assets/ui --engine-dir /path/to/engine/libs` 或 `ninegrid auto ...`。该入口使用自动候选与验收策略，和下文手动设置误差预算的流程分别保留。候选可能保留原图尺寸而只设置边距，或使用 repeat=1 平铺；用 `decode_candidate`／`reconstruct_candidate` 解码其记录，不要套用只支持 stretch 的重建函数。

## 命令行

```bash
# 只分析；零写入。
ninegrid scan --project /path/to/project --input assets/ui --mode visual

# 预演完整流程；零项目写入、不启动引擎校验。
ninegrid-run --project /path/to/project --search-dir assets/ui --dry-run

# 只生成优化图、meta 和报告，不改场景。
ninegrid-run --project /path/to/project --search-dir assets/ui --build-only

# 完整流程：生成 → 引擎校验 → 回写 → 校验。
ninegrid-run --project /path/to/project --search-dir assets/ui --mode visual

# 搜索和输出相同：保留路径及 UUID，备份并校验后原地覆盖。
ninegrid-run --project /path/to/project --search-dir assets/ui --output assets/ui --mode visual

# 分阶段验证与撤销。
ninegrid verify --project /path/to/project
ninegrid rollback --project /path/to/project --run-id <run-id>
```

Python／CLI 默认 strict；仓库 `run_9grid.sh` 显式配置 visual 和同目录覆盖。修改 sh 参数后可使用 `./run_9grid.sh --project /path/to/project --dry-run`。源码 sh 的工程路径相对仓库目录；安装后的命令相对当前工作目录，搜索和输出路径始终相对工程。

正式 `apply`／`verify --engine` 额外需要 Node.js、Playwright、Chrome 及 与工程版本一致的 LayaAir **3.4.1／3.4.2** 引擎 JS。可在目标工程执行 `npm install --save-dev playwright`，用 `--engine-dir` 或 `NINEGRID_ENGINE_DIR` 指定引擎库；库需包含 core、webgl_2D、ui、ui2。Playwright 也可通过 `NINEGRID_PLAYWRIGHT_MODULE` 指定模块入口，浏览器 channel 可由 `NINEGRID_BROWSER` 配置。库不附带引擎、IDE 或浏览器。

原有 scan/build/apply 流程仅回写符合条件的静态 `Image/GImage`，保留显示尺寸和布局。默认按路径中的 `icon`、`ico`、`图标` 忽略图标；遇到已有九宫格、不支持的纹理设置或引用会跳过。原地模式必须检查整个工程的共享引用。节省率指纹理像素面积，PNG 文件大小及 DrawCall 不保证同比下降。

完整参数、产物和安全规则见 [使用说明](docs/usage.md)，历史引擎验收见 [兼容性](docs/compatibility.md)。

## 测试与构建

```bash
.venv/bin/python -m pytest -q
# 68 类图像 × 192 个变体：13,056 张生成图片，保存报告与可视化样本；无需 pytest/GPU：
.venv/bin/python scripts/stress_ninegrid.py --output out/stress/full
# 需要已准备真实引擎、Playwright 和 Chrome：
# 3.4.2 引擎请同时设置 NINEGRID_TEST_PROJECT_VERSION=3.4.2
NINEGRID_ENGINE_TESTS=1 .venv/bin/python -m pytest -q tests/test_engine.py tests/test_inplace.py

# 生成 wheel 和源码包：
.venv/bin/python -m build
# 在隔离环境中安装 wheel，验证 API、CLI 和资源文件：
.venv/bin/python scripts/verify_package.py --wheel dist/loom_image9grid-1.1.0-py3-none-any.whl
```

`verify_package.py` 可额外接受 `--engine-dir`、`--playwright-module`，验证安装后的真实 GPU apply。`dist/` 可用于本地安装或后续发布；本仓库不会自动上传包。

大规模生成测试的覆盖范围、复现和报告说明见 [生成测试说明](docs/generated-tests.md)。
