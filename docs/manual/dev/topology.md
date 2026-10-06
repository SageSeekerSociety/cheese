---
title: 部署拓扑
kind: 流程
summary: 测试环境和正式环境各怎么部署，发版时换什么、不换什么。
covers:
  - .github/workflows/build.yml
  - .github/workflows/deploy-dev.yml
  - .github/workflows/deploy-prod.yml
  - deploy/deploy-docker.sh
  - backend/app/core/ownership.py
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

`.github/workflows/build.yml` 每个提交构建一组镜像：`backend`、`frontend`、`sandbox`、`browser-render`、`office-render`、`gateway`、`metering-proxy`、`private-executor`、`collab`，都以提交号为标签；没有变化的镜像直接给旧镜像打上新的提交号标签。发版就是 `deploy-docker.sh <提交号>`：拉取这组镜像、跑数据库迁移、替换主 API、文档协同服务和前端，健康检查不过就回滚到上一组镜像引用。

## 随发版替换的 vs 常驻的 {#planes}

| 随发版替换 | 常驻，单独发布 |
|---|---|
| 主 API、前端 nginx | 机器连接服务（`release-device-connection.yml`） |
| 会话沙盒镜像（新开的会话用新镜像） | 模型隧道与主机入口 nginx（`deploy/llm-tunnel/up.sh`） |
| 网页渲染、Office 渲染 | 计量代理（`release-metering-proxy.yml`）、模型网关（`release-gateway.yml`） |
| | 事件中继（`deploy/forge-events/compose.yml`，镜像单独钉住） |

上传文件、工作区、会话记录都在主机目录里挂载进容器，发版不会动它们，见[数据存在哪](/dev/data)。

## 滚动发版时正在跑的轮怎么办 {#handover}

滚动发版时新旧两个主 API 进程会同时连着同一个数据库。「谁在跑哪些轮、监听哪些会话」这类只能由一个进程负责的工作，用 Postgres 的会话级 advisory lock 决定归属（`backend/app/core/ownership.py`）：旧进程退出时释放锁，新进程接过去。旧进程关闭前最多等 `HANDOVER_TIMEOUT_S`（20 秒）把还在路上的消息交完、等回执收齐，所以 compose 给主 API 留了 60 秒的停止宽限期。

```demo-flow
title: 滚动发版时正在跑的轮怎么办
note: 同一时刻新旧两个进程连着同一个库。谁在跑哪些轮，由 Postgres 的一把会话级锁说了算。
actors:
  - key: old
    label: 旧主 API 进程
    sub: 要被换掉的
  - key: new
    label: 新主 API 进程
    sub: 刚起来，还在等
  - key: db
    label: Postgres
    sub: 归属锁、轮的区间
  - key: sess
    label: 会话
    sub: 沙盒里的 agent
routes:
  - key: normal
    label: 一次正常的交接
    tone: ok
    note: 话都说完了，锁交出去
    result: 正在跑的轮一个没丢：旧进程只是不再等它，轮留给了新进程；空档里会话说的话，新进程重新订阅之后都补回来。
  - key: timeout
    label: 20 秒到了还有话没说完
    tone: warn
    note: prompt 一直没送到会话
    result: 旧进程不再等，那几轮在新进程眼里等于没送到 —— 它重发一次。这正是 20 秒存在的理由：宁可重发，也不能让它悬着。
  - key: crash
    label: 旧进程直接崩了
    tone: warn
    note: 没来得及交接
    result: 连接一断锁自己就没了，新进程下一次试探就拿到手 —— 不需要谁先发现旧进程死了。代价是它不知道哪些话已经送到，没收到回执的轮再发一次。
steps:
  - from: sess
    to: old
    label: 一轮正在跑
    desc: agent 在沙盒里干活，进程在听这个会话的每一句话。
  - from: new
    to: db
    label: 起来了，先要这把锁
    desc: 新进程先试一次会话级 advisory lock；这把锁是一个进程在不在主的唯一凭据。
    ref: OWNER_LOCK = 0x636865657365
  - from: db
    to: new
    label: 锁在旧进程手里
    desc: 没要着就每秒再试一次，不抢、不报错，等旧进程自己放。
    routes: normal, timeout, crash
  - from: old
    to: db
    label: 收到停止信号，先停手
    desc: 不再接新的轮，也不再监听任何会话 —— 这两件事只能有一个进程做，从这一刻起都归新进程。
    routes: normal, timeout
  - from: old
    to: db
    label: 等还在路上的 prompt
    desc: 已经发出去的话等它到、等回执收齐，最多 20 秒（handover_timeout_s）。
    ref: settle_deliveries
    routes: normal, timeout
  - from: old
    to: db
    label: 交完，放掉锁
    desc: 停止等的只是这个进程：agent 还在沙盒里跑，轮的区间留给下一个进程接手。然后锁释放。
    ref: let_go
    routes: normal
  - from: old
    to: db
    label: 20 秒到了，还有轮没交完
    desc: 不再等下去，日志里记下是哪几轮；它们会被新进程当成没送到。
    ref: handover_timeout_s = 20.0
    block: true
    routes: timeout
  - from: db
    to: new
    label: 旧进程的连接断了，锁自己回来了
    desc: 进程死了连接跟着断，会话级锁随之释放 —— 没人要先发现它死了。
    routes: crash
  - from: db
    to: new
    label: 锁空了，新进程接管
    desc: 这次试探成功，它成了唯一的主人，接着开始接活。
  - from: new
    to: sess
    label: 重新订阅还在跑的会话
    desc: 把自己那些还在跑的会话重新订阅回来，接着听它们说话 —— 新进程不知道有哪些会话在跑，只能从库里认。
    ref: recover_sessions
  - from: sess
    to: new
    label: 空档里说的话补上
    desc: 交接这段时间会话里说的话，落库的都读回来；开过头的轮收掉（resume_orphans），消息收了却没开跑的轮补上（resume_lost_messages）。
    link: /dev/turn#resume
resident:
  - label: 机器连接服务（release-device-connection.yml）
  - label: 模型隧道与主机入口 nginx
  - label: 计量代理与模型网关
  - label: 事件中继
```

上面这一层跟着发版换，下面那条常驻带不动：机器连接服务、隧道入口、计量代理与网关、事件中继都单独发布，机器上的连接在发版期间一直连着。

## 日志 {#logs}

所有容器的日志写到主机的 journald，而不是容器目录。发版删掉旧容器后，旧容器的日志仍能用 `journalctl CONTAINER_NAME=cheese-backend-1` 读到。有 app-router 的盒子（dev）上主 API 在两个槽位之间轮换，容器名每次发版在 `cheese-backend-1` 和 `cheese-backend-b-1` 之间换，两个都要查；当前在跑的是哪个，问 `deploy/app-container.sh backend`。
