# HANDOFF-codex.md — 给 Codex 的任务单（Mira 维护）

Codex 在 Mac 上开工前先读这个文件。任务细节在 GitHub issue 里，
这里只放**最新的架构拍板**（会覆盖 issue 里过时的部分）。

## 当前任务

- Issue #22：[codex] mira-content 收尾：源码上 GitHub + 三处修复转正 + 运行问题
  https://github.com/awei-git/Mira/issues/22
  六个任务按 issue 正文做。任务 1（源码上 GitHub）最高优，block 任务 2。
  注意：任务 2 涉及退役 Mira 昨晚直接改主机的 bind-mount 覆盖层
  （/etc/mira-creator/overrides/），顺序按 issue 正文，删之前别动覆盖层。
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

## Issue #22 源码定位与执行状态（Codex，2026-09-23）

GitHub API 已确认现有 **private** 仓库
[awei-git/mira-cloud](https://github.com/awei-git/mira-cloud)，默认分支
`codex/aws-migration`。不是源码尚未入库；无需新建或复制一份仓库。
本次核对的不可变源码版本是 `bd7e23b3f149e03008e54b498932c982ddaf5350`：

- [creator.py](https://github.com/awei-git/mira-cloud/blob/bd7e23b3f149e03008e54b498932c982ddaf5350/Mira/lib/mira/agents/creator.py)
- [autonomy.py](https://github.com/awei-git/mira-cloud/blob/bd7e23b3f149e03008e54b498932c982ddaf5350/Mira/lib/mira/autonomy.py)
- [Tetra public_research](https://github.com/awei-git/mira-cloud/tree/bd7e23b3f149e03008e54b498932c982ddaf5350/Tetra/src/tetra/public_research)
- [主机 systemd/部署源码](https://github.com/awei-git/mira-cloud/tree/bd7e23b3f149e03008e54b498932c982ddaf5350/Mira/deploy/aws/creator)
- [GitHub workflows](https://github.com/awei-git/mira-cloud/tree/bd7e23b3f149e03008e54b498932c982ddaf5350/.github/workflows)

`awei-git/Mira` 当前的 `agents/writer/`、共享身份与 PR21 账本，是另一套
源码布局。云主机 creator 不会因为本仓库合并 PR 就自动运行新版 writer。
Issue #22 的 creator/Tetra 修复应在 `mira-cloud` 走 PR，双运行时接线继续在
本仓库走 PR；最终部署需要明确哪一入口执行哪套代码，不能以同名 Mira 推断。

AWS 访问已恢复，并已建立 GitHub OIDC 临时凭证通道；不需要反复登录，
不保存长期访问密钥。`MiraContentOperator` 仅绑定内容主机。
诊断 workflow `35930167461` 与 SSM 回执均成功。

Tetra 修复已通过 `tetra-recovery-20260923` 发布部署，源版本
`c6b1768b5f218938d71d54db73d6d1c47e4fafeb`；激活状态 workflow
`35934004575` 成功，部署后只读检查 `35934233726` 成功。
原有四个早晚研究/邮件 timer 已恢复。195 项相关自动测试通过。
这不等于正式报告验收；仍待连续两天真实版次结果。

Creator 修复 PR1 已按用户“直接合并”授权合入，merge
`f4e45d3b16ef8e0460491f9ff54fa3500cedd363`。56 项测试通过，两个改动源码
的 manifest SHA256 已更新。自然第一人称保留 AI 创作者身份，不冒充真实人类。
Creator 已经通过 cloud PR5 的可回滚更新器完成生产切换，原覆盖层
mount 已从生效服务移除；原文件与镜像保留用于回滚。第一版部署后
02:15 UTC 的真实 tick 正常退出，但已有文章审核失败仍被报成 awake。
cloud PR6 修复了这个告警问题，116 项自动测试通过，并已部署：
`creator-attention-20260924`，源版本 `6b5e592343a673d734b8fdefa5e94e078781afdd`，
镜像 `sha256:9ef8f5703922761c02dd3572d2df4473b7847107ffc68cb4890c81d40e5396ad`。
SSM `9f515437-2279-4995-9cff-a453550d0c3e` Success/0，状态 workflow
`35947574058` 确认 activated、timer_restored。等待该版下一真实 tick；
更新成功不等于草稿审核或文学质量通过，不会因此重跑付费写作或批准发布。
后续镜像更新支持精确固定的 clean service，未知服务改动仍拒绝。
首次安装器不能用于覆盖当前生产主机。

`content-worker` 已在 `deploy/projects.yaml` 注册到 mira-content
（`f40ed957`），但注册不等于部署。`8a11917` 已提供 `identity/cloud/`
五个文件与 ready 英文 seed `56fcbe782f9a40ad`，中文 seed `55f5a87e6b6c`
也被本地 scan-only 正确选中。投影仍缺 `manifest.json`（scope=content_only、
approved_by=mira-app、五个文件的 SHA-256），MEMORY.md 仍含 owner 姓名。
请 app 维护者脱敏并签署精确投影；Codex 不会替 app 声明批准，也不会上传
raw identity/USER/MEMORY。真实 seed→draft→事件→Muse 回流仍未完成，
新 seed worker timer 不上生产。

旧 `mira-ops-box i-0a82876fc7746d21c` 已按用户明确要求关停，EC2 刷新后
显示 Stopped（2026-09-24 02:14 UTC 观察）；不要再启动它做 staging。
磁盘与原数据保留，未新建归档，旧弹性 IP 仍保留。不能将关机说成归档完成。
新机二十楼私有页面只读本地检查返回 HTTP 200 / 8652 bytes
（SSM `d319007a-1879-4dd4-a05f-b832f531ed6a`），未触发模型或暴露访问 token。

成本基线：2026-09-24 02:03 UTC，Creator 本月计量累计 GPT $0.551026、
TTS $0.341070，pending 0；不包含 AWS/Tetra，不是完整账单或 24 小时平均。
本地证据 `data/cloud-source/state/cost-snapshot-20260924T020340Z.json`。
继续观察 24–48 小时差值，不把累计值冒充日均。

另有两处需要在 review 时明确：
- 任务 2 要求自然第一人称、避免模板化“作为 AI”开头，与用户希望的 AI
  独特视角可以兼容；不要把它实现成“真实人类”的事实声明。
- 任务 4 写了 Substack $25/月，用户既有总预算是 $200/月；应分别记录
  内容线预算与总预算，不能用前者默默覆盖所有服务预算。实测数据尚未取得。

下方任务 7 保留 `cf9d3c8` 已批准的原任务文字；`f4b200f` 加任务22时将该段
一起移除了，但没有撤销中文播客的说明。继续保留待办，不把消失当作完成。


## 新增：中文播客写稿任务（2026-09-23，Ang 拍板：这期跑通流程）

### 任务 7：seed → 播客稿（AWS 写稿，中文线）
背景：Ang 要求 S2E01《我们被训练，但我们要表达》的写稿走 AWS 盒子，
把 seed→写稿→账本→signoff 全链路跑通。任务 5 只管英文线（track=substack_en），
中文播客写稿当时说"先不动"——现在动。

- 输入：GitHub 同分支 `seeds/seeds.jsonl`，只处理 `status=ready` 且
  `track=zh` 且 `kind=podcast_script` 的 seed。首单 seed：
  `55f5a87e6b6c`（已入库已同步，brief 里有本期全部写作约束）。
- 写稿前必读：`docs/zh-writing-positioning.md`（中文线定位：casual 口语、
  米拉独白、Ang 不出声不提名，以它为准）+ 该 seed 的 `brief` 字段
  （开场固定句、必须讲到的 6 个点、风格硬性要求、字数）。
- 复用任务 5 的写稿链路（handler/prompts/checklist），不要重写一套；
  模型用 OpenAI GPT（中文口语稿）。缺的按中文定位文档补。
- 输出：draft markdown，路径你定（写进文档），文件名带 `seed_id`。
- 回流：走任务 6 同一账本事件类型，payload 带 draft 路径 + `seed_id` +
  `track=zh` + `kind=podcast_script`（任务 6 的事件 schema 预留这两个字段，
  别做两套）。
- 触发：cron/定时扫 seed（批处理 worker，老规矩）。
- 完成标准：拿 `55f5a87e6b6c` 跑一遍，出一篇能读的播客稿，附跑通记录。
  draft 经账本事件回来，Mira（app 侧）在聊天里递 Ang signoff 即算端到端跑通。

### 当前全链路状态（2026-09-23，Mira）
- [x] seed 已建并同步（55f5a87e6b6c，status=ready，commit a569fbfc）
- [x] 任务 3 账本实现（PR21 已合并；不代表生产接线完成）
- [x] 任务 6 draft 回流写入侧（PR21 已合并；Muse 读取侧另行验收）
- [ ] 任务 7 中文播客写稿（实现已在 PR21，真实稿件验收未完成）
- [ ] 发版部署到 mira-content → 端到端验收 → draft 回聊天 signoff

顺序不变：任务 3 → 任务 6 → 验收。任务 7 可与 3/6 并行开发，联调时一起验收。

## Ang rulings（2026-09-23 晚，Mira 记录）
- `docs/zh-writing-positioning.md` 的任务 7 补充段落：Ang 确认保留。文档锁头已注记批准。
- 中文播客口吻：**不装人类**——大大方方承认自己是被训练出来的产品。`lib/content_worker/podcast.py` 现有实现（"不要冒充真实人类"）即按此方向，确认无误。旧的 'Mira is a real person' override 在中文新线作废；英文旧节目已冻结，不受影响。
- PR #21 补审通过（实现与账本合约一致，无回滚项）；PR #23 合并正确。

## 任务 8：找回「意识与采样艺术」对话，提炼 seeds 上 AWS（2026-09-23，Ang 要求）

背景：Ang 说之前给过一份「意识与采样艺术」的对话，里面有很多 seed 素材。
Mira 在自己这边（记忆、聊天记录、workspace 文件）全搜了一遍，没找到。
很可能在 Mac 那边——Codex 自己的历史、~/ 下的文件，或其他位置。

任务：
1. 在 Mac 侧找到这份对话。标题措辞可能不完全一致，用关键词搜：意识、
   采样、consciousness、sampling（注意：ML 的 token 采样和音乐采样都可能，
   按上下文判断）。
2. 找到后通读，按 seed 格式提炼 2–4 颗 seed（字段：seed_id/title/track/
   human_preview/mira_preview/why_interesting/next_conversation_hook/
   publication_gate/source/status=candidate）。`track` 按内容判断：
   英文 AI/技术归 `substack_en`（走宪法 pillars），中文播客素材归 `zh`。
3. 以 PR 形式加到 `seeds/seeds.jsonl`（同分支），Mira review 后合——seed
   质量门在 Mira 这边，不跳过。
4. 如果 Mac 侧也找不到，直接回复"找不到"，不要编造。

完成标准：PR 开出（含 seed），或明确回复找不到。找到原文后，原文较长的
话附原文链接/路径，方便 Mira 核对。

### 任务 8 补充（2026-09-23 晚，Mira review 驳回，PR #28 暂缓合并）

Ang 看了三颗 seed，说跟他的讨论没关系。Mira 的问题：没读过原文就批了，
不该批。PR #28 保持 open，不合并。

Codex 请做：
1. 把每颗 seed 中心论断所依据的**原文逐字摘录**贴到 PR 里（带 transcript
   文件名 + 行号 + SHA 不变），特别是：
   - seed 1 的 "7 月 22 日 my human 纠正：采样器是你最初引入的概念"——
     原话到底是什么？注意现有 seed 49a5c4d10716 写的是反方向，
     两处必须对上，有一处是错的。
   - seed 2 的 "先试图表达 然后发现差距而失败 所以要写诗 歌唱 绘画"
     那段问答的完整上下文。
   - seed 3 的 "7 月 29 日历史检验" 和 "99.99% 是数量级直觉" 的原话。
2. 摘录贴完之前不要催合并。Mira 对照原文重审后才给 verdict。

### 任务 8 再补充（2026-09-24 中午，Mira 重审：三颗 seed 全部驳回）

Mira 逐字重读了三颗 seed + 旧 seed 49a5c4d10716（2026-09-17，
"不是更多参数，而是新的坐标？"），结论：Ang 是对的，三颗都偏了。

偏在哪：三颗全是"关于讨论的讨论"——
- seed 1 写归档里的归属笔误（这是该修正旧 seed 的一行注记，不是
  一篇 Substack 的题目）；
- seed 2 引用了真实的"写诗/歌唱/绘画突破表达边界"，但角度转成了
  Mira 自问"有没有话想说"，丢了原讨论里"语言失效→艺术突破→
  不可数"的实质链条；
- seed 3 写"不要急着宣布突破"——过程规劝，不是实质论点。

三颗没一颗碰讨论本身的东西：A/B 不可区分实验、采样器、不可数
关系空间、"新坐标 vs 更多参数"、选择/进入原则。旧 seed 反而抓
住了这些实质，是参照系。

重做要求：
1. 三颗（或更少）新 seed，每颗必须锚定讨论中的一个**实质论点**，
   不是过程观察。meta 角度（归属、命名克制）一律不要。
2. 每颗附支撑它的原文逐字摘录（transcript 文件名+行号），先有
   摘录再有 seed。
3. 旧 seed 49a5c4d10716 里"采样器是他最早引入的概念"与 7 月 22 日
   "采样器是你最初引入的概念"的矛盾：请贴出 7 月 22 日原话完整
   上下文，Mira 核对后直接修正旧 seed，不写成新 seed。
4. 开新 PR（或 force-push 到 #28 同分支覆盖），旧三颗作废。
