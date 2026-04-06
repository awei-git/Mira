# Mira System Docs

更新时间：2026-04-06

这套文档是 Mira 当前阶段的 canonical docs。

目的：

1. 统一 Mira 的方向、目标、设计、使用方式和落地顺序。
2. 减少 `README.md`、`DESIGN.md`、`TODO.md`、review 文档之间的状态漂移。
3. 让后续 PR 有明确的 reference point，而不是按感觉改系统。

文档顺序：

1. [north-star.md](./north-star.md)
   Mira 现在是什么，最终要成为什么，north star 是什么。
2. [objectives-and-metrics.md](./objectives-and-metrics.md)
   短期 / 中期 / 长期目标，以及可验证指标、闸门和非目标。
3. [system-design.md](./system-design.md)
   Mira 的 canonical system design。后续涉及 runtime、workflow、memory、persona、publish、safety 的 PR 都必须引用这里。
4. [operations-handbook.md](./operations-handbook.md)
   Mira 能做什么、每天怎么运行、怎么交互、怎么记录、典型 workflow 是什么。
5. [production-roadmap.md](./production-roadmap.md)
   从现状到 production readiness 的分阶段执行计划，具体到每一步解决什么问题、如何验收。
6. [architecture-decisions.md](./architecture-decisions.md)
   关键架构与产品决策日志，记录为什么这样设计、为什么放弃另一种方案。
7. [runbooks/operator-dashboard.md](./runbooks/operator-dashboard.md)
   operator dashboard 的查看顺序和排障手册。
8. [runbooks/restore-drill.md](./runbooks/restore-drill.md)
   backup / restore dry-run 的执行与恢复手册。
9. [../CONTRIBUTING.md](../CONTRIBUTING.md)
   贡献与改动规则，约束后续 PR 如何对齐 design、验证和文档更新。

使用规则：

1. 想理解 Mira 的基本脉络，先读 `north-star.md`。
2. 想判断当前优先级，读 `objectives-and-metrics.md` 和 `production-roadmap.md`。
3. 想改代码，先读 `system-design.md`，再看 `production-roadmap.md` 对应阶段。
4. 想知道 Mira 平时怎么工作，读 `operations-handbook.md`。
5. 想排障或恢复，读 `docs/runbooks/`。

文档优先级：

1. `docs/system-design.md` 是设计层唯一真相。
2. `docs/objectives-and-metrics.md` 是目标与验收层唯一真相。
3. `docs/production-roadmap.md` 是执行顺序层唯一真相。
4. 根目录 `DESIGN.md` 视为历史背景，不再作为唯一 canonical spec。

这 5 个主文档已经覆盖当前阶段 95% 的需要。

第 6 个文档不是另一份总纲，而是轻量 decision log：

1. 记录关键取舍。
2. 避免反复争论同一问题。
3. 给后续 PR 保留上下文。

除了它之外，剩下的 change-control 规则仍在 `system-design.md` 里：

1. 什么改动需要更新 system design。
2. 什么改动需要更新 operations handbook。
3. 什么改动需要更新 production roadmap。
4. PR 应该引用哪些章节。
