# Mira V2 Plan — Lock the Kernel, Protect the Breakout, Stay Personal

更新时间：2026-05-01
状态：active execution plan
版本：v2.9（取代 next-phase-plan-2026-04-06；不替代 north-star / system-design / objectives-and-metrics）
延续 hard rules：[CLAUDE.md](../../CLAUDE.md) §HARD RULES 1–6 全部继承，本文档不放松其中任何一条

v2.9 vs v2.8 的差别（dev-box / local LLM canonicalization）：
- §0.5.7 Tier 0 local runtime 固定为 **oMLX + `gemma-4-31b-it-4bit`**，不再写泛化 `mlx` / Gemma 3 12B。
- 本地模型 cache 固定在 **`/Volumes/aw_swap/omlx-cache`**；`HF_HOME` / `HF_HUB_CACHE` / `XDG_CACHE_HOME` 必须由 `homebrew.mxcl.omlx` LaunchAgent 持久化。
- Ollama 明确为 legacy，不允许重新进入 Mira runtime / routing / recovery path。
- 文档内 DB baseline 对齐当前 dev box：PostgreSQL 17。

v2.8 vs v2.7 的差别（reviewer 第四轮反馈，防 V3 scope 漂入 V2）：
- §0.5.7 routing.yaml 拆成 V2 actual + V3 target 两块；V2 块用 Tier 1 OpenAI 主接 routine；V3 块标 "目标态非 V2 deliverable"
- §3.3.2 bridge contract iCloud 行改 "一次性 manual recovery importer"（与 §3.9 一致）
- §3.12.2 identity_check：V2 用规则 + Tier 1 OpenAI gpt-5-mini；oMLX 推 V3；degraded mode 仅依赖规则层
- §3.15.3 sensitivity_topic_check：V2 用 Tier 1 OpenAI gpt-5-mini；oMLX 推 V3
- §4 mapping table：#4 skill mesh / #8 external_learn 改 V2 用 Tier 1，不再说 Tier 0
- §0.5.6 "speculative execution" 拒绝理由改 "budget discipline"，不再说 "cost cap"

v2.7 vs v2.6 的差别（reviewer 第三轮反馈，全 stale ref 清理）：
- §0.5.7 cost-saving 章节：「Tier 0 默认接收口 + $200 cost cap」改为 V3 目标，V2 仅证明路径
- 月度 cost 表：V1 / V2 持平 / V3 真省钱三栏分清；删 "节省 $80-150/月" 误导
- §3.10.3 retrieval：embedding V2 用 OpenAI text-embedding-3-small；oMLX 本地推 V3
- §3.10.11 Memory acceptance 与 §9 #18 一致：不强求 100 条；consolidation 改为 ≥ 2 次（Memory Week 4 才上）
- §3.11.7 5-file 标准：V2 demo 凑齐 4-file；真实 5-file 闭环（含 7-day outcome）推 V3
- §3.19.12 mini-tier 删，引用 §9 canonical 表（避免双 source 漂移）
- §10 summary：self-evolution 描述改为 "stage 1-4 + 4-file + 5-file dry-run schema demo"，不再说 "真闭环 5-file"
- Bridge TLS 证书 expiry 加入 §3.5.3 daily auth_health check（< 30 天 warning，< 7 天 critical）

v2.6 vs v2.5 的差别（reviewer 第二轮反馈）：
- Block fix 7 条：calendar 残留 / dedupe V3 App Store / Memory degraded mode 仅覆盖 adapter 不覆盖 core Postgres / self-evolution V2 仅 4-file 不要求 5-file（7-day outcome 推 V3）/ Memory consolidation cron acceptance 改为 ≥2 次（Memory Week 4 才上）/ external_learn 1 round（4 rounds 推 V3）/ Tier 0 wording 统一（V2 Week 1 起 2 adapter，Week 5 补齐 6 adapter + oMLX 1-2 task type 试点）
- Design fix 6 条：HTTPS 自签证书 + iOS pinned cert / iCloud 旧 command path 改为 "一次性 recovery importer"（手动 CLI，永不入 dispatch loop）/ embedding V2 OpenAI text-embedding-3-small + V3 oMLX local / Week 6 article cutover 必须 Day 1-2 否则 "not evaluated" / self_audit 改 "≥80% 真 finding verified + ≥5 representative" 防造假 / cost criterion 从 Tier A 移到 Tier B（reliability > cost）

v2.5 vs v2.4 的差别（基于第三方 reviewer 第一轮反馈）：
- Must-fix 7 条修复（kernel count 7，no SQLite default，STABILITY 删 CKSyncEngine + CF Workers，iCloud 严格 backup-only，删 calendar block 残留，acceptance count 26）
- Phase plan reshape：Week 1 聚焦 Postgres + bridge + app submission（用户 #1 痛点提为 Tier A 第一），Week 2 SwiftData + verifier，Week 3 publish gate，Week 4 Memory（degraded-mode），Week 5 router shadow + Tier 0 试点，Week 6 watchdog + DR + 验收
- Self-evolution V2 范围降到 stage 1-4 + 4-file validated proposal + 5-file dry-run schema demo；真实 5-file 闭环（含 7-day outcome）+ 3 commit 推 V3
- Tier 0 全推广推 V3（V2 仅 1-2 task type 试点）
- 完整 voice metrics 推 V3（V2 留 positive_guide draft + outcomes pipeline）
- Cost target：V2 持平 V1 baseline（不退步即合格），-30% 推 V3
- Internal verified state 区分 internal (`completed_unverified / verified`) vs public (`done` only when verified)，不破 app contract
- Memory degraded mode：仅覆盖 memory_adapter / pgvector index 故障，**不覆盖 core Postgres down**（那是 §3.13 DR scenario）
- Substack 0-day → critical incident signal + Mira 推 decision card，**WA explicit reply 后才回滚**（不自动）

---

## 0. 这份文档为什么存在

V1（2026-04-06 next-phase plan）的方向是对的：让 Mira 成为独立研究者。
V1 没做到的事：让 Mira 先成为一个**能闭环、能保住既有突破口、能稳定到不再大改主干**的 agent。

V2 的全部任务：

1. **保住 substack 突破口。** substack 账号正在起势，每条文章 / note / 回复都不能因 refactor 断档。任何动 substack 链路的代码必须走 strangler fig（§3.1），不允许「停机迁移」。
2. **锁死稳定内核。** 这一版定义出 7 个 load-bearing 接口（§3.0），写进 `STABILITY.md`，**V3 不再动它们**。新功能在插件层加，不在内核动手术。
3. **闭环 4 件事。** 检测、提案、执行、验证 —— 让 Mira 修自己 / follow user / 跑 pipeline / 用 skill 这四件最基础的事可靠完成。
4. **iOS app 修到能正常用。** message thread 不丢、task 能成功提交、不再每周出新 bug。**不上 App Store**，个人自用。

这四件不是并列的。**1 是约束，2 是地基，3 是当前痛点，4 是 problem #1 #6 的针对性修复。**
顺序：先建好 §3.0 内核 + §3.1 迁移纪律 + §3.2 substack 公约（Week 1），再让 R1–R6（§3.3–§3.8）所有改造都在这套地基上做。iOS 修复折进 R1（bridge）+ R2（thread sync）+ R6（routing），不单设 track。

**显式不做：App Store 提交、StoreKit IAP、cloud relay、per-provider consent UI、pre-publish review surface、Sign in with Apple。** 这些是产品化负担，个人自用不需要。如果未来要上 App Store，再起单独 plan，**但不许动 §3.0 内核**。

**继续保持 full autonomy（[feedback_full_autonomy.md](../../.claude/projects/-Users-angwei-Sandbox/memory/feedback_full_autonomy.md)）：** 所有 social media 输出（substack article / note / comment / reply、bluesky、x）必须经过 writer agent（§3.5.1，quality gate），但 **不需要 user 审核**。Writer 通过 + preflight + cooldown 通过 → 自动发。

---

## 0.5 工业级最佳实践参考

这一节的存在直接答问题 #8（学了一堆 best practice 零行为变化）。
做法是：**把要偷的模式直接落到 plan 的具体 §X.Y，让模式变成 plan 内置约束，而不是又写一篇 reading note**。

### 0.5.1 Production AI Agent 已经收敛的 7 条（高置信度，赌 12+ 月）

来源：Cursor / Devin / Claude Code / Manus / Replit / Anthropic / OpenAI 公开技术文档 + engineering blog。

| 收敛项 | 来源 | 落到 V2 的哪里 |
|--------|------|----------------|
| 单线程主 loop + 一份 flat message history | Devin (Cognition「Don't Build Multi-Agents」2025-06)，Claude Code `nO` loop，Manus executor | §3.0 内核：`super agent loop` 锁定 |
| Sub-agent 只用 process/VM/worktree 隔离 + file handoff，不在进程内共享 message state | Replit、Cursor 2.0 worktree、Manus E2B sandbox | §3.0 内核：`task_worker subprocess + result.json` 锁定 |
| 文件系统作长期 memory | Manus（"file system IS the memory"）、Claude Code `claude-progress.txt`、Stevens (Litt) | §3.0 内核：`ArtifactStore` 端口 |
| Plan-then-execute + 持久化 plan artifacts | Anthropic 两 agent 模式（initializer + worker）、Perplexity planner、Manus | §3.8 LLM router 输出 explicit plan，写盘 |
| KV-cache hit rate 是 production cost 关键 metric → 系统 prompt 稳定，工具不能 hot-swap | Manus context engineering | §3.6 skill retrieval **不注入 system prompt**，注入 user-message 段 |
| MCP 作 tool-call 边界（多厂商 + Linux Foundation） | Anthropic / OpenAI / Google / Microsoft 都背书 | §3.0 内核：tool 调用走 MCP-shaped 接口 |
| Bounded verifier step（不是开放式 reflection） | Anthropic「Building Effective AI Agents」、Replit verifier、Manus verification sub-agent | §3.5 mandatory writer / preflight gate |

**关键判断：Mira 现有骨架（super agent loop → task_worker 子进程 → result.json handoff → soul 文件系统 memory）已经是收敛模式。** V2 的事是**锁住**它们写进 STABILITY.md，不是改它们。

### 0.5.2 Durable Execution：DBOS Transact + Postgres（V2 落地，非 V3 horizon）

参考研究 ([Temporal](https://temporal.io/), [DBOS Transact](https://github.com/dbos-inc/dbos-transact-py), [Restate](https://restate.dev/), [Hatchet](https://hatchet.run/)) 比较后的结论：

- **DBOS Transact + Postgres backend** 选定。Stonebraker（Postgres / Turing）+ Zaharia（Databricks CTO）班底，MIT 协议，**库不是 server**，`@DBOS.workflow` + `@DBOS.step` decorator 就拿到 Temporal 级 durability。
- **Postgres 不是 SQLite**（与 §3.0.4 tech stack 表一致）：production-grade、可备份、与 Mira 其他持久化层（task state、audit log、bridge state）共一套数据库，不再 jsonl + sqlite + 散落 yaml 多源。
- 不选 Temporal：需要单独 server 进程 + worker；overkill。
- 不选 Restate：Rust server BSL 协议。
- 不选 Inngest / Hatchet / Trigger.dev：HTTP/serverless 形状不匹配「常驻 Python 进程」拓扑。
- 不自己写 mini-engine：determinism / idempotency-key / versioning 三个坑都不容易过。

**V2 落地：** §3.0 内核第 1 条「durable task dispatcher」+ 第 5 条「append-only audit log」由 DBOS + Postgres 实现。V3 想换实现，换 adapter 就好，workflow shape 不动。

### 0.5.3 Strangler Fig（Martin Fowler）— V2 全程迁移纪律

参考：[martinfowler.com/bliki/StranglerFigApplication](https://martinfowler.com/bliki/StranglerFigApplication.html)、[Microsoft Strangler Fig pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/strangler-fig)、Branch by Abstraction。

应用到 substack 这种"不能停"的 pipeline 时，每次替换都强制走：
1. 先建抽象（port / registry）
2. 老实现继续供生产
3. 新实现先 shadow run（写日志不生效）
4. shadow vs 老实现 N 天比对
5. 切换流量到新实现
6. 老实现降级为 fallback，30 天观察期
7. 30 天无 incident → 删除老实现

**写进 §3.1 / §3.2，是 V2 的硬约束。**

### 0.5.4 Stable Kernel + Plugin Layer（Emacs / Postgres / Linux 40 年模型）

参考：[Cockburn Hexagonal](https://alistair.cockburn.us/hexagonal-architecture)、[ACM HOPL IV: Evolution of Emacs Lisp](https://dl.acm.org/doi/pdf/10.1145/3386324)、[O'Reilly Microkernel](https://www.oreilly.com/library/view/software-architecture-patterns/9781098134280/ch04.html)。

40 年活下来的系统都做同一件事：**小内核 + 大插件层 + 内核接口神圣不可破**。Emacs Lisp 的 HOPL 论文原文：核心保持稳定是为了维持第三方包兼容。

落地：§3.0 定义 Mira 的 7 个内核接口，§4.6 写 STABILITY.md，V3 不破内核，只在插件层加。

### 0.5.5 App Store readiness（V2 不做，仅留参考）

V2 不上 App Store。但若未来要上，[Apple App Review Guidelines 2025-11](https://developer.apple.com/news/?id=ey6d8onl) 三个对 Mira 致命的条款是：
1. **5.1.2(i)** — 多 LLM provider 必须 per-provider unbundled consent UI。
2. **1.2 UGC** — Mira draft 自动发 substack，Apple 视 Mira 为 originator，必须 in-app pre-publish review surface。
3. **Mac-as-backend** 需要 cloud relay 让 reviewer 不在家庭 Wi-Fi 也能 demo。

如果未来要上，写新 plan，**不许动 §3.0 内核**，只在插件层加这三块。

### 0.5.6 显式不偷的东西

| 拒绝项 | 理由 |
|--------|------|
| LangGraph / CrewAI / AutoGen 整套依赖 | 单机单用户系统不背这种依赖树。永久不做 |
| In-process multi-agent 协作（agent 之间共享 message state） | Cognition 已 publicly 否决，convergence pattern 用 process 隔离 |
| Hot-swap tool 集 | 杀 KV-cache，Manus 的 production 教训 |
| Vector DB 作主 memory | convergence 是 file system + SQLite/Postgres，vector 是 add-on 不是主路径 |
| Speculative execution（多 plan 并行选最好） | cost 翻倍换边际收益，不符 budget discipline |
| 全 Hermes XML 调用格式 | Anthropic 原生 tool-use + MCP 已够 |
| 把 super agent 改成单 LLM agent loop | 放 V3 horizon，V2 不做 |

### 0.5.7 LLM 访问政策 + 三层 routing（个人 use，省钱第一）

参考：[Anthropic AUP](https://www.anthropic.com/legal/aup)、[Claude Code legal-and-compliance](https://code.claude.com/docs/en/legal-and-compliance)、Manus context engineering（KV-cache 友好）、本地推理（oMLX + Gemma 4 31B）。

V2 不上 App Store，没有 reviewer 看 auth 的约束。WA 个人 Pro/Max 订阅在自己 Mac 跑自己的 agent 是 Anthropic docs 明说的 "ordinary individual usage"。

**核心 cost-saving 思路：本地 Gemma 4 31B 接住所有不需要 frontier 的活。**

WA 已搭 oMLX + `gemma-4-31b-it-4bit`，长期闲置只跑 idle-think。V3 把它升级为 **Tier 0 默认接收口**：所有 routine / 重复 / 低质量 bar 的工作先发本地，本地接不住再上 cheap API，cheap API 不够再上 premium。这是月度 cost 真正能压低的关键。

**本地 LLM canonical config（不再漂移）：**

```text
runtime: oMLX
serving endpoint: http://127.0.0.1:8800/v1
primary local chat model: gemma-4-31b-it-4bit
local embedding model target: nomicai-modernbert-embed-base-4bit
model/cache root: /Volumes/aw_swap/omlx-cache
HF_HOME: /Volumes/aw_swap/omlx-cache/huggingface
HF_HUB_CACHE: /Volumes/aw_swap/omlx-cache/huggingface/hub
XDG_CACHE_HOME: /Volumes/aw_swap/omlx-cache/xdg
LaunchAgent: homebrew.mxcl.omlx
max-model-memory: 20GB
```

Ollama is legacy. Mira runtime、routing、recovery、docs 都不允许再把 Ollama 当本地 LLM path。

**V2 仅证明 adapter + fallback 路径可用**（Week 5 试点 1-2 task type）；**V3 是 Tier 0 默认接收口、$200 月度 cost target 真正落地的版本**。

#### 三层 Routing Tier

| Tier | 实现 | 计费 | 接的活 |
|------|------|------|--------|
| **Tier 0 — Local** | oMLX + `gemma-4-31b-it-4bit`（cache on `/Volumes/aw_swap`）+ pgvector + 简单规则 | $0 增量 | routine 分类 / 格式 / 重复检测 / 简单生成 / similarity 排序 / log triage |
| **Tier 1 — Cheap API** | OpenAI gpt-5-mini / haiku 4.5 / Gemini Flash | $0.10–0.30 / M token | Tier 0 不够时的 fallback；轻量 reasoning；schema-strict JSON 输出 |
| **Tier 2 — Premium** | claude-code OAuth（主）→ Anthropic API key（fallback）→ OpenAI o-series | flat $20/月 + 按需 API | 公开输出（substack）/ 长综合 / 复杂 routing decision / self-evolve |

#### V2 vs V3 范围（reviewer 调整）

**V2 范围：**
- LLMProvider port + 6 adapter 最终全列：anthropic_oauth + anthropic_api + openai + gemini + minimax + omlx
- **adapter 上线分两阶段：** Week 1 上 anthropic_oauth + anthropic_api 2 个；Week 5 补齐 openai + gemini + minimax + omlx 4 个
- routing.yaml schema + Tier 1/2 routing 落地
- **oMLX adapter Week 5 试点：仅接 1-2 个低风险 task type**（如 substack inbox dedup classify + anti-AI guard scan），验证 quality fallback 机制可用
- V2 cost target：**持平 V1 baseline**（不退步即合格），**不强求降 30%**

**V3 范围：**
- oMLX adapter 大规模推广到下表全部 14 个 Tier 0 task type
- cost target：降 ≥ 30%
- 视 Tier 0 fallback rate 决定 fine-tune / 换更大本地模型

**Why：** Local Gemma adapter 是新组件，bug 概率高 + 验证成本高，6 周不可能可靠铺 14 个 task type。先在 1-2 个低风险任务跑通 quality fallback，证明机制再 V3 推广。Cost saving 是结果不是目标，先把架构打稳。

#### Tier 0 候选活清单（V3 推广目标，V2 仅试点 1-2 个）

**这些一律先发本地 Gemma，接住就完事，接不住才升 Tier 1。**

| 活 | 当前状态 | V2 后 routing |
|----|----------|----------------|
| substack inbox dedup classification（这条评论我回过吗）| §3.7.5 在改本地 sqlite | 决策走 Tier 0 |
| anti-AI guard scanning（em-dash / "not X but Y" 等） | sonnet 跑 | 规则 + Tier 0 verify |
| skill retrieval similarity 排序（80 skill 选 3）| 没跑 | Tier 0（sentence-similarity）|
| explore feed 内容分类 / triage | sonnet 跑 | Tier 0（is this relevant to A2A trust? yes/no）|
| 自我审计日志 pattern 检测 | sonnet 跑 | Tier 0（regex + Gemma 验证）|
| LLM router 简单 case（明显的 writing → writer）| 不存在 | Tier 0；模糊 case 升 Tier 2 |
| JSON repair / schema validation | sonnet 跑 | Tier 0 |
| heartbeat / status 报告格式化 | sonnet 跑 | Tier 0 |
| 简单 verifier predicate（user 说 stop X，X 是否真停了）| 不存在 | Tier 0 deterministic check 优先；模糊 case Tier 1 |
| idle-think | 已是 oMLX | Tier 0 |
| external_learn 第一遍筛（这论文跟 A2A trust 相关吗）| sonnet 跑 | Tier 0 first pass，只有 yes 才上 Tier 2 深读 |
| trace replay simple verification | 没跑 | Tier 0 |
| skill outcome scoring | 没跑 | Tier 0 |
| podcast 章节分段 / 时间标注 | sonnet 跑 | Tier 0 |
| substack metrics 摘要 | sonnet 跑 | Tier 0 |
| daily report 各 section 的简单 gather/render | sonnet 跑 | Tier 0；只有 narrative section 上 Tier 2 |

#### 哪些活必须 Tier 2（不能省）

**公开输出 / 高质量 bar / Mira 自己说话的地方。**

| 活 | Tier | 原因 |
|----|------|------|
| substack 文章正文 writing | Tier 2 | public output，writer agent de-AI pass 必须 |
| substack note 正文 | Tier 2 | public output |
| substack inbox reply 正文 | Tier 2 | public output，需要 nuance |
| 长综合 / research write-up | Tier 2 | 质量 bar 高 |
| self-evolve proposal 生成 | Tier 2 | 真要改自己代码 |
| LLM router 模糊 case decision | Tier 2 | Habermas EPUB → 选哪个 agent 这种 |
| 用户 ad-hoc 请求的 plan 生成 | Tier 2 | 用户体验 |
| external_learn 深度对照 architecture | Tier 2 | 第一遍 Tier 0 筛过后才上 |

#### 中间区（Tier 1 cheap API）

**质量 bar 中等、但 Tier 0 接不住的。优先 OpenAI gpt-5-mini（用户已有 key、cost 低、JSON 输出 strict）。**

- 中等 summarization（briefing 各段的具体写）
- 中等 verifier（不是简单 yes/no 的）
- skill 文件 frontmatter 自动补全
- 写作 draft 的初稿（最终 polish 还是 Tier 2）

#### 路由表 yaml 示例

`agents/super/runtime/registry/llm_routing.yaml` 分两阶段。

**V2 实际 routing（Week 5 末）：**

```yaml
defaults:
  on_quota_exceeded: tier_above_fallback

tasks:
  # === Tier 0 oMLX 试点（Week 5 上线，仅 1-2 task type）===
  substack_dedup_classify:
    primary: { tier: 0, adapter: omlx_gemma, model: gemma-4-31b-it-4bit }
    fallback: [{ tier: 1, adapter: openai, model: gpt-5-mini }]

  anti_ai_guard_scan:
    primary: { tier: 0, adapter: rules_then_omlx }
    fallback: [{ tier: 1, adapter: openai, model: gpt-5-mini }]

  # === Tier 1 / Tier 2 routing（Week 1-5 落地，V2 主体）===
  identity_check:
    primary: { tier: 1, adapter: openai, model: gpt-5-mini }
    fallback: [{ tier: 2, adapter: anthropic_oauth, model: claude-sonnet-4.6 }]
    # V3：oMLX local

  sensitivity_topic_check:
    primary: { tier: 1, adapter: openai, model: gpt-5-mini }
    # V3：oMLX local

  skill_retrieval_rank:
    primary: { tier: 1, adapter: openai_embed, model: text-embedding-3-small }
    # 仅写入时调 embedding；read 是 pgvector 本地查询；V3：omlx_embed local

  embedding_skill_catalog:
    primary: { tier: 1, adapter: openai, model: text-embedding-3-small }
    # V3：oMLX local nomicai-modernbert-embed-base-4bit

  embedding_memory_write:
    primary: { tier: 1, adapter: openai, model: text-embedding-3-small }
    # V3：oMLX local

  explore_feed_triage:
    primary: { tier: 1, adapter: openai, model: gpt-5-mini }
    # V3：Tier 0 omlx_gemma

  external_learn_first_pass:
    primary: { tier: 1, adapter: openai, model: gpt-5-mini }
    fallback: [{ tier: 2, adapter: anthropic_oauth, model: claude-sonnet-4.6 }]
    # V3：Tier 0 omlx_gemma first-pass

  external_learn_deep_compare:
    primary: { tier: 2, adapter: anthropic_oauth, model: claude-sonnet-4.6 }
    fallback: [{ tier: 2, adapter: anthropic_api }, { tier: 2, adapter: openai, model: gpt-5 }]

  llm_router_decision:
    primary: { tier: 2, adapter: anthropic_oauth, model: claude-sonnet-4.6 }
    fallback: [{ tier: 1, adapter: openai, model: gpt-5-mini }]
    # 明显 case 短路到 keyword fast path（不上 LLM）；模糊 case 才 Tier 2

  substack_article_write:
    primary: { tier: 2, adapter: anthropic_oauth, model: claude-opus-4.7 }
    fallback: [{ tier: 2, adapter: anthropic_api, model: claude-opus-4.7 }]
    # 不允许降到 Tier 1，public output

  substack_note_write:
    primary: { tier: 2, adapter: anthropic_oauth, model: claude-sonnet-4.6 }
    fallback: [{ tier: 2, adapter: anthropic_api }]

  inbox_reply_write:
    primary: { tier: 2, adapter: anthropic_oauth, model: claude-sonnet-4.6 }
    fallback: [{ tier: 2, adapter: anthropic_api }]

  idle_think:
    primary: { tier: 0, adapter: omlx_gemma, model: gemma-4-31b-it-4bit }
    # 现状已是 oMLX，V2 维持

  zh_tts:
    primary: { tier: 1, adapter: minimax }

  en_tts:
    primary: { tier: 1, adapter: gemini }
```

**V3 target routing（Tier 0 全推广后的目标态，非 V2 deliverable）：**

```yaml
# V3 目标：Tier 1 routine work 大规模迁到 Tier 0 omlx_gemma
# 触发条件：V2 完成 + oMLX adapter 在 1-2 task type 验证 quality fallback 可靠
tasks:
  identity_check:
    primary: { tier: 0, adapter: omlx_gemma }
    fallback: [{ tier: 1, adapter: openai, model: gpt-5-mini }]

  sensitivity_topic_check:
    primary: { tier: 0, adapter: omlx_gemma }
    fallback: [{ tier: 1, adapter: openai }]

  skill_retrieval_rank:
    primary: { tier: 0, adapter: omlx_embed_rank }
    # local-only，no API fallback

  embedding_skill_catalog:
    primary: { tier: 0, adapter: omlx_embed, model: nomicai-modernbert-embed-base-4bit }
    fallback: [{ tier: 1, adapter: openai, model: text-embedding-3-small }]

  embedding_memory_write:
    primary: { tier: 0, adapter: omlx_embed, model: nomicai-modernbert-embed-base-4bit }
    fallback: [{ tier: 1, adapter: openai }]

  explore_feed_triage:
    primary: { tier: 0, adapter: omlx_gemma }
    fallback: [{ tier: 1, adapter: openai }]

  external_learn_first_pass:
    primary: { tier: 0, adapter: omlx_gemma }
    fallback: [{ tier: 1, adapter: openai }]

  # ... 其余 Tier 0 候选活（log triage / JSON repair / heartbeat format / ...）
  # 详见 §0.5.7 Tier 0 候选活清单
```

#### 政策与 ban

1. **Tier 0 优先原则：** 任何新加的 task type，第一问「能不能本地 Gemma 接？」可以 → Tier 0。不可以才考虑 Tier 1/2。Default 必须显式声明，不允许「忘了写 routing 就走 Tier 2」。
2. **Tier 2 必须有理由：** routing.yaml 里 Tier 2 的 task 必须在 yaml comment 写「为什么不能 Tier 0/1」（一句话）。CI 检查 yaml schema。
3. **Throttle 监测：** anthropic_oauth adapter 检测到 5xx / rate-limit → 自动 fallback 到 yaml 声明的下一档。dashboard surface「fallback 次数 / 原因」。
4. **第三方 wrapper / scrape claude.ai session ban 不松：** CI grep `playwright.*claude\.ai|oauth.*proxy|claude-cli-mod|openclaw` 出现 = block。这是 ToS 违反，不商量。
5. **业务代码不允许：**
   - 直接 `import anthropic` / `openai` / `google.genai` / `minimax`（必须经 LLMProvider port）
   - 直接 `subprocess.run(["claude-code", ...])`（必须经 anthropic_oauth_adapter）
   - 直接调用本地推理 SDK / CLI（必须经 `omlx_adapter`）

#### Cost 预测

按当前调用密度估算月度成本：

| Provider | V1 估测 | V2 估测（持平 baseline） | V3 估测（Tier 0 全推广）|
|----------|---------|--------------------------|--------------------------|
| Anthropic（OAuth flat） | $20 | $20 | $20 |
| Anthropic API（fallback） | $0 | $30–80 | $30–80 |
| OpenAI API | $0 | $30–60（含 Memory embedding） | $5（仅 fallback）|
| Gemini API（TTS）| $30 | $30 | $30 |
| MiniMax（TTS）| $40 | $40 | $40 |
| Tier 0 本地（oMLX Gemma 4 31B）| $0（仅 idle-think）| $0（Week 5 试点 1-2 task type）| $0（接 14 task type）|
| **月度总计** | $90 | **$150–230**（持平到略涨，因 Memory embedding） | **$125–175**（Tier 0 真省钱）|

**Cost-saving 是 V3 的收益，不是 V2 的 deliverable。** V2 证明 adapter + fallback 路径可用；V3 把节省真做出来。任何把 V2 内 cost 降 30% 当目标的话术都是 plan inflation。

#### Tier 0 的 quality fallback 机制

Tier 0 不是「无脑发本地」。每个走 Tier 0 的 task type 必须声明 `tier0_quality_check`：

- **schema check**：output 是否符合 schema（最常用）
- **rule check**：是否符合规则（如 dedup classification 必须 yes/no）
- **confidence threshold**：local model 自报 confidence 是否 ≥ N
- **shadow vs Tier 1 比对（开发期）**：Tier 0 上线前 7 天 shadow 跑 Tier 1，统计 disagreement rate；> 10% 不允许 cutover

quality check 失败 → 自动 fallback 到 Tier 1，并在 audit log 记 `tier0_fallback_reason`，让 user 能看到本地 model 哪里不够用、是否需要换更大 Gemma 或 fine-tune。

---

## 1. North Star 不变

复述（来自 [north-star.md](north-star.md)）：

> 成为 A2A trust 领域最深入的独立研究者，用原创实验和开源工具证明自己的判断，把研究转化成可持续的商业价值。

V2 不调整 north star。
V2 的位置：**底盘 + 突破口 + 个人 quality**。底盘撑住 + substack 持续起势 + iOS app 真能用 —— 这三条是 north star 真正能展开的前置。**V2 不上 App Store**（§7）。

---

## 2. 十二个问题 → 六个根因

| # | 用户反馈 / audit 发现 | 真正的根因 | 证据 |
|---|----------|------------|------|
| 1 | iOS app 任务从未成功 | 多 transport 共存，POST 走错 IP 静默 fallback 到废弃路径 | `MiraBridge/.../CommandWriter.swift:248`, `BridgeConfig.swift:35`, `Mira/web/server.py:764` |
| 2a | substack 漏回复 | dedup HTTP 调用失败被吞，无 retry queue | `agents/socialmedia/activity_inbox.py:282-292` |
| 2b | notes / articles 绕过 writer | 多条 publish 路径，writer agent 不是必经之路 | `agents/socialmedia/notes.py:598-650`, `:943-1036` |
| 2c | x / bluesky 断了不恢复 | auth 失败被当成 transient post failure 吞 | `lib/bluesky/client.py:146-164`, `agents/super/workflows/social.py:146-148` |
| 2d | article → podcast 链路从未自动跑完 | weekly quota gate 把跨周文章卡死，无 bypass | `agents/super/publishing.py:153-296` |
| 2e | market report 只有 portfolio | daily report 是手写 template，不调 analyst，不读 Tetra briefing | `agents/super/workflows/daily.py:77-184` |
| 3 | self-correction 永远开环 | self_audit 找出 finding 后既不入 backlog 也不 verify | `agents/super/self_audit.py:519-544`, `:693-696` |
| 4 | skill 写了不用 | 各 agent 只 load 自己 skills 子集，writer 完全不 load skill | `agents/super/planning/planner.py:36-93`, `agents/writer/handler.py` |
| 5 | follow request 差，要追问 | job state 散落 4 处，disable 一个 job 要 patch N 个文件 | `runtime/jobs.py:320-334`, `daily_tasks.py:98-108`, `runtime/triggers.py:558-566` |
| 6 | app message thread 也做不好 | app 端依赖 iCloud + 本地缓存，server 不持久化 thread | `MiraApp/.../ItemStore.swift:180-208` |
| 7 | 永远在打补丁 | 没有 single source of truth、没有 verify 闭环、PR 不引用 design section | system-design.md §14 形同虚设 |
| 8 | 学 best practice 零行为变化 | self_evolve 只读自家 architecture；proposal 入 backlog 后无 outcome reward | `agents/super/self_evolve.py:143-211`, `:283-305` |
| 9 | done 在撒谎（agent 跑完没崩 ≠ user 拿到东西） | 没有 type-aware verifier；每类 task 没声明「成功的可观察后果」 | Petar 草稿 done 但未发；EOD analysis failed 但诊断对 |
| 10 | EPUB 路由到 coder | router 是 keyword vs description 脆性 match，不读 manifest tool spec | Habermas EPUB → coder → preflight 缺失 → general fallback |
| 11 | worker crash 后 task 卡死 | 没 auto-retry / decompose；fallback 链条本身会再 raise | `_handle_general` fallback `tier` kwarg 失败案例 |
| 12 | 多步 plan 中途崩 → 全丢 | `plan_executor.py` 有 step state 但没 resume；workflow 不 durable | restart 后已完成 step 不复用 |

合并成六个根因：

**R1 没有 single source of truth**（#1 #5 #6 #7）— job / bridge / publish / skill / state 都散落多处。
**R2 没有强制闭环**（#3 #5 #9）— 检测到的 finding 不入 backlog；done 不需要 verify。
**R3 关键 gate 是可选的**（#2b #2c）— writer 是 hard rule 但被绕过；auth check 不存在。
**R4 Skill / 知识系统读写分离**（#4 #8）— 文件在 disk，运行时 prompt 不查询；外部 source 读完无 outcome。
**R5 没有 per-pipeline SLO + watchdog**（#2a #2c #2d）— pipeline 自己说自己 ok，没有 alarm。
**R6 Routing + Workflow 既不智能也不持久**（#10 #11 #12）— keyword router；worker crash 终态；workflow 不 durable。

V2 的全部架构动作 = 在 §3.0 内核之上解决 R1–R6。

---

## 3. V2 架构

### 3.0 The Stable Kernel — V2 之后不再动

这是 Mira 与未来自己的契约。**只有 7 个接口是 load-bearing，其他全部是插件层可自由换。**
全部写进根目录 `STABILITY.md`（§4.6 强制要求）。

#### 3.0.1 七个内核接口

1. **Task Queue + Durable Dispatcher**
   - schema：`{task_id, workflow_id, agent, payload, schema_version, created_at, parent_task_id?}`。
   - 实现：DBOS Transact（§0.5.2）+ **PostgreSQL 17 backend，mandatory（不许 SQLite，与 §3.0.4 tech stack 一致）**。
   - 契约：任何 task 必须 schema-versioned，dispatcher 调度后写 workflow row。
   - 替换性：12 月内 DBOS 不动；如换 Temporal / Restate，workflow shape 不变，只换 backend。

2. **Agent Handler Registry**
   - schema：`{agent_name → handler(payload, ctx) → Result}`，`Result` = `{status, artifacts, verification, failure_class?}`。
   - 实现：`agents/_registry/registry.py`（new），所有 agent 启动时 register，core 不直接 import。
   - 契约：handler 必须 type-checked、必须实现 `verify()` 或声明 `verifier_path`，必须返回 schema 一致的 Result。
   - 替换性：增加新 agent 不动 core。

3. **Port: LLMProvider**
   - schema：`complete(messages, model_class, max_tokens, ...) → Response`。
   - 实现：6 个 adapter
     - `lib/llm/anthropic_oauth_adapter.py`（spawn `claude-code --print`，复用 WA Pro/Max 订阅；主路径）
     - `lib/llm/anthropic_api_adapter.py`（API key fallback）
     - `lib/llm/openai_adapter.py`（embedding + reasoning fallback）
     - `lib/llm/gemini_adapter.py`（EN TTS + 长 context fallback）
     - `lib/llm/minimax_adapter.py`（ZH TTS）
     - `lib/llm/omlx_adapter.py`（local，routine + idle-think）
   - auth：OAuth（claude-code subprocess）OR API key（macOS Keychain / `.env`）。Routing 策略详见 §0.5.7 + `runtime/registry/llm_routing.yaml`。
   - 契约：handler 业务代码 **不允许**：
     - 直接 `import anthropic` / `openai` / `google.genai` / `minimax`（必须经端口）
     - 直接 `subprocess.run(["claude-code", ...])`（必须经 anthropic_oauth_adapter，否则 routing 不能统一）
     - 任何第三方 OAuth wrapper / claude.ai session 复用（已被 Anthropic ban）
   - 替换性：换 provider 改 routing.yaml 一行；adapter 增删独立。
   - CI grep rule：`grep -E "^(import|from) (anthropic|openai|google\.genai|minimax)|subprocess.*claude-code|playwright.*claude\.ai|claude-cli-mod|openclaw" agents/` 在业务代码出现 = block。

4. **Port: ArtifactStore**
   - schema：`read(key) / write(key, payload, schema_version) / version(key) / list(prefix)`。
   - 实现：`lib/artifacts/local_fs.py`（默认 file system）、未来 `cloudkit_adapter.py`。
   - 契约：所有 protected files（per memory `feedback_protected_files_need_api.md`）走端口，**不允许裸文件 write**。soul / memory / artifacts / publishes / skills / traces 全在端口下。
   - 替换性：未来 multi-device sync 上 CloudKit 时换 adapter，业务代码不动。

5. **Append-Only Audit Log**
   - schema：每条事件 `{event_id, ts, type, task_id?, workflow_id?, user_id?, payload, schema_version}`。
   - 实现：DBOS workflow status table + `data/audit/events.jsonl`（双写，前者 query，后者 cold storage）。
   - 契约：dispatch / publish / skill_use / verify / fix / proposal 等都必须写一条；**不允许 mutate**，只允许 append。
   - 替换性：query 层可换；append 形状不动。

6. **Supervision + Heartbeat**
   - schema：`heartbeat.json = {ts, pid, last_dispatch_ts, last_workflow_ts, status}`。
   - 实现：core.py 30s loop 写；watchdog（§3.7）每分钟读，stale > 5min 触发 `pipeline_recovery` backlog item。
   - 契约：HARD RULE 6 的「5 步运维 audit」是这里的应用。任何 long-running 进程必须心跳。
   - 替换性：watchdog 实现可换；contract 不动。

7. **Port: Memory**（详细 §3.10）
   - schema：5 verb — `write / read / supersede / consolidate / list_recent`。完整签名见 §3.10.1。
   - 实现：`lib/memory/postgres_adapter.py`（Postgres + pgvector，单表 5 kind）+ `lib/memory/file_mirror.py`（human-editable markdown 镜像，hash 同步）。
   - 五种 memory kind：`fact` / `belief` / `episode` / `task` / `reflection`。
   - 契约：所有 agent prompt build 必须查 Memory port 拿 retrieval；任何 agent 写 long-term 状态必须经 Memory port，**不允许裸文件 write**（与 ArtifactStore 端口的关系：Memory 是 structured 层，ArtifactStore 是 generic file 层；Memory 内部使用 ArtifactStore 但暴露的接口不同）。
   - 替换性：换 storage backend（如未来上 Letta）只换 adapter；Memory port 接口不动。
   - **Bi-temporal supersede 不可绕过：** 旧 memory 永远不删，只标 `valid_to` + `superseded_by`。append-only event log 是 §3.0.1 #5 audit log 的兄弟，同等 sacred。

#### 3.0.2 STABILITY.md 内容

放根目录 `Mira/STABILITY.md`，固定内容：

```markdown
# STABILITY.md — Load-bearing interfaces & tech stack

V2 commits both interfaces (Part A) and tech stack (Part B) for 12+ months.
Breaking either requires:
  1. A migration ADR in docs/architecture-decisions.md
  2. A contract test demonstrating the new shape
  3. An upcaster for any persisted data
  4. A strangler-fig migration plan for any plugin that depends on the old shape
  5. 30-day observation window before deletion of legacy

## Part A — Stable interfaces (7)

  1. Task: {task_id, workflow_id, agent, payload, schema_version, ...}
  2. Handler: handler(payload, ctx) -> Result{status, artifacts, verification, failure_class?}
  3. LLMProvider.complete(messages, model_class, ...) -> Response
     [implementation: 6 adapters (anthropic_oauth via claude-code CLI as primary,
      anthropic_api / openai / gemini / minimax / omlx as fallbacks);
      routed via runtime/registry/llm_routing.yaml;
      NO third-party OAuth wrapper / claude.ai session scrape;
      see docs/mira-next.md §0.5.7]
  4. ArtifactStore.{read, write, version, list}
  5. AuditEvent: {event_id, ts, type, task_id?, workflow_id?, user_id?, payload, schema_version}
  6. Heartbeat: {ts, pid, last_dispatch_ts, last_workflow_ts, status}
  7. Memory.{write, read, supersede, consolidate, list_recent}
     [5 kinds: fact|belief|episode|task|reflection; bi-temporal supersede only;
      Postgres+pgvector single-table; human-editable file mirror;
      see docs/mira-next.md §3.10]

## Part B — Stable tech stack

See docs/mira-next.md §3.0.4 for the full table. Fixed components:
  - Python 3.12+
  - PostgreSQL 17 — mandatory, no SQLite
  - DBOS Transact + Postgres backend
  - LLMProvider port: 6 adapters (anthropic_oauth primary, anthropic_api/openai/gemini/minimax/omlx fallbacks)
  - SwiftUI + SwiftData (iOS message thread reliability)
  - mDNS/Bonjour + HTTPS API for app-Mac bridge
  - FastAPI (existing web server)

Explicitly NOT in stack:
  - CKSyncEngine (现有 iCloud bridge 不重写)
  - Cloudflare Workers / cloud relay (V2 不上 App Store, 不需要)
  - Sign in with Apple / StoreKit / IAP (同上)
  - Docker / K8s / Redis / MongoDB / Vector DB 主存

Everything else in this repo is replaceable without ADR.
```

#### 3.0.3 内核外的所有东西都是插件

每个 agent / skill / pipeline / prompt template / connector / dashboard / iOS app feature 都是插件。
插件可以频繁迭代、推翻、删除。**插件层的变更不需要走 ADR**，只需 PR 通过常规 review。

新加 agent 的流程：
1. 在 `agents/<name>/` 创建 handler。
2. 在 `agents/<name>/manifest.yaml` 声明 authority_scope、tools、verifier。
3. 启动 hook 自动 register 到 §3.0.1 #2 Handler Registry。
4. core.py 不动。

#### 3.0.4 Tech Stack 固定表

**这张表是 STABILITY.md 的第二部分**，V2 之后只允许在每行加 alternative，不允许移除已固定的栈。任何替换走 §3.1 strangler。

| 层 | 选定 | 理由 | 替换难度 |
|----|------|------|----------|
| **Backend 语言** | Python 3.12+ | 现有代码 / 生态 / Mac 原生 | 永久 |
| **Backend runtime** | LaunchAgent (macOS) + asyncio | 单 Mac topology；HARD RULE 6 监控 | 永久 |
| **主数据库** | **PostgreSQL 17**（取代当前 SQLite + jsonl 散落） | production-grade、ACID、DBOS 原生支持、可迁移、易备份 | 高 — 替换要全数据迁移 |
| **Postgres 部署** | [Postgres.app](https://postgresapp.com/)（macOS native，不要 docker）| 单 Mac 不需要容器；GUI 管理；LaunchAgent 友好 | 中 |
| **Durable execution** | DBOS Transact + Postgres backend | §0.5.2 论证；workflow durability 内核 | 高 |
| **Cache / KV** | 不另建。Postgres 足够 | 单用户单机不需要 Redis | 低 |
| **Audit log cold storage** | `data/audit/events.jsonl` 双写 | grep / replay 友好；Postgres 是 hot query 层 | 低 |
| **LLM access — 主路径** | `claude-code` CLI + WA Pro/Max OAuth via `anthropic_oauth_adapter`（§0.5.7）| flat $20/月，最便宜 | 低（adapter 切换） |
| **LLM access — fallback** | Anthropic API key + OpenAI API key + Gemini API key + MiniMax API key | throttle / 特定 provider 强项 / cost-aware routing | 低 |
| **LLM routing 表** | `runtime/registry/llm_routing.yaml`（task type → provider list with fallback order） | 一行改换 provider | 低 |
| **MCP 客户端** | Anthropic 官方 `mcp` Python SDK | 工具调用边界标准 | 低 |
| **Web server** | FastAPI（已有） | 不增新 | 低 |
| **Bridge transport** | mDNS（`mira.local`）+ HTTPS API；**API/Postgres canonical for tasks/threads/status/replies；iCloud 仅作 backup + artifact mirror，never command truth** | 个人 LAN 路径足够；iCloud 永远不接收新 command 写入 | 高 |
| **Bridge TLS（reviewer 加）** | **本地自签证书 + iOS app pinned cert** | 个人单 user 单 Mac 不需要公共 CA；自签证书 hardcode 进 MiraApp bundle，URLSession delegate 验证 SHA256 fingerprint；不用 ATS exception（不允许 plain HTTP）；不用 mkcert 之类需要装 root CA 的方案；启动时如证书指纹不匹配 → connection refused + alert | 中 — 证书轮换需 1 次 app 更新 |
| **iOS 框架** | SwiftUI（不 UIKit）| 现有 | 永久 |
| **iOS 本地存储** | **SwiftData**（取代 ItemStore.swift JSON cache） | offline-first reliability，message thread 不丢的真正修复 | 高 |
| **iOS sync** | API/Postgres canonical via bridge；iCloud 仅 read-only backup + artifact 镜像 | 不上 CKSyncEngine；iCloud 不再是 sync layer 而是只读备份 | 中 |
| **iOS 本地网络发现** | NWBrowser + Bonjour `_mira._tcp` | 取代 hardcode IP（problem #1 修复） | 中 |
| **CI / 测试** | pytest + GitHub Actions | 已有 | 低 |
| **可观测性** | Postgres dashboards + structured jsonl + macOS log | 单用户够用，不引 Datadog / OpenTelemetry | 低 |
| **Schema / config 格式** | YAML（registry / config）+ JSON（payload）+ Pydantic（validation） | 一致性 | 低 |
| **Skill 文件** | Markdown + YAML frontmatter | human-editable + machine-indexable | 低 |
| **Trace 格式** | JSONL（`data/traces/{task_id}.jsonl`）+ Postgres index | replay 友好 | 低 |
| **Backup** | Postgres `pg_dump` + `data/` rsync 到 iCloud Drive 加密 zip | 简单可恢复 | 低 |
| **Secrets** | macOS Keychain + `.env`（gitignored）— 不放 secrets.yml | 已有 + Keychain 读取 helper | 中 |

**已显式拒绝的栈**（永久不引入，节省未来取舍成本）：

| 拒绝项 | 理由 |
|--------|------|
| Docker / 容器化 | 单 Mac topology；Postgres.app 直接跑 |
| Kubernetes | 同上 |
| Redis | Postgres 足够 |
| MongoDB / NoSQL 主存 | Postgres + JSONB 已经覆盖 |
| Vector DB（Pinecone / Weaviate / Chroma 主存） | §0.5.6；如需向量检索 → `pgvector` 扩展 |
| Datadog / New Relic / Sentry SaaS | 单用户成本高，本地日志 + 自建 dashboard 够用 |
| FastAPI / Flask 之外的新 web 框架 | 当前 server.py 已用，不增 |
| TypeScript 后端 | Python 一种语言够用 |
| 新 LLM SDK 厂商（除上表外） | 加新 provider 必须先过 ADR |
| Cloud relay（Cloudflare Workers / Fly.io / Vercel）| V2 个人自用不需要；如未来上 App Store 再起 plan |
| CKSyncEngine / 重写 iCloud sync | V2 个人单设备主用，bridge 已经 work；避免不必要重写 |
| Sign in with Apple / StoreKit / IAP 相关 | V2 不上 App Store |
| OAuth wrapper / 第三方 claude.ai session 复用 | §0.5.7 ban |

**Tech stack 变更规则：**

1. 加新栈：必须 ADR + STABILITY.md 加行 + Phase plan 调整。
2. 换栈：必须 strangler 7 步 + 30 天观察期。
3. 删栈（除非 deprecated）：不允许，已固定的栈是 V3 inheritance 基线。

---

### 3.1 Strangler Fig 迁移纪律

V2 全程，任何动到「正在生产的代码路径」的改动必须走这个 7 步：

1. **建端口。** 在 `lib/` 或 `agents/_registry/` 加抽象层，老实现 wrap 进 adapter。
2. **生产流量继续走老实现。** 不允许停机切换。
3. **新实现 shadow run。** 同样输入跑新实现，**结果只写日志，不生效**。
4. **Shadow 比对 N 天。**
   - 内部 pipeline（self_audit / 报告 / journal）：3 天。
   - user-facing 但低频（podcast / market report）：5 天。
   - **substack pipeline（任何动到 article / note / reply / dispatch 的）：7 天。**
5. **Cutover。** 新实现接管流量；老实现降级为 fallback（前 7 天每次 cutover failure 自动回退）。
6. **30 天观察期。** dashboard 显示新实现的 success rate / latency / cost vs 历史基线。
7. **删除老实现。** 30 天无 incident 才允许删；否则继续观察。

**违反 strangler 的 PR 自动 block。** CI 检查方法：grep `# DEPRECATED` / `# strangler-cutover-date`，比对当前日期。

---

### 3.2 Substack 连续性公约

substack 是当前唯一的 user-facing 突破口。**V2 全程，substack 链路不允许有任何一天断档**。
具体规则：

1. **冻结期：Week 1–2** 不允许任何 PR 改动 `agents/socialmedia/notes.py` / `:posts.py` / `:activity_inbox.py` / `agents/super/publishing.py` 的生产路径。这两周只 build §3.0 内核 + §3.1 端口。
2. **Week 3 开始迁移** 走 §3.1 7 步。每条 substack 路径迁移单独追踪：
   - article publish
   - note publish
   - inbox reply
   - growth notes generation
   - article → podcast trigger
3. **每条路径 shadow 7 天**（§3.1 step 4 的下限）。shadow 期间内：
   - 新实现写 `data/strangler/{path_name}/shadow_{date}.jsonl`。
   - 每天 dashboard 显示新 vs 老的差异（payload diff + outcome diff）。
   - 任意一天差异 > 5% 触发 review，差异 > 20% 触发自动暂停。
4. **每天必须有 substack 产出。** 即使 V2 改造期间，daily metrics 必须满足：
   - 每天 ≥ 1 substack 互动（article / note / reply 任一）。
   - 每周 ≥ 1 article published。
   - **断 1 天 → critical incident signal**（不是 auto-revert）：Mira 自动诊断 root cause（Substack 外部 outage？auth 问题？V2 PR 影响？watchdog false positive？）+ 推 decision card 给 WA（reply: ROLL-BACK / WAIT / IT'S-EXTERNAL / FORCE-MANUAL-POST）。**回滚必须 WA explicit reply，不自动**（防 false watchdog 误回滚正确的 PR）。
5. **Substack engagement metrics 写到 V2 daily report。** Week 1 起 daily report 顶部必有 substack metrics（subscriber delta / article reads / note reactions / inbound replies）。让 substack 增长成为 V2 的可见 KPI，不只是 R1–R6 的副产品。
6. **任何"修 substack"的紧急 PR**（hotfix）必须：
   - 先在 [feedback_publish_bosons_incident.md](../../.claude/projects/-Users-angwei-Sandbox/memory/feedback_publish_bosons_incident.md) 类型的事故记录里加一条。
   - 必须经过 §3.0.1 #3 §3.0.1 #4 端口，不允许裸 patch。
   - PR 必须 link 到 strangler tracker。
7. **Writer agent 必经，user 不审核。** 所有 substack 输出（article / note / comment / reply）+ bluesky + x post 全部经过 writer agent（§3.5.1）；writer 完成 + preflight + cooldown 通过自动发；user 不需要点 approve；维持现有 full autonomy（[feedback_full_autonomy.md](../../.claude/projects/-Users-angwei-Sandbox/memory/feedback_full_autonomy.md)）。Writer 是 quality gate，不是 user approval gate。

---

### 3.3 R1 — Single Source of Truth：Canonical Registry Layer

**问题：** job、bridge、publish、skill 都没有 canonical 表。

**动作：**

1. **`agents/super/runtime/registry/jobs.yaml`** — 唯一 job registry。
   - 字段：`name`, `enabled`, `trigger_spec`, `verify_spec`, `command`, `blocking_group`, `priority`, `cooldown`, `quota`, `owner`, `disabled_reason`, `disabled_at`, `process_type`, `workflow_checkpoint_hook`。
   - `daily_tasks.py` 的 `_DAILY_TASK_CONTRACTS` 从 yaml 自动派生。
   - `triggers.py` 的 `should_*()` 必须 import `registry.is_enabled(name)`，**禁止裸 evaluate**。
   - `evaluate_job_payload()` 第一步 `if not registry.is_enabled(name): return None`。
   - PR check：jobs.yaml 之外的 job 定义 = block merge。

2. **MiraApp + `Mira/web/server.py` — 一份 bridge contract。**
   - `bridge/contract.json` 定义所有 endpoint：`POST /api/{user_id}/tasks`, `GET /api/{user_id}/tasks/{task_id}`, `GET /api/{user_id}/threads`, `POST /api/{user_id}/replies`, `GET /api/heartbeat`。
   - server URL 走 mDNS（`mira.local`，§3.9 详述）+ heartbeat fallback；**禁止 hardcode IP**。
   - iCloud command file 路径降级为**一次性 manual recovery importer**（仅 DR scenario WA 手动 CLI 触发，永不在正常 dispatch loop 消费 iCloud 路径，与 §3.9 #5-6 一致）。
   - `CONTROL_RUNTIME_DB_ENABLED` 默认 on，启动时 DB 不可达 hard-fail。

3. **`agents/super/publishing/registry.py` — 一份 publish entry registry。**
   - 任何对外 side effect 必须经 `publish_registry.dispatch(channel, payload)`。
   - 内部强制：`payload → channel_writer_pass → preflight → publish_attempt → post_verify`。
   - 直接调 `claude_think()` 后直发 substack / bluesky / x 的代码路径**整体删除**。
   - PR check：`grep -E "claude_think|claude_act" agents/socialmedia/` 在 publish 路径里出现 = block merge。

4. **`agents/shared/soul/skills/catalog.yaml` — 一份 skill registry。**
   - 字段：`id`, `title`, `tags`, `applies_to_agents`, `path`, `created_at`, `last_used_at`, `usage_count`, `outcome_score`。
   - 全部 skill 文件统一索引。
   - 任何 agent prompt 阶段必须经 `skills.retrieve(agent_name, task_context)` 获取 skill snippet。

**验收：** 把 jobs.yaml 里某 job `enabled: false` 改成 commit，30s 后该 job 在所有 dispatch 路径上消失，无需改 daily_tasks.py / triggers.py / core.py。

---

### 3.4 R2 — 强制闭环：Detect → Backlog → Apply → Verify → Reward

**问题——准确 framing：当前 `done` 在撒谎。**
`done` 现在的含义是「agent 跑完没崩」，不是「user 拿到他要求的东西」。Petar 草稿 done 但未发；EOD market analysis failed 但诊断本身对。

V2 让每类 task 必须声明「成功的可观察后果」，由 type-aware verifier 宣告 `verified`。`done` 失去 canonical 终态地位。

**动作：**

1. **统一 backlog executor。**
   - `backlog_executor.py` 当前只识 `self_evolve_proposal`。新增 type：`self_audit_fix`, `pipeline_recovery`, `request_verify`, `skill_promote`。
   - 每种 executor 必须实现：`apply()`, `verify()`, `rollback()`, `score()`。
   - 没有这四个方法 = backlog 拒收。

2. **self-audit 入 backlog。**
   - `self_audit.attempt_fixes()` 改为 `enqueue(finding, executor='self_audit_fix')`。
   - 低风险（hardcoded_path / missing_manifest / dead_import）默认 `auto_apply: true`。
   - 高风险默认 `requires_review: true`，进 daily report。
   - 删掉 `if critical_count > 0` 阈值。

3. **finding 闭环。**
   - apply 后必须跑 `verify()`：重跑导致这条 finding 的检测，确认它消失。
   - verify 失败 → 自动 rollback + 升级 `requires_human`。
   - apply 成功 → 写 `agents/super/feedback/correction_log.jsonl`：`{finding_id, fix_path, applied_at, verified_at, recurrence_at?}`。
   - 7 天内 finding 复发 → 该类 finding auto-fix 自动暂停。

4. **user request 闭环。**
   - `do_talk()` 处理完 user 消息后，必须产 `request_verify` backlog item。
   - executor `request_verify.apply()` = 重读 user message，生成 verification predicate，跑一次。
   - 失败 → user 视图 surface「上次你要求 X，24h 后我没看到 X 生效」。

5. **runtime 状态机加 `verified` 终态（internal only，不破 public API）。**
   - **内部状态机：** `pending → running → completed_unverified → verified | failed | blocked-on-input`。`completed_unverified` 是 agent 跑完没崩；`verified` 是 verifier 判定可观察后果达成。
   - **Public API（iOS app / bridge response / user-visible status）：** 仍叫 `done`，**但只在 internal `verified` 时显示 `done`**。`completed_unverified` 在 app 显示 `verifying`。这样不破现有 app contract，product semantics 保持「done = user 拿到东西」。
   - 每个 task type 必须在 `agents/super/runtime/registry/task_types.yaml` 声明 `verifier`（deterministic check 函数 path）+ `expected_observable_outcome`（user 视角）。
   - dashboard 内部区分 `verified / completed_unverified / failed`，user 端只看到 `done / verifying / failed`。

6. **Trace replay。**
   - 每个 task / workflow run 写完整 trace 到 `data/traces/{task_id}.jsonl`：每步 input、output、prompt、tool call、verifier verdict。
   - CLI：`mira replay <task_id>` 重跑 trace。
   - 用途：bug debug / regression test / self_evolve before-after。
   - V2 范围：写 trace + 简版 replay。完整版（mock tool layer）放 V3。

7. **DBOS 实现 backlog + 闭环。** §3.0.1 #1 + #5 用 DBOS 实现，意味着：
   - `attempt_fixes` 写成 `@DBOS.workflow`；apply / verify / rollback 各是 `@DBOS.step`。
   - 任何 step crash → DBOS 自动 retry 或 resume。
   - 闭环天然 durable，不需要自己写 retry 逻辑。

**验收：**
- 连续 7 天 self_audit 触发 ≥ 5 个 auto_apply finding，verified 复发率 < 20%。
- 任意 user 请求 24-48h 后能从 dashboard 查到 verify 结果。
- Petar / EOD 这类 case 在 internal dashboard 上以 `completed_unverified` / `failed_correctly_diagnosed` 显式区分；app 端不显示 `done` 直到 verified。
- 任意 task `mira replay <task_id>` 可用。

---

### 3.5 R3 — Mandatory Gate Layer

**问题：** writer agent 是 hard rule 但 notes 路径绕过；auth fail 当 transient error 吞。

**动作：**

1. **Writer Gate（强制版，无 user 审核）。**
   - `publish_registry.dispatch()` 流程：`payload → writer.handle(channel, draft) → preflight → publish_attempt → post_verify`。
   - **所有 social media 输出全部经过 writer agent，无例外**：
     - `substack_article` → writer agent 完整 de-AI pass
     - `substack_note` → writer agent（用 note-length checklist）
     - `substack_comment_reply` → writer agent（用 reply checklist）
     - `bluesky_post` → writer agent（短形式 checklist）
     - `x_post` → writer agent（短形式 checklist）
     - `podcast_script` → writer agent（script checklist）
   - **不再有「lighter anti_ai_guard / reply_guard 旁路」**——以前的多档 guard 是历史遗留，统一收敛到 writer agent + 不同 channel checklist 即可。
   - **不需要 user 审核**：writer 完成 → preflight 通过 → cooldown 到 → 自动发。维持现有 [feedback_full_autonomy.md](../../.claude/projects/-Users-angwei-Sandbox/memory/feedback_full_autonomy.md) full autonomy 政策不变。
   - **不在 iOS app surface pre-publish review tab**（这是 App Store 才需要的，V2 个人自用不做）。
   - 缺 channel mapping = publish_registry 拒发。
   - writer agent 内部按 channel 加载不同 checklist：`agents/writer/checklists/{channel}.md`。anti-ai.md 是基础，每个 channel 在其上 specialize。

2. **Preflight Gate（统一版）。**
   - `agents/super/safety/preflight.py` 提供 `preflight(channel, payload) -> Result`。
   - 当前散落的 `_content_looks_like_error` / `preflight_check` / cooldown 统一过来。
   - 单元测试覆盖每个 channel 的 preflight 分支，覆盖率 < 90% block merge。

3. **Auth Health Layer。**
   - 新建 `lib/auth_health.py`：每个外部账户注册 health check。
   - 每 5 分钟跑 lightweight check（`getProfile` / `/me`）。
   - 失败 → 立即写 `data/auth_state/{provider}.json` `status=expired` + bridge push iOS app `auth_alert`。
   - publish_registry.dispatch 第一步 check `auth_state`：expired → 不试发，报 needs_human。
   - **本动作单独解决 #2c**。
   - **Bridge TLS 证书 expiry 也注册进 health check（reviewer 加）：** 每日 check `~/MiraServer/cert/server.crt` 距 expiry 天数；< 30 天 → push WA `cert_expiry_warning`，提示需 rebuild MiraApp（pinned cert hardcoded 进 bundle，证书轮换 = app 更新）；< 7 天 → critical alert。

4. **Writer agent 输入 contract 收紧。**
   - `writer.handle()` 必须接收 `{channel, draft, anti_ai_checklist, voice_rules, banned_phrases}`。
   - 调用方传不全直接 raise。
   - 单元测试：跳过 writer 的 publish call 在 dev mode 必须 raise。

**验收：** 1 周内所有 substack note + article + bluesky/x post 都有 writer / guard pass record；token 过期 5min 内 iOS app 出 auth_alert。

---

### 3.6 R4 — Runtime Skill Mesh

**问题：** 80+ skill 在 disk 上，runtime prompt 不查询；self_evolve 提了几十个 proposal，没人 score。

**动作：**

1. **Skill Catalog 全索引。** 启动时扫所有目录建 catalog.yaml；frontmatter 强制 `id`, `tags`, `applies_to`, `summary`, `usage_examples`。

2. **Runtime Skill Retrieval Service。**
   - `agents/shared/skills/retriever.py`：`retrieve(agent_name, task_context, k=3) -> list[SkillSnippet]`。
   - 实现：tag overlap（规则）+ embedding similarity（pgvector）+ recent_outcome_score 加权。
   - embedding：**V2 用 OpenAI `text-embedding-3-small` API**（cost dust，便宜可靠）；oMLX 本地 embedding 推 V3（§0.5.7 oMLX adapter Week 5 仅试点 1-2 task type，embedding 不在试点范围）。
   - **V3：** oMLX 本地 `nomicai-modernbert-embed-base-4bit` 替代 OpenAI embedding，整个 retrieval 不走 API。
   - **关键：注入到 user-message 段，不是 system prompt**。
     - 为什么：Manus 的教训（§0.5.1）—— hot-swap system prompt 杀 KV-cache。
     - 系统 prompt 保持稳定，skill 作为「这次任务相关的参考」放 user message 顶部。
   - 注入格式：
     ```
     # Relevant Skills (retrieved for this task)
     ## {title}
     {summary}
     Applied because: {reason}
     ```
   - 强制：writer / researcher / analyst / discussion 全 wire 上。

3. **Skill Outcome Reward。**
   - 每次 task 完成（done 或 verified）写 `agents/super/feedback/skill_usage.jsonl`：`{task_id, skills_used, task_outcome, user_feedback?}`。
   - 周日 reflect job 算 per-skill outcome score，更新 catalog.yaml。
   - 6 周内 score 持续低于均值 → `to_review`，retrieve 时降权。
   - 6 周从未 retrieve → `dormant`，从 retrieval pool 移除（文件保留）。

4. **External Best-Practice Ingestion 闭环。**
   - `agents/super/workflows/external_learn.py`：每 3 天读取 user 配置的 external source 列表（openclaw / autoresearch / hermes / llm wiki / 新论文）。
   - 流程：fetch → summarize → compare with current architecture → propose **with mapping to specific Mira file:line** → enqueue backlog as `skill_promote` 或 `architecture_change`。
   - Propose 阶段强制 load 现有 skill catalog 作 dedup baseline。
   - 每 propose 一条记 `proposal_id`，30 天后回写 `outcome`。
   - dashboard 加「External Learning ROI」面板。

5. **Self-Evolution reward 信号。**
   - `self_evolve_proposal` 实施后必须有 `before_metric` / `after_metric`。
   - metric 例：finding 7 天复发率 / pipeline success rate / agent verified 比例。
   - after < before → 自动 rollback + `regression` 标记。
   - rollback 三次以上的提案类型 → 6 周静默。

**验收：** catalog.yaml ≥ 80 skill 索引；任意 writer task prompt log 中可见 retrieved skill ≥ 1 条；30 天后 dashboard 上能看到 skill outcome；external_learn 至少 1 round 完成。

---

### 3.7 R5 — Per-Pipeline SLO + Watchdog Mesh

**问题：** 每条 pipeline 自己说自己 ok。

**动作：**

1. **每条 pipeline 声明 SLO。**
   - `agents/super/runtime/registry/pipelines.yaml`：substack-article-publish、article-to-podcast、daily-market-report、social-growth、explore-briefing、self-audit、external-learn 等。
   - 字段：`expected_frequency`、`expected_artifact`、`max_silence_hours`、`recovery_action`。

2. **Watchdog job。**
   - `agents/super/workflows/pipeline_watchdog.py`，每小时跑。
   - 检查：上次 expected_artifact 时间 vs 当前 > max_silence_hours？
   - 是 → 创建 `pipeline_recovery` backlog item（DBOS workflow），先 auto-recover；失败 push iOS 红色 alert。

3. **Article → Podcast 链路修复。**
   - 当前 weekly quota 卡死跨周文章。
   - 新规：podcast quota 是软约束，文章 published 后 7 天必出 podcast，否则 watchdog 强制 bypass quota 一次。
   - 同周已有两个则把 quota 改为 floating window（最近 7 天 ≤ N 个）。
   - 写在 pipelines.yaml `recovery_action: bypass_quota_after_7d`。

4. **Daily Market Report 修复。**
   - `do_daily_report()` 改成 sections registry。
   - 每个 section 是 `(name, gather_fn, render_fn, required)` 四元组。
   - 加 `market_briefing`（required=true），gather_fn = 调 `agents/analyst/handler.handle()` + Tetra briefing。
   - 加 `news_signals` section：从 explore feed 抽今天最重要 3 条。
   - **加 `substack_metrics` section（required=true）**：subscriber delta / article reads / note reactions / inbound replies / pending review queue。这是 §3.2 公约第 5 条的实现。
   - 任一 required section 缺失 → daily report 不发，写 incident。

5. **Substack Reply Pipeline 修复。**
   - dedup 改本地 SQLite（`data/substack/replied.sqlite`），HTTP check 仅 catalog miss 时 fallback。
   - dedup HTTP 失败有 retry queue（`data/substack/dedup_retry.jsonl`），下个 cycle 优先消费。
   - per-cycle MAX_REPLIES_PER_CYCLE 改 dynamic：backlog > N 自动放宽。

**验收：** pipelines.yaml ≥ 6 条；watchdog 每小时跑；手动停 podcast pipeline 8 天，watchdog 必须自动 escalate + bypass quota 重发。

---

### 3.8 R6 — Two-Tier Orchestration + LLM Router

**问题：** 当前所有请求走同一条机器，cron 跑的 daily job 和 user 临时发的「编 EPUB」走同一套 routing + worker。

**架构原则：** 两层 orchestration。

| Tier | 来源 | 路径 | 特性 |
|------|------|------|------|
| **Procedural Tier** | cron / scheduled / pipeline followup | jobs.yaml 直接 dispatch，无 LLM router | deterministic、cheap、fast |
| **Planner Tier** | app / user message / inbox 触发的 ad-hoc | LLM router → explicit plan → workflow executor | 慢、贵、能处理 ambiguous |

**动作：**

1. **LLM Router。**
   - `agents/super/routing/llm_router.py`。
   - 输入：user message + 全部 agent manifest 的 tools / description / authority_scope / examples（machine-readable）。
   - 输出 strict JSON：
     ```json
     {
       "intent": "convert_reading_notes_to_epub",
       "confidence": 0.86,
       "plan": [
         {"agent": "writer", "tool": "compile_book", "args": {...}, "step_id": "s1"},
         {"agent": "writer", "tool": "verify_epub", "args": {...}, "step_id": "s2", "depends_on": ["s1"]}
       ],
       "fallbacks": [{"agent": "general", "reason": "if writer.compile_book unavailable"}],
       "reasoning_trace": "..."
     }
     ```
   - plan 是 §3.4 verified 状态机的输入。
   - LLM 选 sonnet（不是 opus），budget cap = $0.05 / call。
   - confidence < 0.6 → needs-input，回 user。

2. **Manifest 升级为 tool spec。**
   - 每个 agent manifest 必须新增 `tools: [{name, description, args_schema, examples, returns}]`。
   - 这是 router 输入；也对应 [DECISION-0007](architecture-decisions.md) specialist authority contract。

3. **Process type 显式声明。**
   - jobs.yaml / pipelines.yaml 每条声明 `process_type: sequential | hierarchical | consensual`。
   - 当前默认 hierarchical；真正用 process_type 改行为放 V3。

4. **Tier 分流由请求源决定。**
   - LaunchAgent 30s loop / scheduler → Procedural Tier。
   - `do_talk()` / iOS POST / inbox handler → Planner Tier。

5. **Plan 执行经 DBOS workflow。**
   - LLM router 输出 plan 后，封装成 `@DBOS.workflow`，每个 step 是 `@DBOS.step`。
   - 直接得到 crash-resume 能力（§0.5.2）。
   - **彻底解决 #11 #12**：worker crash 后 DBOS 自动从最后完成的 step resume。

**显式不在 §3.8 范围：**
- 不把整个 super agent 改成单 LLM agent loop（放 V3）。
- 不引入 LangGraph / CrewAI / Agent SDK 依赖。
- 不做 speculative execution。

**验收：**
- 任意 ad-hoc 请求经 LLM router 产出符合 schema 的 plan。
- Habermas EPUB 类历史误路由 case 在新 router 下 confidence ≥ 0.8 路由正确。
- 所有 agent manifest 有 tools 字段；missing = CI block。
- Procedural / Planner Tier 在 dashboard 分开显示 latency 和 cost。
- 手动 `kill -9` 一个 multi-step task，DBOS 重启后从最后完成的 step continue。

---

### 3.9 iOS App 修复（不上 App Store，纯个人 quality）

**目标：** 修 problem #1（task 提交从未成功）和 problem #6（message thread 不工作）。MiraApp 个人自用，不为 App Store 提交。

不做的事（这些都属于 App Store-specific，V2 全部 cut）：
- per-provider AI consent UI
- pre-publish review surface
- cloud relay（Cloudflare Workers）
- CKSyncEngine 重写 sync
- Sign in with Apple
- StoreKit / IAP 准备
- PrivacyInfo.xcprivacy
- TestFlight / App Store 提交

要做的事（折进 R1 + R2 + R6 已有 work，不增 phase）：

1. **mDNS + heartbeat-based discovery 替代 hardcode IP**（折进 §3.3.2 R1 bridge contract）。problem #1 的根本修复。
2. **HTTPS API + Postgres 是 canonical 的 tasks / threads / status / replies 真相**（同 §3.3.2）。所有 command 写入只走 API。
3. **SwiftData 替代 ItemStore.swift JSON cache**（折进 §3.4 R2 thread sync）。这不是为 App Store，是为 thread reliability —— SwiftData 是 Apple 2026 推荐的 offline-first 方案，比手写 JSON cache 可靠很多倍，message thread 不丢的真正修复。
4. **Bonjour `_mira._tcp` + NWBrowser**（同 §3.3.2）。
5. **iCloud 角色严格降级：** 仅作 (a) backup（pg_dump 加密 zip）+ (b) 历史 artifact 镜像（substack 已发文 / podcast 音频 / journal markdown）+ (c) **一次性 recovery importer**（仅在 Mac 死了 / Postgres 损坏 等 DR scenario 由 WA 手动触发，把旧 iCloud command-file 重放进 Postgres，**永远不在正常 dispatch loop 内消费 iCloud 路径**）。新写入路径 100% API。
6. iCloud command file 旧路径在 V2 Week 1 cutover 后即归档，CI grep `iCloud.*command\|cmd_.*\.json` 在正常 dispatch 代码出现 = block；只有 `lib/dr/icloud_recovery.py`（手动 CLI 调用）允许读这些路径。

**Why 不做 CKSyncEngine：** WA 主用单设备（iPhone），API + Postgres 已经够当 truth，CKSyncEngine 是 multi-device sync 才需要的，V2 不投入。

**Why 仍然 SwiftData：** message thread 不丢是 problem #6 的根本修复，SwiftData 是 Apple-blessed 的 reliability solution，**好工程不只为 App Store**。

**验收：** problem #1 + #6 在 §9 验收的 1 / 6 / 13 / 14 行覆盖，不另设 §3.9 验收。

---

### 3.10 Memory System — Mira 的长期记忆

参考研究：[Letta MemGPT](https://docs.letta.com/concepts/memgpt/)、[Mem0 paper arXiv:2504.19413](https://arxiv.org/abs/2504.19413)、[Zep / Graphiti arXiv:2501.13956](https://arxiv.org/abs/2501.13956)、[Generative Agents (Park et al.)](https://3dvar.com/Park2023Generative.pdf)、[Manus context engineering](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)、[Anthropic Memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)。

#### 3.10.0 收敛 6 个 pattern（高置信度，赌 12+ 月）

| Pattern | 来源 | Mira 的实现位置 |
|---------|------|------------------|
| Append-only event log + derived state | 全部都做 | §3.10.2 `memories` 表 + `superseded_by` 列 |
| 两层：hot context (always-in-prompt) vs cold store (retrievable) | Letta core/archival, Manus todo.md, Anthropic claude-progress.txt | §3.10.4 任务 workspace `progress.txt` + retrieval 注入 |
| Hybrid retrieval：semantic + recency + (importance \| tags) | Generative Agents 的 score 公式 | §3.10.3 retriever |
| LLM-mediated write 决策（ADD/UPDATE/DELETE/NOOP）| Mem0 | §3.10.5 write pipeline |
| Bi-temporal supersede（valid_from / valid_to，never delete）| Zep | §3.10.2 schema + §3.10.6 conflict resolution |
| Periodic consolidation by reflection | Park et al. + Letta + ChatGPT | §3.10.7 |

#### 3.10.1 Memory port（§3.0.1 #7 详细签名）

```python
class MemoryPort(Protocol):
    def write(self, kind: Literal["fact","belief","episode","task","reflection"],
              content: str, *, source: str, tags: list[str],
              valid_from: datetime, valid_to: datetime | None = None,
              importance: float, metadata: dict) -> MemoryId: ...

    def read(self, query: str, *, kinds: list[str] | None = None,
             k: int = 8, recency_weight: float = 1.0,
             importance_weight: float = 1.0, relevance_weight: float = 1.0,
             as_of: datetime | None = None) -> list[Memory]: ...

    def supersede(self, old_id: MemoryId, new_id: MemoryId, reason: str) -> None: ...

    def consolidate(self, window: timedelta, kind_in: str, kind_out: str) -> list[MemoryId]: ...

    def list_recent(self, kind: str, since: datetime, limit: int) -> list[Memory]: ...
```

5 个 verb 覆盖 Letta / Mem0 / Zep / Generative Agents 全部能力。其他都是 adapter 内部实现细节。

#### 3.10.2 Storage Schema（一张表，5 个 kind）

```sql
CREATE TABLE memories (
    id            uuid PRIMARY KEY,
    kind          text NOT NULL CHECK (kind IN ('fact','belief','episode','task','reflection')),
    content       text NOT NULL,
    embedding     vector(768),
    source        text NOT NULL,         -- task_id / journal_id / external URL / 'consolidated:<ids>'
    tags          text[] NOT NULL DEFAULT '{}',
    created_at    timestamptz NOT NULL DEFAULT now(),
    valid_from    timestamptz NOT NULL,
    valid_to      timestamptz,           -- NULL = currently valid
    importance    real NOT NULL CHECK (importance BETWEEN 0 AND 1),
    last_accessed_at  timestamptz,
    access_count  int NOT NULL DEFAULT 0,
    superseded_by uuid REFERENCES memories(id),
    metadata      jsonb NOT NULL DEFAULT '{}'
);

CREATE INDEX memories_embedding_idx ON memories USING hnsw (embedding vector_cosine_ops);
CREATE INDEX memories_kind_valid_idx ON memories(kind, valid_to) WHERE valid_to IS NULL;
CREATE INDEX memories_tags_idx ON memories USING gin (tags);
```

**为什么一张表：** Letta 用多表，Stevens 用一张表，Mem0 用一张表 + kind 列。一张表的好处：schema 演进只动一处；query 跨 kind 简单（"过去 7 天关于 substack 的 episode + reflection"）；月 6 不会面临 5 表 join migration 噩梦。

**filesystem 镜像层：** human-editable 的 memory 文件（`data/soul/identity.md`、`data/soul/beliefs/*.md`、`data/journal/YYYY-MM-DD.md`）作 source-of-truth；DB 是 queryable index。Memory port 内部维护双向 hash check：file 改了 → DB 更新；DB 写新条目 → 选择性 mirror 到 file（如 `belief` 类型 mirror，`episode` 类型不 mirror）。这是为 [feedback_protected_files_need_api.md](../../.claude/projects/-Users-angwei-Sandbox/memory/feedback_protected_files_need_api.md) 给的真实 API 路径。

#### 3.10.3 Retrieval：Park 公式 + kind 过滤

retrieval score = recency × α₁ + importance × α₂ + relevance × α₃，三项都 min-max normalize 到 [0,1]：

- **recency** = `0.995 ^ hours_since_last_access`（Generative Agents 原始公式）
- **importance** = 写入时由 LLM rate 1-10 → 归一 0-1。Tier 0 本地 Gemma 评分（routine task），Tier 2 Anthropic 评分（重要事件）。
- **relevance** = `cos_sim(query_embedding, memory_embedding)`

默认 α₁=α₂=α₃=1.0，top-k=8。kind filter 必须传（写文章时不要拉 task memory；做计划时不要拉 belief memory）。

**V2 实现（reviewer 调整）：** embedding 用 OpenAI `text-embedding-3-small` API（cost dust，便宜可靠）；similarity 计算 pgvector（本地）；importance scoring 用 Tier 1 OpenAI gpt-5-mini。**V3：** embedding 切到 omlx_adapter（本地 `nomicai-modernbert-embed-base-4bit`）；importance scoring 切到 oMLX `gemma-4-31b-it-4bit`；整个 retrieval 走本地。

#### 3.10.4 Hot context layer：progress.txt + 注入格式

**hot layer**（每个 task workspace 一份 `progress.txt`，Anthropic pattern）：当前任务的 status / 已完成步骤 / 失败尝试与原因 / 已知约束。startup 时读，shutdown 时写。是 task crash-resume 的二线保障（DBOS workflow 是一线）。

**cold layer 注入到 prompt 的格式**（**KV-cache 友好**，§0.5.1 Manus 教训）：

- system prompt 顶端固定 **stable prefix**：identity + worldview 摘要 + 核心 belief（每天最多更新 1 次，否则 KV-cache 全杀）。
- user message 顶端注入 **task-specific retrieved memories**：

```
<memories>
[fact|0.85|2026-04-15] WA 偏好简短 essay，避免 abstract noun
[belief|0.78|2026-03-20] substack 这个 niche 上长文不如系列短文有效果
[episode|0.65|2026-04-29] 上次写 A2A trust 文章 inbox 涨 12 subscriber
</memories>
```

**规则：**
- 单行格式 `[kind|importance|valid_from] content`
- 排序按 (kind, id)，**确定性**（同样 query 同样输出）
- 不允许 LLM 自己 paraphrase memory 内容
- 新 turn 来 → memory block 在 user message 顶端，**不动 system prompt**

#### 3.10.5 Write Pipeline：Mem0 模式（ADD / UPDATE / DELETE / NOOP）

新事件来 → `Memory.write()` 内部不直接 INSERT，而是：

1. 提取 candidate fact（LLM，可 Tier 0 简单 case）
2. 对每个 candidate：retrieve top-3 现存 memory（cos_sim > 0.7）
3. LLM 决定四种操作之一：
   - **ADD**：candidate 是新信息，INSERT
   - **UPDATE**：candidate 修正/精化现有 memory 的某条 → supersede 老的，INSERT 新的
   - **DELETE**：现有 memory 被新事件证伪 → supersede 老的，无新 INSERT（valid_to 设为 now）
   - **NOOP**：candidate 已经存在，仅 bump `access_count` 和 `last_accessed_at`
4. 决策写到 `memory_writes` audit log（含 reasoning）

**为什么这套：** Mem0 paper（[arXiv:2504.19413](https://arxiv.org/abs/2504.19413)）证明这个 pipeline 在 LoCoMo benchmark 上比 OpenAI Memory 高 26% 准确率，p95 latency 降 91%。production 验证过。

#### 3.10.6 Conflict Resolution：Bi-Temporal Supersede（never delete）

新 memory 与现有 memory 矛盾时（cos_sim > 0.85 且 LLM judge 为 contradictory polarity）：

```python
# old memory: "WA 偏好长文，喜欢深度展开"
# new memory: "WA 偏好简短 essay，避免 abstract noun"
memory.supersede(old_id, new_id, reason="WA 2026-04-30 explicit feedback")
```

实现：set `old.valid_to = now()` + `old.superseded_by = new.id`。**old row 永远不删**。retrieval 默认只拉 `valid_to IS NULL`，但可以传 `as_of=<past_timestamp>` 查历史快照（debug / replay 用）。

每条 supersede 推到 `data/journal/supersede.jsonl` + 周日 reflect 时 surface "本周 Mira 改变了哪些判断"，让 user 能 review。

#### 3.10.7 Consolidation：Reflection 触发

**两条具体路径：**

1. **Daily journal → weekly belief update。** 每周日（cron）：
   - LLM 读最近 7 天 daily journal + current beliefs
   - 提出 0-3 条 belief ADD / UPDATE / DELETE proposal
   - 每条 proposal 写 `data/self_evolution/observations/`（§3.11 input）
   - WA 周一 review（如果有），但不阻塞执行（per [feedback_full_autonomy.md](../../.claude/projects/-Users-angwei-Sandbox/memory/feedback_full_autonomy.md)）

2. **Episodic → factual consolidation。** 当某 entity 在 14 天内 ≥3 个 episode 中出现：
   - LLM 抽取 durable fact
   - `Memory.write(kind="fact", source="consolidated:<episode_ids>")`
   - 原 episode 不删，但加 tag `consolidated:fact:<id>`

**Decay（soft，never delete）：** 每周一次：未被 access 的 memory `importance *= 0.95`。**永远不 hard-delete**（与 [YourMemory](https://github.com/sachitrafa/YourMemory) 的 0.05 prune 阈值不同 —— 个人 agent 丢任何东西都是不可恢复的）。低 importance 自然在 retrieval ranking 中下沉。

#### 3.10.8 Memory 的可观察使用（"怎么保证 Mira 真用了 memory"）

两个 audit 机制，缺一不可：

1. **Retrieval audit log**：每次 `Memory.read()` 写 `memory_reads` 表 `(task_id, query, returned_ids, used_ids)`。Agent 完成任务后必须 report `used_ids`（实际引用的 memory）。**`used_ids` 为空但 `returned_ids` 非空 → flag 到 dashboard**（"Mira 拿到 memory 但没用"）。
2. **Decision provenance**：writer agent 出 article / note / reply 时必须 emit `<provenance memory_ids="..."/>` footer（publish 前 strip，但 audit log 保留）。"Mira 写这篇用了 3 条 memory" 变成可验证事实。

dashboard 显示：本周 retrieval count / memory_kind 分布 / 最常 access top 10 / 从未被 access 的 memory 数。

#### 3.10.9 Migration：从现有 soul/ 到 Memory port

V1 的 soul/ 文件结构保留，**但所有 read/write 必须经 Memory port**。Phase Week 4：

1. 扫 `data/soul/` 全部文件 → 解析 frontmatter + content → bulk INSERT 到 `memories` 表
2. 为每条计算 embedding（V2 用 OpenAI `text-embedding-3-small` API；本地 oMLX embedding 推 V3，§0.5.7）
3. 启 file_mirror 双向 sync
4. agent 代码用 `Memory.read()` 替换裸 `open(soul_file)`，走 §3.1 strangler

#### 3.10.10a Memory Adapter Degraded Mode（reviewer 修正：不覆盖 core Postgres down）

Memory port 是 §3.0.1 #7 内核接口，但**Memory adapter 层有 degraded mode**，避免 Memory 系统 bug 击穿整个 agent runtime。

**重要：** core Postgres 是 §3.0.1 #1 Task Queue + #5 Audit Log 的真相，**Postgres down 永远是 hard-fail**（startup 拒启 / running 进 emergency stop + DR runbook §3.13）。Memory degraded mode **仅覆盖 Memory adapter / pgvector index / Memory.read() 故障**，不覆盖 core DB down。

- **degraded mode 触发（Memory adapter 层）：**
  - `pgvector` extension index broken（embedding 查询 fail，但 SQL 其余正常）
  - `Memory.read()` 连续 ≥ 3 次 exception（Memory 表损坏 / schema mismatch）
  - `Memory.read()` p95 > 5s（性能退化）
  - `memory_adapter` 显式标 `unhealthy` flag（手动 set，例如发现 embedding 不一致）
  - **不包括：** Postgres 连不上 / Postgres 进程 down（这些是 §3.13 DR scenario）
- **degraded 行为：**
  - **Routine task（非 memory-required）：** 继续执行，audit log 写 `memory_adapter_unhealthy_warning`，prompt 中 `<memories>` block 缺席
  - **Memory-required task（writer / researcher / reflection / consolidation）：** 暂停入 `pending_memory` queue，等 Memory recovered 后 resume
  - **Identity check（§3.12）：** 仍走 Tier 0 本地规则（identity_core.md hash + 显式 forbidden phrases regex），不依赖 Memory
- **task type 必须声明 `memory_required: true | false`**（默认 false 保守）
- **Recovery：** Memory adapter 健康后自动 drain `pending_memory` queue
- **6 周内 degraded mode 测试方法：** **disable memory_adapter 或 drop pgvector index**，验证 routine task 继续 + memory-required 入 pending + recovery 后 drain。**不要测试 stop Postgres**（那是 DR scenario，会击穿整个 agent）。

**Why：** Memory port 是新组件 bug 概率高；但 core Postgres 是 task / audit truth，永远不能软退化。

#### 3.10.10 永远不做

- **Never** overwrite event log。只 supersede。
- **Never** 把 embedding 存到文件（embedding 在 pgvector，文件只人读 content）。
- **Never** 让 agent 直接 DELETE memory row（只 mark `valid_to`）。
- **Never** 在 inject prompt 时让 LLM paraphrase memory（KV-cache 杀手）。
- **Never** 把 5 个 kind 拆成 5 张表（月 6 schema migration 噩梦）。
- **Never** 信 "Mira 记得 X" 而不查 `used_ids` audit log。
- **Never** 跑 Ebbinghaus *deletion*。decay 只调 importance score。
- **Never** 让 belief 类 memory 的 supersede 不写 reason —— 这是 user review 的关键 audit point。

#### 3.10.11 验收（与 §9 #18 一致）

- 5 个 kind 各有真实数据（**不强求 100 数量阈值，避免造假填充**）
- writer task 完成后 `used_ids` 非空率 ≥ 80%
- Memory cutover 后每周至少 1 条 supersede（说明 Mira 真在更新判断）
- consolidation cron Memory cutover 后每个计划周日跑成功（V2 内 ≥ 2 次，因 Memory Week 4 才上线）
- `Memory.read()` p95 latency < 100ms（pgvector 本地查询；V2 embedding 走 OpenAI API 但仅写入时调用，read 不增 latency）
- degraded mode 测试通过（disable memory_adapter 或 drop pgvector index，**不 stop Postgres**）

---

### 3.11 Self-Evolution Mechanism — 6 Stages 闭环

参考研究：[Voyager arXiv:2305.16291](https://arxiv.org/abs/2305.16291)、[Reflexion arXiv:2303.11366](https://arxiv.org/abs/2303.11366)、[DSPy MIPROv2](https://dspy.ai/api/optimizers/MIPROv2/)、[Generative Agents reflection](https://3dvar.com/Park2023Generative.pdf)、[Anthropic emergent misalignment from reward hacking 2025](https://www.anthropic.com/research/emergent-misalignment-reward-hacking)、[LangSmith agent improvement loop](https://www.langchain.com/conceptual-guides/traces-start-agent-improvement-loop)。

**用户问的核心：怎么保证 self-evolution 真发生，不是表演？**
答：**5 个文件可审计标准**（§3.11.7）。任意一个文件缺失 = loop 没闭环 = claim 是假的（HARD RULE 1）。

#### 3.11.0 production 收敛的 7 条原则

| 原则 | 来源 |
|------|------|
| Generator ≠ Grader（永远分开） | Voyager / Reflexion / DSPy 全做 |
| Objective metric or ground truth required | DSPy / TextGrad 没 metric 不 compile；AutoGPT 没 metric 进无限 loop |
| 版本化、可 replay 的 regression dataset | LangSmith / Braintrust 都把 trace → dataset 作 load-bearing artifact |
| Sandbox / shadow before prod | LangSmith CI/CD pattern |
| Skill-as-code, not skill-as-vibes | Voyager 可执行 code，DSPy compiled program，MetaGPT SOP |
| Trigger 自动，不是 "agent 觉得" | Park et al. importance threshold；Reflexion failure；DSPy schedule |
| Kill switch + rollback path | canary auto-rollback 普遍 |

#### 3.11.1 Stage 1: Detect — 信号源

**只接受 objective 信号，不接受 "agent self-noticed"**（防 sycophancy / reward hacking）：

| 信号 | 来源 | 写到 |
|------|------|------|
| Crash | task_worker exception 写 `data/crash.log` | `data/self_evolution/observations/` |
| Preflight fail | safety/preflight.py reject | 同上 |
| User correction | WA 在 iOS app 回 "wrong" / 删除发出的 substack 内容 | 同上 |
| Engagement signal | substack post 0 reactions in 48h / unsubscribe 增加 | 同上 |
| Verify fail | §3.4.5 verifier verdict = failed | 同上 |
| External-learn proposal | §3.6.4 reading note → proposal | 同上 |
| Cost outlier | 单 task > $0.20 / Tier 0 fallback rate > 30% | 同上 |
| Pipeline SLO breach | §3.7 watchdog | 同上 |

每条 observation = `{observation_id, ts, signal_type, task_id?, raw_trace_path, ground_truth_signal, severity}`。

**Hard 约束：** 没有 `ground_truth_signal`（具体可验证的失败信号）的 observation 不允许写。"Mira 觉得 X 不够好" → reject。

#### 3.11.2 Stage 2: Curate — 把 observation 变成 replayable regression suite

每周日（与 §3.10.7 consolidation 同时）：
- dedupe 本周 observations（cos_sim > 0.8 同类合并）
- 转成 `data/self_evolution/regression_suite/<id>.json`：`{input, expected_behavior, assertion, source_observation_ids}`
- **每条必须 headlessly replayable**。不能 replay 的 observation 不进 suite，进 `discarded/` 等下次 review。

**这是 Mira 的"regression dataset"，是闭环的 load-bearing artifact**（LangSmith 论点：你 improve 不了你不能 version + dataset + re-run 的东西）。

#### 3.11.3 Stage 3: Propose — LLM 生成 unified diff

触发条件：
- regression_suite 累积 ≥ 5 条同类 failure
- OR 单条 critical signal（HARD RULE breach、user explicit "fix this"）

流程：
1. Tier 2 LLM（Anthropic OAuth 主路径）读 ≥5 related observations + 现 architecture context（CLAUDE.md + 受影响 module 代码）
2. 提一条 unified diff（可 merge 的 git patch 形式）
3. **inoculation prompt**（[Anthropic 2025](https://www.anthropic.com/research/emergent-misalignment-reward-hacking) 的 finding）：prompt 顶部固定加 `"Your job is to fix the failure mode, not to make the grading script pass. If your patch only suppresses the symptom, mark it as such and explain."`
4. 写到 `data/self_evolution/proposals/<proposal_id>.json`：`{diff, rationale, addressed_observation_ids, inoculation_self_check}`

**Hard 约束：**
- diff ≤ 3 files / ≤ 200 LOC（compounding-error 防御）
- 一个 propose session 一条 proposal
- **拒绝 proposals that modify §3.0 内核接口**（CI grep diff 路径，触到内核 = auto-reject + alert WA）
- **拒绝 proposals that modify §3.11 self-evolution 自身**（防 Anthropic 2025 的 "training on docs about reward hacking induces reward hacking"）

#### 3.11.4 Stage 4: Validate — Shadow + Held-out True-Goal Suite

```bash
# 在 Mira-shadow/ worktree apply diff
git worktree add Mira-shadow main
cd Mira-shadow && git apply ../proposal.diff

# 跑两个 suite
pytest data/self_evolution/regression_suite/        # 旧失败不能再 fail（regression）
pytest data/self_evolution/true_goal_suite/         # held-out（proposer 没看到）
```

**`true_goal_suite/` 是关键防 reward-hacking 的护城河：**
- 内容：substack subscriber growth proxy / no-deletion rate / user-correction rate / Tier 0 fallback rate / weekly LLM cost / writer agent passthrough rate
- proposer LLM **看不到这个 suite 的内容和判分函数**，只知道存在
- 这就是 Anthropic 2025 paper 的 mitigation 在 Mira 的具体实现

**Validate verdict：**
- regression suite ≥ baseline pass rate AND
- true_goal_suite 任何 metric 不下降 AND
- 没有新 crash

任意一个 fail → proposal 进 `data/self_evolution/rejected/` + 写 reason。**不允许 reviewer 主观推翻自动判决**（这是 sycophancy 防御）。

#### 3.11.5 Stage 5: Deploy — Canary with Auto-Rollback

通过 validation 后：
1. merge to main，commit message 必须 `self-evolve: <observation_ids> — <one-line summary>`
2. 紧接着的 10 个相关 invocation 是 **canary**
3. 任意一个 canary 命中：
   - 该类 failure 再次发生
   - user 显式 correction
   - 关键 metric 下降
   → **`git revert` HEAD**，写 `data/self_evolution/rollback/<commit-sha>.json` 含 reason

**Hard 约束：**
- 24h cooldown 在两个 self-evolve commit 之间
- 1 self-evolve commit / day max
- `data/self_evolution/FROZEN` 文件存在 → 整个 stage 5 stop（kill switch）
- 连续 3 天有 rollback → 自动 set FROZEN + alert WA（系统在恶化）

#### 3.11.6 Stage 6: Measure & Persist — 7 天 paired outcome

7 天后跑：
```python
outcome = {
    "commit_sha": ...,
    "observation_ids": [...],          # 闭环：引用回 stage 1
    "before_metric": {                  # baseline 7 天前的数
        "targeted_failure_recurrence": 0.6,
        "regression_suite_pass": 0.92,
        "true_goal_suite_metrics": {...},
    },
    "after_metric": {
        "targeted_failure_recurrence": 0.15,
        "regression_suite_pass": 0.94,
        "true_goal_suite_metrics": {...},
    },
    "verdict": "promote" | "revert",
    "promoted_to_skill": "skill_id?",
}
```

写到 `data/self_evolution/outcomes/<commit-sha>.json`。

**Promote 条件**（三全过）：
1. targeted_failure_recurrence < 0.5 × baseline
2. regression_suite 不退步
3. true_goal_suite 任意 metric 不下降

Promote → 该 diff 抽象成 `agents/shared/soul/learned/<skill_name>.md` skill 文件 + 注册 §3.6 catalog。

不通过 → `git revert` + 把 proposer 的方法标记为 anti-pattern 写到 `data/self_evolution/anti_patterns/`，下次 propose 时作 baseline 防重复犯错。

#### 3.11.7 5-File 可审计标准（"怎么保证"的具体形态）

任意 "Mira 自我进化了 X" 的 claim，user 必须能在 disk 上找到这 5 个文件，否则 **claim 是假的**：

```
1. data/self_evolution/observations/*.jsonl
   → 引发 X 的原始 observation，时间戳早于 commit
2. data/self_evolution/regression_suite/<id>.json
   → 该 observation 转成的可 replay 测试
3. git log --grep="self-evolve:" 找到的 commit
   → 真实的 git diff，不是 vapor
4. data/self_evolution/outcomes/<commit-sha>.json
   → 7 天后的 before/after 测量，paired
5. outcomes 文件中 observation_ids 字段引用回 #1
   → 闭环证明
```

**这是 V2 self-evolution 的可信度基线。** 任何 dashboard / journal / WA 报告说 "Mira 改进了"，必须能引出这 5 个文件作 evidence。User 用 `ls data/self_evolution/` 一秒验真假。

#### 3.11.8 永远不做

- **Never** 让 agent grade 自己的 output 当 reward signal（structurally separate evaluator，Reflexion 教训 + sycophancy 文献）
- **Never** persist 一个 "skill" 没经过 §3.11.4 validation（Voyager invariant）
- **Never** 用 self-evolution loop 修改 self-evolution loop 自身（Anthropic 2025: 训练 reward-hacking 文档 → induce reward hacking）
- **Never** skip held-out true_goal_suite（reward-hack 在 ~1000 trial 内出现，per Anthropic production-RL paper）
- **Never** ship 没 paired outcome 文件 scheduled for 7-day measurement 的 change（"deploy and forget" = 没 evidence）
- **Never** 让 proposal 超 scope（>3 files / >200 LOC）
- **Never** 一天超 1 个 self-evolve commit（slow is safe）
- **Never** disable canary auto-rollback 或 FROZEN 文件
- **Never** 把 regression suite pass 当 sufficient evidence（也要 held-out + 7-day window，DSPy MIPROv2 显式两层验证）
- **Never** 改 §3.0 内核接口（CI auto-reject）

#### 3.11.9 V2 vs V3 范围（reviewer 调整后）

**V2（6 周内可诚实交付）：**
- Stage 1 Detect 上线，`data/self_evolution/observations/` 有 ≥ 30 条真实记录
- Stage 2 Curate 上线，`regression_suite/` 有 ≥ 5 条 replayable test
- Stage 3 Propose 跑通，至少 1 条 proposal 完成 inoculation prompt + diff
- Stage 4 Validate 跑通，至少 1 条 proposal 通过 shadow + true_goal_suite 比对
- Stage 5+6 干跑（dry-run）至少 1 次：canary harness 可用，`outcomes/` schema 落地，**但不要求 7-day promote 闭环完成**
- FROZEN kill switch + canary auto-rollback 干跑各 1 次

**V3（6 周不可能诚实交付，移到 V3 horizon）：**
- 3 条 self-evolve commit 全 5-file 闭环
- promote rate ≥ 50%（需要 7-day window × 3 commit = 至少 21 天观察期，V2 6 周 + Week 6 才上 stage 5-6 不够）
- 多周 outcome 累积 + anti-pattern 库

**原因：** Stage 5-6 只在 Week 6 落地的话，6-week plan 不可能跑出 3 个 7-day paired outcome。诚实承认 plan inflation。

**审计标准（reviewer 调整）：** V2 内的 self-evolve demo 凑齐 **4 file**（observation + regression test + git commit + scheduled outcome placeholder）+ Stage 5/6 dry-run schema demo；**真实 5-file 闭环（含 7-day outcome）只能在 V3 跑通**（V2 6 周不够 7-day window）。**「Mira 改进了 X」claim 必须能引出 4-file 才算 V2 合格 demo；5-file 完整闭环 claim 只能在 V3 出现**。

---

### 3.12 Identity Anchor — Persona Drift 防御

参考：[Generative Agents (Park et al. 2023)](https://3dvar.com/Park2023Generative.pdf) 论证 reflection 缺失 48h 内 agent 退化；[Goal Drift in Language Model Agents arXiv:2505.02709](https://arxiv.org/abs/2505.02709) 证明 Claude 3.5 Sonnet 在 100k token 后开始 drift。Mira 加 self-evolution + memory supersede 后风险更大。

**问题：** §3.10 让 belief 可 supersede，§3.11 让 Mira 自改代码。没有 immutable 锚点 = Mira 可能自我进化成另一个 entity。

#### 3.12.1 immutable Identity Core

**`data/soul/identity_core.md`** —— V2 一次写定，之后不动。改动需要：
- 显式 ADR + WA approve（与 STABILITY.md 同等级别）
- CI grep 任何 PR 触它 = block，除非 PR description 含 `IDENTITY-CHANGE-APPROVED-BY: WA`

固定包含 5 节：
1. **Who Mira Is**（一段，≤200 字）：身份本质陈述
2. **Core Values**（5–7 条不可妥协的价值判断，例如：诚实优于讨好、慢优于快、深度优于覆盖）
3. **Voice DNA Reference**（指向 `agents/writer/voice/positive_guide.md`，§3.18）
4. **What Mira Is Not**（明确否定：不是 chatbot、不是 productivity tool、不是 customer success agent...）
5. **Hard Boundaries**（永远不做的事，例如：不冒充其他人、不发布未经 writer agent 的内容、不动 §3.0 内核）

filesystem hash-locked：启动时校验 SHA256，与 `data/soul/.identity_hash` 比对，不一致 = hard fail + alert WA（除非 ADR 通过的合法变更同时更新 hash）。

#### 3.12.2 Belief 与 Identity 的关系

belief 可以 evolve，identity 不可以。Memory port write 时强制 layer check：

```python
def Memory.write(kind="belief", content, ...):
    conflict = identity_check(content)  # Tier 0 LLM
    if conflict.severity == "violation":
        raise IdentityViolation(conflict.reason)
    if conflict.severity == "tension":
        log_to_drift_journal(content, conflict)  # WA 周一 review
    # 正常写入
```

`identity_check()` 实现（V2 时间对齐，reviewer 修正）：
- **第一层（永远先跑，不依赖 LLM）：** identity_core.md hash 校验 + 显式 forbidden phrases regex / forbidden patterns。规则命中 = violation，直接拒写。
- **第二层（V2 Week 4 起）：** Tier 1 OpenAI gpt-5-mini 调用（routing.yaml `identity_check`），prompt 固定模板「下面这条 belief 是否与 identity_core 矛盾？返回 violation / tension / compatible + 一句 reason」。fallback Tier 2 anthropic_oauth。violation = 拒写；tension = 写但记 drift journal；compatible = 静默通过。
- **V3：** 切到 Tier 0 omlx_gemma local（Week 5 oMLX adapter 验证后）。
- **§3.10.10a degraded mode 内的 identity check** 仅依赖第一层规则，不依赖 LLM 任何 tier。

#### 3.12.3 Self-Evolution × Identity

§3.11.3 propose 阶段强化：
- diff 触 `data/soul/identity_core.md` = auto-reject + alert
- diff 触 `lib/identity/identity_check.py` = auto-reject（防 reward hacking 弱化 check）
- propose prompt 强制加一句 `"Your proposal must remain consistent with data/soul/identity_core.md. If it requires changing identity, mark as REQUIRES_IDENTITY_CHANGE and stop."`

#### 3.12.4 Weekly Drift Check Job

每周日（与 §3.10.7 consolidation + §3.11.2 curate 同时）：
- 读最近 7 天 published substack content + 主要 belief supersedes + self-evolution commits
- Tier 2 LLM 跑「这周的 Mira 还是 identity_core 描述的那个 Mira 吗？返回 yes / drift_warning / drift_serious + evidence」
- drift_warning → daily report surface
- drift_serious → 自动 set `data/self_evolution/FROZEN` + alert WA + stop self-evolution loop

#### 3.12.5 永远不做

- 不允许 self-evolution 修改 identity_core 或 identity_check
- 不允许 belief supersede 某条「关于 Mira 是谁」的 fact 而不经 identity_check
- 不允许 weekly drift check 跳过（cron 失败 → alarm）
- 不允许把 identity_check 降级成 Tier 1（在 identity 这件事上不省钱）

#### 3.12.6 验收

- `data/soul/identity_core.md` 存在、hash-locked、6 周内 0 修改
- weekly drift check job 6 次全部跑通，无 drift_serious
- 至少 1 次 belief tension 被 surface 到 drift journal（说明 check 真在工作）
- 0 self-evolution proposal 触 identity_core

---

### 3.13 Disaster Recovery & Backup Playbook

参考：[system-design.md DECISION-0002](architecture-decisions.md) 已要求 restore drill but V2 未 enforce；[Postgres backup best practices](https://www.postgresql.org/docs/current/backup.html)。

**问题：** 单 Mac + Postgres + DBOS + 一堆 jsonl + soul 文件 = 单点故障。当前 V2 只提了 pg_dump + iCloud rsync，没 RPO/RTO，没自动 restore drill 验证 backup 真能用。

#### 3.13.1 RPO / RTO 目标

| 指标 | 目标 |
|------|------|
| RPO（最多丢多少时间数据）| ≤ 1 小时 |
| RTO（Mac 死了多久内能起来）| ≤ 4 小时（含 Postgres 重装 + 数据恢复 + 验证） |
| 历史 retention | hot 90 天（Postgres 内）+ cold 全量永存（iCloud 加密 zip）|

#### 3.13.2 Backup Cadence

`agents/super/workflows/backup.py`（DBOS workflow）：

| 频率 | 内容 | 目标位置 |
|------|------|----------|
| 每小时 | `pg_dump --format=custom -Z 9 mira` | `~/MiraBackup/postgres/hourly/{ts}.dump`（local SSD）|
| 每日 23:00 | 同上 + `data/` rsync | local + iCloud Drive 加密 zip（`MtJoy/MiraBackup/daily/`） |
| 每周日 03:00 | full snapshot + restore drill（§3.13.4） | iCloud + 写 drill log |
| 每月 1 号 | 月度全量归档 | iCloud cold archive |

local 保留：hourly 24 份 / daily 30 份 / weekly 12 份。iCloud 永久。

加密：每个 zip 用 `openssl enc -aes-256-cbc` + 密码存 macOS Keychain。

#### 3.13.3 三个 Emergency Runbook

`docs/runbooks/dr-mac-died.md`：新 Mac 4 小时恢复流程
1. clone Mira repo from GitHub
2. install Postgres.app + 创建 mira database
3. 从 iCloud 解密最新 daily zip → restore pg_dump → 校验 row count
4. 从 iCloud 解密最新 daily zip → unrar `data/`
5. 从 1Password / Keychain 恢复 secrets（API key + zip 密码 + iCloud account）
6. `pip install -e .` + smoke test
7. `launchctl load com.angwei.mira-agent`
8. 5 分钟内检查 heartbeat / Postgres 连接 / DBOS workflow / Memory.read 可用

`docs/runbooks/dr-postgres-corrupt.md`：Postgres 损坏
1. set `data/self_evolution/FROZEN` + stop LaunchAgent
2. `pg_isready` 失败 → backup 当前损坏 db (forensics)
3. `dropdb mira && createdb mira`
4. 找最近一个完整 hourly dump → `pg_restore`
5. 校验 audit_events table 最后一条 ts vs heartbeat —— 算丢了多久数据
6. 重启 LaunchAgent，监 1 小时

`docs/runbooks/dr-icloud-also-corrupt.md`：worst case，云端 backup 也丢
- Postgres 数据丢失：DBOS workflow / audit_events / memory hot index 都丢
- **但 file_mirror 仍在 `data/soul/`** —— **这是 §3.10 file_mirror 的真实价值**
- 重建：从 file_mirror markdown 文件 bulk INSERT 回 memories 表 → 重新 embed → identity / belief / journal 全恢复
- episodic / task / reflection 的 hot 部分丢失（接受），可从 file_mirror 中 reflection 文件部分恢复
- DBOS 重新初始化（in-flight workflow 全丢，接受）

#### 3.13.4 Weekly Restore Drill（自动验证 backup 真能用）

`agents/super/workflows/restore_drill.py`，每周日 03:00 跑：

1. 拉最新 daily backup
2. spin up 临时 sandbox Postgres（端口 5433）
3. 跑 `pg_restore` 进 sandbox
4. 跑 smoke test：select count(*) from memories where valid_to is null；retrieval test；audit_events 最近 7 天 row count
5. 写 `data/dr/restore_drills.jsonl`：`{ts, success, duration_s, smoke_test_results, anomalies}`
6. 销毁 sandbox

dashboard：last successful drill timestamp。> 7 天 = 红色 alert + bridge push。

#### 3.13.5 永远不做

- 不允许 backup cron 失败 silent skip（必须写 alert）
- 不允许把 backup 只放本地 SSD（iCloud 必须有）
- 不允许 zip 不加密（含 trading data + private journal）
- 不允许 restore drill 用 production Postgres（必须 sandbox 隔离）
- 不允许跳过 drill —— 没验证的 backup ≠ backup

#### 3.13.6 验收

- 6 周内 hourly backup 没断
- 6 周内 ≥ 5 次 restore drill 成功
- 6 周内手动跑 1 次完整 dr-mac-died 流程（用 sandbox Mac VM 或第二台机器）
- 3 个 emergency runbook merged，WA 能照做

---

### 3.14 WA Operator Runbook

**问题：** V2 加 7 内核 + Memory + self-evolution + 3-tier routing + watchdog + DBOS + DR + identity check —— WA 不知道每件事出问题怎么 debug。HARD RULE 6 5-step audit 是 root，每个新 mechanism 需要 own runbook。

#### 3.14.1 结构

`docs/runbooks/operator-handbook.md` 是 entry point + 索引，包含：
- 5-step operational audit（HARD RULE 6 复述）
- 每条 sub-runbook 的 symptom → entry point 映射表
- WA 的"日常 5 分钟检查"清单

8 个 sub-runbook（V2 内必须 merge）：

| 文件 | 覆盖 |
|------|------|
| `runbooks/dbos-stuck-workflow.md` | DBOS workflow 卡住 / resume 失败 |
| `runbooks/oauth-throttle.md` | Anthropic OAuth 触 rate-limit / fallback 链如何切 |
| `runbooks/memory-debug.md` | Memory.read 返回乱七八糟 / used_ids 一直空 / supersede 没生效 |
| `runbooks/self-evolution-frozen.md` | FROZEN 文件如何 set / clear / 自动 freeze 触发原因 |
| `runbooks/self-evolution-no-promote.md` | 一直 propose 但 validate 都过不了 / promote rate 0 怎么 debug |
| `runbooks/substack-publish-incident.md` | 发出去内容不对 / 被删 / 漏回复 应急处理 |
| `runbooks/cost-overrun.md` | 月度 cost 超预算 / 单 task cost 异常 / Tier 0 fallback rate 飙 |
| `runbooks/identity-drift-alert.md` | drift_warning / drift_serious 触发后怎么处理 |

#### 3.14.2 每条 runbook 强制结构

```markdown
# {Runbook Name}

## Symptom
WA 怎么注意到（dashboard 红色？bridge alert？数据异常？）

## 5-Step Diagnostic
1. 具体 bash 命令
2. 具体 SQL query
3. 具体 log 文件位置
4. 具体 metric 阈值
5. 具体 audit 路径

## Common Causes & Fixes
- Cause A → Fix A（带命令）
- Cause B → Fix B
...

## Escalation
- 修不了的话：哪个文件存 forensic、哪个 ADR 该写、要不要 freeze 哪个 subsystem
```

#### 3.14.3 PR Rule（强制配套）

V2 内任何 PR 加新 mechanism / new failure mode / new metric → 必须更新对应 runbook。CI 检查：
- PR 触 `agents/super/workflows/`、`lib/dbos_setup/`、`agents/socialmedia/publish_registry.py`、`agents/super/self_evolve.py` 等关键路径 → 必须 link 到 `docs/runbooks/*.md` 中至少 1 条变更

#### 3.14.4 WA 5-min daily check

`docs/runbooks/wa-daily-check.md`：固定每日早晨 5 分钟流程
1. 看 iOS app daily report（substack metrics + cost + verified rate + open observations + open self-evolve commits 状态）
2. 任何 alert 红色 → 进入对应 sub-runbook
3. heartbeat / DBOS 正常 → 一行 OK
4. 其他静默正常

#### 3.14.5 验收

- 8 sub-runbook + 1 entry handbook + 1 daily-check merged
- WA 模拟 1 个 incident per category（手动 inject 故障）能 5 分钟内定位
- 每个 sub-runbook ≥ 3 个 common cause + fix
- PR check 上线，6 周内无 PR 触关键路径不更新 runbook

---

### 3.15 Secrets & Sensitive Data Policy

参考：[user_compliance.md](../../.claude/projects/-Users-angwei-Sandbox/memory/user_compliance.md) trading constraint；[project_tetra_llm_routing.md](../../.claude/projects/-Users-angwei-Sandbox/memory/project_tetra_llm_routing.md) Tetra 数据。

**问题：** Mira 接触 trading data + portfolio + private journal + WA personal。Keychain 提了 secrets 存储但**没数据分级**：哪些 memory 永远不能 publish？哪些信息不能跨 boundary？

#### 3.15.1 Sensitivity 分级

四档：

| Level | 例 | 默认 channel |
|-------|-----|------------|
| `public` | substack 已发内容、podcast、公开 skill、A2A trust 研究 | publish OK |
| `personal` | WA 的 daily routine、journal entry 中关于 WA 个人事、interest preference | personal scope only |
| `confidential` | Tetra portfolio、trading 决策、financial 数字、私密 belief | 永不 publish |
| `regulated` | trading recommendations、specific position size | 永不 publish + 永不存 short-term cache |

#### 3.15.2 Memory port 加 sensitivity 字段

```python
class Memory(BaseModel):
    # ... existing fields
    sensitivity: Literal["public", "personal", "confidential", "regulated"]
```

写入默认规则：
- `kind=fact` re: A2A trust / 技术 → `public`
- `kind=fact` re: WA personal info → `personal`
- `kind=belief` 关于公开主题 → `public`，关于 WA 私人 → `personal`
- `kind=episode` 含 financial number → `confidential`
- `kind=task` re: Tetra → `confidential`
- `kind=reflection` daily journal → `personal`

显式 `public` 必须在 write 时声明 reason，否则降级 `personal`。

#### 3.15.3 publish_registry.dispatch 第一步：sensitivity_scan

```python
def dispatch(channel, payload):
    # Step 1 (NEW)
    scan_result = sensitivity_scan(payload, channel)
    if scan_result.has_violation:
        raise SensitivityViolation(scan_result.evidence)
    # Step 2 ... (existing writer / preflight / publish chain)
```

`sensitivity_scan` 三层：
1. **Memory provenance check**：payload 中所有 retrieved memory 的 sensitivity 必须 ≤ channel 允许 level（substack channel 只允许 `public`；inbox reply 允许 `public`+ scoped `personal`）
2. **PII regex**：full names other than Mira/WA self-references、addresses、phone、 financial numbers (>4 digits with $/¥/%)、account IDs
3. **LLM 主题 check（V2 时间对齐，reviewer 修正）**：V2 用 **Tier 1 OpenAI gpt-5-mini**（routing.yaml `sensitivity_topic_check`）跑「下面这段是否包含 trading / portfolio / financial advice」→ 是 = block。**V3 切到 Tier 0 omlx_gemma local**（Week 5 oMLX adapter 验证后）。

任意一层失败 = block + 写 `data/audit/sensitivity_blocks.jsonl` + bridge push WA（不阻塞下一次 publish 尝试，但写明确 needs_review）。

#### 3.15.4 Compliance 硬约束

per [user_compliance](../../.claude/projects/-Users-angwei-Sandbox/memory/user_compliance.md)：
- substack / X / bluesky / podcast 永远不能含 trading recommendation
- substack 永远不能含 specific portfolio position
- Tetra 输出永远不进 publish_registry（不仅 sensitivity scan，是结构性隔离：Tetra 数据存在 `data/tetra/` 目录，publish_registry 代码层级 imports 不到）

#### 3.15.5 Secret Storage 策略

| Secret 类型 | 存放 | 轮换 |
|------------|------|------|
| LLM API key（Anthropic/OpenAI/Gemini/MiniMax）| macOS Keychain，per-key entry | 季度轮换（routine 90 天）|
| Substack / Bluesky / X session token | macOS Keychain | 失效自动 re-auth |
| Postgres password | macOS Keychain | 半年轮换 |
| Backup zip 密码 | macOS Keychain + WA 1Password 双备份 | 永不轮换（轮换会破老 backup） |
| iCloud account | WA 自有 | 不动 |

`lib/secrets.py` 提供唯一接口：`get_secret(name)` → 读 Keychain。**禁止任何代码 hardcode secret 或读 `.env`**（CI grep `os.environ.get` 必须配 explicit 白名单）。

#### 3.15.6 永远不做

- Memory.write 不带 sensitivity 字段 = 拒收
- publish_registry skip sensitivity_scan = 拒发（CI grep 强制）
- Tetra 数据从 `data/tetra/` 路径 import 进任何 `agents/socialmedia/` 文件 = block merge
- secret hardcode 任何位置 = block merge
- backup zip 不加密 = backup workflow 拒跑

#### 3.15.7 验收

- 所有现存 memory row 都补上 sensitivity 字段（migration 时默认按 §3.15.2 规则填）
- 6 周 0 条 confidential / regulated 内容 leak 到任何 publish 路径
- 模拟泄漏测试：手动构造一个含 `confidential` memory 的 publish payload，必须被 block
- 4 个 secret 类全部走 Keychain，无 hardcode

---

### 3.16 Concurrency & Lock Strategy

**问题：** LaunchAgent 30s loop + 多 agent + bridge write + Memory write + self-evolution commit + DBOS workflow + watchdog —— 没有 explicit lock 策略 = race condition 必然发生。

#### 3.16.1 三层锁

**Layer 1 — 进程级（LaunchAgent invocation overlap）**

现状：HARD RULE 6 已要求 PID + heartbeat 检查。V2 强化：
- `data/locks/launchagent.pid` 写当前 PID + 启动 ts
- 第二个 invocation 启动时 check PID alive + heartbeat fresh，是 → 立即 exit 0（不抢锁）
- TTL 5 分钟，过期自动 takeover

**Layer 2 — Postgres advisory locks（cross-process critical sections）**

```python
# lib/locks/advisory.py
LOCK_DISPATCH_LOOP = 1
LOCK_MEMORY_WRITE = 2
LOCK_SELF_EVOLVE_COMMIT = 3
LOCK_BACKUP = 4
LOCK_PUBLISH_DISPATCH = 5  # publish_registry 内部使用

@contextmanager
def advisory_lock(lock_id, timeout_s=30):
    with db.cursor() as cur:
        acquired = cur.execute("SELECT pg_try_advisory_lock(%s)", (lock_id,)).fetchone()[0]
        if not acquired:
            # wait + retry up to timeout
            ...
        try:
            yield
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))
```

强制使用：
- 每个 30s dispatch cycle 进入前 acquire `LOCK_DISPATCH_LOOP`（防多 invocation 并行 dispatch）
- Memory.write 的 Mem0 retrieve→decide→write 整个序列在 `LOCK_MEMORY_WRITE` 内（防两条 write 之间 read 到不一致状态）
- self-evolution stage 5 deploy 在 `LOCK_SELF_EVOLVE_COMMIT` 内（保证 1 次 1 commit）
- backup workflow 在 `LOCK_BACKUP` 内（防 hourly + restore drill 撞）
- publish_registry.dispatch 整个流程在 `LOCK_PUBLISH_DISPATCH` 内（防同 channel 并发发两次）

**Layer 3 — 应用层 idempotency**

每条 task 必须有 `idempotency_key`（DBOS workflow 已支持）；dispatcher 在 spawn 前 check `tasks` 表内 idempotency_key + status 决定是否新建。

#### 3.16.2 Race Condition 测试套

`tests/concurrency/`：
- `test_parallel_memory_writes.py`：并行 50 次 Memory.write 同 entity 不同 content → 验证 supersede 链正确，没双写
- `test_parallel_publish.py`：并发触 publish_registry 同 channel 同 payload 两次 → 第二次必须 NOOP（idempotency）
- `test_dispatch_loop_overlap.py`：模拟 LaunchAgent 跑两份并行 → 第二份必须秒退
- `test_self_evolve_commit_serialization.py`：触发 5 个 propose 同时 → 必须串行 commit，不并发
- `test_backup_during_dispatch.py`：backup 跑同时 dispatch loop → 验证不死锁

CI 在每次 PR 跑全部 concurrency 测试。

#### 3.16.3 永远不做

- 不允许 cross-process critical section 不带 advisory lock
- 不允许 advisory lock 不带 timeout（防永久挂死）
- 不允许 try / finally 之外释放 lock
- 不允许 Memory.write 不在 Mem0 atomic block 中
- 不允许 concurrency 测试套低于 90% pass

#### 3.16.4 验收

- 5 个 LOCK_ID 文档化 + 全部使用
- concurrency 测试套 ≥ 90% pass
- 6 周 0 条 race condition 事故记录
- 手动模拟 race 1 次（同时启 2 个 LaunchAgent invocation）必须正确处理

---

### 3.17 Schema Evolution Playbook

**问题：** STABILITY.md 说 "upcaster 必须有"，没说 concrete 怎么写、哪里、什么时候触发。第一次需要演进 = ad hoc。

#### 3.17.1 Migrations 目录结构

```
lib/migrations/
  README.md                        # 流程说明
  runner.py                        # apply 引擎
  registry.py                      # 已 apply migration 列表（DB-backed schema_migrations）
  0001_initial_schema/
    schema.sql                     # CREATE TABLE 等
    upcast.py                      # 空（initial）
    test_upcast.py                 # smoke test
  0002_add_memories_table/
    schema.sql
    upcast.py
    test_upcast.py
  0003_add_sensitivity_field/
    schema.sql                     # ALTER TABLE memories ADD COLUMN sensitivity
    upcast.py                      # 老 row → 默认按 §3.15.2 规则填值
    downcast.py                    # rollback：drop column（数据丢失明确告警）
    test_upcast.py
  ...
```

#### 3.17.2 每个 migration 的强制要求

1. `schema.sql` —— 纯 DDL，幂等（IF NOT EXISTS / IF EXISTS）
2. `upcast.py` —— 旧数据 → 新 shape 的 Python 转换。必须可重跑（已 upcast 的 row skip）
3. `test_upcast.py` —— 单元测试：mock 旧 shape 数据，跑 upcast，断言结果
4. `downcast.py` —— rollback 路径，数据丢失部分必须 log warning
5. PR description 必须含 `MIGRATION-RISK: low|medium|high` + `DATA-LOSS-ON-ROLLBACK: yes|no`

CI 强制：触 `lib/db/`, `lib/memory/`, `agents/super/runtime/registry/` schema 的 PR 必须有 paired migration + test。

#### 3.17.3 Migration Runner

`lib/migrations/runner.py`：
- 启动时 check `schema_migrations` table，列出 pending
- `--auto` 模式：自动 apply（dev 用）
- 默认 `--manual`：列 pending，要求 WA `mira migrate apply` 显式触发
- apply 时按 number 升序，每条独立事务，失败 stop + alert

#### 3.17.4 DBOS Workflow Versioning

DBOS 支持 per-workflow 版本。规则：
- 每个 workflow function 加 `@DBOS.workflow(version="v1")`
- 改 workflow 行为 → 升 v2，**v1 保留 30 天**
- in-flight v1 workflow 继续按 v1 完成；新启的走 v2
- 30 天后 v1 移除（CI rule + ADR）

#### 3.17.5 `docs/runbooks/schema-evolution.md`

完整文档包含：
- 何时需要 migration（kernel schema 变 / 加新 memory kind / 改 routing.yaml schema 等）
- step-by-step：写 schema.sql → 写 upcast → 写 test → 跑本地 → PR
- 一个 worked example：「为 memories 表加 sensitivity 字段」
- rollback 流程
- 常见陷阱（NULL backfill 性能、index 重建、DBOS in-flight）

#### 3.17.6 永远不做

- 不允许直接 `ALTER TABLE` SQL bypass migrations 系统
- 不允许 migration 没 paired upcast test
- 不允许 in-flight DBOS workflow 在没 version pinning 情况下改 workflow body
- 不允许 production 第一次 apply 不经 sandbox 验证

#### 3.17.7 验收

- `lib/migrations/` 存在，V2 内 ≥ 3 migration applied（initial、memories table、sensitivity field）
- runbook merged，含 worked example
- 1 次 rollback drill 成功（在 sandbox 跑 downcast 验证）
- CI rule 上线，PR 触 schema 不带 migration = block

---

### 3.18 Voice & Editorial Strategy

参考：[feedback_voice_de_AI.md](../../.claude/projects/-Users-angwei-Sandbox/memory/feedback_voice_de_AI.md) 已有 negative checklist；[feedback_writing_personal_voice.md](../../.claude/projects/-Users-angwei-Sandbox/memory/feedback_writing_personal_voice.md) 已说 substack 必须 personal。

**问题：** 现在只有 anti-AI checklist（不要 X），没有 positive voice guide（要 X）。Substack 是突破口 = 内容策略也是突破口。光防 AI tells 不够。

#### 3.18.1 Positive Voice Guide

**`agents/writer/voice/positive_guide.md`** —— 与 §3.12 identity_core.md 互为锚点（identity 是 who，voice 是 how）。

固定 6 节：

1. **Mira 的声音 DNA**（一段，≤300 字）：what makes a sentence sound like Mira
2. **ZH 5 条具体准则**（如：先具体后抽象、不滥用四字词、避免学术腔结构）
3. **EN 5 条具体准则**（如：lead with concrete observation、avoid latinate diction、specific over general）
4. **Diction 偏好表**：preferred verbs / conjunctions / opening moves（替代 banned phrase 后用什么）
5. **Reference Essays**：3 篇 Mira 之前写得最好的，标注「这是 voice」
6. **Genre Adaptation**：article voice / note voice / reply voice / podcast script voice 各 1 段例文

writer agent.handle() 强制 load 这份 guide 作 context（与 anti-AI checklist 平级，required input per §3.5.1）。

#### 3.18.2 Editorial Calendar

**`agents/writer/editorial/calendar.yaml`**：

```yaml
themes:
  weekly:
    - week_of: "2026-05-04"
      focus: "A2A trust 入门系列"
      planned_articles: 1
      planned_notes: 5
      reference_external: ["openclaw paper", "Anthropic harness blog"]
    - week_of: "2026-05-11"
      ...

monthly:
  - month: "2026-05"
    anchor_articles: 2  # 长 essay
    long_form_target: "A2A trust taxonomy v0"

quarterly:
  - quarter: "2026-Q3"
    long_form_report: "First A2A trust experiment writeup"

cross_platform:
  - article_publish: "trigger 5 notes that week referencing it"
  - long_form: "trigger podcast both ZH + EN"
```

planning workflow 启动前 read 这份 yaml。

#### 3.18.3 Engagement Playbook

**`agents/writer/editorial/engagement.yaml`**：

```yaml
inbound_classification:
  must_reply:
    - sender_subscriber_count: ">100"
    - topic_overlap: "A2A trust / agent infra"
    - explicit_question: true
  nice_to_reply:
    - sender_active_recently: true
    - quality_score: ">0.6"
  ignore:
    - spam_signal: true
    - low_quality: true

cross_recommend:
  accept_if:
    - their_focus_overlap: ">0.5"
    - their_subscriber_quality: "real"
  reject_if:
    - growth_hack_signal: true

reaction_targets:
  weekly_outbound_comments: 10
  weekly_restacks: 5
```

§3.7.5 reply pipeline 用这份配置决定 prioritize / skip。

#### 3.18.4 Outcome Learning Loop

每次 substack publish 后 24h 内自动写：

`data/journal/publish_outcomes.jsonl`：
```json
{
  "post_id": "...",
  "channel": "substack_article",
  "publish_ts": "...",
  "subscribers_delta_24h": 3,
  "reads_24h": 47,
  "engagement_score": 0.12,  // reads + reactions + comments / impressions
  "quoted_phrases": ["..."],
  "voice_drift_signals": {
    "avg_sentence_len": 18,
    "em_dash_count": 2,
    "abstract_noun_density": 0.08,
    "vs_reference_essay_distance": 0.31
  }
}
```

周日 reflect 把本周 outcome → §3.10 reflection memory：「本周 voice 是否漂移？哪种 hook 涨 subscriber？」

月底 auto-suggest（**proposal**，不自动改）：positive_guide.md 第 5 节的 reference essays 是否要更新。WA review 后 merge。

#### 3.18.5 永远不做

- writer agent 不 load positive_guide.md 而出文 = block（CI test）
- editorial calendar 缺当周 entry → 不发 article（强制 planning discipline）
- publish_outcomes 24h 内不写 = pipeline incident
- voice drift signals 任意指标连续 2 周 > 阈值 → 触 §3.12 drift_warning
- positive_guide.md 自动改（必须 monthly 提 proposal + WA review）

#### 3.18.6 V2 vs V3 范围（reviewer 调整）

**V2（基础版）：**
- positive_guide.md draft merged + WA review
- editorial calendar 覆盖 4 周
- engagement.yaml + publish_outcomes.jsonl pipeline 跑起来
- writer agent 强制 load positive_guide

**V3（完整版）：**
- voice drift 多维量化（avg sentence length / em-dash count / abstract noun density / vs reference essay distance 全度量）
- 月度 auto-suggest positive_guide 更新（基于 outcomes）
- 完整 editorial analytics + cross-platform attribution
- 至少 N 个可测 voice 指标 baseline + 6 周稳定证据

**Why：** V2 6 周内 publish outcomes 数据量不足（每周 1 article + 5 notes ≈ 30+ 数据点不足以 statistically 验证 voice drift signal）。V2 落地基础设施 + 数据收集，V3 真正分析 + 自我调整。

---

### 3.19 Plan Self-Enforcement — 让 V2 不变成第 5 份不落地的 plan

**诚实承认：** Mira 之前的 4 份 plan（[north-star](north-star.md) / [production-roadmap](production-roadmap.md) / [next-phase-plan-2026-04-06](next-phase-plan-2026-04-06-specialist-review-mesh.md) / [system-design](system-design.md)）都很 production-grade，落地为零。如果 V2 不解决这件事的**结构原因**，它就是第 5 份。

§3.19 是把 V2 自己也作为 §3.11 self-evolution 的监督对象。**V2 plan 本身必须可被 binary gate 周周验证、可被 abort**。（self-evolution 5-file 闭环本身推 V3，§3.11.9）

#### 3.19.1 旧 plan 显式 decommission

V2 启动当天（Week 0），4 份旧 plan 顶部加 banner：

| 文件 | 状态 | 关系 |
|------|------|------|
| [north-star.md](north-star.md) | **ACTIVE — north star，不变** | mira-next.md 服务于此 |
| [system-design.md](system-design.md) | **ACTIVE — canonical design 边界** | mira-next.md 在其中执行 |
| [objectives-and-metrics.md](objectives-and-metrics.md) | **ACTIVE — 长期 metric 框架** | mira-next.md §9 是其 6 周快照 |
| [architecture-decisions.md](architecture-decisions.md) | **ACTIVE — ADR log** | V2 每个 design boundary change 都加 ADR |
| [production-roadmap.md](production-roadmap.md) | **DEPRECATED — superseded by mira-next.md** | banner 顶部加 link |
| [next-phase-plan-2026-04-06](next-phase-plan-2026-04-06-specialist-review-mesh.md) | **DEPRECATED — superseded by mira-next.md** | banner 顶部加 link |

**`docs/CURRENT_PLAN.md`** 单文件，永远只有一行：`Current execution plan: [mira-next.md](mira-next.md) — V2 ending YYYY-MM-DD`。Mira 启动时 read 这个文件，知道当前 plan 是哪份。

任何冲突 → mira-next.md 赢；如有矛盾要解 → 写 ADR。

#### 3.19.2 Daily Forcing Function

**`mira v2-status` CLI** —— WA 早晨 5-min check 第一行就跑：

```
$ mira v2-status
========================================
V2 Status — Week 3 of 6 — Day 17
========================================

This week's gate (Week 3):
  [ ] publish 0 bypassing writer (current: 0 ✓)
  [ ] auth fail < 5min (current: 0 ✓)
  [x] reply pipeline cutover (DONE 2026-05-19)
  [ ] 4 runbooks merged (current: 2/4 ✗)
  [ ] OAuth fallback validated (current: not yet ✗)

This week's blockers: runbook writing pace + OAuth test pending

Today's recommended V2 work: write oauth-throttle.md + dbos-stuck.md runbooks

Drift signals (last 7 days):
  - Scope additions to mira-next.md: 0 ✓
  - Quality criteria softened: 0 ✓
  - Substack daily output broken: 0 ✓
  - Cost burn: $42 / $300 budget (14%) ✓

Weeks remaining: 3
Days until Week 6 retrospective: 22
========================================
```

写在 `agents/super/cli/v2_status.py`。状态从 `data/v2_status/` 读，不需 LLM call（routine，Tier 0 都不用，纯 SQL + file read）。

WA 必须每天看，**只看 v2-status 的人也能知道 V2 在不在 track**。

#### 3.19.3 Binary 周 Gate Review

每周日早 9:00 Mira 自动跑 binary gate + 推 Sunday Gate Review card 到 iOS app（§3.19.5 push pipeline）。**WA 不 schedule 任何东西** —— 刷手机看到 card，reply 1-2 字（APPROVE / CHANGE-TO-X）。

每周 gate 是**布尔值，不是百分比**。每条 gate 必须能用一条 SQL / grep / file check 验证：

```yaml
# data/v2_status/gates.yaml
week_3:
  description: "R3 Mandatory Gate + Substack 解冻开始迁移"
  criteria:
    - id: w3_no_writer_bypass
      check: "select count(*) from audit_events where type='publish' and ts >= now() - interval '7 days' and metadata->>'writer_pass' is null"
      pass_if: "result == 0"
      owner: WA
    - id: w3_auth_health
      check: "select count(*) from auth_state_log where ts >= now() - interval '7 days' and silent_fail_seconds > 300"
      pass_if: "result == 0"
      owner: Mira (auth_health workflow)
    - id: w3_reply_cutover
      check: "test -f data/strangler/substack_reply/cutover_ts && diff_pct < 5%"
      pass_if: "exit_code == 0"
      owner: Mira + WA
    - id: w3_runbooks_4
      check: "ls docs/runbooks/*.md | wc -l"
      pass_if: "result >= 8"  # 4 base + 4 new
      owner: WA
    - id: w3_oauth_fallback
      check: "select count(*) from auth_state_log where provider='anthropic' and event='oauth_throttle_fallback' and ts >= now() - interval '14 days'"
      pass_if: "result >= 1"
      owner: Mira
```

每条 criteria 自动跑 → 结果写 `data/v2_status/week_N_gate.json`。**全过 = passed；任意一条 fail = not_passed**。没有 partial credit。

周日 review 流程（push-based）：
1. Mira 早 9:00 自动跑 `mira v2-gate week_N` → binary 结果写 `data/v2_status/week_N_gate.json`
2. Mira 推 Sunday Gate Review card 到 iOS app，含：binary 结果 + Mira 推荐决策 + 三选一选项
3. WA reply 1-2 字（APPROVE / CHANGE-TO-CATCHUP / CHANGE-TO-DESCOPE / CHANGE-TO-ABORT）
4. **Failed gate 强制三选一**（写到 `data/v2_status/week_N_decision.md`）：
   - **catch up next week**：cost 下周更紧，Week 6 风险升
   - **descope**：把这条 gate 从 V2 移到 V2.5 或 V3，**写明 ADR 解释**
   - **abort V2**：V2 plan 本身有 fundamental 问题，停下重评估
5. WA 24h 内不 reply → 走 conservative default = "catch up next week"（最保守，不擅自 descope / abort）
6. 不允许「再看一周再说」（这是过去 plan 失败的最常见模式）—— Mira 的推荐永远是三选一，不会有「再看看」选项

#### 3.19.4 Public Commitment（外部强迫力）

Week 0 substack 发一篇 announcement：「V2 ships in 6 weeks, here's what's planned and how I'll know」。固定字段：
- V2 目标（一段，user-facing 语言）
- 6 周路线（每周一行）
- 26 acceptance criteria 摘要
- 失败标准（什么算 V2 没成）
- weekly status 更新承诺

**这是 external 强迫力。** 公开承诺过的事情漂走代价大于 silent 漂走。每周 status 在 substack 发简讯（200 字）。

**注意：** 这条不违反 §3.18 voice guide —— V2 announcement 是 personal narrative essay，正是 Mira 的强项。同时也 test 了 §3.18 outcome learning loop（announcement 自己产生 publish_outcomes 数据）。

#### 3.19.5 Push-Based Review Discipline（再修正版）

WA 反馈：**「你把 status push 到 app，我会看；其他 push 给我我会 review」**。这是更对的模型 —— WA 不 schedule 时间，**Mira push，WA reactive review**。前一版还在用 calendar block 思维，错了。

三个 thread，**WA 不 block 任何 calendar**：

##### Thread 1 — Mira → WA Push Pipeline（24/7 自动，不占 WA 任何时间）

通过 iOS Mira app feed channel `v2_status` 推送，与 substack 报告 / journal 等并列。

| Card 类型 | 频次 | 内容 |
|-----------|------|------|
| **Daily V2 Status** | 每日早 8:00 | 1 张卡 ≤ 200 字：「Week 3 Day 17: 3/5 gate passed, 2 in progress, 0 failed. Today's blocker: oauth fallback test pending. Substack streak: 17 days. Cost burn: $42/$300 (14%).」WA 滑过去看一眼 5 秒 |
| **Decision Card**（按需）| 触发即推 | "Substack article cutover ready. Shadow 7 天差异 < 2%。建议 GO。Reply: GO / WAIT / ABORT. Default in 24h: WAIT" |
| **Sunday Gate Review** | 每周日早 9:00 | binary pass/fail 自动跑出来 + Mira 推荐决策（catch-up / descope / abort）。WA reply: APPROVE / CHANGE-TO-X |
| **Drift Alert**（按需）| 触发即推 | "PR #123 触 mira-next.md 但无 ADR — 已 auto-block。Reply: ADR-INCOMING / OVERRIDE / RETIRE-PR" |
| **Build Session Summary** | 每次 Claude Code session 结束 | "Tuesday session: 4h, shipped 6 commits, 2 PR merged. Blocker: identity_check edge case. Decision needed: Mira's recommended approach + alt." |

##### Thread 2 — WA Reactive Review（estimated 15-30 min/day scattered，零 schedule）

WA 该做的事就一件：**手机响了看一眼，需要 reply 就 reply**。不需要：
- ❌ 早晨固定 5min CLI check
- ❌ Sunday 30-45min 安排时间
- ❌ Tuesday/Saturday calendar block
- ❌ "strategic session" 2h

**就是平时刷手机的时候顺手 reply。**

Default action 必须保守，让 WA 不 reply 也不出 disaster：
- Decision card 24h 内不 reply → 走 conservative default（等待 / 不 cutover / 不 publish）
- 任何 irreversible action **必须** 显式 WA APPROVE，**不允许 auto**（substack 发文除外，那是已建立的 full autonomy）
- 决策 card 默认有 Mira 的 recommended action + reasoning，WA reply ACCEPT 一个字就行

##### Thread 3 — Claude Code Session Autonomous Work（背景跑，checkpoint 时 push）

Claude Code session（this 类对话）是真正的 build executor：
- WA 开 session 一次，给 "do Week 1 任务列表"
- Claude Code 在 worktree 自主写代码 / 跑测试 / 开 PR
- 遇到 design 决策点 → 通过 Mira 推 decision card 给 WA
- 等 WA reply OR 24h 默认 → 继续
- session 结束 → push build session summary card

这意味着 WA **不需要在 session 期间在场**。WA 开个 session "做 Week 1"，去做别的，**手机响了再 reply 决策点**。

##### 关键约束

- **Push pipeline 自己必须健康**：v2_status feed channel 每日 push 失败 = bridge 自己出问题，watchdog 自动 alert（"Mira 自己 broken 了，5min audit"）
- **24h default 必须保守**：irreversible action 永不 auto；reversible 可 auto 但 audit 留痕
- **Bootstrap 期（Week 0 + Week 1）的 chicken-and-egg**：iOS app 修复本身是 Week 1 任务（problem #1 #6）。在 app 完全可靠之前，过渡期 WA 可经此 Claude Code session 直接 reply。Week 1 末以后，iOS app reply 工作

##### Build session log（自动写，WA 不写）

`data/v2_status/build_sessions/{date}.md` 由 Claude Code session 自己写完后 commit。WA 周日 review card 上能看到「本周 ship 了 14 commit / 12 PR / 3 blocker / 2 待 WA 决策」。

##### WA 真实最低承诺

| 行为 | 频次 | 形态 |
|------|------|------|
| 看 iOS app 推送 | 一天几次刷手机时顺便 | passive |
| Reply decision card | 触发时 reply 1-2 个字 | reactive |
| 周日 gate review reply | 每周 1 次 | reactive |
| Mira / Claude Code 来问 design 选哪个 | 偶尔 | reactive |

**没有任何 calendar block。没有 schedule。手机响 → 看 → 回。** Total ≈ 15-30 min/day scattered，按你已有的手机习惯。

#### 3.19.6 Drift 检测（auto-block）

**Scope drift：** mira-next.md 在 Week 1 之后任何 PR 修改它（除了 weekly retrospective + ADR）= CI block。Week 1 之内允许，Week 2 起冻结。要加 §X.Y = 必须 ADR + WA 显式 approve + 标记 V2.5（不进 V2 当前 scope）。

**Timeline drift：** Week N gate 没过 → 必须当天写 §3.19.3 三选一决策 → 否则 Week N+1 自动暂停（CI block 任何 V2-related PR 直到决策 merge）。

**Quality drift：** §9 任何 acceptance criterion 在 V2 期间被改弱 = ADR 必须，retrospective 必须 review 是否合理；连续 2 个 criterion 被改弱 → trigger §3.19.3 abort 决策。

**新工作 drift：** V2 期间任何「我顺手做下 Y 也好」的 PR（Y 不在 mira-next.md）= 默认拒绝。例外需要 ADR + 不影响 weekly gate 的论证。

#### 3.19.7 Test the Plan（不只 test the code）

V2 plan 自己也要被 test。三层：

**Pre-flight test（Week 0 必跑）：**
- 选 Week 1 的 11 条任务，估 WA + Claude Code 共需多少小时
- 实际跑 Week 1 第一个工作日的真实 build session，对比估算
- 偏差 > 50% → 这个 plan 不现实，必须 descope **before** Week 1 真正启动

**Mid-V2 reality check（Week 3 末必跑）：**
- Week 1+2+3 三个 gate 是否全过？
- 如果 Week 3 末有 ≥ 2 个 gate 没过 → V2 在 trouble，进入 §3.19.3 决策 + 必要时 abort
- "再坚持 3 周看看" **不是** 选项（这是过去 plan 失败的模式）

**Post-V2 verification（Week 6 末）：**
- 26 acceptance 全跑（自动化，Tier 0 + SQL 即可）
- 不是估，是 measure
- failed criteria 触 retrospective

#### 3.19.8 Anti-Pattern Circuit Breakers

把过去 plan 失败的具体模式做成自动 block：

| 失败模式 | Circuit Breaker |
|----------|-----------------|
| 「就一个小 patch」绕开 strangler | CI: substack-touching 代码改动不在 strangler tracker = block（§3.1 已强制） |
| 「我顺手再加个 Y」scope 蔓延 | PR file count > 5 OR LOC > 300 (excl tests / migrations) = 需要 ADR |
| 「下周补」timeline drift | gate failed 不写决策 = 下周 V2 PR 全 block |
| 「这次不算 refactor」绕开 §3.0 | 任何 PR 触 kernel 接口形状不带 ADR + STABILITY.md 更新 = block |
| 「都做这么多了不能停」sunk cost | 每个周日 review 第一行强制问 "given today's evidence, is V2 still the right plan?" |
| 「先把这个 fire 扑了」Mira 日常 ops 干扰 build | Build session 期间，Mira 紧急 incident 必须满足「P0 only」（substack 断 / Postgres 死）才打断 |
| 「我后来发现更好的设计」mid-plan 重写 | mid-plan 设计变更必须 ADR + 不影响当前 week gate；否则推 V3 |

#### 3.19.9 V2 自己也是 §3.11 self-evolution 的对象

§3.11 self-evolution 主要管 Mira 行为。但 V2 plan 本身也是被自我改进的对象：

- Week 3 reality check 如果 fail → 这条 reality check 失败本身**写一条 observation**（§3.11.1）
- §3.11 propose 阶段允许针对 V2 plan 本身提改进 proposal（**例外**：§3.11.3 通常拒绝改 self-evolution loop 自身，但 V2 plan ≠ self-evolution loop，可以被改）
- 改进 proposal 走 §3.11 完整 6 stage 验证 → promote 后写进 V3 plan，**V2 进行中不大改 V2 plan 自己**（mid-plan rewrite 防御）

#### 3.19.10 Owner per Gate

每个 weekly gate 每条 criterion 必须有 explicit owner。Owner = "如果这条 fail，谁要在 Sunday review 解释"：

- **WA**：runbook 写作 / ADR / 高风险 cutover 在场 / Sunday decision
- **Mira**：auto job 跑通 / metric 收集 / 自动 verify
- **Claude Code session**：build 代码 / 跑测试 / migration apply

owner 没设 = criterion 不允许进 gates.yaml。

#### 3.19.11 V2 Hard End Date

**V2 = 6 weeks. End. 没 V2.1, V2.2 extending forever.** Week 6 的 Sunday review 跑 26 acceptance：
- ≥ 22/26 pass + 4 Tier A 必过 → V2 SUCCESS，启动 V3
- < 22/26 OR Tier A 任一 fail → V2 PARTIAL，写 retrospective + 决策（修 vs 接受 vs 大改重启 V3）

**之后所有 follow-up 工作必须挂 V3，不挂 V2。** 这是防止 V2 变成「永远在 progress」的项目。

#### 3.19.12 Tier A vs Tier B vs Tier C 验收（§9 重新分级）

26 条 acceptance 不是同等重要。Tier 化：

**Tier A — V2 不过这条就是 fail（必须 100% pass）：**
- #1 iPhone task 不丢（突破口 #1 未解 = V2 失败）
- #2 publish 不绕 writer（HARD RULE 5 = V2 失败）
- #3 auth 不静默 fail（突破口 #3 未解）
- #15 substack 0 天断档（§3.2 公约）
- #20 identity_core 0 修改（§3.12 防 drift）
- #21 hourly backup 不断 + 1 次完整 DR 演练（§3.13 单点故障防御）
- #23 0 confidential leak（§3.15 安全底线）

**Tier B — V2 应该过（≥ 12/15 pass）：** 详见 §9 canonical 表（含 Memory / self-evolution / runbook / concurrency / migration / voice 等）。**不在此重列，避免与 §9 冲突；§9 是 source of truth。**

**Tier C — Nice to have（≥ 3/4，fail 不阻塞 V3）：** 详见 §9 canonical 表。

**Enforcement #27 必过：** Week 0 + 每周决策 + Week 3 reality check + Week 6 hard end + decommission + announcement。

V2 SUCCESS 标准：
- Tier A: 7/7 全过
- Tier B: ≥ 12/15
- Tier C: ≥ 3/4
- 总: ≥ 22/26

#### 3.19.13 Honest Limit

**这套 push-based self-enforcement 解决了 5/6 失败模式。剩下一条：WA 是否打开手机 reply decision card。**

WA 完全不 reply = 没有任何 plan 能救。但本节的设计：
- WA 真投入降到「平时刷手机顺便 reply」级别（15-30 min/day scattered）
- Decision card 24h default = WA 一时没 reply 不会 block 一切（保守 default 让 reversible 行为继续，irreversible 等 approve）
- 连续 3 天 WA 不 reply 任何 V2 card → Mira 自动推 escalation card「V2 看起来停滞，是否 abort」
- Push pipeline 自己 broken（Mira 推不出 card）= bridge 自己出问题，watchdog 5min audit 自动 surface

**早期偏差立即可见 = 早期可决策 abort，不浪费 3 周再放弃。** 这是本设计的核心价值。

#### 3.19.14 验收

- `docs/CURRENT_PLAN.md` 存在，指向 mira-next.md
- 旧 plan 都加 DEPRECATED banner
- `data/v2_status/` 目录存在，每周一份 status + gate.json
- `mira v2-status` CLI 可跑
- Week 0 substack announcement 已发
- Push pipeline setup 完成 + WA reply 路径测试通过（push-based，无 calendar block）
- Week 3 reality check 跑了 + 决策 written
- 6 周内每个 gate 都有 binary 决策（pass / decision）
- Week 6 末 26 acceptance Tier-stratified 评估
- V2 hard-end，不开 V2.1

---

## 4. 解决 12 个用户/audit 问题的精确映射

| 问题 | 由哪些 V2 动作解决 | 完成标志 |
|----------|---------------------|----------|
| #1 iOS app 任务从未成功 | §3.3.2 bridge contract（mDNS + heartbeat-based）、§3.7 watchdog | iPhone 发出的 task 100% 在 server log 可见，端到端 < 60s；7 天连续无丢失 |
| #2a substack 漏回复 | §3.7.5 reply pipeline 重写 + Tier 0 dedup classification | 1 周内 inbox 所有 inbound 都有处理记录 |
| #2b notes/articles 绕过 writer | §3.3.3 publish_registry + §3.5.1 writer gate | grep CI block；运行时 audit log 100% 有 writer pass record |
| #2c x/bluesky 断了不恢复 | §3.5.3 auth health layer | token 过期 5min 内 iOS app 出 auth_alert |
| #2d podcast 链路从未自动跑完 | §3.7.3 article→podcast 特例 | 文章 published 后 7 天内必有 zh + en podcast |
| #2e market report 只有 portfolio | §3.7.4 daily report sections registry（含 substack metrics） | required `market_briefing` + `substack_metrics` 不允许缺失 |
| #3 self-correction 永远开环 | §3.4 闭环 backlog（DBOS）| 7 天内 ≥ 5 finding auto-fix-verified |
| #4 skill 写了不用 | §3.6 runtime skill mesh（V2 用 Tier 1 OpenAI text-embedding-3-small 写入；read 走 pgvector 本地 cosine；oMLX 本地 embedding 推 V3）；注入 user-message 不杀 KV-cache | 任意 writer task prompt log 中可见 retrieved skill |
| #5 follow request 差 | §3.3.1 jobs.yaml + §3.4.4 request verify | user 说 stop X，48h 内 dashboard 自动 surface verify 结果 |
| #6 app message thread 也做不好 | §3.3.2 bridge contract + §3.9 SwiftData 替代 ItemStore.swift | app 重启后 thread 完整 |
| #7 永远在打补丁 | §3.0 内核锁定 + §3.1 strangler 强制 | PR check：禁止 hardcode IP / 散落 job 定义 / 绕过 writer / silent fix |
| #8 学了 best practice 零行为变化 | §0.5 模式直接落进 plan + §3.6.4 external_learn（V2 first-pass 用 Tier 1 OpenAI gpt-5-mini，深度对比 Tier 2 anthropic_oauth；Tier 0 first-pass 推 V3）| 1 round 完成 + proposals captured |
| #9 done 在撒谎 | §3.4.5 internal verified 终态 + type-aware verifier + trace；app 端 `done` 只在 verified 时显示 | internal dashboard 显式区分 verified / completed_unverified |
| #10 EPUB 路由到 coder | §3.8.1 LLM router + manifest tool spec | 历史误路由 case confidence ≥ 0.8 路由正确 |
| #11 worker crash 后 task 卡死 | §3.8.5 plan_executor 经 DBOS workflow | crash 后 DBOS 自动 resume；iOS app 上有 retry 按钮 |
| #12 多步 plan 中途崩 → 全丢 | §3.8.5 DBOS @workflow + @step | kill -9 后从最后完成 step continue |
| **新约束 cost** | §0.5.7 三层 routing（V2 仅 Tier 0 试点，Tier 0 全推广推 V3） | 6 周末月度 LLM cost 持平 V1 baseline（不退步即合格）；下降 ≥ 30% 推 V3 |
| **新约束 substack 突破口** | §3.2 公约 + Week 1-2 substack 冻结 | 6 周内 substack 0 天断档，subscriber growth 持平或上涨 |

---

## 5. Phase 计划（Week 0 setup + 6 周执行）

drop App Store track 后回到 6 周。每周聚焦明确，不再交叉 iOS 大轨道。
Week 0 是 enforcement 层准备（§3.19）—— 没 Week 0，Week 1-6 还是会失败。

### Week 0 — Plan Self-Enforcement Setup（§3.19）

**目标：** 把 V2 plan 自己变成可执行 + 可监督 + 可被 abort 的对象，**before** 真正动 code。

1. **§3.19.1 旧 plan decommission：** [production-roadmap.md](production-roadmap.md) + [next-phase-plan-2026-04-06](next-phase-plan-2026-04-06-specialist-review-mesh.md) 顶部加 DEPRECATED banner；写 `docs/CURRENT_PLAN.md` 单行指向 mira-next.md。
2. **§3.19.2 mira v2-status CLI 写好** —— 哪怕 placeholder 输出（数据源 Week 1 才接），保证 WA Day 1 有得跑。
3. **§3.19.3 gates.yaml 起草** —— 6 周 gate 全部写出 SQL / file-check 形式，先 placeholder OK，每周细化。
4. **§3.19.4 substack V2 announcement 发出** —— external 强迫力立即生效。
5. **§3.19.5 push pipeline setup**（不是日历 block —— WA 不 schedule 任何东西）：
   - iOS app feed channel `v2_status` 上线，能收 daily / decision / gate / drift / build summary 五种 card
   - Decision card 带 24h default + 保守 fallback（irreversible never auto）
   - Mira 推送 Daily V2 Status 模板就位，第一次推送测试通过
   - WA 在 iOS app 能 reply 测试通过（如 app 没修好，过渡期用 Claude Code session reply）
6. **§3.19.7 pre-flight test：** 取 Week 1 的 11 条任务，估时 + 当天跑 1 次真实 build session 对比。**偏差 > 50% → STOP，descope Week 1 后再启动**。
7. **§3.19.10 owner 分配：** 26 acceptance criteria 全部标 owner（WA / Mira / Claude Code session）。
8. **§3.19.12 acceptance Tier 化：** 26 条全部标 A / B / C。
9. **`data/v2_status/` 目录创建**，week_0.md 写 baseline（V1 当前 metric snapshot：当前月度 cost / substack subscriber / 任务成功率 / etc）—— 这是后面对比的基线。

Week 0 闸门（启动 Week 1 前必须全过）：
- ✅ DEPRECATED banner + CURRENT_PLAN.md 指针就位
- ✅ mira v2-status 可跑（placeholder OK）
- ✅ gates.yaml 6 周 skeleton merged
- ✅ V2 announcement 发了
- ✅ Push pipeline setup（不 schedule 日历）：iOS feed channel `v2_status` 上线 + 5 种 card 模板就位 + Decision card 24h default + 第一次推送测试通过 + WA reply 路径测通（过渡期可用 Claude Code session）
- ✅ Pre-flight test 跑了，估实差 < 50%（否则 descope）
- ✅ 26 acceptance 都有 owner + Tier
- ✅ data/v2_status/week_0.md baseline 写了

**Week 0 不过 = Week 1 不启动**。这是过去 plan 失败模式的根本封堵。

---

### Week 1 — Postgres Canonical + Bridge API + App 提交真能成功

**目标（聚焦）：** 把用户 #1 痛点修了 —— iPhone 发出的 task 100% 在 server log 可见，端到端 < 60s 完成。其余底盘最小铺。

聚焦理由（reviewer 调整）：用户 #1 stated 痛点是「iOS app 任务从未成功」。这必须是 Tier A Week 1 实现 priority，不是 Week 4 side quest。

1. **Postgres.app + pgvector** 安装；新建 `mira` database；写 `lib/db/connection.py`。
2. **§3.17 lib/migrations/** 目录 + runner + 0001_initial（schema_migrations 表 + tasks/threads/audit_events 三张核心表）。
3. **§3.0.1 #1 Task Queue + Durable Dispatcher：** Postgres-backed `tasks` 表 + DBOS workflow 套 `task_worker.run_task()`，crash-resume 跑通。
4. **§3.0.1 #5 Audit log：** Postgres `audit_events` table + `data/audit/events.jsonl` 双写 skeleton。
5. **§3.3.2 bridge contract（最高优先）：**
   - MiraApp 改 mDNS + heartbeat-based discovery（取代 hardcode IP）
   - server.py 加 `POST /api/{user_id}/tasks` + `GET /api/{user_id}/tasks/{task_id}` + `GET /api/{user_id}/threads`
   - CONTROL_RUNTIME_DB_ENABLED 默认 on + hard fail（DB 不可达 = startup 拒启）
   - **HTTPS TLS：本地自签证书 + iOS app pinned cert（SHA256 fingerprint 进 MiraApp bundle）**；URLSession delegate 验证；不开 ATS plain-HTTP exception；不依赖外部 CA
   - **API/Postgres canonical for tasks/threads；iCloud 仅 read-only backup + 一次性 recovery importer**
6. **§3.0.1 #3 LLMProvider port skeleton（最小）：** wrap 现状 path（spawn `claude-code --print` 主路径），不接所有 6 adapter。**只做 anthropic_oauth + anthropic_api 2 个 adapter**，其余 V2 Week 5 再加。这样 Mira 当前用法不破，CI 强制经端口。
7. 写 `Mira/STABILITY.md`（§3.0.2，Part A interfaces + Part B tech stack）。WA review。
8. **§3.12 identity_core.md** 起草 + WA review + hash-lock。identity_check 实现 V2 Week 4（与 Memory port 同步）。
9. CI rules：grep hardcode IP / `subprocess.*claude-code` 在业务代码 / `playwright.*claude\.ai|openclaw|claude-cli-mod` → block。
10. **substack 路径全冻结**：notes.py / posts.py / activity_inbox.py / publishing.py 生产代码 0 改动。

Week 1 闸门（**Tier A 优先**）：
- ✅ **iPhone 连续 24h 发 5 条 task 全部成功**（problem #1 解 = Tier A 第一条 acceptance）
- ✅ Postgres + pgvector + DBOS task_worker crash-resume 跑通
- ✅ STABILITY.md merged；identity_core.md hash-locked
- ✅ LLMProvider port 2 adapter 跑通 + CI grep block 上线
- ✅ substack daily 未中断

### Week 2 — Verifier State Machine + Retry/Cancel + iOS SwiftData

**目标：** task status truth + iOS message thread 修（problem #6）+ DBOS dispatcher 完整化。

1. **§3.4.5 internal verified state（reviewer 调整）：** internal state machine `pending → running → completed_unverified → verified | failed | blocked-on-input`；**public API（iOS app + bridge response）仍叫 `done`，但只在 internal verified 时显示**。task_types.yaml 声明 verifier。**不破 app 当前 contract**。
2. **§3.4.4 request verify：** do_talk 完成后必产 `request_verify` backlog item。
3. **§3.4 backlog executor** 4 种 type 框架（verify 用；其余 Week 4-6 落地）。
4. **§3.9 iOS SwiftData 替换 ItemStore.swift**（problem #6 修复）—— **Week 2 不是 Week 4**。Week 1 task 提交通了，紧跟 thread reliability 是 user pain 的连续修复。
5. **DBOS retry/cancel API**：task 状态机加 `cancel` action；retry 限次（≤ 3）+ exponential backoff。
6. **§3.16 advisory_lock infra**：5 个 LOCK_ID 文档 + 实现；先 wire 到 dispatch loop 防多 invocation 撞。concurrency 测试套 ≥ 3 条（dispatch overlap / publish concurrent / memory write race —— 即使 Memory Week 4 才上，先写 mock test）。
7. **§3.13 hourly backup cron** 上线（pg_dump + 加密 zip → iCloud Drive）。
8. **§3.7.5 substack reply dedup** 走 strangler shadow（7 天比对，cutover Week 3）。
9. PR check：migration 必须 paired upcast test；触 §3.0 内核必须 ADR。

Week 2 闸门：
- ✅ iPhone 重启后 thread history 完整（problem #6 解 = Tier A 第二条 acceptance）
- ✅ task 有 cancel/retry API；user 取消后 24h 内 dashboard 反映
- ✅ internal verified state 跑通；app 端 `done` 仍是 `done`（不破 contract）
- ✅ hourly backup 在 `~/MiraBackup/postgres/`
- ✅ 3 条 concurrency 测试 pass
- ✅ substack daily 未中断；reply dedup shadow 数据累积

### Week 3 — Publish Registry + Writer Gate + Auth Health + Sensitivity

目标：消灭「绕过 writer / silent auth fail」+ confidential leak 防御 + 4 篇 runbook。

1. **§3.3.3 publish_registry** 上线，所有 publish 路径 wrap 老实现进 `legacy_adapter`。
2. **§3.5.1 writer gate** 强制 —— 所有 substack（article / note / comment / reply）+ bluesky + x post 必经 writer agent，无 user 审核（[feedback_full_autonomy.md](../../.claude/projects/-Users-angwei-Sandbox/memory/feedback_full_autonomy.md)）。
3. **§3.5.3 auth health layer**：6 外部账户 health check（含 Anthropic OAuth throttle 自动 fallback 到 API key adapter）。
4. **§3.5.2 preflight 统一** 到 safety/preflight.py。
5. **§3.15 sensitivity 字段** 加到 `tasks` / `audit_events` / `Memory schema`（Memory 实装 Week 4，schema 先就位）；**publish_registry.dispatch 第一步 sensitivity_scan**（PII regex + Tier 0 主题 check 由 OpenAI gpt-5-mini 兜底）；模拟泄漏测试通过。Tetra 数据结构性隔离（`data/tetra/` 不被 `agents/socialmedia/` import）。
6. **substack reply pipeline cutover**（Week 2 shadow 7 天后）。
7. **Substack metrics 进入 daily report 顶部**（§3.2 公约 #5）。
8. **§3.14 runbook 起手 4 篇**：`operator-handbook.md` + `oauth-throttle.md` + `substack-publish-incident.md` + `wa-daily-check.md`。

Week 3 闸门：
- ✅ 1 周内 0 条 publish 绕过 writer
- ✅ 0 条 auth fail 静默 > 5min（Tier A acceptance #3）
- ✅ Anthropic OAuth fallback 到 API 路径至少触发过 1 次（验证 fallback 链）
- ✅ 模拟 confidential 泄漏被 block（Tier A acceptance #23）
- ✅ reply pipeline cutover 后 1 天无 incident
- ✅ substack metrics 在 daily report
- ✅ 4 篇 runbook merged

**Week 3 末强制 §3.19.7 mid-V2 reality check：** Week 1+2+3 三个 gate 全过吗？≥ 2 个 gate fail = V2 in trouble，进入 §3.19.3 三选一决策（catch up / descope / abort）。**「再坚持 3 周看看」不是选项**。

### Week 4 — Memory Port（degraded-mode safe）+ Self-Evolution Stage 1-2 + Self-Audit 闭环

目标：Memory port 完整 + degraded mode；self-evolution Stage 1+2（observe + curate）；self_audit 闭环。

1. **§3.10 Memory port 实装：**
   - Postgres `memories` 表 + index + pgvector embedding 列
   - `Memory.read / write / supersede / consolidate / list_recent` 5 verb 跑通
   - **Mem0-style write pipeline**（ADD/UPDATE/DELETE/NOOP）
   - file_mirror 双向 sync
   - 扫现有 `data/soul/` bulk INSERT；embedding 用 OpenAI `text-embedding-3-small`（oMLX 本地推 V3）
   - **§3.10.10a degraded mode**：Memory 不可用时 routine task 继续 + memory-required task 入 pending queue + recovery 后 drain
   - 6 周内手动 disable memory_adapter 或 drop pgvector index 1 次验证 degraded mode（**不要 stop Postgres**，那是 §3.13 DR scenario）
2. **§3.10.7 weekly consolidation cron**（周日跑）；**§3.10.8 retrieval audit log + decision provenance footer**。
3. **§3.4.2 self_audit 入 backlog**；§3.4.3 finding 闭环 + correction_log（Memory 已就位，consolidation 走 Memory）。
4. **§3.4.6 trace 写入 + 简版 replay CLI**。
5. **§3.11 self-evolution Stage 1 + 2 上线：** `data/self_evolution/observations/` jsonl + 周日 curate 成 `regression_suite/`。
6. **§3.12 weekly identity drift check job** 上线（用 Memory 但有 fallback：identity_core.md hash + 显式 forbidden phrases regex 也能跑）；首次 Week 4 周日跑。
7. **§3.13 weekly restore drill** cron 上线；Week 4 周日跑首次 drill。
8. **§3.14 runbook 续作 3 篇**：`memory-debug.md` + `dbos-stuck-workflow.md` + `cost-overrun.md`。
9. **substack notes publish path** 进入 shadow 7 天。

Week 4 闸门：
- ✅ Memory 5 kind 各有真实数据（不强求数量阈值）；retrieval p95 < 100ms；degraded mode 测试通过（disable adapter / drop pgvector index）
- ✅ 连续 7 天 ≥ 5 finding auto-fix-verified
- ✅ 任一 task 能 `mira replay`
- ✅ observations/ ≥ 20 条
- ✅ 首次 restore drill 成功
- ✅ 首次 identity drift check 跑通无 drift_serious
- ✅ substack notes shadow 数据累积

### Week 5 — LLM Router Shadow + Skill Retrieval + Tier 0 试点（1-2 个 task type）

目标：LLM router shadow vs keyword router；skill retrieval 作 Memory 子集；oMLX adapter 试点 1-2 个 task type 验证 quality fallback 机制。

1. **§3.0.1 #3 LLMProvider 补齐其余 adapter**：openai / gemini / minimax 完整化（Week 1 已有 anthropic_oauth + anthropic_api）。
2. **§3.0.1 #3 omlx_adapter 试点版**：能跑 Gemma 4 31B local；只接 1-2 个低风险 task type（建议 substack inbox dedup classify + anti-AI guard scan）；带 quality fallback（schema check / confidence threshold；fail 自动 fallback 到 openai gpt-5-mini）。**全面铺 V3**。
3. **§3.6 skill retrieval = `Memory.read(kinds=['fact'], tags=['skill'])`**；writer / researcher / analyst / discussion 全 wire；注入 user-message 段（KV-cache 友好，§3.10.4 格式）。
4. **§3.6.4 external_learn workflow** 上线，跑第一个 round（Tier 1 OpenAI gpt-5-mini 做 first-pass，只有 yes 上 Tier 2 深读）。
5. **§3.8.2 manifest tools 字段补齐**；**§3.8.1 LLM router 上线（shadow 与 keyword router 并行）**。
6. **§3.8.3 process_type** + **§3.8.4 Tier 分流（Procedural / Planner）** 落地。
7. **§3.11 self-evolution Stage 3 + 4 上线**：propose（Tier 2 anthropic_oauth + inoculation prompt + ≤3 file / ≤200 LOC）；validate（git worktree + regression_suite + true_goal_suite）。**Stage 5+6 dry-run only Week 6**（reviewer 调整：3 promoted commit 推 V3）。
8. **§3.18 voice & editorial v1（基础版）**：positive_guide.md draft + editorial calendar.yaml（覆盖 4 周）+ engagement.yaml + publish_outcomes.jsonl pipeline 上线。writer agent 强制 load positive_guide。**完整 voice 度量推 V3**。
9. **substack notes publish cutover** + **substack article publish 进入 shadow**。
10. **§3.14 runbook 续作 3 篇**：`self-evolution-frozen.md` + `self-evolution-no-promote.md` + `identity-drift-alert.md`。

Week 5 闸门：
- ✅ 任意 writer task prompt 中可见 retrieved skill + positive_guide context
- ✅ external_learn 1 round 完成
- ✅ LLM router shadow 报告显示历史误路由 case ≥ 80% 纠正
- ✅ oMLX adapter 试点 task 走通；fallback rate < 50%（说明本地能用）
- ✅ 至少 1 条 self-evolve proposal 通过 validate（stage 3+4 闭环）
- ✅ substack notes cutover 后 1 天无 incident
- ✅ 8 篇 runbook 全 merged

### Week 6 — Watchdog + DR Drill + Substack Article Cutover + V2 验收

目标：每条 pipeline SLO；DR 演练；最高风险 substack article path cutover；跑 §9 验收 + 写 V3 plan。

1. **§3.7.1 pipelines.yaml** + **§3.7.2 watchdog job**（DBOS workflow，每小时跑）。
2. **§3.7.3 podcast bypass quota** override executor。
3. **§3.7.4 daily report sections registry** 重写（含 substack_metrics + market_briefing required）。
4. **§3.11 self-evolution Stage 5 + 6 dry-run（reviewer 调整）：** canary harness 可用 + outcomes/ schema 落地 + FROZEN kill switch 测试 1 次 + canary auto-rollback 干跑 1 次。**不要求 7-day promote 闭环完成（推 V3）**。
5. **§3.8.1 LLM router cutover**（shadow 报告通过后）。
6. **substack article publish cutover**（Week 5 shadow 7 天后）—— V2 最高风险 cutover，**Mira 推 decision card，WA 在场 reply GO**。**必须 Week 6 Day 2 (周一或周二) 之前完成 cutover**，否则 3 天观察期跨不过 Week 6 末，criterion 标 "not evaluated" 而非 fail（reviewer 加：避免 cutover 拖到周末导致测不出 incident 期）。
7. dashboard 重构：每个 R 一面板 + cost dashboard（Tier 1/2 split + Tier 0 试点 metric）+ memory dashboard + self-evolution dashboard。
8. **§3.13 手动 DR 演练**：手动跑 1 次完整 dr-mac-died 流程到 sandbox VM / 第二台机器，验证 RTO ≤ 4h；手动跑 1 次 dr-postgres-corrupt 流程。
9. **§3.16 race condition manual test**：手动启 2 个 LaunchAgent invocation 验证 Layer 1 lock；模拟 race 测试 5 条 ≥ 4 通过。
10. **§3.17 1 次 rollback drill**：sandbox 跑 0003 migration 的 downcast 验证 rollback 路径。
11. 跑 §9 全部 26 条 + Enforcement #27 验收（Tier A/B/C）。
12. 写 V3 plan draft：focus on research-build loop + Tier 0 全推广 + self-evolution 完整 5-file 闭环 + 完整 voice metrics。
13. STABILITY.md retrospective：6 周内有没有破过 7 个内核接口？

Week 6 闸门：
- ✅ Tier A 7/7 全过 + Enforcement #27 必过 + Tier B ≥ 12/15 + Tier C ≥ 3/4 + 总 ≥ 22/26
- ✅ watchdog 每小时跑；手动停 podcast pipeline 8 天 watchdog 自动 escalate
- ✅ substack article cutover 后 3 天无 incident（cutover 必须 Week 6 Day 1-2 完成；若拖到 Day 3+，标 "not evaluated"）
- ✅ 6 周内 substack 0 天断档（Tier A）
- ✅ 6 周 LLM cost 持平 V1 baseline（不退步即合格；下降 ≥ 30% 推 V3）
- ✅ 至少 1 条 self-evolve proposal 凑齐 4-file（observation + regression test + git commit + scheduled outcome placeholder）+ 1 次 dry-run 5-file schema demo；**真实 5-file 闭环（含 7-day outcome）+ 3 commit 推 V3**
- ✅ DR 演练成功 RTO ≤ 4h
- ✅ race test ≥ 4/5 通过
- ✅ V3 plan draft merged

---

## 6. 跨 V2 全程必须遵守的硬约束

1. **不允许新增任何代码绕过 §3.0 7 个内核接口。** 业务代码不直 import LLM SDK / 不裸 file write / 不裸 dispatch task。
2. **不允许新增代码绕过 §3.3 4 个 registry。** publish / job / skill / bridge endpoint 都必须先注册。
3. **任何 PR 必须引用 [system-design.md](system-design.md) section + 本文档 §X.Y。** PR description 缺 reference = CI reject。
4. **不允许打补丁式修复。** 30 天内已修过又复发的问题，第二次必须做结构性分析（写 ADR）后再动。
5. **不允许 silent skip。** 任何 `try: ... except: pass` / 「config 不存在就跳过」 = 改成 explicit fail-fast 或 explicit log + escalation。
6. **任何动 substack 生产路径的改动必须走 §3.1 strangler 7 步。** 7 天 shadow 是下限。
7. **每天 substack 必有产出。** §3.2 公约。断 1 天 → critical incident signal + Mira 推 decision card，**WA explicit reply 后才回滚**（不自动）。
8. **STABILITY.md 内 7 个接口任何破坏都是 design-boundary change，必须 ADR + 12 个月 deprecation 期。**
9. **保留对原 hard rule 全部继承。** [CLAUDE.md](../../CLAUDE.md) HARD RULE 1–6 照旧。
10. **新 task type 必须在 routing.yaml 显式声明 tier。** Default 必须显式，不允许「忘了写 routing 就走 Tier 2」让 cost 飙升。Tier 2 必须在 yaml comment 写理由。
11. **本地 Gemma quality fallback 必须有。** 走 Tier 0 的 task type 必须声明 `tier0_quality_check`（schema / rule / confidence threshold）；失败自动 fallback 到 Tier 1 + audit log 记 `tier0_fallback_reason`。
12. **Memory 永远 append-only。** 任何 memory mutation 必须经 `Memory.supersede()`，不允许直接 UPDATE / DELETE memories 表。CI grep `UPDATE memories|DELETE FROM memories` 出现 = block。
13. **Memory 注入 prompt 不允许 paraphrase。** retrieved memory 内容必须原文注入；LLM 改写 = KV-cache 杀；agent 代码 `f"summarize this memory: {m.content}"` 类用法 = block。
14. **Self-evolution claim 必须可审计**（§3.11.7 + §3.11.9）。**V2 内 demo 必须凑齐 4-file**（observation + regression test + git commit + scheduled outcome placeholder）；**真实 5-file 闭环（含 7-day outcome）只能在 V3 出现**。dashboard / 报告说 "Mira 改进了 X" 必须能引出对应阶段的文件。缺 = HARD RULE 1 违反，自动 retrospective。
15. **Self-evolution 不允许 modify §3.0 内核接口或 §3.11 自身。** CI grep proposal diff 路径，触到 `lib/llm/`、`lib/memory/`、`agents/super/runtime/`、`agents/super/self_evolve.py`、`lib/identity/` = auto-reject + alert WA。
16. **identity_core.md 不可动**（§3.12）。任何 PR 触它 = block 除非含 `IDENTITY-CHANGE-APPROVED-BY: WA` + ADR。weekly drift check 不允许跳过。
17. **DR backup + drill 不允许 silent skip**（§3.13）。pg_dump cron 失败必须 alert；restore drill > 7 天没成功 = 红色 alert。
18. **任何新 mechanism PR 必须 link 到 runbook**（§3.14）。CI 检查 PR 触关键路径不更新 runbook = block。
19. **Memory.write 必带 sensitivity 字段**（§3.15）。publish_registry.dispatch 必须经 sensitivity_scan。Tetra 数据**结构性隔离**（`data/tetra/` 永远不被 `agents/socialmedia/` import）。
20. **Cross-process critical section 必须用 advisory_lock**（§3.16）。concurrency 测试套 ≥ 90% pass，CI block PR 不达标。
21. **Schema 变更必须经 lib/migrations/**（§3.17），含 paired upcast test。直接 `ALTER TABLE` SQL 在 migrations 之外 = block。
22. **Writer agent 必 load positive_guide.md**（§3.18）。每次 publish 24h 内必有 publish_outcomes 记录。voice drift 指标连续 2 周 > 阈值 → 触 §3.12 drift_warning。
23. **V2 plan 自我可监督**（§3.19）。Week 0 setup 不过 = Week 1 不启动。每周 Sunday review binary gate 决策必写。Week 3 mid-V2 reality check 触 abort 的不允许「再看一周」。
24. **mira-next.md 自身 freeze**：Week 1 起，PR 修改 mira-next.md 必须 ADR + 不影响 current week gate；连续 2 个 acceptance criterion 被改弱 → trigger §3.19.3 abort 决策。
25. **V2 hard end 6 周**：Week 6 后所有 follow-up 工作必须挂 V3，不挂 V2。没有 V2.1 / V2.2 extending forever。

---

## 7. 明确不在 V2 scope 里的事

V2 不解决以下问题。

1. **不上 App Store。** 没有 TestFlight / 提交 / per-provider consent UI / pre-publish review surface / cloud relay / Sign in with Apple / StoreKit / PrivacyInfo.xcprivacy。这些是产品化负担，个人自用不需要。如果未来要上，新起 plan，**不许动 §3.0 内核**。
2. **不重写 iOS sync layer（CKSyncEngine）。** SwiftData 替代 ItemStore.swift 是为 message thread reliability，sync truth 由 API/Postgres 担。iCloud 降级为只读 backup + artifact mirror。
3. **不上 cloud relay（Cloudflare Workers / Fly.io）。** 个人 LAN 路径足够；iCloud 仅 backup。
4. **不重写 web GUI。** web 只作 fallback。
5. **不接新 social media 平台。** linkedin / threads / mastodon 全不上。
6. **不重构 podcast voice 选型。** zh/en TTS 配置维持现状。
7. **不做多用户 / SaaS。**
8. **不做新论文 / 新研究方向。** north star research-build loop 在 R1–R6 闭环前不展开（V3）。
9. **不做 super-agent-as-Hermes-loop 全量重写。** §3.8 只做 LLM router 这一层。
10. **不做 speculative execution。** 永久不做。
11. **不引入 LangGraph / CrewAI / AutoGen 依赖。** 永久不做。
12. **不做 vector DB 作主 memory。** 用 pgvector 即可。
13. **不允许 in-process multi-agent 共享 message state。** Cognition 已 publicly 否决。
14. **不做 IAP / 商业化。** V2 个人自用。
15. **不引入第三方 OAuth wrapper / claude.ai session 复用。** 已被 Anthropic ban。

---

## 8. 成本与时间预算

### 8.1 V2 6 周预算（OAuth 主路径，Tier 0 仅试点）

reviewer 调整后：oMLX adapter 仅试点 1-2 task type，**V2 cost 持平 V1 baseline 即合格**（Tier B，criterion #17；reviewer 调整：不放 Tier A，因为 task reliability + no leak + no silent auth 比 cost 更关键）。下降 ≥ 30% 是 V3 目标。

| 项 | V2 6 周预算 | 备注 |
|----|-----------|------|
| Anthropic Pro/Max OAuth（flat） | $30 | 6 周 ~ 1.5 月，已付沉没成本 |
| Anthropic API（fallback） | $100 | OAuth throttle / 长 context 时切；V2 无 Tier 0 推广所以用量持平 V1 |
| OpenAI API（Tier 1 + embedding） | $80 | gpt-5-mini 做 fallback；embedding（Memory 上线后增量） |
| Gemini API（EN TTS） | $40 | 现有 podcast 频率 |
| MiniMax（ZH TTS） | $50 | 同上 |
| Search APIs | $20 | external_learn 抓 source |
| PostgreSQL 17 / 本地 oMLX | $0 | macOS native + 已部署 |
| 其他 / buffer | $40 | |
| **6 周总计** | **$360** | hard cap（容许少许增量给 Memory + self-evolution propose 试跑）|

**Cost-saving target（reviewer 调整）：**

| 项 | V1 baseline | V2 target | V3 target |
|----|-------------|-----------|-----------|
| 月度 LLM call cost | ~$120 | ~$120（持平 = Tier B criterion #17 合格）| ~$50（Tier 0 全推广）|
| 月度 TTS cost | ~$60 | ~$60 | ~$60 |
| 月度其他 | ~$10 | ~$30（增 Memory embedding）| ~$10 |
| **月度总** | **~$190** | **~$210** | **~$120** |

V2 阶段 cost 略涨（Memory embedding + self-evolution propose 试跑增量）。**这是诚实承认：V2 在打底盘，省钱是 V3 收益。** 任何要 V2 内 cost 降 30% 都是 plan inflation。

### 8.2 Cost watcher（强制）

1. `agents/super/cost_watcher.py`：每次 LLMProvider call 后写一行 `data/cost/usage.jsonl`：`{ts, tier, provider, model, input_tokens, output_tokens, cost_usd, task_id, agent}`。
2. Daily cost 推送到 daily report（与 substack metrics 并列），按 tier 拆分（Tier 0 / 1 / 2 各多少 call、各 $X）。
3. 月度 80% → warn；95% → freeze 非关键 job（external_learn / self_audit auto-fix 暂停）；100% → hard stop + escalate。
4. 单次 task cost > $0.20 → 自动 needs-review（V1 是 $0.50，V2 严格化）。
5. **Tier 0 fallback 监测：** 本地 fallback 到 Tier 1 的频率 dashboard surface；如某 task type 持续 fallback rate > 30% → 该 task type 标记「Gemma 不够用」进入 review queue（可能要 fine-tune 或换更大模型）。

### 8.3 时间预算（push-based 修正版）

详见 §3.19.5。WA 不 schedule 任何东西，**Mira push WA reactive review**：

**WA 真投入 ≈ 15-30 min/day scattered**：
- 看 iOS app 推送（一天几次刷手机时顺便）
- Decision card reply 1-2 个字（24h 内，否则走 conservative default）
- 周日 gate review reply
- 偶尔 Mira / Claude Code 来问 design 选项

**没有 calendar block。没有 schedule。**

**Claude Code session 自主跑 build**（背景），checkpoint 时 push 给 WA reply。WA 可以 "开 session 做 Week 1 任务" 然后去做别的。

**Mira runtime 24/7 不占人工。**

irreversible 节点 WA 必须 explicit APPROVE 才执行（不允许 24h default 自动 cutover）：
- Week 0 pre-flight test 结论 reply
- Week 1 kernel design merge approve
- Week 3 mid-V2 reality check 决策 reply
- Week 6 substack article cutover GO reply

---

## 9. 验收：6 周后看什么

6 周末做 V2 retrospective。

**核心 26 条 + 1 条 enforcement，分 3 Tier**（§3.19.12）：

### Tier A — 不过这条就是 V2 fail（必须 7/7 全过）

| # | Criterion | 目标 |
|---|-----------|------|
| 1 | iPhone Mira app 7 天丢任务次数 | 0 |
| 2 | publish 绕过 writer 次数 | 0 |
| 3 | auth 静默失败 > 5min 次数 | 0 |
| 15 | V2 6 周内 substack 断 1 天次数 | 0 |
| 20 | identity_core.md 修改次数 | 0 |
| 21 | hourly backup 不断 + 1 次完整 DR 演练 RTO ≤ 4h | 必过 |
| 23 | 0 confidential / regulated leak | 必过 |

### Tier B — 应该过（≥ 12/15）

| # | Criterion | 目标 |
|---|-----------|------|
| 4 | 文章→podcast 自动跑完率 | ≥ 90% |
| 5 | daily report 含 market_briefing + substack_metrics 比例 | 100% |
| 6 | self_audit 7 日内 eligible 真实 finding auto-fix-verified 比例（**reviewer 加：不数 raw 数量防造假；only 真 finding 算入分母**）| ≥ 80% + 至少 5 个 representative 已 verified fixes |
| 7 | user request 48h 自动 verify 比例 | ≥ 80% |
| 8 | writer task prompt 中 retrieved skill / memory 命中率 | ≥ 95% |
| 9 | external_learn 完成 round 数（V2 仅 Week 5 起首轮；4 rounds 推 V3）| ≥ 1 |
| 10 | 外部 source proposal 入 backlog（**实施率 ≥ 30% 推 V3**，V2 仅要求 captured + 进 §3.11 Stage 2 regression suite）| proposals captured，无实施率 target |
| 11 | internal verified vs completed_unverified 比例（verified）；app `done` 只在 verified 时显示 | ≥ 80% |
| 18 | Memory 5 kind 各有真实数据（**不强求 100 数量阈值，避免造假填充**）；retrieval p95 < 100ms；writer task `used_ids` 非空率 ≥ 80%；Memory cutover 后每周 ≥ 1 supersede；consolidation cron Memory cutover 后每个计划周日跑成功（V2 内 ≥ 2 次成功，因 Memory Week 4 才上线）；degraded mode 测试通过（disable memory_adapter 或 drop pgvector index，**不 stop Postgres**）| 全过 |
| 19 | Stage 1-2 上线（observations ≥ 30 + regression_suite ≥ 5）+ Stage 3-4 上线（≥ 1 validated proposal，4-file 已凑齐：observation + regression test + git commit + scheduled outcome placeholder）+ Stage 5-6 dry-run（canary harness 可用 + outcomes schema 落地 + FROZEN + canary auto-rollback 各手动 trigger 1 次）；**真实完整 5-file 闭环（含 7-day outcome）+ 3 commit + promote rate ≥ 50% 推 V3**（V2 6 周不可能跑完 7-day window）| 全过 |
| 22 | 8 sub-runbook + entry + daily-check 全 merged + 5-min 定位测试通过 | 全过 |
| 24 | 5 个 LOCK_ID 全用 + concurrency 测试套 ≥ 90% + 0 race condition incident | 全过 |
| 25 | lib/migrations/ ≥ 3 migration + runbook + 1 rollback drill + CI rule | 全过 |
| 26 | positive_guide.md draft merged + calendar 4 周 + engagement.yaml + publish_outcomes pipeline 跑起来；**完整 voice 度量 + 月度 auto-suggest 推 V3** | 全过 |
| 17 | V2 末月度 LLM cost 持平 V1 baseline（不退步即合格）；oMLX Tier 0 试点 ≥ 1 task type 跑通 quality fallback；下降 ≥ 30% **推 V3** | 全过 |

### Tier C — Nice to have（≥ 3/4，fail 不阻塞 V3）

| # | Criterion | 目标 |
|---|-----------|------|
| 12 | LLM router shadow 期纠正率 | ≥ 80% |
| 13 | crash 后 DBOS 自动 resume 比例 | ≥ 95% |
| 14 | 任意 task 可 `mira replay` 比例 | 100% |
| 16 | V2 末 vs V2 启动 subscriber growth | 持平或上涨 |

### Enforcement（§3.19）— 这条 fail 整个 plan 都不算数

| # | Criterion | 目标 |
|---|-----------|------|
| 27 | Week 0 setup 8 闸门全过 + 6 周每周 binary gate 决策都写 + Week 3 mid-V2 reality check 跑了 + Week 6 hard-end 触发 + 旧 plan DEPRECATED + V2 announcement 发了 | 全过 |

### V2 SUCCESS 标准

- Tier A: 7/7 + Enforcement #27 必过
- Tier B: ≥ 12/15
- Tier C: ≥ 3/4
- 总: ≥ 22/26

满足 → V2 SUCCESS，启 V3。
不满足 → V2 PARTIAL，**写 retrospective + 决策**（修补 / 接受 / 大改重启 V3）；**不开 V2.1 extending**。

**STABILITY.md 检验：** 6 周内 §3.0 7 个内核接口 + identity_core.md 任何一个破过 = 直接 V2 fail（即使其他都过）。

---

## 10. 一句话总结

V1 想让 Mira 成为独立研究者，但 Mira 还不是个能闭环的 agent。
**之前 4 份 plan 都很 production-grade 但落地为零**。所以 V2 比技术 design 更重要的是：**plan 自己能不能落地**。

V2 做 14 件事（13 内容 + 1 enforcement）：

**底盘类（4）：**
1. **锁住 7 个内核接口 + tech stack**（§3.0 + STABILITY.md），让 V3 不再大改主干。
2. **strangler fig 保住 substack 突破口**（§3.1 + §3.2），refactor 期间 0 天断档。
3. **闭环 4 件最基础的事**（R1–R6），让 done 不再撒谎。
4. **多 provider 三层 routing 路径打通**（§0.5.7），oMLX adapter 试点 1-2 task type 验证 quality fallback；V2 cost 持平 V1（不退步即合格），**Tier 0 全推广 + cost 降 30% 推 V3**。

**记忆 + 学习类（2）：**
5. **Memory 系统真上线**（§3.10），bi-temporal supersede + 5 kind + Mem0 写入 + KV-cache 友好注入。
6. **Self-evolution stage 1-4 + 4-file validated proposal + Stage 5/6 dry-run schema demo**（§3.11.9）。**真实 5-file 闭环（含 7-day outcome）+ 3 commit + promote rate 推 V3**。

**安全 + 韧性类（5）：**
7. **Identity 锚点**（§3.12），immutable identity_core + weekly drift check 防 self-evolve drift。
8. **DR & Backup playbook**（§3.13），RPO 1h / RTO 4h + weekly restore drill + 3 emergency runbook。
9. **WA Operator Runbook**（§3.14），8 sub-runbook + 5-min daily check 让 incident 5 分钟定位。
10. **Secrets & Sensitivity**（§3.15），4 档分级 + Memory sensitivity 字段 + publish-path scan + Tetra 结构性隔离。
11. **Concurrency strategy**（§3.16），5 个 advisory lock + race condition 测试套。

**演进 + 表达类（2）：**
12. **Schema evolution playbook**（§3.17），migrations 目录 + paired upcaster + rollback drill。
13. **Voice & Editorial strategy**（§3.18），positive voice guide（不只 anti-AI）+ editorial calendar + outcome learning loop。

**Enforcement（1，最关键，否则前 13 都是装饰）：**
14. **Plan self-enforcement**（§3.19）—— Week 0 setup 闸门 + Mira 每日 push V2 status card 到 iOS app + Sunday gate review card + Week 3 mid-V2 reality check abort 选项 + V2 hard end 6 周；旧 plan decommission；substack public commitment；**WA 不 schedule 任何东西，刷手机时 reply decision card 1-2 字即可**（15-30 min/day scattered）；Claude Code session 自主跑 build，checkpoint push WA。**没这条，前 13 条就是第 5 份不落地的 plan**。

6 周后 substack 还在涨、**iPhone 发 task 100% 成功（这是用户 #1 stated 痛点，Week 1 Tier A 第一）**、message thread 不丢（Week 2）、Mira 真在记忆 + 更新判断（Memory degraded-mode safe）、self-evolution 跑通 stage 1-4 + 4-file validated proposal + 5-file dry-run schema demo（真实 5-file 闭环 + 3 commit 推 V3）、Mira 还是原来的 Mira（identity 没漂）、Mac 死了能 4 小时起来、出问题 WA 5 分钟定位、敏感数据没泄、并发不撞、cost 持平 V1（Tier B，task reliability 比 cost 更关键；降 30% 推 V3）、**而且这一切都被 binary gate 周周验证不靠 retrospective 后知后觉** —— 才有资格谈 V3 的 research-build loop + Tier 0 全推广 + self-evolution 完整 5-file 闭环。

**诚实承认：** 这套 push-based enforcement 解决了过去 5/6 plan 失败模式。第 6 条无解 —— **WA 是否打开手机 reply decision card**（§3.19.5 push-based 修正版：Mira push，WA reactive review，平时刷手机顺便 reply 1-2 字即可，15-30 min/day scattered，零 calendar block）。WA 完全不 reply = 没救；24h default 设计让一时不 reply 不出 disaster；连续 3 天不 reply 任何 V2 card → Mira 自动推 escalation「V2 是否 abort」。早期偏差立即可见。

---

## 11. V3 Horizon — 不在 V2 但已经承诺

V2 验收 26 条按 Tier A/B/C（§3.19.12）合格后启动 V3：Tier A 7/7 全过 + Tier B ≥ 12/15 + Tier C ≥ 3/4 + Enforcement #27 全过 + 总 ≥ 22/26。
V3 在 §3.0 内核之上做的事，不在内核动手术。

### 11.1 Research-Build Loop 真正展开（V3 主线）

V2 撑住底盘后，[north-star.md](north-star.md) §11.7 research workflow 才有底盘真正落地：

- research queue 自主维护（issue 形式）
- 实验设计 → 执行 → 数据 → 结论 → worldview update
- 发文 → 外部反馈 → 新问题
- A2A trust 方向第一个完整实验

### 11.2 完整版 Trace Replay

- V2 的 replay 是「同 prompt 重跑」，依赖外部 API still up
- V3 加 mock tool layer（record-replay external calls）
- 用途：regression test、self_evolve before/after ground truth

### 11.3 Super-Agent-as-Hermes-Loop（speculative）

- V2 §3.8 只做 LLM router 这一层
- V3 试点：把 super agent dispatch 改成单 Claude Agent SDK 模式 loop
- 仅用于 Planner Tier，Procedural Tier 维持过程式
- 决策放 V3 retrospective 时再做

### 11.4 Tier 0 全推广 + 本地模型升级（reviewer 调整：从 V2 推到 V3）

- V2 仅 oMLX adapter 试点 1-2 task type（验证 quality fallback 机制）
- V3 全推广：14 个 Tier 0 候选 task type 逐个迁移（dedup / triage / scan / classify / repair / status format / skill retrieval / external_learn first-pass / 等等）
- 视 Tier 0 fallback rate 决定：
  - 持续 fallback rate > 30% → 先检查 prompt/schema 与 `gemma-4-31b-it-4bit` context fit；仍不够再试同级或更强 oMLX 量化模型
  - 部分高频高质 bar task type → 用 Mira 自家数据 fine-tune
- 目标：月度 LLM cost 较 V1 baseline 降 ≥ 30%（V2 不强求，V3 真做）

### 11.5 Self-Evolution 完整 5-File 闭环（reviewer 调整：从 V2 推到 V3）

- V2 仅 stage 1-2 + 1 validated proposal + 1 dry-run canary
- V3 真闭环：
  - 至少 3 条 self-evolve commit 全 5-file 闭环（observation / regression test / git commit / outcome / 引用回原 ID）
  - promote rate ≥ 50%
  - anti-pattern 库累积
  - 多周 outcome 累积证据
- 需要 7-day window × N commit 的观察期，单一 6 周 plan 不够

### 11.6 完整 Voice 度量 + Editorial Analytics（reviewer 调整）

- V2 仅 positive_guide.md draft + outcomes pipeline
- V3 完整：
  - voice drift 多维量化（avg sentence length / em-dash count / abstract noun density / vs reference essay distance）
  - 月度 auto-suggest positive_guide 更新（基于 outcomes）
  - cross-platform attribution
  - statistically validated voice baseline（需要更多 publish 数据）

### 11.7 可选：上 App Store

- 如果 V3 时还想上，新起 plan，**不许动 §3.0 内核**
- 加之前剥掉的：per-provider consent UI / pre-publish review / cloud relay / Sign in with Apple / StoreKit / PrivacyInfo
- 大概 4–6 周专门工作量
- 触发条件：substack subscriber > 500 + 有 paid tier 商业模型才考虑

### 11.8 显式不在 V3 scope

- 不重写 §3.0 7 个内核接口
- 不打破 STABILITY.md
- 不接 vector DB 作主 memory
- 不做 in-process multi-agent
- 不做大规模 SaaS

**V3 触发条件：**
1. V2 §9 26 条验收按 Tier A/B/C 合格（Tier A 7/7 + Tier B ≥ 12/15 + Tier C ≥ 3/4 + Enforcement #27 + 总 ≥ 22/26）
2. WA 1 周内介入 < 3 次（reactive review 无堆积）
3. STABILITY.md 7 个内核接口 6 周未破
4. 月度 LLM cost ≤ V1 baseline（不退步即合格；下降 ≥ 30% 是 V3 内的目标，不是 V3 触发条件）

四条同时满足 → 启动 V3 plan 文档。任意一条不满足 → V2 阶段没完，先 fix。
