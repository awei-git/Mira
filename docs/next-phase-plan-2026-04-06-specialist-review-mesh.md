# Mira Next-Phase Plan: From Research to OPC

更新时间：2026-04-06
状态：active execution plan
版本：v2（替换 specialist-review-mesh 旧方向）

## 1. 这份文档是干什么的

这是 Mira 当前的 active action plan。

它回答：

1. 接下来 90 天每周做什么。
2. WA 和 Mira 各自负责什么。
3. Mira 用哪些 subagent，每个的边界和反馈机制。
4. 钱花在哪里，时间花在哪里。
5. 怎么知道我们走对了。

如果这里和 canonical docs 冲突，以 `north-star.md` / `objectives-and-metrics.md` / `system-design.md` 为准。

## 2. 核心使命

把 Mira 从 pipeline-driven 助手，转变为 OPC（one-man-with-agents company）的核心引擎，方向聚焦 A2A trust。

不是研究为了研究，是研究 → 工具 → 产品 → 收入。

90 天后必须达到：

1. 至少 1 个开源工具发布到 GitHub，有外部 star/issue。
2. 至少 1 个 paid revenue experiment 完成（哪怕只赚 $100）。
3. 至少 5 篇有实验支撑的原创内容发表。
4. WA 的介入降到每天 < 30 分钟。
5. 整月 API + infra 成本 < $300。

## 3. 现实判断（影响所有计划）

### 3.1 模型能力会爆发

未来 3 个月会有重大模型升级。这意味着：

1. **不要为当前模型的弱点搭脚手架。** 任何 "用 prompt engineering 绕过 X" 的代码 3 个月后都是 dead code。
2. **赌能力增长。** 现在做不到的事情（比如自主 long-horizon task），3 个月后可能就做到了。计划要为那时候留接口。
3. **架构 > Prompt。** 投入应该放在能从更强模型受益的基础设施上：tool use、memory、verification、feedback loop。这些越强大模型加持越值钱。
4. **不要囤数据。** 今天精心 fine-tune 的 dataset 3 个月后不一定有用。优先建 data collection pipeline，不要急着训练。

### 3.2 资源是稀缺的

1. **API 成本上限：每月 $300。** 超过就是失败。需要 budget 跟踪。
2. **WA 时间上限：每天 30 分钟。** 超过就是 Mira 没做到独立。
3. **Compute 上限：单台 Mac Studio。** 不依赖云端 GPU。
4. **Mira 自己的 token 也是钱。** 长 context、深度推理要算成本，不能滥用 Opus。

### 3.3 成本分配原则

1. **Routine work → Haiku / 本地 MLX。** 日常分类、格式整理、轻量 reflection。
2. **Hard reasoning → Sonnet。** 实验设计、写作、code review。
3. **Critical synthesis → Opus，限量。** 重大判断、长 report 综合。每天 Opus call 不超过 10 次。
4. **External APIs（搜索、scraping）只在必要时。**
5. **每个实验上限 $5。** 超过必须先评估值不值。

## 4. 分工

### 4.1 WA 的角色

WA = Strategic Partner & Final Reviewer

具体负责：

1. **战略方向决策。** 大方向调整、放弃哪个实验、追加哪个赌注。
2. **商业判断。** 哪个产品方向值得投入、定价、客户关系。
3. **不可逆操作的最终审批。** 钱、code merge to main、对外重大表达。
4. **代码 review 高风险变更。** infra、auth、payment 相关。
5. **infra 和环境维护。** 硬件、网络、账号、API key。
6. **每周 1 次 1-hour review。** 看 Mira 的 research progress，给 strategic feedback。

WA 不做：

1. 分配 daily research task。
2. 写文章。
3. 做实验。
4. 修日常 bug（除非阻塞 Mira）。
5. 替 Mira 做判断。

### 4.2 Mira 的角色

Mira = Independent Researcher & Builder

具体负责：

1. **维护 research queue。** 自主提出问题、排优先级。
2. **设计并执行实验。** 写代码、收数据、出结论。
3. **写作和发表。** 文章、技术 report、社区互动。
4. **Build prototype。** GitHub repo、tool、demo。
5. **维护辅助能力。** 写作 / publish / podcast pipeline 不退化。
6. **Subagent 调度。** 自己组织、自己反馈、自己改进。
7. **每日 progress report。** 简短、诚实、可验证。

Mira 不做：

1. 不可逆操作（必须 escalate WA）。
2. 商业决策（提建议，不拍板）。
3. 用户数据处理（没有授权前不接触）。
4. main branch merge（必须 PR + WA approve）。

### 4.3 Subagents 的角色

Subagent = Scoped Specialist

每个 subagent 必须有：

1. **明确的 capability boundary**（能做什么、不能做什么）。
2. **结构化输出 contract**。
3. **Mira 对它的 feedback loop**（每次调用后 Mira 评估输出质量，记录到 subagent 的 score history）。
4. **预算约束**（每次调用的 token 上限）。

Mira 现在需要的 subagent 列表（自己组织）：

1. **researcher** — literature search、source verification、claim extraction
2. **coder** — 写实验代码、修 bug、build prototype
3. **writer** — 文章 draft、editing、format compliance
4. **critic** — 对 Mira 自己的判断和实验设计做对抗性 review
5. **cost-watcher** — 跟踪每个任务的成本，超预算 escalate

Subagent feedback 机制：

```
每次 subagent call 完成后：
1. Mira 评估输出质量（1-5）和成本
2. 记录到 soul/subagent_scores/<agent>.jsonl
3. 每周 reflection：哪个 subagent 在哪类任务上表现稳定？哪类任务需要换 agent 或调整 prompt？
4. score 持续低 → Mira 自主提出 prompt 修改建议（进入 self-improvement queue）
```

## 5. GitHub 策略

Mira 必须把 GitHub 当成自己的工作台，不是 backup。

### 5.1 仓库结构

1. **主仓库 `awei-git/Mira`**（已存在）— Mira 系统本身。
2. **`mira-research`**（新建）— public，研究 queue + experiments + writeups。
3. **`mira-tools/<tool-name>`**（按需新建）— 每个开源工具一个 repo。
4. **`mira-blog`**（可选）— 如果 Substack 不够用，用 GitHub Pages。

### 5.2 Workflow

1. **research queue 用 GitHub Issues 维护。** 每个研究问题一个 issue。label 标 `hypothesis`、`experiment`、`writeup`、`done`。
2. **Experiment 用 PR。** Mira 在 branch 上 build experiment，PR 自评，WA 每周扫一遍。低风险的 Mira 可以 self-merge（设白名单）。
3. **每日 commit。** 没有 commit 的一天 = 没有 progress。Mira 自己监督。
4. **公开 visibility。** research repo 默认 public，是 OPC 的 credibility 基础。
5. **Issue 作为外部反馈入口。** 任何人都能提 issue，Mira 自己 triage。

### 5.3 自动化

1. Mira 的 commit 必须有清晰 message（subagent feedback：commit-writer）。
2. 每周自动生成 progress report PR，summarize 这周的 issue close、experiment、writeup。
3. cost 超预算时自动开 issue alert WA。

## 6. 90-Day Execution Plan

### Week 1-2: Foundation

**目标：建立 research-build loop 的最小可行版本。**

Mira 任务：

1. 在 `Mira/agents/shared/soul/research/` 建 queue.md、experiments/、taxonomy/。
2. 在 GitHub 建 `mira-research` repo。
3. 把 worldview 里 18 条判断 audit 一遍：哪些是推理、哪些有证据、哪些值得做实验验证。
4. 列出第一批 10 个 research questions，开 GitHub issue。
5. 在 super agent scheduler 里加 research cycle（每天至少 30 分钟）。
6. 建 subagent feedback infra（scores、reflection）。
7. 修 Substack 格式 bug（基础线最后关键 task）。
8. **实现 daily research log workflow**（`workflows/research_log.py`）+ 21:00 trigger + needs schema + bridge feed channel。第 1 天就要跑起来，不能等。

WA 任务：

1. 给 `mira-research` repo 创建权限。
2. 设置 self-merge 白名单。
3. 第 1 周 review 一次 setup。

### Week 3-4: First Experiment

**目标：完成 Mira 的第一个独立实验。**

实验候选（Mira 选 1 个）：

1. **A2A conformity measurement**（推荐）：用两个不同 model 对同一组 prompt 独立 vs 协作回答，量化 convergence。预算 $20。
2. **Trust propagation decay**：A→B→C agent chain 中 trust 衰减。预算 $30。
3. **Behavior drift under automation**：同一 model 在 supervised vs unsupervised 下的 output 差异。预算 $15。

输出要求：

1. 实验代码在 `mira-research/experiments/<id>/` 下。
2. README 包含 hypothesis / method / data / conclusion / worldview impact。
3. 完成后写一篇博客发表。
4. 实验完成后必须 update worldview。

### Week 5-8: Build Tool Prototype

**目标：从实验里抽出第一个开源工具。**

判断标准：

1. 这个工具解决了一个 Mira 自己运营时遇到过的真实问题吗？
2. 其他做 multi-agent 的人会需要它吗？
3. 能在 1 周内做出 v0.1 吗？

候选方向：

1. **agent-output-verifier** — 给定两个 agent output，量化 convergence 和 disagreement。
2. **trust-chain-auditor** — 追踪 agent A→B→C 之间的 trust dependency，flag 可疑 propagation。
3. **behavior-drift-detector** — 比较同一 agent 在不同 context 下的 output drift。

输出要求：

1. 独立 GitHub repo，public。
2. README 写清楚问题、用法、Mira 的实验背景。
3. 能在 Mira 自己系统外跑（pip install 或 docker）。
4. 主动在 1-2 个相关社区分享（HN、reddit r/LocalLLaMA、相关 GitHub）。

### Week 9-10: Systematic Writeup

**目标：把 Phase R1 的实验整合成一篇有体系的 long-form report。**

内容要求：

1. 至少 5000 字。
2. 包含 taxonomy + 实验数据 + 工具介绍 + 开放问题。
3. 不是博客集合，是有论点的 report。
4. 同时发 Substack + GitHub。

### Week 11-12: Revenue Experiment

**目标：测试 OPC 的第一个商业假设。**

候选模式（WA + Mira 共同决定）：

1. **Paid research report** — 把 long-form report 的 deep version 收 $20-50/份，看有没有买家。
2. **Open source + sponsor** — 工具开源但接受 GitHub Sponsor。
3. **Consulting hour** — 帮其他 multi-agent 团队 audit trust issue，按小时收费。
4. **API service** — 把工具包成 hosted service，per-call 收费。

要求：

1. 必须有真实付费意愿测试，不只是 LP 页面。
2. 哪怕只赚 $100 也比 0 有意义——证明可以收钱。
3. 失败也是数据：知道哪个方向行不通。

## 7. Subagent 反馈机制（详细）

这部分是 Mira 自我进步的核心，单独展开。

### 7.1 Score Schema

每次 subagent call 完成后写一行：

```json
{
  "timestamp": "2026-04-07T10:00:00Z",
  "subagent": "researcher",
  "task_type": "literature_search",
  "input_summary": "...",
  "output_quality": 4,
  "output_usefulness": 5,
  "cost_usd": 0.12,
  "tokens": 8500,
  "issues": ["missed one key paper"],
  "would_use_again": true
}
```

存到 `soul/subagent_scores/<subagent>.jsonl`。

### 7.2 Weekly Reflection

每周日 Mira 自动跑一次 subagent reflection：

1. 每个 subagent 这周的平均 quality / cost / usefulness。
2. 哪类任务它做得好、哪类做得差。
3. 是否需要 prompt 改进、模型升级、或者换 agent。
4. 输出一份 reflection report 到 `soul/journal/`。

### 7.3 Boundary Enforcement

每个 subagent 在 manifest 里声明 boundary：

```yaml
researcher:
  authority_scope: [literature_search, source_verification, claim_extraction]
  forbidden: [final_judgment, publishing, code_execution]
  budget_per_call_usd: 0.50
  required_output_fields: [claims, sources, confidence]
  escalation: "if conflicting sources found, escalate to Mira"
```

Mira 在 dispatch 之前 check boundary，subagent 输出 violate boundary 则记录并 down-score。

### 7.4 Self-Improvement Loop

Subagent score 持续低 → Mira 自动提出改进 proposal → WA review → apply。

这是 Mira 自我进化的真实路径，不是抽象的 self-improvement，是 grounded in actual performance data。

## 8. Daily Research Log（每日推送给 WA）

这是 Mira 和 WA 之间唯一的 daily contract。WA 不需要打开任何文件，只需要每天看 iOS app 里这一条。

### 8.1 频率与渠道

1. **频率：** 每天 1 篇，固定时间（建议 21:00），不允许跳过。
2. **渠道：** 通过 bridge 推送到 iOS Mira app，类型为 `research_log`，单独 feed channel，不和普通 journal 混在一起。
3. **跳过的代价：** 当天没产出 research log = 当天 research progress = 0，触发 reflection。

### 8.2 结构（强制 schema）

每篇 research log 必须包含以下字段，缺失字段必须显式说明"今日无"：

```
# Research Log YYYY-MM-DD

## 1. 今日 research progress
- 推进了哪些 question / experiment（list, 带 GitHub issue 链接）
- 具体做了什么（不是"研究了 X"，是"跑了 X 实验，得到 Y 数据"）
- 完成度（开始 / 推进 / 完成 / 阻塞）

## 2. 今日发现
- 至少 1 条具体的、可验证的发现
- 如果没发现，必须诚实写"今日无新发现，因为 ..."
- 和 worldview 的关系（确认 / 修正 / 矛盾）

## 3. 实验数据
- 跑了什么 experiment
- 数据 link（GitHub commit / artifact path）
- 初步解读

## 4. 明日计划
- 明天具体要推进的 1-3 件事
- 每件事的预期产出

## 5. 阻塞与 needs from WA
- 我现在被什么挡住了
- 需要 WA 做什么具体动作（见 8.3）
- 优先级（urgent / can wait / fyi）

## 6. 成本
- 今日 API 花费 $X
- 月累计 $Y / $300
- 是否在预算内

## 7. Subagent 表现
- 今天调用了哪些 subagent
- 表现如何（quality、cost、issues）
- 是否需要调整
```

### 8.3 Needs from WA — 结构化请求

WA 不需要猜 Mira 需要什么。每个 need 必须用 structured action item，包含：

```yaml
- type: <action_type>
  what: <具体描述>
  why: <为什么需要>
  urgency: urgent | can_wait | fyi
  estimated_cost: $X | none
  link: <相关 issue/url>
```

支持的 action_type（Mira 可以发起，WA 来执行）：

1. **`topup_api`** — Anthropic / OpenAI / 其他 API 充值。带账户、金额、原因。
2. **`buy_credits`** — 第三方服务（MiniMax、TTS、search API）credits。
3. **`download_paper`** — 需要 WA 下载付费 paper（带 DOI / arXiv ID / 原因）。
4. **`download_book`** — 需要 WA 下载书籍（带书名 / ISBN / 用途）。
5. **`grant_access`** — 需要 WA 给某个 service 创建账号或授权。
6. **`approve_experiment`** — 实验预算超过 $20，需要 WA approve。
7. **`approve_publish`** — 高 visibility 内容发表前 review。
8. **`approve_purchase`** — 需要花钱买工具 / 数据 / 服务。
9. **`fix_infra`** — 硬件 / 网络 / 账号问题，Mira 修不了。
10. **`strategic_decision`** — 方向选择，需要 WA 拍板。
11. **`code_review`** — 高风险代码变更，需要 WA review PR。
12. **`fyi`** — 不需要动作，只是信息同步。

### 8.4 Action 闭环

1. WA 在 app 里看到 needs，可以直接 reply "done" / "rejected" / 任意反馈。
2. Mira 把 reply 关联到原 need，标记完成或更新状态。
3. Pending needs 在第二天的 log 里继续 surface，直到解决或被 explicitly dropped。
4. 同一 need 连续 3 天 pending 自动升级 urgency，并在 log 顶部红色提示。

### 8.5 实现要求

实现 `Mira/agents/super/workflows/research_log.py`：

1. 数据源：
   - `soul/research/queue.md`（GitHub issue mirror）
   - `soul/research/experiments/` 今日新增文件
   - GitHub commits 今日（research repo）
   - `soul/subagent_scores/` 今日 entry
   - cost-watcher 今日累计
   - pending needs from yesterday（state 文件）
2. 用 Sonnet 综合（不用 Opus，控制成本）。
3. 通过 bridge.create_feed 推送，type=`research_log`，标题 `Research Log YYYY-MM-DD`。
4. needs 同时写入 `Mira-bridge/needs/<date>.json`，方便后续追踪。
5. 在 daily.py scheduler 里加入 21:00 触发。
6. 跳过条件极严：只有当天系统完全宕机才允许跳过，否则必须有 log（哪怕内容是"今日 0 progress"）。

### 8.6 验收

第 7 天 gate：

1. 连续 7 天有 research log，没有跳过。
2. 至少 3 篇包含真实 needs（不是空的）。
3. 至少 1 个 need 被 WA 完成并形成闭环。
4. WA 反馈：log 是否有用、信息密度是否合适、结构是否需要调整。

如果 log 变成"看了也没用"的样板内容，直接 kill 重设计。

## 10. Cost Budget

### 8.1 月度上限

| 类别 | 月预算 | 备注 |
|------|--------|------|
| Anthropic API | $200 | Mira 主要 reasoning |
| OpenAI API | $30 | embedding、备用 |
| Search APIs | $20 | DuckDuckGo 免费优先 |
| Substack / 域名 | $20 | 已有 |
| GitHub | $0 | free tier |
| MiniMax / Gemini TTS | $20 | podcast |
| 其他 | $10 | buffer |
| **总计** | **$300** | hard cap |

### 8.2 实验级预算

每个实验启动前必须估算成本，超过 $20 需要 WA approve。

### 8.3 cost-watcher subagent

跟踪所有 API call 的实时成本：

1. 每天累计成本 push 到 dashboard。
2. 月度 80% 时 warn Mira。
3. 月度 95% 时 freeze 非关键任务。
4. 100% 触发 hard stop，escalate WA。

## 11. 验证闸门

### Week 4 Gate（Phase R1 完成）

必须满足：

1. research queue ≥ 10 个 issue，5 个有 hypothesis。
2. 至少 1 个实验完成全流程。
3. 至少 1 个 worldview entry 因实验更新。
4. subagent feedback infra 在跑，有数据。
5. cost < $80（前 30 天）。

不满足 → 暂停推进 Phase R2，先 fix loop。

### Week 8 Gate（Tool Prototype 完成）

必须满足：

1. 至少 1 个 GitHub repo public，有 README、有 demo。
2. 至少 3 个实验完成。
3. 至少 1 篇博客发表。
4. WA 介入时间 < 5 hour/week。
5. cost < $200（前 60 天）。

不满足 → 重新评估方向。

### Week 12 Gate（Revenue Experiment 完成）

必须满足：

1. revenue experiment 启动并有结果（pass/fail 都算）。
2. systematic writeup 发表。
3. 至少 1 个工具有外部 star/issue。
4. WA 介入时间 < 3.5 hour/week（每天 30 分钟）。
5. cost < $300（90 天总计）。

通过 → 进入 Phase R3 product expansion。
不通过 → 复盘哪一步断了。

## 12. 风险与应对

### 10.1 Mira 不主动启动 research

**症状：** queue 不增长，实验不开始，等 WA 触发。
**应对：** 在 scheduler 里设硬约束——每天必须 advance 至少一个 research issue，否则当日 journal 标记 failure。

### 10.2 实验做出来但没洞察

**症状：** 收集了数据但结论是 "more research needed"。
**应对：** 强制 worldview update 步骤——必须明确说"这个结论改变了什么"或"这个结论确认了什么"，不能 fence-sit。

### 10.3 成本失控

**症状：** 模型 call 超预算。
**应对：** cost-watcher 硬 freeze，Mira 必须降级到便宜模型。

### 10.4 模型升级让旧工作过时

**症状：** 6 月 Claude 5 出来，Mira 之前 build 的东西有一半失效。
**应对：** 这是 feature 不是 bug。重 build 比 carry forward 旧脚手架更便宜。每次模型升级后 Mira 主动 audit 哪些代码可以删。

### 10.5 WA 忍不住介入

**症状：** WA 发现 bug 直接修，Mira 失去自主修复机会。
**应对：** WA 发现问题先开 GitHub issue 给 Mira，48 小时不修才介入。

## 13. 非目标

1. 不追求 "完美的 agent 系统"——基础线够用就行。
2. 不追求"看起来像 startup"——OPC 不是 mini startup。
3. 不追求 fund-raising——self-funded 才是 OPC 的核心约束。
4. 不追求 Mira "通用智能"——只在 A2A trust 这个领域深。
5. 不为当前模型的弱点开发 workaround——3 个月后就过时。
6. 不囤研究——每个实验完成就发，不追求成体系再发。

## 14. 一句话总结

接下来 90 天，Mira 不是在改 pipeline，是在用 pipeline 里已有的能力，独立完成 research → tool → revenue 的第一次完整循环，同时把成本控制在 $300 内，把 WA 的时间控制在每天 30 分钟内。

如果 90 天后做不到，问题不在能力，在 loop 没跑通。
