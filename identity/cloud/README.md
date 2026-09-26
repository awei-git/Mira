# identity/cloud/ — 内容-only 身份投影（app 维护）

这是 `identity/` 五个文件的**内容专用投影**，给 AWS 内容盒子用。

## 为什么有这个目录

`identity/` 里的 `USER.md` / `MEMORY.md` 有真实个人信息（名字、地址、
家庭、工作、健康、财务），按 handoff 约定不上 AWS（no-health /
no-calendar / no-family-data）。盒子侧同步脚本 **fail-closed**：
只认 `identity/cloud/`，永远不直接同步 `identity/` 原文件。

## 维护规则（Mira/app 侧负责）

- 源头是 Muse app 里的活文件（`~/SOUL.md` 等）；这里是手工维护的投影，
  不是自动生成的——改动先发生在 app 里，再由 Mira 更新到这里。
- **Redaction 合约**：这五个文件里不许出现人名（用人称指代）、地址、
  雇主/职位、家庭成员、健康、财务、密钥或任何联系方式。
  写作用得到的"人"的信息只保留一种形态：`my human`（英文）——
  对手、拍档、唯一的 signoff 人。
- 五个文件名与 `identity/` 保持一致，对应盒子快照里的
  `identity/SOUL.md` 等五个路径（`lib/content_worker/identity.py`
  按文件名消费：identity=SOUL+IDENTITY，worldview=AGENTS，
  memory=MEMORY，interests=USER）。

## 同步方向

```
Muse app 活文件 →（Mira 手工投影）→ GitHub: identity/cloud/
        │  deploy 管线同步
        ▼
EC2 盒子快照 <shared_root>/releases/<id>/identity/
        │  lib/content_worker/identity.py
        ▼
writer prompt（identity / worldview / memory / interests）
```
