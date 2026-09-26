# identity/ — Mira 的身份与规则（canonical source）

这里是 Mira 身份与行为规则的**唯一源头**。

## 文件

- `SOUL.md` — 人格与语气：她是谁、怎么说话。
- `IDENTITY.md` — 名字、形象、标识。
- `USER.md` — 她服务的人：Ang 是谁、在乎什么。
- `AGENTS.md` — 工作手册：跨会话的操作约定、工具 quirks、吃过的亏。
- `MEMORY.md` — 长期记忆： durable 的事实、偏好、承诺。

## 同步方向（单向）

```
Muse app 里的活文件 (~/SOUL.md 等)
        │  定时同步（每天一次，大改手动推）
        ▼
GitHub: 本目录
        │  deploy 管线同步
        ▼
EC2 盒子 (/opt/mira/Mira)
```

- **源头是 Muse app 里的活文件**——Mira 每天实际改的就是那几个文件。
- 本目录是**快照 + 版本**，不是实时库。改动先发生在 app 里，再同步到这里。
- 盒子上的 `data/soul/*` 从这里**生成**，永远不要直接改盒子上的 soul 文件。
- 有分歧时以 app 里的活文件为准。

## 红线

- 本 repo 必须保持 private。
- 密钥类（密码、API key、token、验证码）**永远不上 GitHub**。
  身份可以同步，秘密不行——秘密走 Secure Vault / 盒子本地文件，
  且盒子本地秘密必须进 `deploy/projects.yaml` 的 `data_dirs`/`env_file`
  保护名单（2026-09-23 bridge `.token` 教训）。
