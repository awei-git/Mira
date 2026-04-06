# Contributing To Mira

更新时间：2026-04-05

## 1. 先读什么

任何改动 Mira 的人，先读：

1. [docs/README.md](./docs/README.md)
2. [docs/system-design.md](./docs/system-design.md)
3. [docs/production-roadmap.md](./docs/production-roadmap.md)

如果改动会影响方向、目标、主路径行为、架构边界，还要读：

1. [docs/objectives-and-metrics.md](./docs/objectives-and-metrics.md)
2. [docs/architecture-decisions.md](./docs/architecture-decisions.md)

## 2. 核心规则

Mira 不是一个可以随意 patch 的 repo。

默认规则是：

1. 先对齐 design，再改代码。
2. 先解释边界，再扩能力。
3. 先补验证，再宣称完成。
4. 先走 canonical path，不新增隐性主路径。

## 3. 改动分类

### 3.1 Implementation Change

特点：

1. 不改变 design 边界。
2. 只是在现有 design 内修 bug、补测试、补实现。

要求：

1. 在 PR 中引用相关 design section。
2. 说明验证方式。

### 3.2 Workflow Change

特点：

1. 改变某条 workflow 的主路径行为、状态机或 side effect 处理。

要求：

1. 更新 `docs/system-design.md` 对应 workflow 部分，或明确说明 design 未变。
2. 更新 `docs/operations-handbook.md` 中用户可感知行为。
3. 补 workflow-level tests。

### 3.3 Design-Boundary Change

特点：

1. 改 runtime contract。
2. 改 capability class。
3. 改 approval model。
4. 改 user scope / persona / memory contract。
5. 改 control-plane trust boundary。

要求：

1. 更新 `docs/system-design.md`。
2. 更新 `docs/architecture-decisions.md`。
3. 说明 migration / rollback。
4. 明确受影响的 readiness step。

## 4. 任何 PR 都必须回答的 7 个问题

1. 这次改动对应 `system-design.md` 的哪一节？
2. 这是 implementation、workflow，还是 design-boundary change？
3. 如果是新能力，它的 capability class 是什么？
4. 如果有 side effect，它的 approval / verify / failure semantics 是什么？
5. 如果有状态写入，它的 user scope、owner、lifecycle 是什么？
6. 这次改动如何验证？
7. 哪些 canonical docs 需要同步更新？

## 5. 默认禁止的改法

1. 在 handler 里偷偷新增 side effect。
2. 用绕过 preflight / verifier 的方式修问题。
3. 新增第二套长期并存的主路径。
4. 继续堆 legacy-only path，但不写迁移计划。
5. 改了行为却不更新 handbook。
6. 改了边界却不更新 design 或 architecture decisions。

## 6. 必须同步更新文档的情况

1. 改设计边界：更新 `docs/system-design.md`
2. 改目标或闸门：更新 `docs/objectives-and-metrics.md`
3. 改用户可见行为：更新 `docs/operations-handbook.md`
4. 改执行顺序或阶段计划：更新 `docs/production-roadmap.md`
5. 改长期取舍：更新 `docs/architecture-decisions.md`

## 7. 验证要求

最少要有一种：

1. unit tests
2. workflow integration tests
3. smoke tests
4. deterministic verification steps
5. 明确的人工验证记录

以下改动不能只靠“看起来没问题”：

1. publish
2. workflow state machine
3. user scope
4. memory / persona injection
5. control-plane exposure

## 8. 临时例外

允许临时 shim，但必须：

1. 明确写明是临时方案。
2. 有 cleanup owner。
3. 有移除条件或截止时间。
4. 在 PR 中说明为什么不能直接走 canonical path。

## 9. 合并标准

如果以下任一项不成立，默认不应合并：

1. 改动能映射到 design。
2. 改动有验证方法。
3. 相关 canonical docs 已同步。
4. side effect / state / user scope 语义清楚。
5. 没有悄悄扩大系统边界。

对于 `main` 分支，额外要求：

1. 必须通过 PR 合并。
2. 必须至少有 1 个非作者 reviewer approving review。
3. 必须通过 CI 和 PR policy checks。

## 10. 自动化闸门

repo 里已经有自动化 PR policy gate。

它会检查：

1. PR 是否声明 change type。
2. PR 是否引用 `docs/system-design.md`。
3. workflow / design-boundary change 是否引用 `docs/production-roadmap.md`。
4. 是否填写验证信息。
5. design-boundary change 是否同步更新 `docs/system-design.md` 和 `docs/architecture-decisions.md`。
6. workflow change 是否同步更新 `docs/operations-handbook.md`。
7. 指向 `main` 的 PR 是否至少有 1 个非作者 approving review。

如果后续要真正做到“不能绕过”，还需要在 GitHub 上把这个 check 设为 required status check。
