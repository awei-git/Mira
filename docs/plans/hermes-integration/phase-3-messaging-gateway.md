# Phase 3 — 统一消息网关

**目标**：多平台读者 / 社区反馈能进 Mira，并进入 learning loop 变成 reward 信号。

**依赖**：Phase 1（否则反馈没处去；会变成又一个坏反馈源）。

**预估**：每 adapter 约 1 天。

## 当前进度（2026-04-16 ABC + stubs 完成）

**不动** 现有 `lib/bridge.py`。新包 `lib/bridge_gateway/` 提供 ABC + 注册器 + 两个 in-memory stub，让 supervisor/registry/Phase-1 reward 接入能在真实 connector 到位之前就测到契约。

| 模块 | 路径 | 状态 |
|---|---|---|
| ABC + 数据类 | `lib/bridge_gateway/adapter.py`（`BridgeAdapter` ABC：`read_incoming / send_outgoing / heartbeat`；`BridgeMessage` dataclass；`bridge_message_from_dict` 解析器） | ✅ |
| 注册器 | `lib/bridge_gateway/registry.py::AdapterRegistry`（多 adapter 并发 poll、`poll_all` 失败隔离、`send` 按 `source` 路由、`heartbeats` 聚合） | ✅ |
| Telegram stub | `lib/bridge_gateway/stub_telegram.py::TelegramStubAdapter`（in-memory 队列 + `send_sink` 测试钩） | ✅ |
| Discord stub | `lib/bridge_gateway/stub_discord.py::DiscordStubAdapter`（入站自动打 `reader_feedback` tag，供 Phase 1 reward 识别；tag 可自定义） | ✅ |
| 契约单测 | `tests/bridge_gateway/test_adapter_contract.py` + `test_registry.py`（13 tests green：duplicate/empty name rejection、poll 隔离、路由、sink 拦截、auto-tag） | ✅ |

待办（真正的 adapter 替换 stub 时做）：

- **Step 3.1 整合 existing `lib/bridge.py`**：把 `lib/bridge.py::Mira` 的 iCloud/Notes 路径包成 `NotesBridgeAdapter` 注入 `AdapterRegistry`，让 `do_talk` 通过 registry fan-in 读 items。此步会最终替代 `lib/bridge.py` 的散装使用，但可渐进。
- **Step 3.2 真 Telegram adapter**：`lib/bridge_gateway/adapters/telegram.py` 用 `python-telegram-bot`，bot token 从 `.env.secret`（参 `feedback_secrets_check`）；long-poll 起在 supervisor 下；send 走 Phase 0 circuit breaker + idempotent retry。
- **Step 3.3 真 Discord adapter**：`discord.py`，默认只读、单频道，入站继承 stub 的 `reader_feedback` tag 约定。
- **启用条件**：Phase 0 柱子 1（supervisor）到位后才能把 adapter 跑在独立进程；Phase 1 `ENABLE_TRAJECTORY_V2` 打开后 `reader_feedback` 才真正进入 reward 计算。

## 现状

- [Mira/lib/bridge.py](Mira/lib/bridge.py)`::Mira` 包装 iCloud MiraBridge。
- Items 存于 `bridge_dir/users/<uid>/items/`。
- 唯一 adapter：Apple Notes（iCloud 落地）。
- iOS app + web GUI 都走这条 bridge。

## Hermes 模式参考

单 gateway 进程同时接 Telegram / Discord / Slack / WhatsApp / Signal / iMessage / WeChat / CLI——**一个 state，多个 adapter**。

## 设计

重构 bridge.py 为**adapter 架构**：

```
BridgeAdapter (ABC)
├── NotesBridgeAdapter   (既有 iCloud)
├── TelegramBridgeAdapter
└── DiscordBridgeAdapter
```

Adapter 接口：

```python
class BridgeAdapter(ABC):
    def read_incoming(self) -> list[BridgeItem]: ...
    def send_outgoing(self, item: BridgeItem) -> bool: ...
    def heartbeat(self) -> datetime: ...
```

所有 adapter 都写入**同一个** `bridge_dir/users/<uid>/items/` 格式（BridgeItem schema 来自 Phase 0 柱子 2）。core.py 的 `do_talk` 不动——它读的是 items 不是 adapter。

## 步骤

### Step 3.1 — 重构 bridge.py

1. 抽 `BridgeAdapter` ABC 到 `lib/bridge/adapter.py`。
2. 现有 iCloud 实现移到 `lib/bridge/adapters/notes.py`，接口保持行为一致（柱子 4 的 integration 测试兜底）。
3. `Mira` 类变 adapter registry + fan-out：每 tick 轮询所有 enabled adapter 的 `read_incoming()`，写入 items 目录。
4. 配置：`config.ENABLED_BRIDGE_ADAPTERS = ["notes"]`（初始仅 notes，保证零行为变化）。
5. Phase 0 柱子 1 的 supervisor 看管每个 adapter 进程。

### Step 3.2 — Telegram adapter

`lib/bridge/adapters/telegram.py`：

- `python-telegram-bot`。
- 单用户（你 @weiang0212）；其它用户消息**丢弃**并告警。
- 收到消息 → 构造 `BridgeItem(source="telegram", user_id=<mapped>)` → 写入 items。
- 发送 → `bot.send_message`，通过 Phase 0 柱子 3 的 idempotent retry 包裹（防网络抖动重复发）。
- Bot token 走 `.env.secret`——参 `feedback_secrets_check`。
- 长连接用 polling 简单可靠，起在 supervisor 下；webhook 推迟到确实需要再考虑。

### Step 3.3 — Discord adapter

`lib/bridge/adapters/discord.py`：

- `discord.py`。
- 单 guild，单频道（Substack 社区反馈专用）。
- 消息进 items，带 `tags: ["reader_feedback"]`——被 Phase 1 的 reward 计算识别。
- **只读优先**（不回复），避免 moderation 开销；如要回复，白名单触发词。
- 直接服务 `project_substack_growth_target.md`（2026-05-11 前 30 订阅）。

### Step 3.4 — 测试 + 灰度

**单测**（每个 adapter）：
- mock 各自 SDK → 断言 BridgeItem 格式正确、idempotency key 被传递。

**集成**：
- `tests/integration/test_bridge_adapters.py` 复用柱子 4 的 notes bridge 测试模板。

**灰度**：
1. Telegram 先开 1 天（仅你）→ 观察消息 roundtrip、supervisor 对网络抖动的重启、与 Notes/iOS 流是否互扰。
2. Discord 后开只读 1 天 → 确认 `reader_feedback` tag 在下一轮 reflect 中进入 reward delta。

## 成功标准

- 连续 7 天零干扰既有 Notes/iOS 路径（衡量：items-processed 数量不变）。
- Telegram roundtrip 延迟 p95 < 5s。
- Discord feedback items 在下一轮 reflect 时进入 reward delta（有 provenance）。
- 任一 adapter 崩溃不拖垮其它 adapter（supervisor 隔离）。

## Rollback

- 单 adapter 级别：`config.ENABLED_BRIDGE_ADAPTERS` 里去掉该 adapter。
- 全盘回退：恢复旧 bridge.py（保留 git tag），fallback 到仅 Notes。
- 因为所有 adapter 写同一 items 格式，core.py 不变，回退无数据迁移。

## 非目标（明确排除）

- **不做**完整聊天机器人体验（话题管理、线程追踪等）。
- **不做** WhatsApp / Signal / iMessage / WeChat——待 Telegram + Discord 稳定后再评估需求。
- **不做**多租户扩展——目前明确单用户（你 + Substack 只读社区）。
