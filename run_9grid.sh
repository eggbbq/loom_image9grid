#!/usr/bin/env bash
set -euo pipefail

# 用法：./run_9grid.sh；修改下面 args 中的值，或在命令后追加参数覆盖配置。
# 示例：./run_9grid.sh --search-dir "assets/ui/商城_给程序" --dry-run
# 路径含空格或中文时请加双引号。开关参数单独写，不填 true / false。
# 取消以 # 开头的参数行注释即可启用；普通说明注释无需删除。
args=(
    # Laya 工程目录：相对本脚本目录，或填绝对路径；目录内需有 *.laya 文件。
    --project          "laya-project"

    # 搜索目录或单张 PNG：相对上面的工程目录，必须在 assets 内；目录递归扫描。
    # 多个目录写在同一参数后，例如：--search-dir "assets/ui" "assets/icons"
    --search-dir       "assets/ui"

    # 输出目录：相对工程目录；与 --search-dir 相同时自动覆盖原 PNG，无需其他参数。
    # 同目录：原 PNG 路径和 UUID 保留，更新 meta 九宫格及引用节点，先备份、验证后提交。
    # 不同目录：按原资源结构另存优化图，并回写引用；原图保留，扫描时排除输出目录。
    # 示例：搜索 assets/ui、输出 assets/ui = 原地写回；输出 assets/ninegrid_auto = 另存。
    --output           "assets/ui"

    # CPU 计算进程数：填非负整数；0 自动使用可用核心，1 单进程，4/8 指定进程数。
    # 图片分析、PNG 编码和 CPU 校验并行；备份、引用修改和事务提交仍集中执行。
    --workers          0

    # 最低纹理面积节省率：填 0～1 的小数，0.20 = 至少节省 20%；非 PNG 文件体积。
    --min-saving       0.20

    # 模式：visual = 允许预算内像素变化；strict = RGBA 和引擎截图均严格无损。
    # 以下误差/中心段/轮廓/方向参数只对 visual 生效；Python 入口默认 strict。
    --mode             visual

    # 平均误差上限：填 0～255 的数，默认 1.0；按预乘 Alpha 的 RGBA 通道统计。
    # 它是整图通道误差均值，不表示每个像素最多只能变化 1。
    --max-mean-error   1.0

    # 严重误差像素占比上限：填 0～1 的小数，0.01 = 最多 1%。
    # CPU 重建和真实 Laya 渲染都需同时满足此限制与上面的平均误差限制。
    --max-bad-pixel-ratio 0.01

    # 严重误差阈值：填 0～255 的整数，任一通道误差 > 此值即计为严重误差像素。
    --pixel-error-threshold 32

    # 收缩后保留的中心段宽/高：填 1～64 的整数；额外各加 1px 保护双线性采样。
    --center-size      8

    # 候选中段可见轮廓起伏容差：填 0～8 的整数像素；0 要求边界位置相同。
    --edge-tolerance   2

    # 拉伸方向：auto 自动选单向/双向；horizontal 只左右三段；vertical 只上中下三段。
    --axis             auto

    # 可选场景范围：相对工程目录，可填多个 .lh/.ls 文件或目录；默认扫描整个 assets。
    # 同目录写回需检查全部共享引用，请保持此参数注释。
    # --scene-root     "assets/ui" "assets/Scene.ls"

    # 可选引擎 JS 目录：相对本脚本目录或绝对路径，需含 core/webgl_2D/ui/ui2 库。
    # 默认查找工程 engine/libs 或已安装的 LayaAir IDE；正式回写需 LayaAir 3.4.1。
    # --engine-dir     "/Applications/LayaAirIDE.app/Contents/Resources/engine/libs"

    # 可选开关：预演生成和引用回写，不写项目、不启动引擎校验。
    # --dry-run

    # 可选开关：只扫描并打印候选，零写入；与 --build-only 二选一。
    # --scan-only

    # 可选开关：生成优化 PNG、meta、参数和报告，不改场景、不启动引擎校验。
    # 同目录时只写 .ninegrid 内的暂存图和原图备份；原 PNG/meta 在正式 apply 时才覆盖。
    # 不填 scan/build 开关时：生成 → 实际引擎校验 → 回写安全引用 → 验证。
    # --build-only
)

# 复杂逻辑在 Python 中；命令行追加的同名参数覆盖上面的配置。
exec python3 "$(dirname -- "$0")/src/ninegrid/run_9grid.py" "${args[@]}" "$@"
