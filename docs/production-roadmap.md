# Mira Production Roadmap

更新时间：2026-04-06

## 1. 这份文档是干什么的

这不是愿景文档，也不是设计总纲。

这份文档只回答：

1. 现在最该先做什么。
2. 每一步具体解决什么问题。
3. 每一步要产出什么。
4. 每一步怎么验收。

## 2. 现阶段最关键的问题

当前最关键的不是“能力不够多”，而是：

1. runtime contract 还不够硬。
2. workflow 状态机还不够清楚。
3. safety / verification 还没有成为真正统一层。
4. control plane 和多用户边界还不够收紧。
5. recovery / restore / retry discipline 还不够系统。

## 3. 执行原则

1. 先止血，再扩能力。
2. 先做全局规则，再修局部 workflow。
3. 先修 production path，再修边角路径。
4. 先让系统诚实，再让系统更聪明。
5. 闸门不过，不进下一阶段。

## 4. Phase 0: Runtime Hardening

目标：

把 Mira 从 prototype runtime 收紧成受控运行系统。

### Step 0.1 明确 canonical scope

解决问题：

1. 系统边界过宽。
2. 文档和代码都容易把 prototype path 当主路径。

动作：

1. 明确只支持 supervised creator workflows。
2. 明确 connector classification。
3. 明确 implemented-now / partially-working / planned。

验收：

1. 不再把未稳定能力写成 production promise。

### Step 0.2 建立 capability policy matrix

解决问题：

1. side effect 没有统一治理层。

动作：

1. 定义 `read-only / local-write / external-draft / external-publish / system-mutate`。
2. 为每类定义审批、验证、重试、回退。

验收：

1. 每个高价值动作都有 policy class。

### Step 0.3 补全 task / step state

解决问题：

1. worker 中断后状态悬空。
2. done 语义不可靠。

动作：

1. 固化 plan artifact。
2. 增加 step state machine。
3. 记录输入摘要、输出摘要、失败原因、重试次数。

验收：

1. 任意中断都能解释停在哪一步。

### Step 0.4 建立统一 preflight / verify

解决问题：

1. safety 还是分散的 fragment。
2. false completion。

动作：

1. 所有高风险 side effect 接 preflight。
2. 建立 artifact verify 和 external post-condition verify。
3. 明确 `blocked / needs-input / failed / done` 语义。

验收：

1. “说完成但没产物”显著下降。

### Step 0.5 收紧 control plane

解决问题：

1. web / bridge / app 入口仍可能过宽。

动作：

1. localhost-first。
2. known-user enforcement。
3. token / auth baseline。
4. path traversal 和越权 artifact 访问阻断。

验收：

1. 默认配置下不存在明显越权入口。

### Step 0.6 建立 retry / timeout / ceiling discipline

解决问题：

1. transient failure 直接失败。
2. 持续失败无限重试。

动作：

1. provider retry with backoff。
2. task retry ceiling。
3. connector timeout。
4. bridge / sync health checks。

验收：

1. transient failure 不再轻易把关键 workflow 打死。
2. 无限空转被消除。

### Step 0.7 写最小 runbooks

解决问题：

1. 故障恢复依赖人脑记忆。

动作：

1. launch / restart
2. stuck task recovery
3. publish incident recovery
4. backup restore
5. web / bridge lockdown

验收：

1. 操作者不靠临场猜测也能恢复系统。

## 5. Phase 1: Workflow Reliability

目标：

让关键 workflow 成为 production workflow。

### Step 1.1 写作 workflow 状态机化

解决问题：

1. 写作路径容易漂移。
2. 反馈与定稿关系不清。

动作：

1. 固化 plan / draft / review / revise / finalize 状态。
2. 每阶段都有 artifact contract。

验收：

1. 用户知道当前处于哪一阶段。

### Step 1.2 publish workflow 单一事实源

解决问题：

1. connector failure 会造成状态错乱。

动作：

1. publish manifest 变成单一事实来源。
2. publish 后必须 verify。

验收：

1. 不再出现“以为发了，其实没发”。

### Step 1.3 podcast workflow 解耦

解决问题：

1. 生成、审核、发布语义混淆。

动作：

1. article select、audio generation、review、publish 分离。
2. curated queue 与自动发现分离。

验收：

1. 生成完成不等于发布完成。

### Step 1.4 growth workflow 降真空叙事

解决问题：

1. 运营自动化被误描述成增长闭环。

动作：

1. 明确 article promotion、spark posting、engagement 的边界。
2. 分清“动作已执行”和“效果已产生”。

验收：

1. growth 只作为运营自动化表述。

### Step 1.5 persona / memory 接入主路径

解决问题：

1. 不同主路径还可能读不同人格或记忆层。

动作：

1. discussion / general / researcher / writer 统一 persona context。
2. retrieval 附 freshness / confidence / provenance。

验收：

1. 主路径的人格和记忆注入基本一致。

## 6. Phase 2: Observability And Recovery

目标：

让 Mira 成为可运维系统。

### Step 2.1 结构化日志

解决问题：

1. 故障难归因。

动作：

1. workflow_id
2. task_id
3. user_id
4. capability
5. result
6. duration
7. failure_class

验收：

1. 关键 workflow 都能被追踪。

### Step 2.2 operator dashboard

解决问题：

1. 只能查散乱日志。

动作：

1. active tasks
2. failed tasks
3. stuck tasks
4. publish queue
5. connector health
6. recent incidents

验收：

1. operator 能快速定位当前系统状态。

### Step 2.3 backup / restore drill

解决问题：

1. 有备份，不等于能恢复。

动作：

1. 周期性 restore dry-run。
2. backup integrity checks。
3. restore runbook。

验收：

1. 至少完成一次真实 restore drill。

## 7. Phase 3: Self-Improvement As Production Subsystem

目标：

把 self-improvement 从概念功能推进成 production subsystem。

### Step 3.1 backlog executor 最小版

解决问题：

1. 只有 diagnosis，没有 execution。

动作：

1. 支持低风险 action 执行。
2. action state machine：`proposed / approved / in_progress / verified / rejected`。

验收：

1. 至少一类低风险改进能自动闭环。

### Step 3.2 改进效果验证

解决问题：

1. 系统会提建议，但不知道是否有效。

动作：

1. action outcome verification。
2. rollback policy。
3. blast radius control。

验收：

1. 未验证改动不会进入系统真相。

## 8. 当前实现状态

截至 `2026-04-06`，当前 supervised production scope 下的 roadmap 主体已落地：

1. Phase 0：scope / policy / state artifact / preflight / verify / control-plane hardening / retry timeout ceiling 已落地。
2. Phase 1：runtime contract、workflow tracking、persona/memory 主路径统一已落地。
3. Phase 2：operator dashboard、failure aggregation、backup manifest、restore dry-run、restore runbook 已落地。
4. Phase 3：bounded backlog executor 已落地，当前只对低风险 `self_evolve_proposal` 生效。

当前闭环边界：

1. self-improvement 只自动执行已批准、低风险、单 executor 路径。
2. 更广的 rollback / multi-executor blast-radius control 仍属于后续扩展，不在当前 production promise 内。

## 9. 测试与闸门

每阶段都要补对应测试：

1. planner / executor resume
2. manifest progression
3. publish verify failure
4. duplicate job suppression
5. prompt-injection quarantine
6. per-user isolation
7. retry ceiling behavior
8. backup restore dry-run

## 10. 90 天路线

### 前 30 天

1. scope 收紧
2. policy matrix
3. state artifact
4. preflight / verify
5. control plane hardening
6. retry / timeout / ceiling
7. runbooks

### 30-60 天

1. writing / publish / podcast / growth 状态机
2. persona / memory 主路径统一
3. operator dashboard
4. smoke suite
5. user scope 贯穿

### 60-90 天

1. restore drill
2. backlog executor 最小版
3. managed-beta connector 降级策略完善
4. 事实表统一为 implemented / partial / planned

## 11. 结论

Mira 的 readiness 不是靠“再加一个 agent”完成的。

它依赖于：

1. runtime contract 硬化
2. workflow state machine 化
3. safety / verification 真正统一
4. control plane 与 user scope 收紧
5. observability 与 recovery 成熟
