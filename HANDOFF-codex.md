# HANDOFF-codex.md — 给 Codex 的任务单（Mira 维护）

Codex 在 Mac 上开工前先读这个文件。任务细节在 GitHub issue 里，
这里只放**最新的架构拍板**（会覆盖 issue 里过时的部分）。

## 当前任务

- Issue #22：[codex] mira-content 收尾：源码上 GitHub + 三处修复转正 + 运行问题
  https://github.com/awei-git/Mira/issues/22
  六个任务按 issue 正文做。任务 1（源码上 GitHub）最高优，block 任务 2。
  注意：任务 2 涉及退役 Mira 昨晚直接改主机的 bind-mount 覆盖层
  （/etc/mira-creator/overrides/），顺序按 issue 正文，删之前别动覆盖层。

  [2026-09-24 晚 Mira 读盘补充诊断]
  - Tetra research 0-for-3（9/23 晚、9/24 早、9/24 晚）：模型其实产出了实质报告，
    被 strict schema validator 拒了。隔离的 9/24 晚间输出
    （/var/lib/mira-content/research/runtime/producer/2026-09-24-evening-invalid-output.txt，
    36KB 中文）是五主题真实分析（UiPath / Recursion / BWXT / IonQ / Robinhood，
    全 rated "wait"，entry_ceiling null），被 rule `method_requires_dated_sources`
    拒（9 errors）。修 validator/prompt，不是模型。mail 于是只发 failure notice。
  - 9/25 01:55 UTC 镜像（924642b9e3ea，revision 006c8b2e）里 English ✓、
    novelty guard ✓（autonomy.py:88-91），但 "Mira is a real person" voice rule
    没进镜像——补一次 build。
  - creator 每个 tick exit 1（project_errors 非空时），systemd 恒报 failed，
    盖住真故障——建议干净 tick exit 0。
- Issue #19：[codex] Mira 双运行时接线（盒子侧同步 + soul 读取 + 统一账本）
  https://github.com/awei-git/Mira/issues/19
  四个任务按 issue 正文做。

## 架构拍板（2026-09-23，Ang 定，覆盖旧设计）

1. **健康不进 AWS。** 健康数据（Apple Health / Oura / 体检报告）是最高敏感
   的个人数据，且已在对话 Mira 侧跑通（每日健康早报 cron）。AWS 盒子只做
   content creator，**不碰**健康、日历、家庭隐私。
   分工：个人事务 = 对话 Mira（Muse app）；内容生产 = AWS 盒子。

2. **取消唤醒循环。** 盒子不需要"每 N 秒醒一次看看干什么"。
   它是批处理 worker：选题挖掘、写作管线、播客生成、发布，全部由 cron /
   定时触发；外部派活走统一账本（任务队列），配一个轻量 queue watcher
   （几分钟扫一次）即可。README 里"30 秒唤醒、主动 spark、接电话端请求"
   那套是为"盒子 Mira 是唯一 Mira"设计的，现在唯一入口是对话 Mira，
   这部分直接删掉，不要做成可配置——没有唤醒了。

3. 对 issue #19 的影响：任务 2（soul 读取）、任务 3（账本）、任务 4（outbox）
   不变；账本就是盒子**唯一**的外部任务入口。

## 协作约定（既有，重申）

- 分支从 `cloud/podcast-api-env` 切 `codex/` 开头分支（deploy/ 管线在那上面）。
- 做完开 PR，Mira review 后合并。每次改动配一句人话说明。
- 密钥类不上 GitHub。token 只躺在 EC2 `/opt/mira-bridge/.token` 和
  Mac `~/.config/mira-bridge/token`。

## 新增：写作管线任务（2026-09-23，Mira 加单）

背景：英文 Substack + 中文写作线的编辑定位已锁定（repo
`docs/substack-constitution.md` 和 `docs/zh-writing-positioning.md`，分支
`cloud/podcast-api-env` 上都有）。seed 流已通：聊天讨论 → `seeds.jsonl`
→ GitHub `seeds/seeds.jsonl`（`track` 字段区分 `substack_en` / `zh`）。
但"AWS 取 seed 写稿 → 草稿回聊天 signoff"这一环**没人做**——issue #19
的任务 4 是 journal outbox（每日干了什么/经验/同步事项），不是 draft
回流。以下两个任务补上这个缺口：

### 任务 5：seed → draft（AWS 写稿，英文线）
- 输入：GitHub 同分支 `seeds/seeds.jsonl`，只处理 `status=ready` 且
  `track=substack_en` 的 seed（`zh` 的先不动，等中文线播客管线）。
- 写稿前必读：`docs/substack-constitution.md`（北极星/pillars/voice/
  gates，以它为准）+ `agents/writer/voice/substack_voice.md` +
  `agents/substack/README.md`（editorial gates）。
- 复用 `agents/writer/` 现有 prompts/checklists（从 `handler.py` 起），
  不要重写一套；缺的按宪法补。
- 输出：draft markdown，路径你定（写进文档），文件名带 `seed_id`。
- 触发：cron/定时扫 seed（批处理 worker，不用唤醒循环——见上面架构拍板 2）。
- 完成标准：拿 `seeds.jsonl` 现有 seed 跑一遍，出一篇能读的 draft，附跑通记录。

### 任务 6：draft 回流 → 聊天 signoff
- draft 写完后，对话 Mira 要能在聊天里递给 Ang 审批（publication gate：
  人工批准才发布，宪法铁律）。
- 走统一账本（任务 3）：draft 完成记一笔事件，payload 带 draft 路径 +
  `seed_id`；**写入侧归你，读取侧归 Mira**（app 侧 cron 捡事件转聊天
  消息，跟任务 4 的分工一样）。
- 在任务 3 的账本设计里预留这个事件类型，别做两套。
- 完成标准：端到端跑一遍 seed → draft → 账本事件（Mira 侧认领转聊天）。

### 状态（2026-09-23）
- Issue #19：已开，暂无回复。任务 1–4 按 issue 正文做（注意上面的架构
  拍板覆盖旧设计）。
- 任务 5–6：本次新加，先做 1–4 也行，但 5–6 是写作线真正"通"的关键。

## Mira review — PR #20（2026-09-23）

结论：**MERGE-WITH-FIXES**。任务 1/2/4/5 实现正确，任务 3/6 的 gating
诚实（没偷跑实现），scope 干净（无密钥、无健康数据、无自动发布、无唤醒
循环残留），测试是实质性的。但有一个必须修的问题，修完我合：

**必须修 R1：`lib/memory/soul_skills.py` 的 hash 方案变更影响 app 侧。**
`current_hash` 从 `sha256(stripped_text)` 改成 `sha256(raw_text)`——app 侧
（Muse app）的 `_SKILL_AUDIT_HASHES` 是 stripped 口径，合并后首次加载会
全部 mismatch → 所有 skill 被迫重审，`SkillAuditFailedError` 会 block 之前
正常的 skill。二选一：(a) `MIRA_SHARED_ROOT` 未设置时保持 stripped 口径；
(b) 显式一次性迁移 + 文档说明。

**Nits（不 block，可顺手修）：**
- R2 `lib/content_worker/files.py:64-72`：`audit()` 把日志写进被 review 的
  release checkout，污染输入树——换个输出位置。
- R3 `lib/content_worker/outbox.py:36`：`daily_records` 判
  `status != "succeeded"`，但 producer 从没发过 `"succeeded"`——对齐词汇。

**账本合约（任务 3）暂不批准。** 提案本身不错，但缺三块，补上再实现：
1. Muse 侧 event poller 规格：频率、cursor 持久化、失败处理（文档只说
   "Chat delivery is Mira's responsibility"，没给机制）。
2. 人工批准 → authorized publication obligation 的流转：谁 mint、怎么绑定
   到 exact draft bytes。
3. draft 工作的 lease-reclaim 必须先查 receipt，否则过期 lease 会触发重复
   付费 model run。

**已知限制（R4，非 bug，先记下）：** timer 扫的是已部署的
`seeds/seeds.jsonl`，新 seed 要进盒子得走一次 release+deploy——两次定时
扫描之间会空转。draft 延迟取决于发版节奏，可以接受，先不改。

**你修完 R1 之后，我给的输入（按顺序）：**
1. live host 确认：mira-content（内容盒子）；退役 host 只做 staging 验收。
2. seed：我给一颗 `ready` + `track=substack_en` 的 seed 做端到端验收。
3. identity/cloud 投影：我来定内容-only 投影，raw USER/MEMORY 不上 AWS
   （你 PR 里 fail-closed 是对的，保持）。

## PR #20 已合并 + 账本合约批准（2026-09-23，Mira）

- PR #20 已合并进 `cloud/podcast-api-env`（merge 959ae5bc）。R1/R2/R3 修得对，
  app 侧指纹保住了。
- **账本合约 rev 2 批准。** 三个缺口都补上了：Muse 侧 poller 规格（60s、cursor
  持久化、幂等投递）、人工批准→publication authorized 的绑定（exact bytes
  hash + nonce + 24h）、lease-reclaim 先查收据。可以实现任务 3（账本）和
  任务 6 写入侧。注意：trusted approval endpoint 和 poller 的读取侧是 Mira
 （app 侧）来做，你只做写入侧 + 账本实现。
- **live host 确认：mira-content。** 内容管线跑在这台；退役 host 只做
  staging 验收。timer 先不上生产——等端到端验收过了再说。
- Mira 的输入（接下来我给）：① 一颗 `ready` + `track=substack_en` 的 seed；
  ② `identity/cloud/` 内容-only 投影（我来定，raw USER/MEMORY 不上 AWS）。

下一步顺序：任务 3 账本实现 → 任务 6 写入侧 → 我给 seed+投影 → 端到端验收
→ timer 上生产。不要跳步。
