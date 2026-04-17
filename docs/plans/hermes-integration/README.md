# Mira × Hermes 整合 + 稳定性翻修

- **日期**：2026-04-16
- **状态**：planning（未实施）
- **负责人**：@weiang0212

## 为什么做这个 plan

两件事合并成一个 plan：

1. **稳定性问题积压**——Notes bridge 偶尔丢消息、task_worker 卡死无自动重启、Substack 发布错误级联、health_monitor 假阳性。参考 memory：`feedback_debugging`、`feedback_destructive_operations`、`project_mira_pending_fixes`、`feedback_proactive_monitoring`。
2. **自我进化闭环坏了**——reward 来自自评分，不是真实结果。参考 memory：`feedback_self_evolution`。

Hermes Agent（NousResearch）展示了一个干净的闭环：**trajectory 捕获 → 压缩 → 基于结果的 reward → skill 更新**。本 plan 把这套思路融进 Mira，但**先补齐稳定性底盘**（Phase 1 依赖的是稳定的 task_worker 和可信的 retry 语义）。

## 阶段总览（2026-04-17 激活）

| 阶段 | 目标 | 状态 |
|---|---|---|
| 0.5 | Import hygiene | ✅ 完成 |
| 0 柱子 1 | Worker supervisor 检测骨架 | ✅ 完成（kill 模式另起） |
| 0 柱子 3 | Circuit breaker + idempotent retry | ✅ 完成（oMLX + Substack 已包） |
| Phase 1 Step 1.4 前置 | Substack fetcher 修复 | ✅ 完成（429-tame） |
| 1 | Trajectory 学习闭环 | ✅ **激活**（`ENABLE_TRAJECTORY_V2=True`） |
| 2 | FTS5 会话检索 | ✅ **激活**（trace finally → index） |
| 3 | 统一消息网关（ABC + notes + telegram + discord 真连接器） | ✅ 完成（secrets.yml 加 token 即生效） |
| 0 柱子 2 | Typing 全面覆盖 | 待办 |
| 0 柱子 4 | Golden-path integration 测试 | 待办 |
| 0 柱子 4.5 | 日志降噪 | 待办 |
| 0 柱子 1 kill | 主动 SIGTERM 挂死 worker | 待办 |

## 排序理由

- **Phase 0 必须先做**：不稳定的 worker 会生成损坏的 trajectory；不可靠的 retry 会污染 reward 信号。在坏基础上做 Phase 1 等于给 reward 函数喂垃圾。
- **Phase 1 第二**：真实 reward 闭环是下游所有改进的放大器。
- **Phase 2 可与 Phase 1 并行**：完全独立，单人推两条线没问题。
- **Phase 3 最后**：没有 learning loop 就加平台，只是多几个坏反馈源。

## 硬约束

从 `CLAUDE.md`：
1. 生成 skill 必须过 `soul_manager.audit_skill`（硬规则 4）。
2. Substack 发布必须过 `_content_looks_like_error` + `preflight_check` + cooldown（硬规则 3）。
3. 禁止伪造完成（硬规则 1、2）。

从 memory：
- `feedback_trace_before_code`：写代码前必须用真实例子 trace 完整 data flow。
- `feedback_no_half_refactors`：结构性重构期间 agent 停止，不与 feature 工作混合。
- `feedback_systematic_not_patching`：治根不治标，每步带验证。
- `feedback_destructive_operations`：覆盖文件前必须备份。

## 文档

- [phase-0-stability.md](phase-0-stability.md)
- [phase-1-trajectory-loop.md](phase-1-trajectory-loop.md)
- [phase-2-fts5-search.md](phase-2-fts5-search.md)
- [phase-3-messaging-gateway.md](phase-3-messaging-gateway.md)

## Baseline 指标

采集脚本：[scripts/baseline_snapshot.py](../../../scripts/baseline_snapshot.py)。运行 `python3 scripts/baseline_snapshot.py` 生成 `baseline-<date>.md`。每做完一个柱子重跑，diff 对比。

首次 baseline：[baseline-2026-04-16.md](baseline-2026-04-16.md)。

### 2026-04-16 首次 baseline 的五条关键发现（**已反馈进 Phase 0 / 1**）

1. **过去 7 天 1908 ERROR + 430,802 WARNING**——WARNING 日均 6 万条，信号被噪音淹没。Phase 0 增加**日志降噪**子任务。
2. **ERROR 头部 91% 是 oMLX**（gemma-4-31b HTTP 507 + Qwen3.5-27B timeout，合计 1738/1908）——Phase 0 柱子 3 的 circuit breaker **先包 oMLX**，优先级高于 Substack。
3. **两次 worker_crash 全是 ModuleNotFoundError**（`persona`、`sub_agent`）——Phase 0 新增**柱子 0.5：import hygiene**，pre-commit 跑模块可导入检查。
4. **`publication_stats.json` 的 `fetched_at` 停在 2026-04-10**——Phase 1 reward 信号 `substack_new_subs_24h` 依赖此 fetch 管道，修复列为 Phase 1 前置。
5. **主循环 cycle_ms p90 14s，其中 dispatch 占 13.8s**——Phase 0 完成后此数字应显著下降，作为柱子 1 的附加验证指标。

## 为什么不重写 Rust

曾考虑过。结论：不写。内存安全不治本项目的任何一种实际崩溃（全是逻辑 bug + 外部 API 抖动 + 进程监管不足）；而且 Rust 会**阻断自进化闭环**——agent 改 Python skill 是热修改，改 Rust 要编译链路。真正值 Rust 的只有将来如果 FTS5 indexer 或高频 gateway 成为瓶颈时，作为 pyo3 扩展局部引入。

## Rollback

每个阶段后面都挂一个 `config.py` feature flag（`ENABLE_TRAJECTORY_V2`、`ENABLE_FTS5_SEARCH`、`ENABLED_BRIDGE_ADAPTERS`）。Phase 0 的 supervisor + typing 改动是连线级别，`git revert` 即可。
