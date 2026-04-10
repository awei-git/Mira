# Data Directory Migration Plan

## 问题

运行时数据散落在 repo 的 7 个不同位置:

| 当前位置 | 内容 | 类型 |
|---------|------|------|
| `.agent_state.json` | 调度器状态 (哪些 job 跑过) | state |
| `.session_context.json` | 最近 40 个 cycle 的决策记录 | state |
| `.bg_health.json` | 后台进程健康状态 | state |
| `.pending_publish.json` | 待发布文章队列 | state |
| `agents/shared/soul/` | 身份、记忆、日记、研究 等 20+ 子目录 | soul |
| `agents/shared/autoresearch_runs/` | 自研优化历史 | data |
| `agents/shared/scheduled_jobs.json` | 调度配置 | state |
| `agents/.bg_pids/` | 后台进程 PID 文件 | runtime |
| `agents/super/proposals/` | 自进化提案 (72 个) | data |
| `agents/socialmedia/*_state.json` | 社媒状态 (7 个文件) | state |
| `logs/` | 每日日志 + 使用量 | logs |
| `feeds/` | RSS 源数据 | feeds |
| `tasks/` | 任务工作区 + 历史 | tasks |

全部被 .gitignore 忽略, 但找起来要翻遍整个 repo.

## 目标结构

```
Mira/data/
├── README.md              # 本文档的简版, 每个目录一句话说明
├── state/                 # 运行时状态 (调度器, session, 健康, 发布队列)
│   ├── agent_state.json
│   ├── session_context.json
│   ├── bg_health.json
│   ├── pending_publish.json
│   └── scheduled_jobs.json
├── soul/                  # Mira 的身份和记忆 (从 agents/shared/soul/ 迁来)
│   ├── identity.md
│   ├── worldview.md
│   ├── memory.md
│   ├── interests.md
│   ├── beliefs.json
│   ├── scores.json
│   ├── journal/           # 每日日记 (90天留存)
│   ├── reading_notes/     # 阅读笔记 (90天留存)
│   ├── episodes/          # 任务档案 (60天留存)
│   ├── conversations/     # 对话历史
│   ├── experiences/       # 自进化经验记录 (新)
│   ├── lessons/           # 自进化提炼的教训 (新)
│   ├── variants/          # A/B 测试方案 (新)
│   ├── knowledge/         # 提炼的永久知识 (新)
│   ├── learned/           # 自学的技能文件
│   ├── research/          # 研究队列和实验
│   ├── research_logs/     # 每日研究日志
│   └── scorecards/        # 自评记分卡
├── logs/                  # 运行日志
│   ├── YYYY-MM-DD.log     # 每日主日志
│   ├── usage_YYYY-MM-DD.jsonl  # 每日 API 用量
│   ├── timing.jsonl       # cycle 性能计时
│   └── bg-*.log           # 后台进程日志
├── tasks/                 # 任务工作区 (用户请求产生的)
│   ├── history.jsonl      # 完成的任务历史
│   ├── status.json        # 活跃任务状态
│   └── {task_id}/         # 每个任务的工作目录
├── feeds/                 # 信息源数据
│   ├── raw/               # 原始抓取 (每日 JSON)
│   └── apps/              # 外部 app feed
├── pids/                  # 后台进程 PID (从 agents/.bg_pids/ 迁来)
├── social/                # 社媒状态 (从 agents/socialmedia/ 迁来)
│   ├── comment_state.json
│   ├── growth_state.json
│   ├── notes_state.json
│   ├── twitter_state.json
│   └── publication_stats.json
├── proposals/             # 自进化提案 (从 agents/super/proposals/ 迁来)
└── autoresearch/          # 自研优化历史
```

## 迁移步骤

### Phase 1: 更新 lib/config.py 路径常量

```python
# 在 lib/config.py 中:
DATA_DIR = MIRA_ROOT / "data"
SOUL_DIR = DATA_DIR / "soul"          # 原: _AGENTS_DIR / "shared" / "soul"
LOGS_DIR = DATA_DIR / "logs"          # 原: MIRA_ROOT / "logs"
FEEDS_DIR = DATA_DIR / "feeds"        # 原: MIRA_ROOT / "feeds"
STATE_FILE = DATA_DIR / "state" / "agent_state.json"  # 原: MIRA_ROOT / ".agent_state.json"
```

新增常量:
```python
DATA_DIR = MIRA_ROOT / "data"
PIDS_DIR = DATA_DIR / "pids"
SOCIAL_STATE_DIR = DATA_DIR / "social"
PROPOSALS_DIR = DATA_DIR / "proposals"
TASKS_DIR = DATA_DIR / "tasks"
SESSION_FILE = DATA_DIR / "state" / "session_context.json"
HEALTH_FILE = DATA_DIR / "state" / "bg_health.json"
```

### Phase 2: 更新硬编码路径

需要 grep 并修改的模式:

| 模式 | 位置 | 替换为 |
|------|------|-------|
| `MIRA_ROOT / ".agent_state.json"` | config.py | `DATA_DIR / "state" / "agent_state.json"` |
| `MIRA_ROOT / ".session_context.json"` | core.py | `from config import SESSION_FILE` |
| `MIRA_ROOT / ".bg_health.json"` | health_monitor.py | `from config import HEALTH_FILE` |
| `_AGENTS_DIR / ".bg_pids"` | dispatcher.py | `from config import PIDS_DIR` |
| `agents/super/proposals` | self_evolve.py, backlog_executor.py | `from config import PROPOSALS_DIR` |
| `socialmedia/*_state.json` | growth.py, notes.py, twitter.py | `from config import SOCIAL_STATE_DIR` |

### Phase 3: 物理移动文件

```bash
# 创建 data/ 目录
mkdir -p data/{state,soul,logs,tasks,feeds,pids,social,proposals,autoresearch}

# 移动 soul/
mv agents/shared/soul/* data/soul/

# 移动 state files
mv .agent_state.json data/state/agent_state.json
mv .session_context.json data/state/session_context.json
mv .bg_health.json data/state/bg_health.json
mv .pending_publish.json data/state/pending_publish.json
mv agents/shared/scheduled_jobs.json data/state/

# 移动 logs
mv logs/* data/logs/

# 移动 feeds
mv feeds/* data/feeds/

# 移动 tasks
mv tasks/* data/tasks/

# 移动 pids
mv agents/.bg_pids/* data/pids/

# 移动 socialmedia state
mv agents/socialmedia/*_state.json data/social/
mv agents/socialmedia/publication_stats.json data/social/
mv agents/socialmedia/reply_tracking.json data/social/
mv agents/socialmedia/notes_queue.json data/social/

# 移动 proposals
mv agents/super/proposals/* data/proposals/

# 移动 autoresearch
mv agents/shared/autoresearch_runs/* data/autoresearch/

# 清理空目录
rmdir agents/shared/soul agents/shared/autoresearch_runs agents/.bg_pids agents/super/proposals logs feeds tasks
```

### Phase 4: 更新 .gitignore

替换所有散落的 gitignore 条目为:
```
data/
```

保留的非 data/ 条目:
```
secrets.yml
config.yml
*.xcuserstate
*.mp4
*.mp3
*.wav
*.m4a
*.npy
*.sqlite
*.lock
__pycache__/
```

### Phase 5: 更新 LaunchAgent

`bin/mira-agent.sh` 如果硬编码了 logs/ 路径, 需要改为 data/logs/.

### Phase 6: 写 data/README.md

```markdown
# Mira Runtime Data

所有运行时数据都在这个目录下. 不提交到 git.

| 目录 | 内容 | 留存 |
|------|------|------|
| state/ | 调度器、session、健康状态 | 永久 |
| soul/ | 身份、记忆、日记、研究 | 部分有留存策略 |
| logs/ | 运行日志和 API 用量 | 14 天 |
| tasks/ | 用户请求的任务工作区 | 7 天 |
| feeds/ | RSS 源原始数据 | 7 天 |
| pids/ | 后台进程 PID 跟踪 | 临时 |
| social/ | 社媒平台状态 | 永久 |
| proposals/ | 自进化提案 | 永久 |
| autoresearch/ | 自研优化历史 | 永久 |
```

## 验证清单

- [ ] `python3 -m pytest agents/ lib/ -m "not slow"` 全部通过
- [ ] `launchctl kickstart -k gui/$(id -u)/com.angwei.mira-web` web server 正常
- [ ] `curl localhost:8384/api/ang/jobs` 返回数据
- [ ] Mira agent 运行一个完整 cycle 无报错 (检查 data/logs/ 最新日志)
- [ ] agents/shared/ 只保留 persona/ (代码) 和 tests/ (测试) — 没有数据文件
- [ ] 根目录没有 .agent_state.json, .session_context.json 等散落文件
- [ ] `git status` 干净 (所有新数据文件被 .gitignore 覆盖)

## 注意事项

- 这个迁移必须在 Mira agent 停止时执行 (`launchctl unload com.angwei.mira-agent`), 否则 agent 在 30s cycle 中会写到旧路径
- 建议在迁移前做一次 `git stash` 保存当前 soul/ 状态
- socialmedia state 文件的路径在 growth.py, notes.py, twitter.py 中是硬编码的 (`Path(__file__).parent / "xxx_state.json"`), 必须改为使用 config 常量
