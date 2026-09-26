---
title: 数据存在哪
kind: 参考
summary: 每一类数据的位置、谁写、怎么备份。
covers:
  - deploy/db-backup.sh
  - deploy/r2-upload.py
  - deploy/r2-sync-uploads.py
  - deploy/systemd/
  - deploy/README-backup.md
---

# 数据存在哪 {#data}

每一类数据的位置、谁写、怎么备份。

> 讲：数据的位置和备份。不讲：数据库表结构。

## 位置 {#where}

| 数据 | 位置 | 说明 |
|---|---|---|
| 记录、事件、权限 | Postgres | dev 和正式环境的数据库在单独的主机上；etrip 在容器里 |
| 上传文件（题目 PDF、附件、头像） | 主机目录，挂到主 API 的 `/data/uploads`（`UPLOADS_HOST_PATH`） | 不在镜像里，发版不动 |
| 会话文件、附件、迁移用的源仓库 | 主机目录，挂到 `/app/.workspaces`（`WORKSPACES_HOST_PATH`） | 活跃的代码仓库在代码托管上，不在这里 |
| 项目网站的发布快照 | 工作区卷里的 `.sites/<项目>/<版本>` | 备份工作区卷时一起带上 |
| 代码 | 项目绑定的代码托管：平台自带的 Forgejo 或 GitHub | 采纳即合并到那里 |
| 缓存 | Valkey | 丢了可以重建 |
| 模型网关的账 | LiteLLM 自己的数据库（`litellm-db`） | 与平台数据库分开，网关升级碰不到平台数据 |

这些目录都在主机上挂载进容器，所以发版替换容器不会动数据（`deploy/compose/docker-compose.base.yml`）。

## 备份 {#backup}

脚本在 `deploy/`，由主机上的 systemd 定时器执行（`deploy/systemd/`）：

| 什么 | 脚本 | 频率 |
|---|---|---|
| 数据库转储（`pg_dump -Fc`，校验后保留 30 天） | `db-backup.sh` | 每小时整点 |
| 转储的异地副本（Cloudflare R2） | `r2-upload.py` | 每次转储校验通过后 |
| 上传文件增量镜像到 R2（远端不删） | `r2-sync-uploads.py` | 每小时 :30 |
| 会话记录归档镜像到 R2 | 同一个脚本，`UPLOADS_PREFIX=transcripts` | 每小时 :45 |

## 检查备份真的能用 {#verify}

- `.github/workflows/backup-freshness.yml`：每天检查，最近一次备份超过 26 小时就报警。
- `.github/workflows/backup-restore-test.yml`：每周一把最新的转储恢复进一个临时 Postgres 并做抽查，恢复失败就报警。

从 R2 恢复上传文件和数据库的命令见 `deploy/README-backup.md`。
