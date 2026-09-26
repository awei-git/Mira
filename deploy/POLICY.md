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

## Mac / CI 离线打包

`local_bundle.py` 不依赖 `~/workspace`、自定义 GitHub CLI 或 AWS 凭据：

```sh
python3 deploy/local_bundle.py --repo /path/to/checkout --project content-worker \
  --commit FULL_40_CHARACTER_COMMIT --tag RELEASE_TAG --output /new/output/directory
```

先 fetch 已发布 tag；命令校验本地 tag 指向指定 commit，并从该 commit 同时
读取 registry 和源码，不读取工作区改动。输出 `bundle.tar.gz` 与 `receipt.json`，
包含归档和逐文件 SHA-256。相同输入生成相同包；已有输出目录拒绝覆盖。
包按该版本 registry 排除运行数据、原始身份和独立同步的 skills；桥接的
`extra_files` 从同一 commit 读取。仅打包普通文件，不跟随 symlink。

边界：本地 tag 匹配不等于远程 Release 已审核，上传工作流仍须核验远程发布。
本工具不上传、不申请 AWS 权限、不部署、不重启，也不批准包内 agent skills。
package exclusions 仍以版本化 registry 为准，不是任意内容的隐私扫描器。
后续必须保留源 commit/包 hash/S3 version 收据并完成技能审核和主机 staging；
当前默认部署命令未被本工具替换，端到端流水线尚需接线。
