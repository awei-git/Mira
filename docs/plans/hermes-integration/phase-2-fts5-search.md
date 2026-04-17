# Phase 2 — FTS5 会话全文检索

**目标**：跨 session 全文召回 Mira 自己的历史对话。

**依赖**：无。可与 Phase 1 并行（不同模块，不同测试）。

**预估**：半天。

## 当前进度（2026-04-16 scaffold 完成）

| 模块 | 路径 | 状态 |
|---|---|---|
| FTS5 虚拟表 + 连接管理 | `lib/memory/session_index.py`（`DB_FILE = data/soul/session_index.db`，`unicode61 remove_diacritics 0` tokenizer，中文友好） | ✅ |
| 写入 API | `index_trajectory(TrajectoryRecord)` —— 逐 turn insert，`tool_result_preview` 合并入文，空内容跳过 | ✅ |
| 查询 API | `search(query, k=5, agent=None, since=None)` —— FTS5 prefix match + BM25 rank，支持 agent / 时间窗过滤 | ✅ |
| 查询清洗 | `_normalize_query` 去除 `" * ( )`，保留 CJK + 字母数字，每 token 自动加 `*` 前缀（提高召回） | ✅ |
| Retention | `prune_older_than(days=90)` —— 按 ts < cutoff 删 | ✅ |
| Soul prompt 注入辅助 | `format_soul_recall(query, k=3, max_chars_per_snippet=200)` —— 产出 markdown block，空命中返回空串 | ✅ |
| 健壮性 | 所有操作 soft-fail（log + 返回默认值），不进 critical path | ✅ |
| 单测 | `tests/memory/test_session_index.py`（9 tests green，覆盖 index/search/filter/prune/sanitize/empty） | ✅ |

### Step 2.2 — ✅ 完成

[lib/evolution/trace.py](Mira/lib/evolution/trace.py) 的 flag-on `finally` 段追加了 `memory.session_index.index_trajectory(record)`——用**未压缩**的原始 record 入索引，这样 phrase recall 不会被 `[CONTEXT SUMMARY]` 吞掉细节。

### Step 2.3 — ✅ 完成

[lib/memory/soul.py::format_soul](Mira/lib/memory/soul.py) 新增 `context_query: str | None = None` 关键字参数：

- 不传：行为完全不变（向后兼容）。
- 传了：调 `format_soul_recall(query, k=3, max_chars_per_snippet=200)`，命中就追加 `## Relevant past conversations` block。没命中或索引缺失——silent skip。

调用方按需 opt-in，比如 `format_soul(soul, context_query=user_prompt)`。

## 现状

- Journal 是"每日摘要"，不是原始对话。
- PostgreSQL `episodic_memory` 表有向量检索，但：
  - 语义检索 ≠ 精确短语召回。
  - 粒度是 journal-level，不是 turn-level。

## 设计

SQLite FTS5（与 PostgreSQL 并存，职能不重叠；目的单一，库小）。

`lib/memory/session_index.py`：

- DB：`data/memory/session_index.db`，FTS5 virtual table。
- Schema：`task_id, agent, ts, role (human/assistant/tool), text`。
- API：
  - `index_trajectory(TrajectoryRecord)` —— 把 trajectory 的 conversations 逐 turn 入库。
  - `search(query: str, k: int = 5, agent: str | None = None, since: datetime | None = None) -> list[Snippet]`。
- 保留期：90 天，每日凌晨 prune 一次。
- 与 Phase 1 的 TrajectoryRecord 复用，不搞第二套 schema。

## 步骤

### Step 2.1 — 模块

写 `lib/memory/session_index.py`，单文件 < 200 行。schema 复用 Phase 0 柱子 2 的 `TrajectoryRecord`。

### Step 2.2 — task_worker 收尾时 index

[Mira/agents/super/task_worker.py](Mira/agents/super/task_worker.py)`::main` 的 finally 段：

```python
try:
    session_index.index_trajectory(trajectory)
except Exception as e:
    log.warning("session_index failed (non-critical): %s", e)
```

**不抛**，不进关键路径——索引失败不影响 task result。

### Step 2.3 — 注入 soul context

[Mira/lib/memory/soul.py](Mira/lib/memory/soul.py)`::format_soul` 里，当前 task 有 prompt 时：

1. 调 `session_index.search(prompt, k=3, since=now-30d)`。
2. Snippet 注入在 `## Relevant past conversations` header 下。
3. 总 token 预算不超过 soul prompt 现有上限的 10%（避免挤掉已有 context）。

### Step 2.4 — 测试

`tests/memory/test_session_index.py`：

- 索引 50 条 mock trajectory → `search` 返回 FTS ranked 结果。
- 保留期：塞入老数据 + 新数据 → 跑 prune → 老数据消失。
- Soul 注入：mock prompt → 断言 snippet 进了 formatted soul，且 token 预算没超。
- 失败容忍：mock sqlite 抛异常 → 断言 task_worker 主流程不中断。

## 成功标准

- `session_index.db` 的 row 数 ≥ 过去 3 天任务数 × 平均 turn 数。
- 人工抽查：问 Mira 一个上周聊过的具体内容 → 能召回具体 snippet（不是 hallucinate）。
- task_worker 的 timing 指标无明显退化（index_trajectory < 50ms p95）。

## Rollback

- 删 `session_index.db`，移掉 `index_trajectory` 调用，移掉 soul 注入段。
- 纯加法改动，核心路径零影响。
- 或：`config.ENABLE_FTS5_SEARCH = False` 跳过索引与查询两端。

## 与 Phase 1 的关系

- 技术上独立，可并行开发。
- 若 Phase 1 的 `TrajectoryRecord` schema 在变，Phase 2 应等 schema 冻结再写 index。
- 若时间紧，Phase 2 可在 Phase 1 之前做（schema 用 Phase 0 柱子 2 定义的即可），提早拿到调试用的会话检索能力。
