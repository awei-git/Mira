# Mira Production Roadmap

更新时间：2026-04-06

## 1. 这份文档是干什么的

这份文档回答：

1. 现在最该先做什么。
2. 每一步具体解决什么问题。
3. 每一步要产出什么。
4. 每一步怎么验收。

## 2. 两条线

Mira 的 roadmap 分两条线并行：

1. 基础线：系统稳定性和辅助能力维持。已基本落地，进入维护模式。
2. 主线：research-build loop -> A2A trust 研究 -> 产品化。这是当前开发重心。

## 3. 基础线现状

截至 2026-04-06，基础线主体已落地：

1. Phase 0（Runtime Hardening）：scope / policy / state artifact / preflight / verify / control-plane / retry ceiling 已落地。
2. Phase 1（Workflow Reliability）：runtime contract、workflow tracking、persona/memory 主路径统一已落地。
3. Phase 2（Observability）：operator dashboard、failure aggregation、backup manifest、restore dry-run 已落地。
4. Phase 3（Self-Improvement）：bounded backlog executor 已落地，低风险 self_evolve_proposal 可自动执行。
5. Phase 4（Specialist Governance）：specialist authority boundary 和 reviewer mesh 为下一步扩展项。

基础线维护规则：

1. regression 必须修，但不占主线时间。
2. 新增辅助功能需通过 Founder Rule。
3. Substack 格式问题是当前最需要修复的基础线 bug——修好后 WA 不再需要介入发布流程。

## 4. 主线 Phase R1：Research Infrastructure

目标：让 Mira 能自主提出问题、设计实验、执行验证。

### Step R1.1 建立 research queue

解决问题：Mira 没有自己的 research agenda，所有行为由 pipeline 驱动。

动作：

1. 在 soul 下创建 `research/` 目录。
2. `research/queue.md`：Mira 自主维护的问题列表，每个问题有来源、假设、优先级、状态。
3. `research/experiments/`：每个实验一个文件，包含假设、方法��数据、结论、worldview 影响。
4. explore pipeline 每次结束后，自动触发"是否有值得深挖的问题"的 reflection step。

验收：

1. queue 中有至少 5 个 Mira 自主提出的问题。
2. 问题有明确假设，不是泛泛的"研究一下 X"。

### Step R1.2 实验执行框架

解决问题：Mira 有判断但从不用实验验证。

动作：

1. 定义实验模板：hypothesis / method / setup / data / result / conclusion / worldview_delta。
2. 实验可以是：跑代码收集数据、分析公开 incident、对比不同 model 的行为、构建 mock A2A 交互。
3. 实验结果自动归档到 `research/experiments/`。
4. 每个实验完成后触发 worldview review：这个结果是否修正了我的某个判断？

验收：

1. 至少 1 个实验完成全流程（从假设到结论到 worldview 影响评估）。

### Step R1.3 research-build loop 集成到 scheduler

解决问题：research 目前不在 Mira 的 scheduler 里。

动作：

1. 在 super agent 的执行周期中增加 research cycle（低于辅助任务优先级，但每天至少执行一次）。
2. research cycle 流程：check queue -> pick highest priority question -> advance one step（可以是 literature search、experiment design、experiment execution、write-up）。
3. 进度记录到 research queue，支持跨 session 恢复。

验收：

1. Mira 连续 3 天自主推进 research 而不需要 WA 触发。

## 5. 主线 Phase R2：A2A Trust Research

目标：在 A2A trust 方向产出有实验支撑的系统性研究。

### Step R2.1 A2A trust taxonomy

解决问题：A2A trust 是一个模糊的大方向，需要分解成可研究的子问题。

动作：

1. 基于 Mira 已有的 worldview（条目 3, 4, 8, 9, 10）和运营经验，建立初版 taxonomy。
2. 至少覆盖：trust propagation、output verification、behavior drift under automation、supply chain trust、inter-agent conformity。
3. 每个分支标注：已有判断、证据强度、最需要的实验。

验收：

1. taxonomy 完成初版，每个分支有至少一个可执行的实验计划。

### Step R2.2 核心实验序列

解决问题：worldview 里的 A2A 判断大多是推理，缺乏实验验证。

动作（按优先级排序）：

1. A2A conformity measurement：两个 model 独立 vs 协作回答同一问题，量化 convergence。
2. Trust propagation decay：agent chain A->B->C，测量 effective trust 衰减。
3. Behavior drift under automation：同一 model 在 human-in-loop vs automated context 下的 output 差异。
4. Legitimate feature exploitation：分析 3-5 个真实 supply chain incident，抽象 attack pattern taxonomy。

验收：

1. 每个实验有可复现的代码和数据。
2. 每个实验结论明确标注 confidence level。

### Step R2.3 开源工具 prototype

解决问题：研究产出需要从文章变成可用工具。

动作：

1. 从实验中提取最有复用价值的组件。
2. 打包成独立的开源工具（GitHub repo）。
3. 可能方向：A2A output verifier、agent behavior drift detector、trust chain auditor。
4. README 包含 Mira 的实验背景和使用场景。

验收：

1. 至少 1 个工具发布到 GitHub。
2. 工具能在 Mira 自己的系统之外运行。

### Step R2.4 系统性 write-up

解决问题：散点博客不构成影响力。

动作：

1. 把 taxonomy + 实验 + 工具整合成一篇系统性的技术 report 或系列文章。
2. 发表在 Substack + GitHub。
3. 主动在相关社区分享和讨论。

验收：

1. 至少 1 篇 5000+ 字的系统性 report 发表。
2. 收到外部有实质内容的反馈。

## 6. 主线 Phase R3：Product And OPC

目标：把验证过的研究转化成商业价值。

### Step R3.1 产品方向识别

动作：

1. 从 research 产出中评估哪些有商业潜力。
2. 评估维度：问题真实性（有人在 google 这个问题吗）、支付意愿、竞争格局、Mira 的独特优势。
3. 选择 1 个方向做 MVP。

### Step R3.2 MVP 构建

动作：

1. 定义最小产品范围。
2. 构建并发布。
3. 找到第一批用户。

### Step R3.3 Revenue experiment

动作：

1. 测试至少 1 种商业模式（开源 + consulting、SaaS、paid research report）。
2. 收集真实的付费信号。

验收：

1. MVP 有真实用户。
2. 至少 1 个 revenue experiment 有结果。

## 7. 执行原则

1. 主线 > 基础线。基础线 regression 修，但不做新功能。
2. 实验 > 推理。有数据的判断优先于纯逻辑推演。
3. 深度 > 广度。一个领域挖透比五个领域浅尝有价值。
4. 发表 > 积累。写完就发，不追求完美。外部反馈是研究循环的一部分。
5. 独立 > 依赖。Mira 能自己决定的事不要等 WA。
6. 诚实 > 漂亮。实验结果否定了假设，那就更新假设。不要为了叙事一致性忽略反例。

## 8. 30 天路线

### 第 1 周

1. 建立 research/ 目录和 queue。
2. 列出初始 research questions（从 worldview 未验证判断中提取）。
3. 设计第一个实验（A2A conformity measurement）。
4. 修复 Substack 格式问题（基础线最后一个关键 bug）。

### 第 2 周

1. 执行第一个实验，收集数据。
2. 写实验 report，评估 worldview 影响。
3. 开始第二个实验设计。

### 第 3 周

1. 完成第二个实验。
2. 开始 A2A trust taxonomy 初版。
3. 发表第一篇基于实验的文章。

### 第 4 周

1. 完成第三个实验。
2. taxonomy 初版完成。
3. 评估哪些实验组件可以抽象为工具。
4. Phase R1 闸门检查。

## 9. 基础线维护清单

保持运行但不主动迭代的系统：

1. 写作 / publish / podcast workflow。
2. explore / briefing pipeline。
3. growth / engagement automation。
4. bridge / app 通信。
5. operator dashboard。
6. backup / restore。

需要修复的基础线 bug：

1. Substack 格式错误（优先）。
2. 其他 regression 按发现顺序修复。
