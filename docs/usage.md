# NineGrid Optimizer

本文描述原有 scan/build/apply 流程。独立库同时保留自动优化入口，见 [README](../README.md)。兼容性样例与截图路径来自原仓库的历史验收，大型工程与截图未迁移；当前代码接受 3.4.1／3.4.2，实际引擎必须与目标工程版本一致。

独立的 PNG 九宫格优化工具，提供严格无损和视觉近似两种模式。算法、项目适配代码及 Python 入口位于 `src/ninegrid/`，便捷 sh 入口为仓库根目录的 `run_9grid.sh`，Python 入口为 `src/ninegrid/run_9grid.py`。作为 `loom-image9grid` 独立发行，Python 导入名为 `ninegrid`；不依赖 PSD 导出器。

只处理已有 PNG 和磁盘上的 Laya 项目：寻找可收缩的行/列区间，生成较小的 RGBA PNG，验证原尺寸重建，然后回写真实 `.lh/.ls` 引用。支持横向三段、纵向三段和双向九宫格。不同目录另存优化图；同目录自动原地覆盖，原图保存在备份中。

## 环境

- Python 3.11+，Pillow、NumPy。
- 当前适配 LayaAir **3.4.1／3.4.2**、`_$ver: 1` 的 JSON 场景格式；其他版本拒绝写入。
- `scan/build/verify/rollback` 的 CPU 部分不需要启动 IDE。
- 正式 `apply` 需要 Node.js、Playwright、Chrome 和 与工程版本匹配的 LayaAir 3.4.1／3.4.2 引擎 JS，以执行实际渲染校验；不自动安装浏览器或改动目标工程配置。

在仓库根目录运行，使用现有虚拟环境即可：

```bash
.venv/bin/python -m pip install -e .
.venv/bin/python -m ninegrid --help
```

也可直接从独立仓库安装：

```bash
python -m pip install "git+https://github.com/eggbbq/loom_image9grid.git"
ninegrid --help
ninegrid-run --project /path/to/laya-project --search-dir assets/ui --dry-run
```

pip 安装后，`ninegrid-run` 使用当前 Python 环境，工程和引擎的相对路径基于当前工作目录；仓库 sh 仍基于仓库根目录。`ninegrid` 底层命令和 Python API 的搜索/输出路径仍相对 `--project`。实际 GPU 校验使用随包安装的 `engine_probe.mjs`，Playwright 可安装在目标 Laya 项目目录的 `node_modules`，或通过 `NINEGRID_PLAYWRIGHT_MODULE` 指定模块入口。引擎 JS 和浏览器需单独准备。

如只使用九宫格，可单独安装 `src/ninegrid/requirements.txt` 中的 NumPy/Pillow 依赖，并设置 `PYTHONPATH=src` 后使用 `python -m ninegrid`。不依赖 PSD、Photoshop 或原转换程序。所有相对输入/输出路径均基于 `--project`，不是当前工作目录。

## 一键处理整个目录

在仓库根目录运行：

```bash
./run_9grid.sh
./run_9grid.sh --search-dir assets/ui/商城_给程序 --dry-run
./run_9grid.sh --search-dir assets/icons assets/backgrounds --build-only
```

`run_9grid.sh` 仅保存参数数组并调用 `src/ninegrid/run_9grid.py`：

```bash
args=(
    --project          "laya-project"
    --search-dir       "assets/ui"
    --output           "assets/ninegrid_auto"
    --min-saving       0.20
    --mode             visual
    --max-mean-error   1.0
    --max-bad-pixel-ratio 0.01
    # --axis           vertical
    # --dry-run
    # --scan-only
    # --build-only
)
```

默认按顺序执行递归扫描、生成优化图、引擎截图校验、回写安全引用、CPU 与引用完整性验证。逐张打印可优化/跳过及原因，并显示事务编号和报告位置。遍历所有子目录中的 PNG（包含 `.PNG`），输出目录自动排除；不满足当前模式误差限制或引用要求的图会跳过。sh 已显式配置视觉近似；Python 和底层 CLI 未指定模式时仍默认为严格无损。

`src/ninegrid/run_9grid.py` 自动准备仓库 `.venv`，仅安装 NumPy/Pillow 依赖。项目路径以仓库根目录为基准；搜索、输出、场景目录以 Laya 项目为基准。搜索目录必须位于项目的 `assets/` 内，可使用绝对路径。此入口用于已存在的 Laya 项目，不调用 PSD 导出流程。

可直接运行 Python，也可追加参数覆盖 sh 中的配置：

```bash
python3 src/ninegrid/run_9grid.py --project laya-project --search-dir assets/ui
./run_9grid.sh --scan-only             # 仅递归分析，零写入
./run_9grid.sh --build-only            # 生成 PNG 和参数，不写场景，不启动浏览器
./run_9grid.sh --dry-run               # 从首次运行也可预览 PNG 和节点回写，项目零写入
./run_9grid.sh --help
```

`--search-dir` 也可写作 `--input`，一次可传多个目录。`--scene-root` 可限制场景扫描范围。正式回写继续遵守 GPU、备份和跳过规则；生成与回写是两笔事务，失败时可以用下方 `rollback` 命令按输出的事务编号撤销。双线性渗色等引擎验证失败时，已生成的 PNG 和报告保留，场景不提交。

输入、输出目录相同时自动原地写回，不需要额外开关：

```bash
# 原地覆盖原 PNG，保留资源路径和 UUID
./run_9grid.sh --search-dir assets/ui --output assets/ui

# 预演同目录写回，项目零写入
./run_9grid.sh --search-dir assets/ui --output assets/ui --dry-run

# 不同目录：另存优化图，保留原图
./run_9grid.sh --search-dir assets/ui --output assets/ninegrid_auto
```

原地模式分两步：build 在 `.ninegrid/originals/` 备份原图、在 `.ninegrid/staged/` 暂存优化图；apply 对原图与暂存图做 CPU/真实 Laya 校验，通过后将原 PNG、原 `.png.meta`、必要的场景修改和 manifest 一起作为事务提交。原 UUID、资源路径、显示尺寸不变；GImage 从纹理元数据读取九宫格，Image 同步节点 sizeGrid。失败不覆盖原图，rollback 可恢复覆盖前文件。`--build-only` 仅生成备份和暂存图，不覆盖源 PNG；随后用底层 `apply` 命令提交。

原地覆盖会影响所有共享引用，因此必须检查整个工程，不能限制 `--scene-root`。若同一张图存在不适合处理的引用，该图整体跳过。已有九宫格的图不重复缩小；工具生成的原地结果会校验备份和当前文件后跳过。之前另存模式产生的资源若仍被引用，会将安全引用转回原图的 UUID；之前生成的另存 PNG 保留，不自动删除。

删除旧资源目录或切换到其他 PSD 后，程序会自动剔除“源 PNG 已删除且没有场景引用”的旧记录，将其转入 manifest 的 `retiredItems`，备份保留，不恢复已删除文件，也不影响当前待处理图片。若已删除的资源仍被场景引用，会明确列出引用文件，避免把真实缺图误当成过期记录。扫描和 dry-run 只预览，正式构建或回写时才保存记录清理。

不同目录模式仍排除输出目录，避免重复优化。输出可以位于搜索目录内，例如搜索 `assets/ui`、输出 `assets/ui/optimized`；只排除该输出子目录。搜索路径位于输出目录内、但并未明确选择相同目录时仍给出配置提示。多个输入共用原地模式时，应全部位于该输出目录内。

## 多核并行

`src/ninegrid/run_9grid.py` 和 sh 默认 `--workers 0`，自动使用可用 CPU 核心；`--workers 1` 为单进程，`--workers 4` / `8` 可手动指定。sh 参数数组已注明用途和填写方法。

```bash
./run_9grid.sh --workers 0
./run_9grid.sh --workers 4 --dry-run
.venv/bin/python -m ninegrid build --project laya-project --workers 8
```

图片分析、PNG 编码、CPU 重建、渲染截图差异计算使用独立进程，并在一次运行中复用进程池。扫描到的候选直接用于裁图，不再次搜索。任务完成顺序可以不同，但输出按原资源顺序整理；备份、UUID/场景更新和事务提交仍在主进程中执行。浏览器 WebGL 截图按顺序采集，误差限制不变。运行时显示进度和各计算阶段耗时；进程数不写入导出设置，不影响图片内容或缓存有效性。

核心 Python API 的 `scan/build/apply/verify_project` 支持 `workers=N`，默认 1 以兼容现有调用；命令行入口默认自动多核。`spawn` 启动方式兼容 macOS；从自己的 Python 脚本调用多进程 API 时，应放在 `if __name__ == "__main__":` 入口内。

## 模式与误差参数

```bash
./run_9grid.sh --mode visual --dry-run  # 视觉近似预演
./run_9grid.sh --mode strict           # 覆盖 sh 的配置，使用严格无损
./run_9grid.sh --mode visual --axis vertical  # 仅上中下三段拉伸
```

- `strict`：整行/整列逐通道 RGBA 完全相同才收缩，包含透明像素的隐藏 RGB。CPU 重建和 GPU 截图均要求零差异，沿用原算法。
- `visual`：允许少量像素变化。以固定代表行/列寻找中段，允许跨过少量局部边框差异，再用整图重建检查原有预算，避免因单个不匹配位置提前截断；不使用相邻像素链式近似，防止整段渐变被误判；保留端部装饰，默认保留 8px 中心段及两侧各 1px 采样保护，降低双线性采样的接缝。候选间隙合并仍保留原有轮廓与平均误差检查，也保留未合并候选作为回退。

视觉模式默认预算如下，同时约束 CPU 重建和真实 Laya 渲染；不通过就不提交场景。预算随每张图片写入 manifest，apply 不会自动放宽。

| 参数 | 默认 | 含义 |
|---|---:|---|
| `--max-mean-error` | 1.0 | 预乘 Alpha 后的 RGBA 绝对通道误差均值，量程 0～255 |
| `--max-bad-pixel-ratio` | 0.01 | 任一通道误差超过下方阈值的像素占比最多 1% |
| `--pixel-error-threshold` | 32 | 计算上述占比的通道误差阈值 |
| `--center-size` | 8 | 收缩后保留的中心段像素数，范围 1～64 |
| `--edge-tolerance` | 2 | 候选中段可见轮廓起伏容许像素数，范围 0～8 |
| `--axis` | auto | `auto` 自动选单向或双向，`vertical` 只纵向，`horizontal` 只横向 |

平均误差不是每个像素的最大误差保证。透明度会参与比较；完全透明的隐藏 RGB 不计入视觉误差，但原始 RGBA 差异仍单独报告。因此视觉模式不能称为逐像素无损。引擎截图仅比较图片自身区域，避免画布空白稀释误差。

两个实际样例已在独立工程中完成原生 LayaAir 构建、纹理导入和 Chrome 截图验证，位置与显示尺寸不变：

| 图片 | 原图 → 优化图 | 纹理面积节省 | 方式 | 实际截图平均误差 / 255 |
|---|---|---:|---|---:|
| 图层 68 | 575×172 → 70×82 | 94.20% | 九宫格 | 0.406 |
| 组 37 | 738×1078 → 738×592 | 45.08% | 上中下三段 | 0.099 |

原始素材和严格模式拒绝结果保存在测试夹具中；前后预览、差异图及统计位于 `out/validation/ninegrid/visual-examples/`。`组 37` 的顶部、底部保留，中段木柱纹理会有近似变化。

## 使用

```bash
# 分析，递归扫描 assets，零写入；结果输出到终端
.venv/bin/python -m ninegrid scan --project laya-project --min-saving 0.20

# 可指定 PNG / 目录，也可用多个 --input；限制场景范围用 --scene-root
.venv/bin/python -m ninegrid scan --project laya-project \
  --input assets/ui --scene-root assets/ui

# 生成优化 PNG、.meta、manifest 和报告，尚不修改场景
.venv/bin/python -m ninegrid build --project laya-project \
  --output assets/ninegrid_auto --min-saving 0.20

# 预演引用回写，严格零写入，也不会启动浏览器
.venv/bin/python -m ninegrid apply --project laya-project --dry-run

# 先做 Laya/Chrome 对比，按 manifest 的模式和预算验收，通过后才提交场景修改
.venv/bin/python -m ninegrid apply --project laya-project

# CPU 重建、源图哈希、纹理 UUID、九宫格及已回写引用完整性
.venv/bin/python -m ninegrid verify --project laya-project

# 额外重新运行实际 WebGL 截图校验
.venv/bin/python -m ninegrid verify --project laya-project --engine

# 使用 apply 输出的 runId 撤销；加 --dry-run 可预演
.venv/bin/python -m ninegrid rollback --project laya-project --run-id <run-id>
```

所有命令支持 `--dry-run`。`scan` 本身始终零写入。`build --dry-run` 不创建 PNG、报告或目录。

`--engine-dir` 可为 `apply/verify` 指定引擎目录，其中需包含 `laya.core.js`、`laya.webgl_2D.js`、`laya.ui.js`、`laya.ui2.js`。也可设置 `NINEGRID_ENGINE_DIR`；macOS 会尝试已安装 LayaAir IDE 的引擎库。实际加载后仍检查版本号，不能用其他版本冒充目标工程版本。

Playwright 从目标工程、当前工作目录或本仓库的 `node_modules` 查找，也可设置 `NINEGRID_PLAYWRIGHT_MODULE` 为其可加载模块路径。默认使用已安装 Chrome；`NINEGRID_BROWSER` 可设置其他已安装的 Playwright channel。

## 原生回写方式

| 节点 | 修改内容 | 保持不变 |
|---|---|---|
| `Image` | 另存时更新 `skin`，原地时保持资源引用；`sizeGrid` 字符串，如 `16,16,16,16,0` | 布局和其他字段 |
| `GImage`（New UI） | 另存时更新 `src`，原地时保持资源引用；生成纹理 `.png.meta` 的 `importer.sizeGrid` 数组 | 节点上不添加旧 UI 的 `sizeGrid`；布局和其他字段保持原样 |

九宫格顺序为 **top, right, bottom, left, repeat**，仅使用 `repeat=0`。UUID 引用使用 `res://<uuid>`；原生相对路径引用也可识别，按原引用形式替换。另存模式为每张源图生成固定路径和确定 UUID；原地模式保留原路径和 UUID，避免链式优化。

GImage 资源级九宫格格式来自本机 IDE 内置纹理元数据；真实 CLI 构建、资源导入和运行截图已另行验证，见 [兼容性与证据](compatibility.md)。

## 安全与限制

- 严格模式逐像素、逐通道比较完整 RGBA；视觉模式按显式预算验收。两者都不做调色、量化或图片合并。
- 默认最小纹理面积节省率为 20%。600×300 → 33×33 的面积节省为 99.395%，不代表文件体积或 DrawCall 同比下降。
- 必须显式保留原显示尺寸；节点宽高与 PNG 尺寸不同，默认跳过。自动取源尺寸、已有九宫格、动态组件/控制器、布局约束、旋转缩放、特殊采样等引用也跳过。
- 当前只修改已验证的静态 `Image/GImage`。`GLoader`、按钮状态图、自定义父类行为、继承预制体覆盖等不推测处理；会给出原因。
- 自动图集配置本身不再导致整目录跳过。已验证原图在普通自动图集内、优化图单独导入的链路，另存模式不修改原图和原图集配置；原地模式只覆盖 PNG 和对应纹理元数据，检测到图集缩放、旋转、量化或过滤覆盖等未支持变换时仍跳过。动画 PNG、多帧/旋转纹理、16-bit PNG、mipmap、PNG8、导入时缩放等仍跳过。
- **双线性过滤可能让 1px 中心带产生渗色。** 严格模式下，此时 CPU 验证虽通过，实际 GPU 校验仍会失败，整笔 apply 不提交。视觉模式保留采样保护段，并按预算验收 GPU 结果。不会自动改过滤方式或扩大预算。
- 全零边距的 1×1 结果在引擎中属于普通拉伸效果，不能据此宣称存在非零边框九宫格。
- 任何 PNG/元数据变化会使旧 manifest 失效。视觉算法版本记录在 settings 中。原地模式对已完成的图验证后跳过；需要重新优化时先撤销原地 apply，恢复原图。已经应用后需要重建时，先 rollback 旧 apply 事务（无需先撤销 build），再重新运行。场景被外部修改时，事务拒绝覆盖。
- 所有写入限制在项目内，拒绝 `..` 和符号链接路径；未知版本、无可解析场景时退出。

## 产物和撤销

```text
项目/
├── assets/ui/panel.png                 # 另存模式原图不变；原地模式在 apply 后覆盖
├── assets/ninegrid_auto/ui/panel.png   # 优化 PNG
├── assets/ninegrid_auto/ui/panel.png.meta
├── assets/.../*.lh 或 *.ls             # apply 后更新引用
└── .ninegrid/
    ├── originals/                      # 原地模式原始 PNG 备份
    ├── staged/                         # 原地模式暂存优化 PNG
    ├── manifest.json
    ├── report.json / report.md
    ├── apply-report.json
    ├── validation/                     # 前后截图、差异统计及必要的 diff
    └── backups/<run-id>/
        ├── transaction.json
        └── files/...                  # 修改前的原始文件
```

`build/apply/rollback` 均使用备份、哈希检查和逐文件原子替换；异常时恢复本笔已修改文件。`apply --dry-run` 列出精确文件和节点，不产生备份或截图。重复 apply 不再次修改文件或生成资产。

撤销 apply 恢复 apply 之前的场景、manifest 和报告；原地模式还恢复原 PNG 与纹理元数据。build 已生成的优化图、暂存图和备份会保留。需要一并撤销生成文件时，可在撤销 apply 后再撤销对应 build 事务。若某文件在事务后被手工编辑，rollback 拒绝覆盖；备份仍在，便于核对。突然断电留下 `pending` 事务时，保留现场与备份，不自动猜测覆盖用户后续改动。

Manifest 中 `columnBand/rowBand` 是候选区间的半开边界。严格模式保留首行/列，`removedColumns/removedRows` 记录随后删除的位置和数量。视觉模式另外记录 `sourceCenterX/Y`、`samplingGuard`、`retainedCenterSize` 和 `sliceMode`；其 removed 字段记录替换区间起点及净减少数量，不表示单一连续删除段。

## 测试

```bash
# CPU、项目回写、事务、路径保护与撤销
.venv/bin/python -m pytest -q tests

# 必须实际运行的 GPU 测试：不使用渲染 mock
NINEGRID_ENGINE_TESTS=1 .venv/bin/python -m pytest -q tests/test_engine.py tests/test_inplace.py

```

测试覆盖严格模式、视觉误差预算、两个实际 PNG、单向/双向收缩、渐变链式误判、轮廓保护、透明 RGB、错误重建拒绝、原生 UUID/路径引用、dry-run、幂等、事务与撤销。引擎测试实际渲染 Image/GImage，覆盖点采样、双线性、严格渗色拒绝及真实样例近似通过。

商城样例在视觉模式下可识别候选；严格模式继续按零差异规则处理。原生构建验收在 `out/validation/ninegrid/` 的隔离工程完成；实际商城按配置使用另存或原地模式，独立发行不包含原 PSD 导出器。
