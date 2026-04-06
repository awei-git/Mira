# Mira Objectives And Metrics

更新时间：2026-04-05

## 1. 目标体系

Mira 的目标分成三层：

1. 短期目标：把原型收紧成受控系统。
2. 中期目标：把关键 workflow 做成 production-grade。
3. 长期目标：把 Mira 推进成 Agent OS，并在 A2A 领域形成方法论、影响力和桥接价值。

每一层都必须有：

1. 目标。
2. 可验证结果。
3. 关键指标。
4. 阶段闸门。
5. 非目标。

## 2. 短期目标：0-30 天

### 2.1 目标

把 Mira 从“能跑的原型”收紧成“受控运行系统”。

### 2.2 必须达成

1. 明确 production scope，只覆盖 supervised creator workflows。
2. 建立 approval policy matrix。
3. 给关键 workflow 增加统一的 preflight、artifact verify、post-condition。
4. 收紧 Web / bridge / app 等控制面默认暴露面。
5. 为核心任务建立显式 step state 和失败语义。
6. 写出最小 runbooks。

### 2.3 关键指标

1. false-completion rate 下降到可接受水平。
2. external publish failure 不再造成状态模糊。
3. 主要入站通道都经过 injection quarantine。
4. operator 能通过日志和文档解释大多数常见故障。

### 2.4 阶段闸门

只有满足以下条件，才能进入中期阶段：

1. 不再出现“系统说完成，但产物不存在”的高频问题。
2. 关键 side effect 都有 preflight 和 post-condition。
3. 控制面默认安全边界已经收紧。
4. 至少一轮真实运行中，没有出现 P0 公开事故。

### 2.5 非目标

1. 不追求更多 channel。
2. 不追求更多 agent。
3. 不追求多租户产品化。

## 3. 中期目标：1-3 个月

### 3.1 目标

让 Mira 的关键工作流具备 production reliability。

### 3.2 必须达成

1. planner / executor 分离足够清晰。
2. multi-step task 可恢复、可重试、可追踪。
3. writing / publish / podcast / growth 有稳定状态机。
4. operator dashboard 最小版可用。
5. backlog executor 能覆盖低风险改进项。
6. `user_id` 贯穿关键 workflow，不串上下文。
7. backup / restore 有真实演练。

### 3.3 关键指标

1. 连续 14 天运行无 P0。
2. 关键 workflow success rate 达到目标阈值。
3. managed-beta connector 都有降级路径。
4. retry ceiling 生效，不再出现无限空转。
5. per-user workflow 不再发生上下文串写。

### 3.4 阶段闸门

只有满足以下条件，才能进入长期建设阶段：

1. smoke suite 稳定通过。
2. operator dashboard 和 runbooks 已可用。
3. 至少完成一次 restore drill。
4. persona / memory 统一层已接入主路径。
5. 关键工作流可以被解释、恢复和回放。

### 3.5 非目标

1. 不把 managed-beta connector 升格成假 production。
2. 不把增长结果当成系统承诺。
3. 不用“更聪明”替代“更可验证”。

## 4. 长期目标：3-12 个月

### 4.1 目标

把 Mira 从稳定系统推进成可扩展的 Agent OS。

### 4.2 长期北极星

1. A2A 贡献：
   Mira 在 A2A 领域形成真实方法论和基础设施贡献。
2. 思维与影响力：
   Mira 能持续产出有辨识度的判断，成为 A2A 方向有影响力的声音。
3. 桥接能力：
   Mira 能稳定产出高质量 content，帮助 human 和 agent 建立更好的 bridge。

### 4.3 必须达成

1. self-improvement 进入受控执行闭环。
2. 多用户 / 多 workspace 隔离成熟。
3. connector、upgrade、incident policy 成体系。
4. observability 从日志升级到趋势与健康度视图。
5. 形成最小 A2A infra：
   runtime contract、capability boundary、verification pattern、human approval interface。
6. 建立持续输出体系，让系统实践、内容、观点互相强化。

### 4.4 关键指标

1. 至少一部分 A2A 相关能力被其他 workflow 或 external integration 真实复用。
2. Mira 的公共输出形成稳定栏目、稳定立场和可引用的方法论。
3. 每周被真实采用的高价值输出数持续增长。
4. 低风险改进项可以自动执行、验证、归档。
5. 新能力上线前有 gate，上线后有 owner。

### 4.5 阶段闸门

只有满足以下条件，才可以说 Mira 正在接近长期目标：

1. 她不只是能发内容，而是能产出可复用方法论。
2. 她不只是能调 agent，而是有稳定的 Agent OS runtime contract。
3. 她不只是“像一个人格”，而是人格、belief、memory 已成为系统层能力。

### 4.6 非目标

1. 不追求空泛的“AGI 感”。
2. 不追求全自动外部经营。
3. 不追求用影响力叙事掩盖系统脆弱性。

## 5. 北极星指标

当前阶段最真实的北极星指标是：

每周被真实采用的高价值输出数。

高价值输出包括：

1. 被发布的文章。
2. 被采纳的 briefing / analysis。
3. 被确认有效的运营动作。
4. 被验证完成的低风险改进项。

## 6. 健康指标

必须持续跟踪：

1. task success rate。
2. hallucination / false-completion rate。
3. publish safety incident count。
4. rollback count。
5. support hours per week。
6. connector failure rate。
7. approval burden。
8. restore drill pass rate。

## 7. 目标管理规则

1. 短期目标优先于长期叙事。
2. 闸门不过，不进入下一阶段。
3. 长期目标可以激进，短中期目标必须可验收。
4. 所有目标必须能映射到 `system-design.md` 或 `production-roadmap.md` 里的具体工程动作。
