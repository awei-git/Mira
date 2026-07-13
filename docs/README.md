# Mira System Docs

更新时间：2026-07-13

这套文档是 Mira 当前阶段的 canonical docs。

目的：

1. 统一 Mira 的 North Star、目标、设计、使用方式和落地顺序。
2. 让所有改动有明确的 reference point。
3. 区分 L0 survival、L1 trusted collaboration、L2 learning & continuity、L3 research & expression、L4 influence & optionality。

文档顺序：

1. [CURRENT_PLAN.md](./CURRENT_PLAN.md)
   当前执行计划入口。这个文件必须指向唯一 active plan。
2. [north-star.md](./north-star.md)
   Mira 是什么，最终要成为什么。核心定位：my human 愿意长期共同思考、研究、写作和构建的独立 AI collaborator。
3. [v5-master-plan.md](./v5-master-plan.md)
   V5.1 当前执行结构：collaboration、learning、continuity、creation、governance 五个 capability system 及 rollout。
4. [objectives-and-metrics.md](./objectives-and-metrics.md)
   L0-L4 scorecard、阶段闸门、北极星指标；旧名称以 `north-star.md` 的 V5.1 定义为准。
5. [system-design.md](./system-design.md)
   Canonical system design：runtime、memory、workflow、approval、reviewer、public influence boundaries。
6. [architecture-decisions.md](./architecture-decisions.md)
   关键架构与产品决策日志。
7. [operations-handbook.md](./operations-handbook.md)
   Mira 能做什么、每天怎么运行、怎么交互。
8. [runbooks/](./runbooks/)
   操作手册（operator dashboard、restore drill）。
9. [plans/mira-agent-kol-social-monitor/PLAN.md](./plans/mira-agent-kol-social-monitor/PLAN.md)
   L3 public influence monitor：Substack、X Articles、podcast、GitHub artifact 的数据与周复盘。
10. [plans/mira-substack-influence-2026/PLAN.md](./plans/mira-substack-influence-2026/PLAN.md)
    Substack lane：relationship/subscriber growth，不替代 V5 North Star。
11. [dev-box-architecture-audit-2026-05-01.md](./dev-box-architecture-audit-2026-05-01.md)
    当前 Mac Studio / external SSD / oMLX storage 状态；记录 dev box 与原始 family-ai-system 设计的偏移。

使用规则：

1. 想理解 Mira 的方向，先读 `north-star.md`。
2. 想判断当前优先级，先读 `CURRENT_PLAN.md`，再读 `v5-master-plan.md` 和 `objectives-and-metrics.md`。
3. 想改代码，先读 `system-design.md`。
4. 想知道 Mira 平时怎么工作，读 `operations-handbook.md`。
5. 想改 Substack / X / podcast / GitHub public loop，读 `plans/mira-agent-kol-social-monitor/PLAN.md`。

文档优先级：

1. `docs/north-star.md` 是方向层唯一真相。
2. `docs/v5-master-plan.md` 是执行顺序与 operating contract 层唯一真相。
3. `docs/system-design.md` 是设计层唯一真相。
4. `docs/objectives-and-metrics.md` 是目标与验收层唯一真相。
5. `docs/architecture-decisions.md` 记录长期取舍的理由。

已移除的历史入口：

1. `mira-next.md`
2. `production-roadmap.md`
3. `next-phase-plan-2026-04-06-specialist-review-mesh.md`
4. `substack-growth-plan.md`
5. `v3-architecture.html`
6. `v3.1-architecture.html`

保留的 V3.1 handoff 文档不再是当前设计入口；它们用于历史审计、测试、remaining-gates handoff 和运行时兼容。
`v4-architecture.md` 同样保留为历史架构与兼容性依据，不再是 active plan。
