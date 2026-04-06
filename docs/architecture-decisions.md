# Mira Architecture Decisions

更新时间：2026-04-05
状态：placeholder

## 1. 这份文档是干什么的

这不是另一份设计文档，也不是另一份 roadmap。

它只记录一类东西：

对 Mira 产生长期影响的关键决策。

比如：

1. 为什么保留某个 runtime contract。
2. 为什么废弃某个 legacy workflow。
3. 为什么某个 connector 只能是 `managed-beta`。
4. 为什么某个 PR 选择了某种设计边界，而不是另一种。

## 2. 为什么需要它

前 5 份文档已经覆盖了：

1. 愿景
2. 目标
3. design
4. handbook
5. readiness plan

但它们都不适合记录“当时为什么这么决定”。

如果没有 decision log，后续很容易出现：

1. 同一个问题反复争论。
2. 新 PR 推翻旧设计，但没有留下理由。
3. 文档写了结论，却看不到取舍过程。

## 3. 记录原则

只记录高价值决策，不记录日常小改动。

应该记录的决策包括：

1. 改变 canonical design 边界。
2. 改变 production scope。
3. 改变 capability class / approval policy。
4. 改变 workflow 主路径。
5. 改变 persona / memory / user-scope contract。
6. 改变 connector 等级或上线闸门。

不应该记录的内容：

1. 普通 bug fix。
2. 小型重构。
3. 文案改动。
4. 没有长期影响的实现细节。

## 4. 每条决策应该怎么写

建议统一格式：

1. `Decision`
2. `Date`
3. `Status`
4. `Context`
5. `Decision`
6. `Consequences`
7. `References`

最小模板如下：

```md
## DECISION-0001: 标题

Date: YYYY-MM-DD
Status: proposed | accepted | superseded | deprecated

Context:
- 现在遇到的问题是什么
- 为什么需要做决策

Decision:
- 最终决定是什么

Consequences:
- 会获得什么
- 会失去什么
- 后续必须同步更新哪些文档/代码

References:
- docs/system-design.md#...
- docs/production-roadmap.md#...
- PR / issue / review 文档
```

## 5. 当前建议优先补的几条决策

这几条很适合尽快落成正式记录：

1. `One Mira, Many Capabilities` 作为统一产品和架构心智。
2. `Control Plane / Data Plane / Safety Plane` 分离。
3. capability class 与 approval policy matrix。
4. connector classification：`production-supported / managed-beta / experimental / disabled`。
5. persona / memory / user-scope 必须成为系统层 contract。
6. 旧 `DESIGN.md` 降为历史背景，新 `docs/system-design.md` 成为 canonical spec。

## 6. 后续规则

如果一个 PR：

1. 改了 `docs/system-design.md` 的核心边界，
2. 或推翻了已有阶段闸门，
3. 或改变了主路径 workflow，

那就应该同时新增或更新一条 decision log。

## 7. 占位说明

这份文档当前还是 placeholder。

下一步不需要先把它写满，只需要在第一次真正的架构级决策落地时，开始写第一条正式记录。
