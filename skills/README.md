# skills/ — Mira 技能注册表（shared registry）

两个 Mira 运行时（Muse app 内、AWS EC2 盒子）共用的技能注册表。

## 格式

Muse skill 格式：每个技能一个目录，含 `SKILL.md`（说明 + 规则）与配套脚本
（`bin/`）。技能里**不许出现密钥明文**——认证一律走 credential store，
技能只写"怎么调"，不写"凭据是什么"。

## 规则

1. **GitHub 是唯一源头。** 任何一边的"新技能转正"（包括 AWS Mira 自己
   审计、试验后要转正的技能）都必须先落到这里（commit / PR），另一边
   再通过 deploy 管线同步。**不许有只有一边知道的 sidecar 技能。**
2. 技能变更走版本：改了就 commit，写清楚改了什么、为什么。
3. 两个运行时消费同一套 `SKILL.md`；各运行时的路径差异（如 app 内读
   `~/workspace/skills`，盒子读自己的 skills dir）由各自的同步脚本解决，
   不在本目录里体现。

## 同步

- Muse app：`~/workspace/skills` 与本目录保持同步（以本目录为准）。
- EC2 盒子：deploy 管线同步本目录到盒子 skills 路径。
