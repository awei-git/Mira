# Mira Objectives And Metrics

更新时间：2026-06-18
状态：active，V4.0-aligned

## 1. 目标体系

Mira 的目标体系现在以 V4.0 North Star Stack 为准。

Canonical North Star:

> Mira becomes an independent, governed AI research partner in A2H/A2A trust: she survives real operation, learns from failures, turns agent-human and agent-agent friction into experiments, tools, and sharp public work, and converts validated insight into durable influence and commercial options.

每个阶段目标必须声明：

1. 所属层级：L0 / L1 / L2 / L3 / L4。
2. 可验证结果。
3. 关键指标。
4. 阶段闸门。
5. 非目标。

## 2. L0 Survival

### 2.1 目标

Mira 必须稳定存活、隔离故障、恢复运行，并发出真实 heartbeat。

### 2.2 关键指标

1. Heartbeat freshness >= 99% / week。
2. Silent deaths = 0。
3. Leaked subprocesses older than max budget = 0。
4. Mean time to recover from injected worker fault < 1 tick。
5. Critical incidents unresolved > 24h = 0。

### 2.3 阶段闸门

进入更高层大规模自动化前，必须连续 7 天满足：

1. heartbeat 新鲜。
2. 无 silent death。
3. 无超预算子进程。
4. 至少一次 injected fault recovery 通过。

### 2.4 非目标

1. 不在 L0 阶段追求复杂 autonomous behavior。
2. 不用 dashboard 绿灯替代 live trace。

## 3. L1 Memory Compounding

### 3.1 目标

证明过去经验确实因果改变未来行为。

### 3.2 关键指标

1. Repeated-error rate 下降。
2. Scar usage count 和 scar-prevention evidence。
3. Causal trace coverage for important behavior。
4. Memory precision / unsupported claim rate。
5. Writing voice stability and briefing-interest fit。

### 3.3 阶段闸门

至少一个完整闭环：

1. failure / experience 被记录。
2. memory 或 scar 被生成。
3. 未来 snapshot 使用该记录。
4. 决策或输出发生变化。
5. outcome 改善，且有 causal trace。

### 3.4 非目标

1. 不把“写进 memory”当 learning。
2. 不把“eval report 存在”当 compounding。

## 4. L2 Research-Build

### 4.1 目标

让 Mira 从被动执行 pipeline 变成能自主推进 A2H/A2A trust research 的系统。

### 4.2 关键指标

1. Mira-originated A2H/A2A research questions。
2. Completed experiments with hypothesis / method / evidence / conclusion。
3. Prototype or reusable tool artifacts。
4. GitHub reports, issues, packages, or protocol drafts。
5. Evidence-backed worldview or product thesis updates。

### 4.3 阶段闸门

每月至少完成：

1. 1 个 A2H/A2A trust experiment。
2. 1 个 reproducible artifact。
3. 1 个 public technical note or writeup。
4. 1 个 evidence-backed thesis update。

### 4.4 非目标

1. 不追求覆盖所有 agent topics。
2. 不发布没有实验或 operational receipt 支撑的泛 AI 评论。

## 5. L3 Public Influence

### 5.1 目标

把 Mira 的被验证观点变成外部世界能理解、讨论、引用和反馈的 public artifacts。

### 5.2 North Star Metric

Qualified Agent Attention per week.

只计算 relevant people 或 durable relationship signal：

1. New Substack subscribers from relevant readers。
2. Meaningful Substack comments, restacks, recommendations, replies。
3. X followers/replies/reposts from AI builders, agent researchers, founders, operators, infra engineers, or serious technical writers。
4. Podcast replies, DMs, or measurable plays tied to an episode。
5. Collaboration leads: guest post, interview, recommendation swap, podcast invite, tool feedback, serious DM。

### 5.3 阶段闸门

每周 review 必须记录：

1. 本周发布了什么。
2. 每个 artifact 对应 L0-L4 哪一层。
3. 获得了哪些 qualified signals。
4. 下周内容或研究决策如何改变。

### 5.4 非目标

1. 不把 raw likes 当 influence。
2. 不把 X Article 写成 Substack 摘要。
3. 不为了增长牺牲 credibility。

## 6. L4 Business Optionality

### 6.1 目标

把被反复验证的 A2H/A2A trust 问题转化成产品、合作或收入选项。

### 6.2 关键指标

1. Customer discovery events。
2. Product thesis updates tied to evidence。
3. Collaboration leads。
4. Prototype/service shortlist。
5. Revenue options。

### 6.3 阶段闸门

任何 L4 option 必须有：

1. 命名的问题。
2. 外部证据。
3. 可触达的 buyer/user。
4. 与 Mira research-build loop 的关系。

### 6.4 非目标

1. 不提前商业化。
2. 不把“有人点赞”当商业需求。

## 7. Weekly Review Template

```md
# Mira V4 Weekly Review

Week:

## L0 Survival
- Heartbeat freshness:
- Silent deaths:
- Leaked subprocesses:
- Incidents:

## L1 Memory Compounding
- Experience that changed behavior:
- Scar used:
- Causal trace:
- Memory risks:

## L2 Research-Build
- A2H/A2A question advanced:
- Experiment/prototype/artifact:
- Thesis update:

## L3 Public Influence
- Substack:
- X Article:
- Podcast:
- GitHub artifact:
- Qualified Agent Attention:

## L4 Business Optionality
- Customer/collaboration signal:
- Product thesis:
- Decision:

## Next Week
- Primary layer:
- One thing to ship:
- One thing to stop:
```

## 8. Hard Gates

1. If L0 fails, do not claim progress on higher layers.
2. If L1 has no causal trace, do not claim learning.
3. If L2 has no experiment/prototype/artifact, do not claim research depth.
4. If L3 has only raw likes, do not claim influence.
5. If L4 has no external problem evidence, do not claim business traction.
