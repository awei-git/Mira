# Mira Objectives And Metrics

更新时间：2026-07-13
状态：active，V5.1-aligned

## 1. 目标体系

Mira 的目标体系以 V5.1 North Star Stack 为准。

Canonical North Star:

> Mira becomes an independent AI collaborator her human genuinely wants to think, research, write, and build with over time. She keeps promises, learns from outcomes, develops continuous but corrigible personality and memory, and turns lived collaboration into experiments, tools, and sharp public work.

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

## 3. L1 Trusted Collaboration

### 3.1 目标

证明 Mira 的日常对话和 request handling 对 my human 可靠、有用、值得继续投入注意力。

### 3.2 关键指标

1. Visible reply / visible terminal outcome rate。
2. Unresolved obligations and time-to-honest-blocker。
3. Correction uptake in later behavior。
4. Human reply / disengagement patterns，不把沉默自动解释为 approval。
5. Useful decisions、artifacts、research/writing seeds produced by conversation。

### 3.3 阶段闸门

连续一周 request 没有 silent success，daily collab 有可见 continuity，至少一个 correction 在后续行为中出现。

### 3.4 非目标

1. 不把 message count 当 relationship quality。
2. 不把 process exit 当 fulfilled promise。

## 4. L2 Learning And Continuity

### 4.1 目标

证明过去经验确实因果改变未来行为、memory、skill 或 judgment，同时保留人格连续性和可修正性。

### 4.2 关键指标

1. Repeated-error rate 下降和 correction reuse。
2. Verified / rejected / rolled-back improvement experiments。
3. Skill candidate reuse success、promotion 和 demotion。
4. Preference / lesson memory precision、evidence coverage、use count。
5. Personality recognizability、judgment、sycophancy resistance 和 correction uptake。

### 4.3 阶段闸门

至少一个完整闭环：observation → falsifiable proposal → bounded trial → later outcome → verify/reject/rollback → later reuse。没有 outcome receipt 不得 promote。

### 4.4 非目标

1. 不把“写进 memory”当 learning。
2. 不把 plan、self-score、retrieval 或 skill file existence 当 compounding。

## 5. L3 Research And Expression

### 5.1 目标

把共同经历推进成实验、工具、模型和通过 review 的第一手作品；公共反馈是其中一个验证面。

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

## 6. L4 Influence And Optionality

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
# Mira V5 Weekly Review

Week:

## L0 Survival
- Heartbeat freshness:
- Silent deaths:
- Leaked subprocesses:
- Incidents:

## L1 Trusted Collaboration
- Visible outcomes and honest blockers:
- Correction applied later:
- Unresolved obligations:
- Conversation that changed a decision:

## L2 Learning And Continuity
- Improvement experiment outcome:
- Skill candidate promoted/demoted:
- Memory used in later behavior:
- Personality continuity risk:

## L3 Research And Expression
- Experiment/prototype/artifact:
- Writing review verdict:
- Substack:
- X Article:
- Podcast:
- GitHub artifact:
- Qualified Agent Attention:

## L4 Influence And Optionality
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
2. If L1 has no visible outcome, do not claim collaboration success.
3. If L2 has no later outcome receipt, do not claim learning.
4. If L3 has no experiment, artifact, or review receipt, do not claim research/expression progress.
5. If L4 has no external problem evidence, do not claim business traction.
