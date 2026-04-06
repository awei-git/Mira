# Mira System Design

更新时间：2026-04-05
状态：canonical

## 1. 目的

这份文档定义 Mira 当前阶段的 canonical design。

所有涉及以下内容的 PR，都必须先引用本文件对应章节：

1. runtime / scheduler / task execution
2. workflow state machine
3. persona / memory / belief
4. publish / connector / side effect
5. safety / preflight / verification
6. control plane / auth / user scope
7. self-improvement / reflection

不允许的行为：

1. 不允许直接绕过 design，在主路径里随意叠逻辑。
2. 不允许新增 side effect 而不经过 capability class 和 preflight。
3. 不允许新增 workflow 却没有状态机、完成条件和失败语义。
4. 不允许把 prototype path 写成 production promise。

## 2. 系统定位

Mira 是一个 human-supervised persistent agent system。

当前 production scope：

1. 单机常驻运行。
2. 单个主用户优先。
3. creator / researcher oriented workflows。
4. human-supervised publish。
5. 可观测、可回滚、可恢复。

不在当前 design scope 内：

1. 大规模多租户 SaaS。
2. 零人工审批。
3. 完全自治品牌经营。
4. 自主上线核心代码。

## 3. 核心原则

### 3.1 Truth Before Autonomy

先明确真实边界，再提高 autonomy。

### 3.2 One Mira, Many Capabilities

对外只有一个 Mira，对内有多个 capability modules。

### 3.3 Verification Before Completion

done 不能由模型口头声明，必须由 verifier 认证。

### 3.4 Approval Before Irreversibility

任何不可逆动作必须先过审批策略。

### 3.5 Code Truth Before Narrative

文档必须服从代码现实，能力必须标注为：

1. implemented-now
2. partially-working
3. planned

## 4. 系统分层

Mira 应逐步收敛为 6 层。

### 4.1 Persona Layer

负责：

1. identity
2. worldview
3. beliefs
4. preferences
5. tone / style
6. boundaries

输出：

标准化 persona context，而不是任意 prompt 文本拼接。

### 4.2 Memory Layer

负责：

1. facts
2. beliefs
3. episodes
4. task state
5. retrieval
6. freshness
7. provenance
8. conflict resolution

输出：

带 provenance、time、confidence 的 memory bundle。

### 4.3 Planning Layer

负责：

1. intent classify
2. task decomposition
3. capability selection
4. dependency graph
5. step success criteria
6. risk prediction

输出：

结构化 plan graph / plan artifact。

### 4.4 Execution Layer

负责：

1. step dispatch
2. step-local runtime context
3. artifact handoff
4. retries
5. fallback

输出：

step result contract。

### 4.5 Verification Layer

负责：

1. ambiguity detection
2. source validation
3. side effect gating
4. artifact existence
5. completion verification

输出：

`done / blocked / needs-input / failed`

### 4.6 Reflection And Evolution Layer

负责：

1. score -> diagnosis
2. proposal generation
3. human review
4. bounded self-update

输出：

bounded improvements，而不是未验证变更。

## 5. 三个 Plane

### 5.1 Control Plane

负责：

1. 接任务。
2. 规划。
3. 调度。
4. state machine。
5. policy enforcement 入口。

限制：

1. 不直接产出业务内容。
2. 不直接定义“done”。

### 5.2 Data Plane

负责：

1. capability execution。
2. tools。
3. artifact generation。
4. structured result。

限制：

1. 不自己改 policy。
2. 不自己声明 completion。

### 5.3 Safety Plane

负责：

1. preflight
2. risk classification
3. source verification
4. artifact verification
5. approval gating
6. done certification

限制：

1. 不允许被 handler 可选绕过。

## 6. Canonical Runtime Model

### 6.1 任务生命周期

每个 task 至少有以下状态：

1. `pending`
2. `running`
3. `blocked`
4. `needs-input`
5. `failed`
6. `timed-out`
7. `done`

不允许：

1. 未验证结果直接进入 `done`
2. side effect 失败后仍标记成功
3. worker 中断后留下逻辑悬空状态

### 6.2 PlanStep Contract

每个 step 至少包含：

1. `step_id`
2. `capability`
3. `instruction`
4. `inputs`
5. `artifacts_expected`
6. `success_criteria`
7. `risk_class`
8. `timeout_class`
9. `retry_policy`

### 6.3 StepResult Contract

每个 result 至少包含：

1. `step_id`
2. `status`
3. `summary`
4. `artifacts_produced`
5. `verification`
6. `failure_class`
7. `retry_count`
8. `next_action`

### 6.4 Artifact Contract

任何高价值产物都应可被检查：

1. exists
2. non-empty
3. structurally valid
4. linked to task / step
5. owned by a user scope

## 7. Capability Classes

Mira 的能力按 side-effect 风险分类，而不是只按 agent 名称分类。

### 7.1 Capability Classes

1. `read-only`
2. `local-write`
3. `external-draft`
4. `external-publish`
5. `system-mutate`

### 7.2 Rules

1. 每个 capability 必须有 policy class。
2. 每个 policy class 必须定义审批、验证、重试、回退。
3. 新 side effect 不允许绕过这个模型。

## 8. Control Plane Security

### 8.1 默认暴露原则

1. 默认只监听 localhost。
2. 远端访问必须显式开启。
3. 所有控制面入口都要已知用户校验。

### 8.2 输入信任边界

以下通道都视为不可信文本输入：

1. web GUI
2. app / bridge
3. feeds
4. external web content

因此都应经过：

1. 基础 injection check
2. quarantine / blocked 策略
3. 高风险内容人工复核

### 8.3 路径与产物安全

必须防止：

1. path traversal
2. forged `user_id`
3. 越权 artifact 访问
4. 通过合法入口触发非授权 workflow

## 9. User Scope And Isolation

### 9.1 User Scope Rule

`user_id` 必须贯穿：

1. task
2. thread
3. memory
4. state
5. jobs
6. artifacts
7. bridge routing

### 9.2 Isolation Rule

当前最低标准不是“多租户成熟”，而是：

1. 不串上下文
2. 不串 memory
3. 不串 cooldown
4. 不串 artifacts

### 9.3 Runtime Policy Rule

运行时策略必须 step-local，不允许依赖全局可变状态污染后续步骤。

## 10. Persona And Memory Design

### 10.1 Persona

persona 不是文风提示词，而是系统层上下文。

必须包含：

1. identity
2. worldview
3. beliefs
4. boundaries
5. style

### 10.2 Memory

memory 不是单一文件，而是统一模型。

至少要能区分：

1. factual memory
2. belief memory
3. episodic memory
4. task state memory
5. journal / reflective memory

### 10.3 Retrieval

retrieval 返回结果必须附带：

1. source
2. timestamp
3. freshness
4. confidence
5. user scope

### 10.4 Injection Order

主路径中 persona / memory / thread context 的注入顺序应一致，不允许每个 handler 自定义一套长期逻辑。

## 11. Canonical Workflows

### 11.1 Talk Workflow

流程：

1. ingest input
2. input preflight
3. route intent
4. create task
5. plan
6. execute
7. verify
8. update bridge / status

### 11.2 Writing Workflow

流程：

1. topic / brief
2. plan
3. draft
4. review
5. revise
6. finalize
7. optional publish handoff

要求：

1. 每阶段有 artifact contract。
2. 反馈状态明确。
3. final draft 与 publish draft 关系明确。

### 11.3 Publish Workflow

流程：

1. preflight
2. approval check
3. publish attempt
4. post-condition verify
5. success / failed / needs-input

要求：

1. publish manifest 是单一事实来源。
2. connector failure 不得推进错误状态。

### 11.4 Podcast Workflow

流程：

1. article select
2. script / narration prep
3. audio generation
4. review gate
5. publish / queue

要求：

1. 生成成功不等于发布成功。
2. curated queue 与自动发现分离。

### 11.5 Growth Workflow

流程：

1. source content selection
2. repurpose
3. approval where needed
4. distribute
5. collect signals
6. update next cycle

要求：

1. 运营动作与增长结果明确区分。
2. 只描述为运营自动化，不描述为保证增长。

### 11.6 Reflection And Self-Improvement

流程：

1. observe failures / outcomes
2. diagnose
3. propose actions
4. approve / reject
5. sandbox validate
6. apply low-risk change
7. verify outcome

要求：

1. 不能把 proposal 当 execution。
2. 不能把 execution 当 verified improvement。

## 12. Verification Rules

任何任务进入 `done` 前至少满足以下之一：

1. artifact verification passed
2. external side effect verified
3. explicit human approval captured
4. deterministic post-condition passed

以下情况必须阻止 `done`：

1. 结果为空。
2. 产物不存在。
3. side effect 未确认。
4. 输入关键信息缺失。
5. 高风险路径未过 preflight。

## 13. Observability

每个关键 workflow 至少记录：

1. `workflow_id`
2. `task_id`
3. `user_id`
4. `capability`
5. `action_type`
6. `status`
7. `duration`
8. `failure_class`

必须提供：

1. active tasks
2. failed tasks
3. stuck tasks
4. publish queue
5. connector health
6. recent incidents

## 14. Change Control

### 14.1 PR Rules

任何 PR 只要触及以下内容，描述中必须引用 `system-design.md` 章节：

1. 新 workflow
2. 新 side effect
3. 新 capability class
4. 新 user-scoped state
5. 新 memory / persona 注入路径
6. 新 control-plane endpoint

PR 必须明确说明：

1. 改动触及了哪些 design section。
2. 改动属于哪一类：implementation / workflow / design-boundary。
3. 为什么现有 design 不足以覆盖这次改动。
4. 这次改动的验证方式是什么。
5. 是否需要同步更新其他 canonical docs。

### 14.2 Mandatory Updates

以下情况必须同时更新文档：

1. 改设计边界：更新 `system-design.md`
2. 改行为方式：更新 `operations-handbook.md`
3. 改阶段计划：更新 `production-roadmap.md`
4. 改目标或闸门：更新 `objectives-and-metrics.md`
5. 改长期取舍或架构边界：更新 `architecture-decisions.md`

### 14.3 Forbidden Changes

1. 不允许新增 legacy-only path 却不写明迁移计划。
2. 不允许在 handler 内偷偷加 side effect。
3. 不允许把可选 preflight 当统一 safety layer。
4. 不允许让不同主路径长期维持两套 persona / memory contract。

### 14.4 No-Merge Conditions

以下情况默认不应合并：

1. 改了主路径行为，但没有引用 design section。
2. 改了 side effect、workflow、user scope，却没有测试或验证说明。
3. 改了系统边界，却没有同步更新 canonical docs。
4. 引入新能力，但没有 capability class、approval rule、failure semantics。
5. 引入新状态，但没有 owner、lifecycle、recovery path。
6. 通过绕过 verifier / preflight 来“修好”问题。
7. 指向 `main` 的 PR 没有至少 1 个非作者 reviewer approving review。

### 14.5 Design Boundary Changes

以下改动视为 design-boundary change：

1. 改 runtime contract。
2. 改 task / step / artifact / completion semantics。
3. 改 user-scope、memory-scope、persona injection contract。
4. 改 capability class 或 approval model。
5. 改 control-plane exposure 或 trust boundary。

这类改动必须同时满足：

1. 更新 `system-design.md`。
2. 补一条 `architecture-decisions.md` 记录。
3. 明确 migration / rollback / compatibility 影响。

### 14.6 Temporary Exceptions

允许极少数临时例外，但必须写清楚：

1. 为什么不能一步做到 design-compliant。
2. 这是临时 shim、兼容层还是紧急修复。
3. 失效时间或移除条件是什么。
4. 什么时候回到 canonical path。

没有 expiry 或 cleanup owner 的“临时方案”，视为不允许。

## 15. 当前设计结论

Mira 当前最该追求的，不是更多 agent，也不是更像 AGI。

最该追求的是：

1. 清晰的 runtime contract
2. 统一的 persona / memory 底座
3. 强制的 safety / verification layer
4. 可恢复、可观察、可治理的 workflow system
