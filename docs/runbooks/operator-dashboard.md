# Operator Dashboard Runbook

更新时间：2026-04-06

## 1. 目的

当 Mira 看起来还在运行，但不清楚具体卡在哪里时，先看 operator dashboard。

标准入口：

1. Web `Operator` tab
2. API `/api/{user_id}/operator`
3. bridge cache `users/<user_id>/operator/dashboard.json`

## 2. 标准排查顺序

1. `tasks.stuck`
2. `recent_incidents`
3. `publish.queue`
4. `backlog.next_actions`
5. `health.processes`
6. `latest_restore_drill`

## 3. 常见情况

### 3.1 任务卡住

先看：

1. `tasks.active`
2. `tasks.stuck`
3. 对应 task 的 `failure_class`

处理：

1. 判断是等待用户输入、worker 卡死，还是 connector 超时。
2. 不要只看 status，要同时看 artifact 是否真的更新。

### 3.2 发布队列堆积

先看：

1. `publish.queue`
2. `publish.stuck`
3. `recent_incidents`

处理：

1. 分清审批未过、connector 失败、verify 失败。
2. 不要通过跳过 verify 来“清空”队列。

### 3.3 self-improvement 只提案不执行

先看：

1. `backlog.counts`
2. `backlog.next_actions`

处理：

1. 确认 item 是否 `approved`。
2. 确认 item 是否带 `executor` 和 `payload`。
3. 当前只允许低风险、已批准、已有 executor 的项自动执行。

## 4. 健康标准

1. 活跃任务数量可解释。
2. stuck task 不长期堆积。
3. publish queue 不长期停在同一状态。
4. recent incidents 没有持续扩大。
5. 最近一次 restore drill 不是空白。

## 5. 不应该做的事

1. 不要手改 item/status 文件来“修好”系统。
2. 不要绕过 preflight / verify 来压告警。
3. 不要把空白 dashboard 当成系统健康证明。
