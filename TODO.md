# Mira Architecture TODO — Production Roadmap

来源：Daily Research 2026-03-26 + 代码审计 + Production Readiness Audit
更新：2026-03-27

---

## 🔴 P0 — Production 基础 ✅ DONE

| # | 项目 | 状态 |
|---|------|------|
| 1 | **Soul 文件写锁** | ✅ `_locked_write()` + `_locked_read_modify_write()` in soul_manager.py |
| 2 | **Atomic writes** | ✅ `_atomic_write()` (tmp+fsync+rename) 全量应用 |
| 3 | **Task record 竞争** | ✅ task_manager.py `_save_status()` + `_append_history()` 加 fcntl |
| 4 | ~~Secrets 加密~~ | ✅ 已是 symlink → `.config/secrets.yml`（不在 git 中） |
| 5 | **Secret rotation** | 未做（低优先） |
| 6 | **Config validation** | ✅ `validate_config()` 启动时检查关键路径 |
| 7 | **requirements.txt** | ✅ 14 个依赖 pinned |
| 8 | **Python 版本统一** | ✅ mira-agent.sh 修复 |
| 9 | **消除 bare except** | ✅ 关键 20 处（silent pass / import guards）已修 |
| 10 | **Graceful shutdown** | ✅ SIGTERM handler + `should_shutdown()` |

---

## 🟠 P1 — 可观测性 & 可恢复性 ✅ MOSTLY DONE

| # | 项目 | 状态 |
|---|------|------|
| 11 | **核心路径测试** | ✅ 每个 agent 都有 integration test |
| 12 | **并发写入测试** | ✅ 8 workers × 50 writes，零丢失 |
| 13 | **Regression test on fix** | ✅ coder agent 有 bug detection + review tests |
| 14 | **结构化日志** | 未做 |
| 15 | **API 成本追踪** | ✅ `_COST_PER_1M` 价格表 + `cost_usd` per call + `usage_summary()` |
| 16 | **Rate limit 保护** | 未做 |
| 17 | **Pre-deploy validation** | 未做（可加 pytest 到 preflight） |
| 18 | **Rollback 机制** | ✅ crash loop → auto git stash → retry |
| 19 | **Backup restore 验证** | 未做 |

### 额外完成（不在原 TODO）
- ✅ Ollama embed 500 → retry with backoff
- ✅ Discussion handler 修复（之前不 return 结果）
- ✅ Coder agent 从零搭建（handler + 18 skills + tests）
- ✅ Explorer handler 重写（删除坏的 agentic_digest.py）
- ✅ math → researcher 重命名，所有引用更新
- ✅ Researcher 迭代深挖 pipeline（plan → research → reflect → loop → synthesize）
- ✅ `do_research()` 改用 researcher agent pipeline
- ✅ pytest.ini + 测试框架搭建
- ✅ 零 broken agents（所有 handler 可加载）

---

## 🟡 P2 — 自我评估 → 自我进化闭环

核心问题：评估系统完善（14维 × ~40子维，493次评估，EMA），但**分数从不驱动行动**。

### Self-Assessment 强化

| # | 项目 | 现状 | 要做什么 |
|---|------|------|----------|
| 20 | **Score → Action pipeline** | `scores.json` 有数据但没人读 | reflect 时读 scores，低分维度 (< 4.0 或连降 3 天) 自动生成改进 plan |
| 21 | **Weak dimension diagnosis** | 不存在 | 低分维度 LLM 分析根因 + 生成 action items（当前：`error_acknowledgment` 0.0, `reading_volume` 0.01） |
| 22 | **Assessment → Prompt tuning** | 分数不影响 prompt | 低分维度改进建议注入下周期 system prompt |
| 23 | **User feedback → Scores** | 反馈只存 memory | 👍/👎 直接更新维度分数，权重 > 自评 |
| 24 | **Comparative assessment** | 只有绝对分数 | 读外部 agent 架构 → 对比自身 → 识别 gap → 提案 |

### Per-Agent Assessment

| # | 项目 | 现状 | 要做什么 |
|---|------|------|----------|
| 25 | **Agent-level scorecards** | 全局 `scores.json` 不区分 agent | 每个 agent type 独立评分，task 完成后归属更新 |
| 26 | **Agent-specific dimensions** | 14 维度一视同仁 | writer 重 `writing.*` + `taste.*`；coder 重 `implementation.*` + `reliability.*`；explorer 重 `curiosity.*` |
| 27 | **Capability baseline** | 不存在 | 每个 agent 定期跑 benchmark task，检测能力退化 |
| 28 | **Low-score auto-response** | 差表现靠人发现 | 连续 N 次低分 → 降级 routing → 诊断 → prompt/skill 调整 → 重测 |

### Self-Evolution

| # | 项目 | 现状 | 要做什么 |
|---|------|------|----------|
| 29 | **Prompt self-mutation** | skill doc 存在，零代码 | DARWIN 循环：detect underperformance → generate variants → A/B test → keep winner |
| 30 | **Architecture introspection** | 不存在 | Agent 读自己的代码 → 分析结构 → 提出重构方案 |
| 31 | **Harness auto-update** | Skills 自学，核心代码不自改 | Code mutation pipeline：propose diff → sandbox test → human approve → apply |
| 32 | **Calibration feedback loop** | `calibration.jsonl` 有数据但从不读取 | Plan 阶段读取历史校准数据，调整估计和 agent 选择 |
| 33 | **Evolution velocity** | 只看分数趋势 | 追踪：哪些 prompt 被改了、哪些 skill 被用了、哪些 fix 生效了 |

---

## 🔵 P3 — 自我修复

| # | 项目 | 现状 | 要做什么 |
|---|------|------|----------|
| 34 | **Auto-fix 扩展** | `self_audit.py` 只修 hardcoded paths | 对 recurring errors (3+ 次同 traceback) 自动生成 patch → sandbox test → apply |
| 35 | **LLM root-cause analysis** | template-based error bucketing | 聚合相关日志 → LLM 推断根因 → 生成修复方案 |
| 36 | **Test-driven repair** | 不存在 | 发现 bug → 写 failing test → 生成 fix → test pass → commit |
| 37 | **Health monitor 误报** | 已知问题 | 减少 false positives，只对 Traceback 报警 |

---

## 🔵 P3 — RAG & Memory 系统化

| # | 项目 | 现状 | 要做什么 |
|---|------|------|----------|
| 38 | **Retrieval feedback** | pgvector 70/30 hybrid，无反馈 | 被使用的 chunk re-rank 提升，未使用的衰减 |
| 39 | **语义去重** | 不存在 | 相似度 > 0.95 的 chunk 合并或跳过 |
| 40 | **Knowledge distillation** | skill doc 存在，未接入 | episodes → 提炼 principles → 写入 skills/worldview |
| 41 | **三层 memory** | short + long 有，mid-term 模糊 | 显式 mid-term layer：跨 session working memory |
| 42 | **Ebbinghaus decay 激活** | 函数已写，metadata 文件未创建 | 确认在 reflect 中运行，初始化 metadata |

---

## 🔵 P3 — Reflection & Multi-Agent

| # | 项目 | 现状 | 要做什么 |
|---|------|------|----------|
| 43 | **Per-task micro-reflection** | 只有周级别 | 复杂任务后 (> 5min) 自动 micro-reflect |
| 44 | **Reflection loop 迭代** | 写作有，一般任务没有 | Code gen / analysis 加 2-3 次 self-critique → revise |
| 45 | **Inter-agent messaging** | Hub-spoke，无 peer 通信 | Agent A output 直接触发 Agent B |
| 46 | **并行 subtask** | 线性 pipeline | Plan 中无依赖 steps 同时 spawn |
| 47 | **Shared working memory** | 每 task 独立 workspace | 同一 plan 内的 steps 共享 key findings |
| 48 | **Semantic compression** | `output[:3000]` 硬截断 | LLM summarization 保留相关结论 |
| 49 | **Planner/executor 分离** | task_worker 混合 | planner → plan JSON → executor 逐步执行 → resume-safe |
| 50 | **Structured env init** | 不存在 | Initializer agent：建立 feature list + progress tracking → 交给 coding agent |

---

## P1 剩余 + P2 中最高 ROI

下一步建议优先级：

1. **#20 Score → Action** — 最快让自我进化闭环转起来的一步。reflect 读 scores，低分自动生成改进 plan
2. **#32 Calibration feedback** — calibration.jsonl 有数据没人读，接上就能改善 task planning
3. **#16 Rate limit 保护** — 防止 API 费用失控
4. **#14 结构化日志** — 可观测性基础
5. **#25 Agent-level scorecards** — 分辨哪个 agent 拖后腿

---

## Production Readiness 总览

```
                        之前          现在
并发安全                 🔴 无锁        ✅ fcntl + atomic write
Secrets                 🔴 明文        ✅ .config/ symlink
Config validation       🔴 静默失败     ✅ validate_config()
依赖管理                 🔴 无 pin      ✅ requirements.txt
Error handling          🟠 bare except ✅ 关键路径已修
Graceful shutdown       🟠 无 handler  ✅ SIGTERM + should_shutdown()
测试                    🟠 ~5%         ✅ 每 agent integration test + 并发 test
Observability           🟠 纯文本日志   🟡 cost tracking 有，结构化日志待做
Rollback                🟠 手动 revert ✅ auto-stash on crash loop
Self-assessment         🟡 记录不行动   🟡 待做 score → action
Per-agent assessment    🟡 不存在       🟡 待做
Self-evolution          🟡 skill doc   🟡 待做
Self-repair             🔵 scan only   🔵 待做
RAG                     🔵 基础可用     🔵 待做
Multi-agent             🔵 hub-spoke   🔵 待做
```
