---
title: 系统总览
kind: 参考
summary: 线上跑着哪些服务、会话机这一层怎么分、哪些随发版替换，以及 8 组开发文档各管哪个部件。
covers:
  - deploy/compose/
  - deploy/llm-tunnel/
  - backend/app/main.py
  - backend/app/device_connection_app.py
  - backend/app/llm_tunnel_app.py
  - backend/app/forge_events_app.py
  - backend/app/domain/agent/central_provider.py
  - backend/app/domain/agent/executor_transport.py
---

# 系统总览 {#overview}

线上跑着哪些服务、会话机这一层怎么分、哪些随发版替换，以及 8 组开发文档各管哪个部件。

> 讲：服务清单、会话与执行的两层、常驻与替换、三条不变量、部件地图。不讲：每个部件内部怎么实现，按地图里那一页进去。

## 一份后端代码，四个进程 {#one-codebase}

后端镜像只有一个（`ghcr.io/sageseekersociety/cheese/backend:<提交号>`），按不同的启动命令跑成四个进程：主 API（`app.main`）、机器连接服务（`app.device_connection_app`）、模型隧道（`app.llm_tunnel_app`）、代码托管事件中继（`app.forge_events_app`）。各跑什么、为什么分开、端口和路由约定见[后端结构与接口约定](/dev/backend-app#processes)。

机器连接服务和模型隧道单独常驻，是为了发版替换主 API 时，设备链接和远端机器的模型请求都不断。

## 平台主机上还有什么 {#host}

同一台主机上，按 `deploy/compose/` 下的 compose 文件起：

- **前端 nginx**（`frontend` 镜像）：托管单页应用，把 `/api` 反代到主 API；设备与执行的长连接（`/connector/agent`、`/connector/session/<id>/screen`、`/api/topics/<id>/execution/<id>`）去机器连接服务，其余 `/connector/*` 仍由主 API 提供。
- **主机的入口 nginx**：常驻的 `deploy/llm-tunnel/` 那一组，占着主机的 :8081，见下一节。
- **计量代理**（mitmproxy，`deploy/metering-proxy/`）：所有模型流量的出口，见[模型调用流程](/dev/llm)。
- **模型网关**（LiteLLM，`deploy/compose/docker-compose.gateway.yml`）：项目虚拟 key 与预算刹车。独立一套 compose，发版不碰它。
- **网页渲染**（`browser-render`，一个共享的无头 Chromium）、**Office 渲染**（`office-render`，LibreOffice）和**在线编辑器**（`office-editor`，OnlyOffice，可缺省）：给芝士读网页、给房间里显示和编辑 Word 和 PPT，见[房间文件与 Office](/dev/documents)。
- **文档协同**（`collab`，Hocuspocus，`frontend/collab/`）：任务文档、项目总览和资料库文档的实时多人编辑。浏览器经前端 nginx 的 `/collab` 连上来，凭主 API 签的短时票据；它从主 API 读文档、停手几秒后存回去，存回才记一版。芝士和其他写入也经它改文档。随发版一起替换，健康检查和回滚都算它一份。
- **Forgejo**：平台自带的代码托管，账号由平台创建。

数据库是 Postgres，缓存是 Valkey。正式环境用主机外部的数据库，etrip 环境由 `docker-compose.etrip.yml` 在容器里起。

## 「会话机」这一层 {#session-machine}

会话不是一个容器，是分在两处的：

- **会话进程**（对话、记忆、平台工具、聊天收发）落在一台「会话机」上。它落哪台记在 `agent_sessions.runtime_location`（`place()` 是读它的唯一入口）；没有它就没有地点，下一轮重新租，而不是去猜房间上记着什么。
- **干活的那一半**（文件、命令、项目 MCP）落在这一间房租到的工作机器上。那台机器由房间的算力选择定，落到每条会话上就是它的 `work_lease`；一间房只有一条算力选择，几位队友工作在算出来的同一台机器上。

两层什么时候分开、一个工具调用怎么从会话机落到工作机器上、够不着时怎么判，见[执行通道](/dev/execution)。四种机器（本机沙盒容器、自托管设备、云机器、中心会话加执行机）各由哪个 provider 供给、怎么被选中和准备，见[设备与机器接入](/dev/machines#kinds)；「中心会话加执行机」这一种由 `central_provider.py` 供给，跨两台的工具调用由 `executor_transport.py` 的 `RemoteClient` 落地。会话与轮次在库里长什么样，见[会话与轮次](/dev/session)。

## 常驻 vs 随发版替换 {#planes}

发版换掉哪些、哪些必须活过一次发版，分两栏：

- **随发版替换**：主 API、前端 nginx、新开会话用的沙盒镜像、网页渲染与 Office 渲染。
- **常驻，单独发布**：机器连接服务、计量代理、模型网关、事件中继，以及主机入口 nginx。

入口 nginx 是 `deploy/llm-tunnel/` 下的常驻一组（由 `up.sh` 手工起，不经发版脚本）：主机的 :8081 由它永久持有，模型隧道的 WebSocket 转给隧道进程 :8091、代码托管事件转给中继 :8093、设备与执行的长连接转给机器连接服务，其余交给 app-router 再分流到当班的 backend 或 frontend。这样换 backend 容器不会切断机器的模型流量，也没有「后端自己占 :8081」这条路可退。完整的两张表和滚动交接见[部署拓扑](/dev/topology#planes)。

## 主机之外 {#outside}

- **浏览器和桌面端**：只和前端 nginx 打交道。
- **设备与云机器**：用户电脑上的 Go 连接器（`cli/`）和云机器（MicroCloud）经机器连接服务接入，只执行命令、存文件；调模型只发生在会话主机上，走模型隧道。
- **GitHub**：平台 GitHub App 的私钥只在主 API；沙盒要推代码时换一张一小时有效、只限绑定仓库的令牌（`backend/app/domain/agent/github_app.py`）。
- **模型厂商**：只有计量代理和网关持有上游凭证。
- **Cloudflare R2**：数据库备份的异地副本（`deploy/r2-upload.py`）。

## 三条不变的约束 {#invariants}

1. **密钥不下发**。沙盒和远端机器只拿平台签发的短期令牌；上游模型 key 在网关，GitHub 私钥在主 API。
2. **模型流量一个出口**。每个请求先问主 API `/llm/admission`，再决定走哪条路。
3. **发版不断活**。主 API 滚动替换时，正在跑的轮由下一个进程接手，见[部署拓扑](/dev/topology#handover)。

## 部件地图 {#map}

开发文档按部件分成 8 组，各部件一页。下面是每一组里的每一页，一句话说它管什么。

| 组 | 部件 | 一句话 |
|---|---|---|
| 会话与骨架 | [会话与轮次](/dev/session) | 会话与轮次在库里长什么样、后台谁推着走、发版怎么交接 |
| 会话与骨架 | [骨架](/dev/harness) | Claude Code、Codex、pi 三种骨架的统一抽象与各自差异 |
| 会话与骨架 | [提示词注入与上下文管理](/dev/context) | 每一轮拿到哪些上下文、各占多少预算、防注入的边界 |
| 会话与骨架 | [系统提示词参考](/dev/ref-prompt) | 提示词每一块的原文、出现条件和预算（自动生成） |
| 会话与骨架 | [记忆](/dev/memory) | 记忆树的分层、两个作用域、写入与整理 |
| 会话与骨架 | [技能](/dev/skills) | 平台说明库、平台技能、项目技能和分身定义怎么装进会话 |
| 会话与骨架 | [平台工具与会话侧 MCP](/dev/mcp) | 平台工具表怎么变成每种骨架手里的工具 |
| 会话与骨架 | [项目自定义 MCP](/dev/remote-mcp) | 项目声明的 MCP 服务怎么被代连、代桥接 |
| 会话与骨架 | [cheese CLI 原理](/dev/cli) | 沙盒里的 `cheese` 命令和用户电脑上的 `cheesehost` |
| 模型与计费 | [模型调用流程](/dev/llm) | 一次模型请求从出发到拿回 token 的完整路径 |
| 模型与计费 | [计量代理](/dev/metering-proxy) | 模型流量的唯一出口：持有订阅凭证，计量、准入、改道 |
| 模型与计费 | [模型网关](/dev/gateway) | LiteLLM 网关：项目虚拟 key、预算刹车与记账 |
| 模型与计费 | [准入与供给](/dev/admission) | 每个请求能不能跑、走哪条路、用哪个模型名 |
| 模型与计费 | [计费流程](/dev/billing) | 用量怎么计、记在谁头上、用完了怎么办 |
| 机器与执行 | [设备与机器接入](/dev/machines) | 四种机器怎么接进来、出错时怎么办 |
| 机器与执行 | [执行通道](/dev/execution) | 会话进程与工作机器分开时，工具调用怎么落到机器上 |
| 机器与执行 | [话题预览](/dev/preview) | 房间预览怎么托管、怎么鉴权 |
| 机器与执行 | [项目网站](/dev/sites) | 已采纳的网页怎么发布成私有网站 |
| 机器与执行 | [本机目录授权](/dev/local-fs) | 让芝士在用户自己电脑的某个目录里干活 |
| 机器与执行 | [资源回收与磁盘](/dev/cleanup) | 资源目录的回收和主机磁盘的三道回收 |
| 交付与成果 | [任务与工作目录](/dev/tasks) | 一条任务 = 自己的对话 + 实况文档 + 分支 + 工作目录，以及看板与待处理清单 |
| 交付与成果 | [验收与采纳](/dev/accept) | 验收卡的状态机、合并态、采纳即合并与人工放行 |
| 交付与成果 | [代码托管](/dev/forge) | Forgejo 与 GitHub 两个实现、令牌和事件中继 |
| 交付与成果 | [房间文件与 Office](/dev/documents) | 房间文件的版本冲突、模板与 Office 渲染编辑 |
| 交付与成果 | [资料库与产物](/dev/library) | 不在 git 树上的那一半文件：资料库、交付快照、产物清单 |
| 交付与成果 | [看板](/dev/boards) | 项目看板、待我处理与平台统计 |
| 协作 | [团队、项目与成员](/dev/teams) | 团队、入团申请与邀请、项目归属与成员名册 |
| 协作 | [空间与题目](/dev/spaces) | 题目版、成员审批与管理员看板 |
| 协作 | [通知与待办](/dev/notifications) | 通知表、投递账本、三条渠道与待办 |
| 协作 | [反馈](/dev/feedback) | 平台级收件箱：提案卡、可见性与分诊 |
| 协作 | [例行与巡检](/dev/routine) | 房间里的常驻工作：规则、触发与一次执行的一生 |
| 协作 | [集成](/dev/integrations) | 借来的邮箱与飞书账号、密钥、授权与入站 webhook |
| 平台与安全 | [登录与令牌](/dev/auth) | 浏览器存了什么、每次请求带什么、芝士和机器用什么令牌 |
| 平台与安全 | [席位与权限判定](/dev/seats) | 谁能在哪里做什么 |
| 平台与安全 | [平台管理员](/dev/admins) | 后台管理的入口与名单 |
| 平台与安全 | [运行记录](/dev/run-records) | 平台运行中记下的事：不进对话，给现场、状态行和管理后台读 |
| 平台与安全 | [后端结构与接口约定](/dev/backend-app) | 四个进程、路由发现、信封与错误、写面闸门、幂等与归属锁 |
| 平台与安全 | [前端结构](/dev/frontend) | 两个入口、壳与路由、房间两栏、工作面板与质量闸 |
| 部署与运维 | [部署拓扑](/dev/topology) | 两个环境怎么部署，发版换什么、不换什么 |
| 部署与运维 | [数据存在哪](/dev/data) | 每一类数据的位置、谁写、怎么备份 |
| 部署与运维 | [CI 设计](/dev/ci) | 一次改动从推送到上线要过哪些检查 |
| 部署与运维 | [文档站与问芝士](/dev/docs-site) | 文档站怎么构建发布、开发文档怎么只对管理员开放 |
| 部署与运维 | [部署脚本与主机定时任务](/dev/deploy-scripts) | `deploy/` 下的发版脚本、镜像计划与主机定时任务 |
| 参考 | [CLI 与平台工具全表](/dev/ref-cli) | 沙盒命令、平台工具、连接器命令（自动生成） |
| 参考 | [环境变量全表](/dev/ref-env) | 全部环境变量（自动生成） |
| 参考 | [CI 工作流全表](/dev/ref-ci) | 全部 CI 工作流（自动生成） |
