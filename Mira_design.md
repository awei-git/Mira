# Mira — iPhone ↔ Mac Agent 通讯系统

## 概述

iPhone 端 SwiftUI app 通过 iCloud Drive 共享文件夹与 Mac 端 Mira agent 通讯。
消息 = JSON 文件，Mac 端 launchd 每分钟 poll 一次。

## 决定

| 问题 | 决定 |
|------|------|
| 通讯通道 | iCloud Drive 文件队列（不是 Notes、不是 CloudKit、不是 server） |
| iPhone app | 原生 SwiftUI，iOS 17+，app 名 "Mira" |
| 多 Apple ID | iCloud 共享文件夹，家人场景 |
| Mac 端架构 | launchd 1 分钟间隔 poll，不需要 menu bar app |
| Apple Notes | 保留，用于正式文档（documentation），不再用于消息通讯 |
| 延迟要求 | 分钟级可接受，不能 stuck |

## 文件协议

```
Mira/Mira-bridge/
├── inbox/                       # phone → mac (phone 写，mac 读)
│   └── {sender}_{yyyyMMdd_HHmmss}_{uuid8}.json
├── outbox/                      # mac → phone (mac 写，phone 读)
│   └── {recipient}_{yyyyMMdd_HHmmss}_{uuid8}.json
├── ack/                         # mac 写，phone 查状态
│   └── {message_id}.json
├── archive/                     # 归档的旧消息
├── tasks/                       # 任务工作区
├── threads/                     # 线程记忆
└── heartbeat.json               # mac 每次 poll 更新，phone 判断 agent 是否在线
```

### 消息格式 (inbox)

```json
{
  "id": "a1b2c3d4",
  "sender": "user-iphone",
  "timestamp": "2026-03-03T10:00:00+00:00",
  "type": "text",
  "content": "消息内容，支持长文",
  "thread_id": "",
  "priority": "normal"
}
```

### 回复格式 (outbox)

```json
{
  "id": "e5f6g7h8",
  "in_reply_to": "a1b2c3d4",
  "sender": "Mira",
  "recipient": "user-iphone",
  "timestamp": "2026-03-03T10:01:30+00:00",
  "type": "text",
  "content": "Mira 的回复",
  "thread_id": ""
}
```

### Ack 格式

```json
{
  "message_id": "a1b2c3d4",
  "status": "done",
  "timestamp": "2026-03-03T10:01:30+00:00"
}
```

状态流转：`received` → `processing` → `done` | `error`

### Heartbeat 格式

```json
{
  "timestamp": "2026-03-03T10:01:00+00:00",
  "status": "online"
}
```

Phone 端判断：heartbeat 距今 < 3 分钟 = agent 在线。

## 防 stuck 机制

1. **Heartbeat** — Mac 每次 poll 更新 `heartbeat.json`，phone 显示 agent 在线/离线
2. **消息幂等** — UUID 去重，iCloud 重复 sync 不会重复处理
3. **launchd watchdog** — 进程挂了自动重启，`ThrottleInterval: 30s`
4. **3 天自动清理** — old acks 和已处理消息自动归档/清理

## 架构

```
┌──────────────┐    iCloud Drive     ┌──────────────────────────┐
│  iPhone App  │ ◄── sync ──────►    │  Mac (launchd 1min)      │
│  (Mira)      │    Mira-bridge/     │                          │
│              │                     │  agents/super/core.py    │
│ send → inbox │ ─────────────────►  │  ↓ dispatch              │
│              │                     │  agents/super/task_worker │
│ read outbox  │ ◄─────────────────  │  → general/writer/...    │
│ read ack     │ ◄─────────────────  │  write outbox + ack      │
└──────────────┘                     └──────────────────────────┘
```

## Agent 架构

```
Mira/agents/
├── super/          # 调度器 — 只分发，不处理
│   ├── core.py         # 主循环入口
│   ├── task_manager.py # 任务生命周期
│   ├── task_worker.py  # 子进程 worker
│   ├── notes_inbox/    # Apple Notes 输入
│   └── notes_outbox/   # Apple Notes 输出
├── shared/         # 共用模块
│   ├── config.py       # 所有路径和配置 (reads config.yml)
│   ├── mira.py         # Mira 消息读写
│   ├── prompts.py      # 共用 prompt 模板
│   ├── soul_manager.py # 身份/记忆/技能管理
│   ├── sub_agent.py    # Claude CLI 调用
│   ├── notes_bridge.py # Apple Notes 桥接
│   ├── thread_manager.py
│   └── soul/           # 共享身份和记忆
│       ├── identity.md
│       ├── memory.md
│       ├── interests.md
│       ├── journal/
│       └── learned/    # 动态学到的新技能
├── general/        # 通用任务 handler
├── writer/         # 写作 pipeline + 资源
│   ├── skills/         # 17 个写作技法
│   ├── prompts/        # writer/reviewer/routine prompts
│   ├── ideas/          # 写作想法
│   ├── frameworks/     # 写作框架
│   ├── templates/      # 脚手架模板
│   └── *.py            # 写作代码
├── explorer/       # RSS/feed 探索
├── publisher/      # Substack 等平台发布
├── video/          # 视频编辑 (4 skills)
├── photo/          # 摄影编辑 (4 skills)
├── analyst/        # 市场分析 (4 skills)
├── researcher/     # 数学研究 (4 skills)
└── coder/          # 编程 (3 skills)
```

## 文件清单

### Mac 端 (Python)
- `agents/shared/mira.py` — Mira 类，消息读写
- `agents/shared/config.py` — 所有路径常量 (reads from config.yml)
- `agents/super/core.py` — 主循环，集成 Mira + Notes + Writing + Explore
- `agents/super/task_manager.py` — 任务分发和收集
- `agents/super/task_worker.py` — 子进程执行，LLM 智能分类
- LaunchAgent 入口脚本 + plist（60 秒轮询）

### iPhone 端 (SwiftUI)
- `Mira-iOS/Mira/Mira/TalkBridgeApp.swift` — 入口
- `Mira-iOS/Mira/Mira/Models/Message.swift` — 消息模型
- `Mira-iOS/Mira/Mira/Services/BridgeService.swift` — iCloud 读写 + 轮询
- `Mira-iOS/Mira/Mira/Views/ChatView.swift` — 聊天界面
- `Mira-iOS/Mira/Mira/Views/ChatBubble.swift` — 消息气泡
- `Mira-iOS/Mira/Mira/Views/SetupView.swift` — 首次选择文件夹

## 首次使用流程

1. Mac: `cp config.example.yml config.yml` 并填入你的路径
2. Mac: `cp secrets.example.yml secrets.yml` 并填入 API keys
3. Mac: Mira-bridge/ 目录已存在（agent 自动创建）
4. Mac: 如果家人用，右键 Finder → 共享 Mira-bridge/ 文件夹给家人 Apple ID
5. iPhone: 打开 Mira app → 选择 iCloud Drive 中的 Mira-bridge 文件夹
6. iPhone: 发消息 → 等 1-2 分钟 → 看回复
