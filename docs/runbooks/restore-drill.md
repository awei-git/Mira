# Restore Drill Runbook

更新时间：2026-04-06

## 1. 目的

备份存在不等于系统可恢复。restore drill 用来验证：

1. 最新 backup 目录完整
2. manifest 没漂
3. 关键文件能被 staged restore

## 2. 标准入口

手动执行：

```bash
python3 agents/super/core.py restore-dry-run
```

自动执行：

1. scheduler 每周触发一次 `restore-dry-run`
2. 结果写入 `logs/restore_drills.jsonl`

## 3. 成功标准

一次合格的 restore drill 至少满足：

1. 找到最新带 `backup_manifest.json` 的 backup 目录
2. manifest hash / size 校验通过
3. `config.yml`
4. `soul/identity.md`
5. `soul/memory.md`
6. `soul/worldview.md`

都能被 staged 到临时 restore 目录

## 4. 失败时怎么处理

### 4.1 `backup_not_found`

先检查：

1. `/Volumes/home/backup/mira` 是否可读
2. 最近一次 backup 是否写出 manifest

### 4.2 `manifest_errors`

说明：

1. backup 目录被篡改
2. 拷贝不完整
3. manifest 与真实文件不一致

处理：

1. 先保留现场，不要重写 manifest 覆盖问题
2. 回退到最近一个完整 backup

### 4.3 `required_errors`

说明：

1. 关键配置或 soul 文件缺失

处理：

1. 修 backup 复制清单
2. 重新做一次 backup，再跑 restore drill

## 5. 记录要求

每次 drill 都必须把以下信息写入 `logs/restore_drills.jsonl`：

1. 时间
2. backup 目录
3. 是否通过
4. manifest / required path 错误
