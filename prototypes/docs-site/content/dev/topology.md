---
title: 部署拓扑
---

# 部署拓扑 {#topology}

测试环境和正式环境各怎么部署，发版时换什么、不换什么。

> 讲：镜像、发版流程、滚动交接、正式环境审批。不讲：每个服务做什么，见[系统总览](/dev/overview)。

## 两个环境 {#envs}

| | 测试环境（dev） | 正式环境（RUC） |
|---|---|---|
| 触发 | main 每次合并后自动部署 | 发布 GitHub Release 或手动指定提交，并经人工批准 |
| 工作流 | `.github/workflows/deploy-dev.yml` | `.github/workflows/deploy-prod.yml` |
| 执行 | 主机上的自托管 runner 运行 `deploy/deploy-docker.sh` | 同一个脚本，runner 标签 `cheese-prod` |

两个环境互不继承状态。dev 从 main 持续部署，所以一次合并就是一次上线（仓库 `CLAUDE.md`「Production changes go through CI/CD」）。

## 镜像按提交号构建 {#images}

`.github/workflows/build.yml` 每个提交构建一组镜像：`backend`、`frontend`、`sandbox`、`browser-render`、`office-render`、`gateway`、`metering-proxy`、`private-executor`，都以提交号为标签；没有变化的镜像直接给旧镜像打上新的提交号标签。发版就是 `deploy-docker.sh <提交号>`：拉取这组镜像、跑数据库迁移、替换主 API 和前端，健康检查不过就回滚到上一组镜像引用。

## 随发版替换的 vs 常驻的 {#planes}

| 随发版替换 | 常驻，单独发布 |
|---|---|
| 主 API、前端 nginx | 机器连接服务（`release-device-connection.yml`） |
| 会话沙盒镜像（新开的会话用新镜像） | 模型隧道与主机入口 nginx（`deploy/llm-tunnel/up.sh`） |
| 网页渲染、Office 渲染 | 计量代理（`release-metering-proxy.yml`）、模型网关（`release-gateway.yml`） |

上传文件、工作区、会话记录都在主机目录里挂载进容器，发版不会动它们，见[数据存在哪](/dev/data)。

## 滚动发版时正在跑的轮怎么办 {#handover}

滚动发版时新旧两个主 API 进程会同时连着同一个数据库。「谁在跑哪些轮、监听哪些会话」这类只能由一个进程负责的工作，用 Postgres 的会话级 advisory lock 决定归属（`backend/app/core/ownership.py`）：旧进程退出时释放锁，新进程接过去。旧进程关闭前最多等 `HANDOVER_TIMEOUT_S`（20 秒）把还在路上的消息交完，所以 compose 给主 API 留了 60 秒的停止宽限期。

## 日志 {#logs}

所有容器的日志写到主机的 journald，而不是容器目录。发版删掉旧容器后，旧容器的日志仍能用 `journalctl CONTAINER_NAME=cheese-backend-1` 读到。
