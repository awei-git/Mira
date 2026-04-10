# Mira Dispatch & Feedback Architecture

更新: 2026-04-10

## 概览

Mira 有两套并行的任务分发系统:

```
LaunchAgent (30s loop)
  └─ core.py cmd_run()
       ├─ [用户请求] do_talk() → TaskManager.dispatch() → task_worker.py
       └─ [定时任务] _dispatch_scheduled_jobs() → _dispatch_background() → core.py <command>
```

## 1. 定时任务系统 (Background Dispatcher)

**代码**: `runtime/jobs.py` + `runtime/dispatcher.py` + `runtime/triggers.py`

### 声明式 Job 注册

每个 job 是一个 `JobSpec` dataclass, 定义:
- `trigger`: 何时触发 (time_window / cooldown / conditional)
- `blocking_group`: 并发分组 (heavy / light / local)
- `priority`: 调度优先级 (数字越小越高)
- `command`: CLI 参数

### 并发分组

| 分组 | 限额 | 包含 | 理由 |
|------|------|------|------|
| **heavy** | 2 | explore, writer, research, analyst, journal | 消耗 Claude API, 需要限流 |
| **light** | 3 | growth, comments, zhesi, assessment, report | 轻量 API 调用 |
| **local** | 10 | idle-think | 主要用 oMLX, 不消耗 cloud API |

同组内共享 slot, 不同组独立. idle-think 不再挡 explore 的路.

### Pipeline 链

定义在 `PIPELINE_CHAINS`:
```
explore 完成 → 自动触发 autowrite-check
autowrite-check 完成 → 自动触发 writing-pipeline
```

链式触发绕过 cooldown/trigger 检查, 在同一个 cycle 内立即 dispatch.
由 `health_monitor.harvest_all()` 返回的完成列表驱动.

### 调度流程

```
每 30s:
  1. harvest_all() → 收割已完成的 bg process, 返回 completed list
  2. _dispatch_pipeline_followups(completed) → 链式触发后续 job
  3. _dispatch_scheduled_jobs() → 遍历 job 表, 评估 trigger, dispatch
  4. _self_repair_daily_tasks() → 重试今天失败的关键任务
```

## 2. 用户请求系统 (TaskManager)

**代码**: `task_manager.py` + `task_worker.py`

### 分发

用户从 iPhone/WebGUI 发消息 → `do_talk()` → `TaskManager.dispatch(msg)`:
1. 创建 workspace 目录
2. 写 msg payload 到 workspace
3. `subprocess.Popen(task_worker.py --msg-file ... --workspace ...)`
4. 记录 TaskRecord (pid, status, tags)

### 路由

`task_worker.py` 根据消息内容路由到具体 agent handler:
- writer, explorer, researcher, coder, analyst, socialmedia, video, photo, discussion, general

### 反馈

- **结果**: worker 写 `result.json` 到 workspace
- **进度**: worker 写 status message 到 `items/{task_id}.json`
- **轮询**: 每 30s `cmd_run()` 调用 `TaskManager.check_tasks()` 检查 PID 存活

### 超时

- 默认: `TASK_TIMEOUT` (config)
- 长任务 (writing/research): `TASK_TIMEOUT_LONG`
- 超时后通知用户, 用户可回复 "kill" 或 "wait"

## 3. 两套系统的区别

| | Background Dispatcher | TaskManager |
|---|---|---|
| 触发 | 定时/cooldown/条件 | 用户消息 |
| 并发控制 | per-group (heavy/light/local) | MAX_CONCURRENT_TASKS (全局) |
| workspace | 无 (stdout→log) | 独立目录, 含 result.json |
| 反馈 | health_monitor 记录成功/失败 | result.json + items/ 进度 |
| 重试 | self_repair + job trigger 下次触发 | TaskManager.reset_for_retry() |
| Pipeline | 支持链式触发 | 不支持 |

## 4. 已知问题

1. **两套超时逻辑**: dispatcher 靠 PID 文件 mtime 做 cooldown, TaskManager 靠 started_at + timeout 做检测. 逻辑不统一.
2. **反馈是单向的**: sub-agent 不能实时通知 super, 必须等下一个 30s cycle 轮询.
3. **result 结构不一致**: TaskManager 有结构化 TaskRecord, dispatcher 只有 health_monitor 的 success/fail binary.
4. **TaskManager 没有并发分组**: 所有用户请求共享 MAX_CONCURRENT_TASKS, 不区分轻重.

## 5. 未来可能改进

- TaskManager 也接入并发分组 (根据 agent type 自动分配)
- 统一结果格式 (所有任务产出 structured result)
- 实时反馈通道 (Unix socket / named pipe, 替代 30s 轮询)
