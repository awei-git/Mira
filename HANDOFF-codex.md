# HANDOFF-codex.md — 给 Codex 的任务单（Mira 维护）

Codex 在 Mac 上开工前先读这个文件。任务细节在 GitHub issue 里，
这里只放**最新的架构拍板**（会覆盖 issue 里过时的部分）。

## 当前任务

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
