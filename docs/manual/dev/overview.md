---
title: 系统总览
kind: 参考
summary: 线上跑着哪些服务、各自管什么，以及它们之间怎么通信。
covers:
  - deploy/compose/
  - backend/app/main.py
  - backend/app/device_connection_app.py
  - backend/app/llm_tunnel_app.py
  - backend/app/forge_events_app.py
---

# 系统总览 {#overview}

线上跑着哪些服务、各自管什么，以及它们之间怎么通信。

> 讲：服务清单、谁连谁、哪些随发版替换。不讲：每条流程的细节，见本栏「关键流程」各篇。

## 一份后端代码，四种启动方式 {#one-codebase}

后端镜像只有一个（`ghcr.io/sageseekersociety/cheese/backend:<提交号>`），按不同的启动命令跑成四个服务：

| 服务 | 启动入口 | 管什么 | 发版时 |
|---|---|---|---|
| 主 API | `app.main` | 页面和接口、投递与调度一轮、会话、计费入账 | 替换 |
| 机器连接服务 | `app.device_connection_app` | 设备和终端的长连接 WebSocket | 不重启 |
| 模型隧道 | `app.llm_tunnel_app` | 远端机器的模型流量；只校验令牌签名，不连数据库 | 不重启 |
| 代码托管事件中继 | `app.forge_events_app` | 接 GitHub App 的 webhook，经 WebSocket 推给各部署 | 单独部署 |

机器连接服务和模型隧道单独常驻，是为了发版替换主 API 时，设备链接和远端机器的模型请求都不断。

## 平台主机上还有什么 {#host}

同一台主机上，按 `deploy/compose/` 下的 compose 文件起：

- **前端 nginx**（`frontend` 镜像）：托管单页应用，把 `/api` 反代到主 API、`/connector` 反代到机器连接服务。
- **会话沙盒容器**：不是 compose 服务，而是主 API 通过 `docker.sock` 起的兄弟容器（`SANDBOX_IMAGE`），里面跑 Claude Code、Codex 或 Pi。
- **计量代理**（mitmproxy，`deploy/metering-proxy/`）：所有模型流量的出口，见[模型调用流程](/dev/llm)。
- **模型网关**（LiteLLM，`deploy/compose/docker-compose.gateway.yml`）：项目虚拟 key 与预算刹车。独立一套 compose，发版不碰它。
- **网页渲染**（`browser-render`，一个共享的无头 Chromium）和 **Office 渲染**（`office-render`，LibreOffice）：给芝士读网页、给房间里显示 Word 和 PPT。
- **Forgejo**：平台自带的代码托管，账号由平台创建。

数据库是 Postgres，缓存是 Valkey。正式环境用主机外部的数据库，etrip 环境由 `docker-compose.etrip.yml` 在容器里起。

## 主机之外 {#outside}

- **浏览器和桌面端**：只和前端 nginx 打交道。
- **设备与云机器**：用户电脑上的 Go 连接器（`cli/`）和云机器（MicroCloud）经机器连接服务接入，模型流量走模型隧道。
- **GitHub**：平台 GitHub App 的私钥只在主 API；沙盒要推代码时换一张一小时有效、只限绑定仓库的令牌（`backend/app/domain/agent/github_app.py`）。
- **模型厂商**：只有计量代理和网关持有上游凭证。
- **Cloudflare R2**：数据库备份的异地副本（`deploy/r2-upload.py`）。

## 三条不变的约束 {#invariants}

1. **密钥不下发**。沙盒和远端机器只拿平台签发的短期令牌；上游模型 key 在网关，GitHub 私钥在主 API。
2. **模型流量一个出口**。每个请求先问主 API `/llm/admission`，再决定走哪条路。
3. **发版不断活**。主 API 滚动替换时，正在跑的轮由下一个进程接手，见[部署拓扑](/dev/topology#handover)。
