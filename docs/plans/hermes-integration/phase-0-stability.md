# Phase 0 — 稳定性底盘

**目标**：先止血。针对 memory 里记录的四类不稳定根因，立四根柱子。

**预估**：2-3 天专注 session。

## 当前不稳定的根因映射（2026-04-16 baseline 已校准）

| 症状 | 根因 | 对应柱子 | baseline 数字 |
|---|---|---|---|
| task_worker 卡死或静默 crash | 无 per-worker supervisor | 1. Supervisor | 7d: 2 次 worker_crash |
| 两次 crash 都是 `ModuleNotFoundError`（`persona`、`sub_agent`） | 导入未校验 | **0.5. Import hygiene** | 对应 42 条 `store_thought() failed` |
| super↔sub↔bridge 的 schema 漂移 | dict 传递无类型 | 2. Typing | ad hoc |
| oMLX/Substack/LLM 抖动级联 | 无 circuit breaker，retry 不幂等 | 3. Circuit breaker | 7d: 1738 条 oMLX 错占 ERROR 91% |
| 日志噪音淹没信号 | 未分级、过度 WARN | **4.5. Log hygiene** | 7d: 430,802 WARNING |
| 逻辑 bug 落 main | 关键路径无 E2E 测试 | 4. Golden tests | — |

---

## 柱子 1 — Per-worker supervisor

### 问题
`task_manager.py::_kill_task` 只能硬 kill，没有机制在 worker crash 时自动重启。`_reap_stale_pids` 每小时跑一次，crash 到被 reap 之间任务状态是错的。

### 设计
新增 `Mira/lib/supervisor/worker_supervisor.py`：

- 每个 worker 进程启动时起一个守护线程，每 10s 写 `data/workers/<task_id>/heartbeat`（原子写 + unix ts）。
- Supervisor 与 core.py 同生命周期，扫描 heartbeat。
- Heartbeat 过期 > 60s → SIGTERM，等 5s，还不死 → SIGKILL。
- 检测到 crash → 写 `data/evolution/crashes.jsonl`（schema：`task_id, agent, exit_code, last_heartbeat_ts, trace_tail`），按 retry budget（默认 2，per-agent 可覆盖）决定是否重启。
- 每 worker 通过 `resource.setrlimit(RLIMIT_AS)` 加内存上限，防 OOM 拖垮宿主。

### 步骤
1. 写 `lib/supervisor/worker_supervisor.py`。
2. 改 [Mira/agents/super/task_worker.py](Mira/agents/super/task_worker.py)`::main` 开头启动 heartbeat 守护线程。
3. 改 [Mira/agents/super/task_manager.py](Mira/agents/super/task_manager.py)`::dispatch` 向 supervisor 注册新 worker PID。
4. core.py 在现有 phases 之前启动 supervisor。
5. `crashes.jsonl` 加入 baseline 指标，Phase 1 reward 函数将直接消费。

### 测试
`tests/integration/test_worker_supervisor.py`：
- 向 worker 发 SIGSEGV → 断言 supervisor 60s 内检测到并重启，任务最终成功。
- worker 死循环 → 同上。
- retry budget 耗尽 → 断言状态 `crashed-final`，无无限重启。

### 验证 & 回滚
- dry-run 模式：supervisor 只记日志不 kill，跑 24h，人工核对 hang 是否被检测到。
- 回滚：`config.SUPERVISOR_ENABLED = False`，dispatch 不注册，worker 照旧跑。

---

## 柱子 0.5 — Import hygiene（baseline 新增）

### 问题
Baseline 7 天 2 次 worker crash 全是 `ModuleNotFoundError`（`persona`、`sub_agent`），另 42 条 `store_thought() failed: No module named 'sub_agent'`。改 shared 模块时改一边漏一边，运行时才炸。

### 设计
两层防线：

1. **pre-commit 静态检查**：`scripts/check_imports.py` 对所有入口点（`agents/super/core.py`、`agents/super/task_worker.py`、`lib/memory/*`）跑 `python -c 'import <module>'` 与 AST 级 import 解析。发现引用不存在的模块则 fail。
2. **运行时 early check**：core.py 启动时对当前 agent registry 里的每个 handler 模块做一次 `importlib.import_module` 预加载，任何失败即刻告警（写 `data/logs/import_failures.jsonl`），不上线。

### 步骤
1. 写 `scripts/check_imports.py`——遍历 `agents/` + `lib/` 的 `.py`，解析所有 `import` / `from ... import`，对内部模块（非 stdlib/第三方）做实际 import，失败列表到 stdout。
2. 接入 pre-commit：新增 `.pre-commit-config.yaml` hook。
3. core.py 在 supervisor 启动前跑一次 early check，失败告警至 bridge inbox。
4. 现有已知 broken imports（`persona`、`sub_agent`）先清理——要么恢复模块，要么删引用。

### 测试
- `tests/scripts/test_check_imports.py`：构造含坏 import 的 fixture package，脚本应 return 非零、输出坏模块名。
- 运行时：手工删一个引用的模块 → 重启 core → 断言 import_failures.jsonl 写入、supervisor 不启动。

### 验证 & 回滚
- 跑一次，把当前所有坏 import 清零再合并。
- pre-commit hook 可临时禁用（`SKIP=check-imports git commit`）但 CI 不放行。

---

## 柱子 2 — 类型化边界接口

### 问题
[Mira/lib/bridge.py](Mira/lib/bridge.py) 的 items、`TaskRequest`、`TaskResult`、`AgentState` 全是 dict。一边改字段另一边不报错，运行时才炸。

### 设计
Pydantic v2 模型，只覆盖**四个边界**，不动内部：

- `BridgeItem`（`bridge_dir/users/<uid>/items/` 里的条目）
- `TaskRequest`（super → worker 的 dispatch payload）
- `TaskResult`（worker → super 写 `result.json`）
- `AgentState`（state.py 的 serialize 部分）
- `TrajectoryRecord`（Phase 1 要用，现在先定义）

`mypy --strict` 只打开这四个模块，增量推。

### 步骤
1. 新建 `lib/schemas/`，每个边界一个文件。
2. 替换四个边界点的 dict 序列化：
   - `bridge.py::create_item` / `get_item`
   - `task_manager.py::dispatch` → `task_worker.py::main`
   - `task_worker.py` 写 `result.json`
   - `state.py::load_session_context` / `save_session_context`
3. `pyproject.toml` 加 mypy config，strict 目标四个 package 下。
4. pre-commit 跑 mypy on staged files。

### 测试
- 喂坏 BridgeItem → 断言 `ValidationError` 带字段级错误，不是 KeyError。
- 每个 schema 的 roundtrip：serialize → deserialize → 等价。

### 验证 & 回滚
- 每改一个边界点，跑完整 integration 测试（柱子 4 产出的）后才进下一个。
- 回滚：pydantic 模型保留但 `.dict()` / `.model_dump()` 让调用方依然拿 dict；不用就不生效。

---

## 柱子 3 — Circuit breaker + idempotent retry

### 问题
Substack、Anthropic、Gemini、MiniMax、HuggingFace、arxiv feed 都有瞬时失败。当前 retry 朴素，Substack 504 能级联成连续重试风暴（历史上导致过重复 publish 风险）。

### 设计

**`lib/net/circuit_breaker.py`**：
- 状态：CLOSED / OPEN / HALF_OPEN。
- per-provider 5min 滑窗错误率。
- 达 50% 且样本 ≥ 10 → OPEN，5min cooldown。
- HALF_OPEN 放单个试探请求。
- `@circuit(provider="substack")` 装饰器。

**`lib/net/idempotent.py`**：
- 所有外部写调用接 `idempotency_key`（建议 `task_id + stage`）。
- SQLite `data/net/idempotency.db` 存 key → response，TTL 7 天。
- 同 key 重放 → 返回 cached 结果，不发真请求。

### 步骤
1. 实现 `circuit_breaker.py` + 单测。
2. 包裹**优先级按 baseline ERROR 体量排序**：
   - (1) **oMLX**——baseline 占 ERROR 91%（1738/1908），先装这个收益最大。
   - (2) Substack（publish/note/comment/fetch——baseline 显示 publication_stats 6 天没更新，fetch 也要包）。
   - (3) 其它 LLM provider（Anthropic / Gemini / MiniMax）在 `lib/llm/*.py` 调用点。
   - (4) Podcast TTS 在 `lib/podcast/tts/*.py`。
3. 实现 `idempotent.py`。
4. 给 Substack publish、Notes 创建、RSS update、podcast 上传都塞 idempotency_key。
5. breaker 状态暴露给 health_monitor，dashboard 可见。

### 测试
- Breaker：10 次失败 → OPEN → cooldown → HALF_OPEN 放行 → 成功后 CLOSED。
- Idempotent：同 key 连调两次 → 真正 HTTP 调用只 1 次，两次返回一致。
- 与柱子 1 联动：breaker OPEN 期间 worker crash → 重启后不应重复发布（idempotency 兜底）。

### 验证 & 回滚
- 灰度：先 Substack 一个 provider 上 breaker + idempotent，跑 3 天观察。
- 回滚：每个 provider 单独 flag（`CIRCUIT_SUBSTACK_ENABLED` 等）。

---

## 柱子 4 — Golden-path 集成测试

### 问题
关键路径（writing pipeline / publish flow / notes bridge / podcast pipeline）没有 E2E 测试，bug 是靠上线后观察发现的。

### 设计
`tests/integration/`：

- `test_writing_pipeline.py`——完整链：notes 灵感 → plan → write → review → publish（外部 mock）。断言所有 artifact 落盘、state 转移正确。
- `test_publish_flow.py`——专攻 CLAUDE.md 硬规则 3：`_content_looks_like_error` + `preflight_check` + cooldown 三层防呆是否真拦得住坏内容。
- `test_notes_bridge.py`——inbox 条目 → dispatch → outbox 回复 roundtrip。
- `test_podcast_pipeline.py`——TTS（mock）→ RSS 更新 → github push（mock）。
- `test_worker_lifecycle.py`——柱子 1 的测试，在此汇总。

pytest mark `@pytest.mark.integration`；`conftest.py` 提供 fixtures（临时 workspace、mock LLM client、mock HTTP）。

### 步骤
1. 每个测试先写骨架 + TODO，提交。
2. 填充 happy path，跑通。
3. 添加 edge case（publish 错误内容、bridge 失效、worker crash）。
4. 接 CI（如有 GitHub Actions）或 pre-commit full suite on release branches。

### 测试
测试就是测试本身。

### 验证
四个集成测试在本地与 CI 上都 green 才算 Phase 0 完成。

---

## 柱子 4.5 — 日志降噪（baseline 新增）

### 问题
Baseline 7 天 **430,802 WARNING**（日均 ~6 万条），ERROR 信号被淹没。柱子 3 装完 circuit breaker 后，oMLX 类可预期失败应**降级到 DEBUG 或一次性告警**，不是每次都 WARNING。

### 设计
- 分类所有当前 WARNING 的 top 20 来源。
- 三种处理：(a) 已知可忽略 → 改 DEBUG；(b) 真 WARNING 但重复 → 加 `@once_per_hour` 抑制；(c) 其实是 ERROR → 升级级别。
- Circuit breaker OPEN 期间，被挡住的请求只记 DEBUG，不再刷 WARNING。

### 步骤
1. 写 `scripts/log_noise_audit.py`：按 `msg[:80]` 聚合 WARNING，输出排名。
2. 人工审阅 top 20，决定每条降级方式，产出 `docs/plans/hermes-integration/log-level-decisions.md`。
3. 一次性批量改代码里对应 `logger.warning(...)` 调用。
4. 添加 `lib/logging/throttle.py::once_per` 装饰器。

### 测试
- `tests/logging/test_throttle.py`：相同消息 1s 内多次 → 只记一次。

### 验证
- 重跑 baseline 脚本，WARNING 日均应降到 < 5000（压一个数量级）。
- ERROR 行数不应增长（只降级或抑制 WARNING，不是把 WARNING 升成 ERROR）。

---

## 总体 rollout

1. **停 agent**：`launchctl unload ~/Library/LaunchAgents/com.angwei.mira-agent.plist`。
2. **采集 baseline**（`python3 scripts/baseline_snapshot.py`）。
3. **柱子 0.5**：清理现有坏 import + pre-commit hook 接入。（最便宜，先拔刺。）
4. **柱子 1**：supervisor 先 dry-run 24h → 开 kill 模式。
5. **柱子 3**：先 oMLX（吃掉 91% 噪音），再 Substack（治 publication_stats fetch），再其它 provider。
6. **柱子 4.5**：日志降噪（柱子 3 之后做，因为 breaker 会自动解决大部分重复 WARNING）。
7. **柱子 2**：typing 一次一个边界点，每个点过柱子 4 的回归。
8. **柱子 4**：测试与其它柱子并行编写（不同文件）。
9. 所有柱子 green → 重启 agent → 观察 3 天稳定再进 Phase 1。

## 成功标准

- 连续 7 天零静默 hang（柱子 1 live 之后）。
- mypy --strict 在四个 package 下 0 error。
- breaker dashboard 可见 provider 状态；模拟一次 oMLX / Substack 宕机恢复用户无感。
- 四个 integration 测试全 green 并进 pre-commit / CI。
- 相对 `baseline-2026-04-16.md`：
  - ERROR 7d 总量下降 ≥ 80%（从 1908 降至 < 400）。
  - WARNING 7d 总量下降 ≥ 90%（从 43 万降至 < 4 万）。
  - oMLX 错误占比下降至 < 20%。
  - import_failures.jsonl 连续 7d 为空。
  - 主循环 cycle_ms p90 下降 ≥ 30%（从 14s 降至 < 10s）。
