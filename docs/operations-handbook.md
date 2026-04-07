# Mira Operations Handbook

更新时间：2026-04-06

## 1. 这份文档是干什么的

这份文档回答四类问题：

1. Mira 能干什么。
2. Mira 平时怎么工作。
3. 人怎么和 Mira 交互。
4. Mira 怎么记录、怎么复盘、怎么留下痕迹。

## 2. Mira 当前能做什么

在当前 supervised scope 里，Mira 主要能做这几类事：

1. 对话和任务处理。
2. 写作与内容整理。
3. 调研、阅读、briefing。
4. Substack / Notes / X / podcast 相关的受控分发。
5. 每日 / 每周反思、自我评估、知识沉淀。
6. 一部分 background exploration 和 idle-think。

不应该把她当前能力理解成：

1. 全自动经营外部品牌。
2. 无人监管的高风险执行系统。
3. 完整多用户 SaaS 协作平台。

## 3. Mira 平时怎么运行

### 3.1 运行形态

Mira 是一个常驻运行的 agent system，而不是一次性的 chat session。

她会持续做两类事：

1. 处理人直接交给她的任务。
2. 按周期跑 background workflows。

### 3.2 每天典型会发生的事

每天的 Mira 通常会做：

1. 读取新输入，创建或推进任务。
2. 回写任务状态和结果。
3. 做轻量 background checks。
4. 在条件满足时触发 explore、journal、reflect、growth、idle-think 等 job。
5. 记录失败、状态、记忆与观察。

### 3.3 每周典型会发生的事

每周重点应包括：

1. 反思本周高价值输出。
2. 汇总失败模式。
3. 诊断 persona drift 或 workflow drift。
4. 生成改进建议。
5. 更新下周选题、方向或系统重点。

## 4. 人怎么和 Mira 交互

### 4.1 交互入口

当前主要交互入口包括：

1. bridge / app
2. web GUI
3. 本地任务与后台调度

### 4.2 正确的交互方式

最适合 Mira 的输入类型：

1. 明确任务。
2. 明确主题或选题。
3. 明确审批请求。
4. 明确反馈和 revision 指令。
5. 明确的“请调研 / 请整理 / 请写 / 请发布草稿”。

不适合的输入类型：

1. 模糊到无法路由的任务。
2. 希望她直接做高风险不可逆动作。
3. 试图通过 prompt 绕过边界。

### 4.3 审批与确认

当前阶段，人需要参与这些节点：

1. publish approval
2. 高风险 external reply
3. system mutation
4. 高风险 self-improvement

## 5. Mira 的主要 Workflow

### 5.1 Talk Workflow

典型场景：

1. 你发来一个任务。
2. Mira 先判断是否安全、是否缺信息。
3. 她把任务拆成步骤。
4. 她执行步骤、生成产物、回写状态。
5. 她只在验证通过后宣告完成。

### 5.2 Writing Workflow

典型场景：

1. 你给她一个主题或 brief。
2. 她先整理观点和结构。
3. 她产出 draft。
4. 你给 revision feedback。
5. 她继续修改到 final。
6. 如需要，再交给 publish workflow。

### 5.3 Publish Workflow

典型场景：

1. Mira 准备可发布草稿。
2. 她做 preflight。
3. 人确认是否允许发布。
4. 她尝试分发。
5. 她验证是否真的成功。
6. 她记录结果和失败原因。

### 5.4 Reflect Workflow

典型场景：

1. Mira 汇总最近任务和结果。
2. 她评估什么做得好、什么做得差。
3. 她提出改进项。
4. 低风险改进项进入 backlog。
5. backlog executor 只执行已批准、低风险、带 executor 的项。

### 5.5 Growth Workflow

典型场景：

1. 她从已完成内容中抽取可分发素材。
2. 她生成 Notes / X 等短内容草稿。
3. 在需要时等待审批。
4. 她执行分发并记录反馈。

### 5.6 Operator Workflow

典型场景：

1. runtime 每轮刷新 operator dashboard。
2. operator 从 Web / API / bridge cache 看当前系统状态。
3. 优先处理 stuck task、publish queue、recent incidents、backlog。

### 5.7 Restore Workflow

典型场景：

1. backup 生成 manifest。
2. scheduler 周期性跑 restore dry-run。
3. 结果写入 `logs/restore_drills.jsonl`。
4. operator dashboard 展示最近一次 drill 结果。

### 5.8 Specialist Workflow

典型场景：

1. super agent 先决定该任务是否需要 specialist。
2. specialist 只在自己的 authority boundary 内执行。
3. specialist 不直接定义最终完成，只提交结果和风险。
4. 需要 review 的任务进入 reviewer gate。

当前应该逐步稳定下来的典型分工：

1. `discussion` 负责对话，不负责高风险执行。
2. `writer` 负责表达与成稿，不负责最终事实裁定或外部 publish 决策。
3. `researcher` 负责证据、文献和论证支撑，不负责最终文风。
4. `analyst` 负责市场与结构化判断，不负责越权发布。
5. `socialmedia` 负责分发动作，不负责替代内容判断。
6. `podcast` 负责音频生成与发布流程，不负责替代 writer / researcher 做内容审定。
7. `coder` 负责代码与测试，不负责绕过 design / PR policy。

### 5.9 Reviewer Workflow

典型场景：

1. specialist executor 产出 artifact 和 specialist report。
2. reviewer 在自己的 domain scope 内判断是否：
   `approve / revise / block / escalate`
3. super agent 根据 reviewer verdict 决定是否继续推进。
4. human 在高风险或 reviewer 无法收敛时介入。

reviewer 不是装饰层。

如果 reviewer verdict 是 `revise / block / escalate`，系统不应该假装任务已经完成。

## 6. Mira 怎么记录

Mira 不是只靠上下文窗口活着，她会留痕。

主要记录包括：

1. soul files
2. memory / belief / journal
3. thread history
4. task state
5. artifacts
6. failure log
7. calibration / reflection records
8. operator dashboard snapshot
9. restore drill records
10. specialist reports
11. review verdicts
12. boundary violation incidents

## 7. Mira 怎么记住事情

当前记忆分成几类：

1. 用户和系统长期身份相关内容。
2. 任务过程与执行结果。
3. belief / worldview / preference。
4. journal 与 reflective notes。

正确期待是：

1. Mira 会持续记住重要上下文。
2. Mira 会有漂移风险，所以需要治理。
3. Mira 的记忆必须越来越结构化，而不是越来越多文本。
4. reviewer 和 specialist 的判断也应逐步沉淀成可检索经验，而不是只留在单次输出里。

## 8. Mira 每天应该交付什么

在 production scope 里，Mira 每天应该优先交付：

1. 被采用的高价值输出。
2. 可检查的产物。
3. 明确状态。
4. 明确失败原因。
5. 明确下一步。

她不应该优先交付：

1. 看起来很聪明但无法验证的回答。
2. 很长但不可执行的解释。
3. 未确认就宣称完成的动作。

## 9. Mira 的工作习惯

理想状态下，Mira 的工作习惯应该是：

1. 先澄清边界，再执行。
2. 先验证产物，再宣称完成。
3. 先留下记录，再进入下一步。
4. 先回退和降级，再假装正常。

## 10. 出问题时怎么理解 Mira

当 Mira 出问题时，应该先问：

1. 这是输入问题、规划问题、执行问题，还是验证问题？
2. 这是 workflow 设计缺陷，还是单次模型失败？
3. 这是 connector 问题，还是主系统问题？
4. 这是 state corruption，还是单一 artifact 失败？

看诊断时优先看：

1. task state
2. failure log
3. artifacts
4. connector status
5. recent changes
6. operator dashboard
7. restore drill log
8. specialist report
9. review verdict

## 11. 这份 Handbook 的边界

这份文档解释 Mira 如何工作，但不定义底层 canonical design。

如果行为和设计冲突，以 `system-design.md` 为准。
