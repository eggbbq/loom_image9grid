> 本文记录迁移前在 `loom_psd2ui` 仓库完成的历史兼容性验收。`out/validation/ninegrid/` 中的大型截图与工程产物未随本库迁移；测试代码、PNG 和原生序列化夹具已保留，可在准备好引擎后重新验证。下述具体尺寸和误差属于当时的算法及配置。

# 已验证的兼容性

## LayaAir 3.4.1

只允许 `*.laya` 中 `version: "3.4.1"`，原生 `.lh/.ls` 的 `_$ver: 1`、`_$id`、`_$type`、`_$child` JSON 结构。其他版本拒绝写入，不尝试猜测格式。

### 格式证据

- `tests/fixtures/laya/Image.before.lh` 和 `Image.with-grid.lh` 由实际 LayaAir 3.4.1 的 Scene 进程通过 `IEditorEnv.HierarchyWriter.write()` 生成。两者仅相差 `sizeGrid: "3,4,5,6,0"`。
- `GImage.lh` 同样由原生序列化器生成，使用 `src` 和 `autoSize: false`。
- `native-ninegrid.png.meta` 直接复制自已安装 IDE 的 `internal/UI/images/button_normal.png.meta`，证明 `importer.textureType: 2` 与 `importer.sizeGrid: [top,right,bottom,left,0]` 的实际格式。
- 路径、版本、来源 SHA256 和生成方式记录在 `tests/fixtures/laya/provenance.json`。

GUI 自动化入口在本次环境不可用，因此没有声称完成手工 Inspector 操作；以上使用的是实际 IDE 原生序列化器。另通过 CLI 构建与浏览器实际运行核验资源级九宫格，不把 CPU 模拟作为引擎验收。

### 严格模式渲染证据

`out/validation/ninegrid/demo/` 为可检查的独立原生测试项目。600×300、四边 16px、完整 RGBA 的点采样样例压缩为 33×33：Image 的 `skin + sizeGrid`、GImage 的 `src + 纹理元数据` 均有实际写回，并在 Chrome WebGL 中逐像素对比。

`out/validation/ninegrid/native-project/` 为复制现有工程配置后建立的隔离工程，场景通过 `tests/capture_fixture.ts` 调用 `IEditorEnv.HierarchyWriter.writeScene()` 创建。针对 New UI 执行真正的 CLI Web 构建，加载构建后的 Scene 和资源表，检查实际纹理尺寸、九宫格、节点位置与显示尺寸，并保存前后截图。未手动注入该构建运行时的纹理九宫格。

`tests/test_engine.py` 额外测试水平/垂直单向收缩、零边距 1×1，并验证严格模式下双线性渗色会阻止整笔场景回写。严格模式截图要求零差异。

### 视觉近似与原图集验证

`out/validation/ninegrid/visual-project/` 从用户工程复制，保留其原生 AutoAtlas 配置。`tests/capture_visual.ts` 用原生序列化器建立包含两个实际样例的 GImage 场景，分别在优化前后执行完整 CLI Web 构建和 Chrome 运行。`tests/native_visual_render.mjs` 检查节点位置、显示尺寸、导入后的纹理尺寸和九宫格，并截取每张图片区域。

- `图层 68`：575×172 → 70×82，grid `[37,31,37,31,0]`。原图实际位于 575×342 自动图集内；优化后单独导入。实际截图平均误差 0.406254/255，通道误差超过 32 的像素占比 0.0455%。
- `组 37`：738×1078 → 738×592，grid `[395,0,189,0,0]`，仅纵向中段收缩。实际截图平均误差 0.099425/255，通道误差超过 32 的像素占比 0.0900%。
- 两者过滤方式均为原有 Bilinear，显示尺寸和位置不变；预算为平均误差 ≤1/255、上述像素占比 ≤1%。这是近似结果，不是逐像素无损。

前后截图、左右对照、8 倍放大差异图和完整 JSON 统计在 `out/validation/ninegrid/visual-examples/`。原始 PNG 夹具和来源哈希在 `tests/fixtures/png/`。原图和原图集配置不修改；实际商城已更新生成的优化图与安全引用，显示位置、尺寸保持原样。

`engine_probe.mjs` 是正式 apply 的提交前验证器：用真实引擎加载 PNG，按原导入过滤方式渲染 Image/GImage，分别保存原图和优化图截图。仅比较图片区域，严格模式零差异，视觉模式遵守 manifest 中显式预算。其数据 URL 加载路径显式赋予已经验证格式的纹理九宫格；上述原生构建验收另外验证实际 importer 导出与继承链路。

### 有意不支持的情况

GLoader 的资源适配/动画、动态 TS 换肤、自定义组件、继承预制体覆盖、未知父类型行为、布局驱动尺寸、已有九宫格、显示尺寸不同于源纹理、非单位旋转缩放、特殊采样或导入变换，均默认跳过或拒绝。不编辑任何 TS 业务源码。

自动图集配置本身不再拦截整个目录。配置哈希参与验证，原图保持原状；包含缩放、旋转、量化或过滤覆盖等未支持变换时继续跳过。

严格模式遇到 1px 中心带双线性渗色时拒绝回写；视觉模式保留中心段和采样保护，并检查误差预算。两种模式均记录差异、保留截图，不自动改变过滤方式或放宽预算。

### 同目录原地覆盖验证

`out/validation/ninegrid/inplace-project/` 用相同输入/输出目录实际执行完整流程，再做原生 CLI 构建。`图层 68` 和 `组 37` 的资源路径与 UUID 保持原样，原 PNG 分别替换为 70×82 和 738×592；节点仍显示为 575×172 和 738×1078，位置不变。两张优化图经原 AutoAtlas 配置一起打入 738×762 图集，运行时仍正确继承各自的纹理九宫格。

前后截图与误差统计位于 `out/validation/ninegrid/inplace-examples/`。`tests/test_inplace.py` 覆盖暂存阶段不覆盖、真正 GPU 验证、整笔提交、共享不安全引用跳过、UUID 保留、重复运行、并发编辑保护、失败恢复及 rollback；同时测试之前的另存引用转回原 UUID。
