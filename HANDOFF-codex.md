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
