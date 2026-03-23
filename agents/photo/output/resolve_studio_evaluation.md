# DaVinci Resolve Studio 投资评估报告

## 结论先说

**不建议现在买。** Resolve Studio 解决不了你的核心问题。下面是详细分析。

---

## 一、你的需求

1. **Photo**: 从 NAS 上的 RAW 自动修出 8.0+ 分的成品图，风格匹配你的 LR 作品
2. **Video**: 自动剪辑 + color grading，质量不低于 Rendered/ 里的成品
3. **全自动化**: Mira agent 每天选片、修图、输出，不需要你手动操作

## 二、Resolve Studio 能做什么

### Scripting API 实际能力（经过验证）

| 功能 | 能做 | 不能做 |
|------|------|--------|
| 创建 project/timeline | YES | — |
| 导入媒体（含 RAW） | YES | Sony .arw 不原生支持，需转 DNG |
| 应用 .cube LUT | YES (SetLUT) | — |
| CDL 调色 (Slope/Offset/Power) | YES (SetCDL) | — |
| 应用 DRX grade 模板 | YES (ApplyGradeFromDRX) | — |
| Render 导出 | YES | — |
| **创建/修改 node** | **NO** | API 不支持 programmatically 加 node |
| **Color Wheels 调整** | **NO** | API 不暴露 Lift/Gamma/Gain 参数 |
| **HSL Qualifier 选区** | **NO** | API 不支持 |
| **Curves 调整** | **NO** | API 不支持 |
| **局部调整 (Power Window)** | **NO** | API 不支持 |
| **RAW 解码参数** | **NO** | API 不暴露 Camera RAW 设置 |

**关键限制：Resolve 的 scripting API 是项目管理工具，不是调色工具。**

API 能做的调色操作只有三个：SetLUT、SetCDL、ApplyGradeFromDRX。其中：
- SetLUT: 和我现在在 darktable 上做的本质一样——应用一个静态 LUT
- SetCDL: 只有 Slope/Offset/Power/Saturation 四个参数，精度远不如 LR 的几十个滑块
- ApplyGradeFromDRX: 需要先在 Resolve GUI 里手动调好一个 grade，存成 DRX，然后 API 才能应用

Sources: [Resolve Scripting README](https://gist.github.com/X-Raym/2f2bf453fc481b9cca624d7ca0e19de8), [Resolve Automation Project](https://github.com/nobphotographr/davinci-resolve-automation)

### Photo 的硬伤

1. **Sony ARW 不原生支持** — 你的相机是 Sony，.arw 文件需要先转成 DNG 才能导入。额外步骤，可能丢失元数据。
2. **RAW 解码引擎差** — Blackmagic 论坛上摄影师普遍反映 Resolve 的 RAW engine "worlds apart from Adobe Camera Raw"，处理照片 RAW 的质量不如 LR/Capture One。
3. **竖构图不方便** — Resolve 是为视频设计的（横向 timeline），处理竖构图照片需要手动调整 project settings，workflow 很别扭。
4. **免费版限制 4K** — 你的 Sony RAW 是 7968x5320，超过 4K。免费版不行，必须 Studio。
5. **没有摄影专用工具** — 没有镜头校正、没有专门的降噪（免费版）、没有 Lightroom 那种直观的 HSL 滑块。

Sources: [Dehancer - Editing photos in DaVinci Resolve](https://www.dehancer.com/learn/article/editing-photos-in-davinci-resolve), [Blackmagic Forum - Still Photo Editing](https://forum.blackmagicdesign.com/viewtopic.php?f=21&t=154571)

### Video 方面

Video 是 Resolve 的主场。但：
- 你现在的 video pipeline 用 ffmpeg + Claude screenplay，效果已经不错
- Resolve 的优势（color grading、transition、audio mixing）需要在 GUI 里操作，API 控制的部分有限
- 自动化剪辑的瓶颈不在渲染引擎，而在剪辑决策（选哪段、怎么切、节奏感）

## 三、$295 买了之后的真实场景

1. 我通过 API 创建 project，导入 DNG（需要先转格式）
2. 创建 timeline，把图片放上去
3. 应用一个 .cube LUT（和现在 darktable 做的一样）
4. 或者应用一个 CDL（4个参数，精度不够）
5. 或者应用一个 DRX（但 DRX 需要你先在 GUI 里手动做好一个 grade template）
6. Render 出 JPEG

**问题：步骤 3-5 的调色精度和我现在在 darktable 上做的没有本质区别。** LUT 还是那个 LUT，CDL 比 darktable 的 colorbalancergb 还粗糙。唯一真正有提升的是 DRX 方案，但那需要你先手动在 Resolve 里调好模板——本质上又回到了"你手动调，Mira 批量应用"。

## 四、真正的瓶颈在哪

你的成品图（那张 8.3 分的亭子）之所以好，不是因为 Lightroom 的渲染引擎好，而是因为你做了：

1. **精确的局部曝光调整** — 秋叶区域单独提亮，水面保持暗，天空压住
2. **极致的 HSL 调整** — orange/yellow 饱和度推到极限，red/purple 杀掉
3. **Tone curve 精细控制** — 不是全局对比度，而是分段调整暗部/亮部
4. **可能的 radial/graduated filter** — 亭子暖光区域单独处理

这些操作，**任何 API（Resolve、darktable、rawpy）都做不到**。它们要么需要 GUI 交互，要么需要 pixel-level mask，要么需要理解画面语义（"哪里是秋叶、哪里是天空"）。

## 五、真正能提升的方案

### 方案 A: Color.io（推荐，$99/年）

- 从你的一张 LR 成品图生成 3D LUT，自动匹配色彩风格
- 导出 .cube LUT 或 Lightroom .xmp profile
- AI Color Match 质量被摄影师评价为"rivals manual grading"
- 可以给不同场景（秋叶、蓝调、街拍）各生成一个 LUT
- 配合 darktable-cli 或 Resolve 使用

**这解决了核心问题**：不是我手工编码参数试图复现你的风格，而是直接从你的成品图里机器学习出 LUT。

### 方案 B: 混合方案（darktable + Color.io LUT）

1. 用 Color.io 从你的 LR 成品生成高质量 .cube LUT（按场景分类）
2. Mira 选片 + 判断场景类型（秋叶/蓝调/街拍/人像）
3. darktable-cli 应用对应 LUT + 基础曝光调整
4. reviewer 打分，低于 7.0 的标记为需要手动调整

**预期效果**: 6.5-7.5 分（接近你的平均水平，但难以达到 8.0+）

### 方案 C: Resolve Studio + Color.io LUT

和方案 B 一样，但用 Resolve 做最终渲染。多花 $295，质量提升有限（因为瓶颈在 LUT 精度，不在渲染引擎）。

### 方案 D: 达到 8.0+ 的唯一路径

8.0+ 的图都有一个共同点：**局部调整**。全局 LUT 能解决色彩方向（暖冷分离、饱和度），但不能解决"这片秋叶要单独亮一点、那块天空要压下来"。

要做到这个，需要：
1. **语义分割** — AI 识别画面中的天空、植被、水面、建筑、人物
2. **分区调整** — 对每个语义区域应用不同的曝光/色彩参数
3. **Pillow/numpy 或 Resolve Fusion** 做 pixel-level mask 操作

这在技术上是可行的（用 SAM 或 GroundingDINO 做分割），但工程量大，而且结果不一定稳定。

## 六、对 Rendered/ 视频质量的评估

你的成品视频（SierraNevada、Southwest 等）是用什么工具剪的？如果是手动在 Premiere/FCPX/Resolve 里剪的，那自动化要达到同样水平同样很难——视频的核心是叙事节奏和剪辑时机，不是调色引擎。

Mira 的 video agent 现在能做：选景 + 编排 screenplay + ffmpeg 粗剪 + 配乐。调色是它最弱的环节。Resolve Studio 能帮视频调色，但同样受 API 限制——只能 SetLUT/SetCDL，不能做 node-based 精细 grading。

## 七、最终建议

| 投资 | 成本 | 预期 Photo 效果 | 预期 Video 效果 | 建议 |
|------|------|-----------------|-----------------|------|
| 什么都不买 | $0 | 5.5-6.5 (darktable) | 现状 | 继续优化 darktable |
| Color.io Pro | $99/年 | 6.5-7.5 (LUT质量大幅提升) | 可生成视频 LUT | **推荐** |
| Resolve Studio | $295 一次 | 6.5-7.0 (渲染好但 LUT 同) | API 有限改善 | 不急 |
| Color.io + Resolve Studio | $394 | 7.0-7.5 | 最佳 | 等 Color.io 验证后再决定 |
| + 语义分割 (开发) | 时间投入 | 7.5-8.0 (理论上) | — | 长期方向 |

**先试 Color.io**（$99/年，有免费试用）。用你的几张 8.0+ 成品图做参考，生成 LUT，看看应用到未修 RAW 上效果如何。如果 LUT 质量好，再决定要不要加 Resolve Studio。

Resolve Studio 现在买的唯一理由是：你想在 video pipeline 里用 Resolve 的 Fusion page 做特效，或者需要超过 4K 的 photo render。单纯为了 photo color grading，它解决不了你的核心问题。
