# Phase 1 — Trajectory 学习闭环

**目标**：把 reward 从"自评分"换成"trajectory 导出的真实结果信号"。对齐 Hermes 的 `batch_runner.py` + `trajectory_compressor.py` 模式。

**依赖**：Phase 0（需要稳定的 task_worker、类型化 `TrajectoryRecord`、幂等 retry、`crashes.jsonl`）。

**预估**：1-2 天专注 session。

## 现状（来自代码探测）

- [Mira/lib/evolution/experience.py](Mira/lib/evolution/experience.py) 已有 `record_experience` / `load_experiences` / `record_task_outcome` / `get_relevant_experiences`。
- `lessons.py` / `rewards.py` / `strategy.py` / `config.py`（`EXPERIENCE_DIR`, `REWARD_WEIGHTS`）都在。
- Experience schema：`{action, outcome, reward, context, agent, task_id}` → `data/evolution/<date>.jsonl`。
- **问题**：reward 是从 task status（done/failed/timeout）硬编码映射，不是真实结果；完整对话轨迹**没有持久化**，reflect 阶段没有原始数据可学。

## Hermes 模式参考

- `batch_runner.py` 抓：`conversations, tool_stats, api_calls, completed, partial, toolsets_used`。
- `trajectory_compressor.py`：保留首条 system/human/assistant/tool + 末 4 turn，中间 LLM 压缩，前缀 `[CONTEXT SUMMARY]:`，用 Gemini Flash。
- 输出：`trajectories.jsonl`（authoritative，去重）+ `statistics.json`（tool stats 聚合）+ `checkpoint.json`。

## 升级步骤

### Step 1.1 — 完整 trajectory 捕获

扩展 [Mira/agents/super/task_worker.py](Mira/agents/super/task_worker.py)，任务结束时在 workspace 下写 `trajectory.jsonl`，schema 为 Phase 0 柱子 2 定义的 `TrajectoryRecord`：

```python
TrajectoryRecord:
  task_id: str
  agent: str                      # writer / explorer / socialmedia / ...
  timestamp: datetime
  prompt_index: int
  conversations: list[Turn]       # {role, content, tool_calls?, tool_response?}
  tool_stats: dict[str, ToolStat] # {name: {count, success, failure}}
  api_calls: int
  completed: bool
  partial: bool
  crashed: bool                   # 来自 Phase 0 supervisor
  model: str
```

实现点：包裹 `claude_act` 调用链，拦截 tool call + tool response 两端记录。Turn 级别写入。

### Step 1.2 — Trajectory 压缩器

新建 `lib/evolution/trajectory_compressor.py`：

- 输入：单个 `trajectory.jsonl` 路径。
- 保留区：首条 system / 首条 human / 首条 assistant / 首条 tool + 末 4 turn。
- 中间区：LLM 压缩为一条 `{role: "system", content: "[CONTEXT SUMMARY]: ..."}`。
- 目标压缩比：60-80%（按 token 估算，不按 turn 数）。
- **模型**：Gemini Flash（你现有配额，见 `project_podcast_pipeline.md`），**不用 Opus**（`feedback_model_choice_quota.md`）。
- 输出：压缩后 record。

### Step 1.3 — 扩展 `record_task_outcome`

改 [Mira/lib/evolution/experience.py](Mira/lib/evolution/experience.py)`::record_task_outcome`（以及 [Mira/agents/super/task_manager.py](Mira/agents/super/task_manager.py)`::_collect_result` 的调用点）：

1. 调 `trajectory_compressor.compress(task_workspace / "trajectory.jsonl")`。
2. 压缩结果 append 到 `data/evolution/trajectories.jsonl`（全局，去重 by `task_id`）。
3. 增量更新 `data/evolution/tool_stats.json`（per-tool count / success / failure + 滑窗）。
4. 原 `experience.jsonl` 写入保留，向后兼容。

### Step 1.4 — 改写 `rewards.py`（Phase 1 核心）

抛弃自评分，reward 信号来自**可验证来源**：

| 信号 | 来源 | 初始权重 |
|---|---|---|
| `tool_success_rate` | `tool_stats.json` 本次 delta | 0.25 |
| `outcome_verified` | 已有字段（文件/URL 真落地了没） | 0.30 |
| `substack_new_subs_24h` | Substack analytics / RSS | 0.20（仅 content 类任务） |
| `notes_user_reply` | bridge items tagged `reader_feedback` | 0.15 |
| `time_cost_penalty` | `data/logs/timing.jsonl` | -0.10 |
| `crash_penalty` | Phase 0 产出的 `crashes.jsonl` | -0.50 |

权重调在 `lib/evolution/config.py::REWARD_WEIGHTS`，不硬编码。计算逻辑单测覆盖。

> **Phase 1 前置（baseline 发现）**：`data/social/publication_stats.json` 的 `fetched_at` 在 2026-04-16 baseline 时停在 2026-04-10（6 天前）。`substack_new_subs_24h` 信号完全依赖这份 stats 是最新的；Phase 1 开工前必须先查明 Substack fetcher 为何停摆并修复（可能是 HTTP 429 限流或 cookie 过期——baseline 日志里有 9 条 `Failed to fetch posts: HTTP Error 429`）。未修复前该信号权重应置 0，避免喂入陈旧数据。

### Step 1.5 — 改写 weekly reflect

core.py 的 reflection phase 当前读 `experience.py::load_experiences`。改为：

1. 读 `trajectories.jsonl`（过去 7 天）。
2. 读 `tool_stats.json` 当前 vs 7 天前的 delta。
3. 读 `rewards.py` 产出的 reward 分布（当前 vs baseline）。
4. Prompt 要求输出一个 **diff**：
   - 新 skill 创建（必过 `soul_manager.audit_skill`——CLAUDE.md 硬规则 4）。
   - 现有 skill 更新（同样 audit）。
   - Prompt / config 调整建议（写入 `data/evolution/proposed_changes.jsonl`）。
5. 影响 publish flow 的 diff**必须**人工 review（写入 inbox 给你），其它按 `feedback_full_autonomy.md` 自动应用。

### Step 1.6 — 测试

`tests/evolution/test_trajectory_pipeline.py`：

- mock 一个 task_worker 跑，含 3 次 tool call（2 成功 1 失败）→ 断言 `trajectory.jsonl` 格式正确、`tool_stats` 计数对。
- 20-turn trajectory → 压缩后 ≤ 8 turns，首尾保留区完整，中间是 `[CONTEXT SUMMARY]:` 前缀。
- 多条 mock trajectory → 跑 `rewards.compute()`，每个信号权重应用正确，边界值（全 crash / 全成功）reward 在 [-1, 1]。
- mock trajectories + tool_stats 喂给 reflect → 断言产出的 skill diff 结构符合预期且能过 audit。

## 成功标准

- `data/evolution/trajectories.jsonl` 连续 3 天 ≥ 10 records/天。
- `tool_stats.json` 显示每工具滑动均值。
- 下一次 weekly reflect 产出 ≥ 1 个 skill update，且可追溯到具体 trajectory IDs（provenance 字段）。
- reward 分布**不再聚在自评分基线**，跨越 [-1, 1] 区间。
- Substack publish 相关 skill 更新 100% 经过人工 review 后才应用。

## Rollback

`config.ENABLE_TRAJECTORY_V2 = False`：
- 关 → `record_task_outcome` 跳过 compressor 调用，走 legacy 路径。
- 老 `experience.jsonl` 格式永不删除，新 `trajectories.jsonl` 是**叠加**，不是替换。
- reflect 可同时有两个模式，通过 flag 选择读哪一套。

## 与 Phase 0 的契约

- `TrajectoryRecord` schema 必须在 Phase 0 柱子 2 里先定义并冻结。
- `crash_penalty` 信号依赖 Phase 0 柱子 1 产出的 `crashes.jsonl`。
- `tool_stats` 的准确性依赖 Phase 0 柱子 3 的 idempotent retry——否则同一 tool call 重放会重复计数。

Phase 0 未完成前，Phase 1 **不得**合并。
