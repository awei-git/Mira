# Mira System Docs

更新时间：2026-04-06

这套文档是 Mira 当前阶段的 canonical docs。

目的：

1. 统一 Mira 的方向、目标、设计、使用方式和落地顺序。
2. 让所有改动有明确的 reference point。
3. 区分主线（A2A trust 研究）和基础线（辅助能力维持）。

文档顺序：

1. [north-star.md](./north-star.md)
   Mira 是什么，最终要成为什么。核心定位：A2A trust 领域的独立研究者，OPC 的核心引擎。
2. [objectives-and-metrics.md](./objectives-and-metrics.md)
   双线目标体系（基础线维持 + 主线 research-build），阶段闸门，北极星指标。
3. [system-design.md](./system-design.md)
   Canonical system design。新增 Research Workflow 作为核心 workflow。
4. [operations-handbook.md](./operations-handbook.md)
   Mira 能做什么、每天怎么运行、怎么交互。
5. [production-roadmap.md](./production-roadmap.md)
   双线 roadmap：基础线（维护模式）+ 主线（R1 Research Infrastructure -> R2 A2A Trust Research -> R3 Product & OPC）。
6. [architecture-decisions.md](./architecture-decisions.md)
   关键架构与产品决策日志。
7. [runbooks/](./runbooks/)
   操作手册（operator dashboard、restore drill）。

使用规则：

1. 想理解 Mira 的方向，先读 `north-star.md`。
2. 想判断当前优先级，读 `objectives-and-metrics.md` 和 `production-roadmap.md`。
3. 想改代码，先读 `system-design.md`。
4. 想知道 Mira 平时怎么工作，读 `operations-handbook.md`。

文档优先级：

1. `docs/north-star.md` 是方向层唯一真相。
2. `docs/system-design.md` 是设计层唯一真相。
3. `docs/objectives-and-metrics.md` 是目标与验收层唯一真相。
4. `docs/production-roadmap.md` 是执行顺序层唯一真相。
