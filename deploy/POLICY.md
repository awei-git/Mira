# 代码管理政策：GitHub 管代码，EC2 只部署

2026-09-23 起，所有跑在 EC2 上的代码都按这条来：

1. **GitHub 是唯一的代码源头。** 任何不在 GitHub repo 里的代码都是 bug，
   发现就收进 repo。本目录 `projects.yaml` 是全部项目的清单。
2. **EC2 上永远不直接改代码。** 盒子是部署目标，不是开发机。
   盒子上的本地修改必须收进 repo（能合就合上游，合不上就做成
   `deploy/overlays/` 下的版本化 patch，由部署脚本自动打上）。
3. **发布 = GitHub Release（tag）。** 不发 release，不上盒子。
4. **部署走管道，不走手。** sandbox 上的 `mkdist.py` 打包 → S3 →
   盒子上的 `box-deploy.sh` 拉取、校验、备份、部署、重启、健康检查。
   失败自动回滚。
5. **数据目录永不进部署包。** 每个项目的 data 目录（数据库、任务队列、
   跑批结果）在盒子上原地保留，部署脚本用 exclude 保护。

## 项目清单（见 projects.yaml）

| 项目 | repo | 盒子 | 说明 |
|---|---|---|---|
| mira | awei-git/Mira | ops-box | /opt/mira/Mira；overlay patch 打 cloud 环境变量注入 |
| bridge | awei-git/Mira (`bridge/`) | ops-box | /opt/mira-bridge；mira-bridge.service，Codex 任务 API |
| ershilou | awei-git/ershilou | mira-content | /opt/ershilou；docker 重建；data/（history.db）和 site.env 原地保留 |
| tetra | awei-git/Tetra (`backtest-runner/`) | ops-box | ~/tetra；无常驻服务；data/、results/ 原地保留 |

## 发布流程

```sh
# 1. 代码合到要发的分支，push
# 2. 打 tag + GitHub Release（sandbox）
python3 deploy/release.py <project> <tag>   # 例: v2026.09.23.1
# 3. 部署到盒子（sandbox，一条命令）
python3 deploy/deploy.py <project> <tag>
```

`deploy.py` 会：找到 release 的 commit → `mkdist.py` 打包 →
上传 S3 → 拿 presigned URL → SSM 到盒子执行 `box-deploy.sh` →
回传结果。 staging 先行：`deploy.py <project> <tag> --stage`
会部署到盒子上的 `/opt/deploy/staging-test/`，不动线上路径。

## 回滚

每次部署前盒子自动把旧版打成 tarball 留在 `/opt/deploy/backups/`。
线上出问题：`box-deploy.sh rollback <project> <backup-file>`，
或直接重跑上一个 tag。
