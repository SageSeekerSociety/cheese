---
title: 任务 → 分支 → PR → 验收合并
kind: 流程
summary: 一条活从开卡到合进主干。
covers:
  - backend/app/domain/review/pr_publish.py
  - backend/app/api/routes/accept.py
  - backend/app/domain/agent/github_app.py
  - backend/app/forge_events_app.py
---

# 任务 → 分支 → PR → 验收合并 {#delivery}

一条活从开卡到合进主干。

> 讲：交付链路上的每一步和它的检查。不讲：验收界面怎么用，见使用文档[验收与采纳](/accept#accept)。

## 代码在哪 {#forge}

每个项目选一个代码托管：新项目默认用部署自带的 Forgejo，也可以绑定 GitHub。绑定 GitHub 的项目以 GitHub 为准。任务机器直接克隆、提交、推送到那里；够不着的机器走平台的认证中转。主 API 不为读取源码、合并或代推维护本地仓库。

推 GitHub 用的令牌是平台 GitHub App 签发的安装令牌，一小时有效、只限绑定的仓库；App 私钥只在主 API（`github_app.py`）。

## 从任务到验收卡 {#flow}

1. 任务声明自己的分支和基准分支，机器推上第一批改动。
2. `pr_publish.sweep_draft_prs` 发现分支领先于基准分支，为这个任务开一个 draft PR；之后的推送更新同一个 PR。
3. `cheese_ready` 去掉 draft 状态。递验收卡也会让 PR 变成可评审；只标记可评审不会生成验收卡。
4. 验收面板显示改动、检查和评审状态。芝士自己跑检查；托管平台上的 CI 由那边的 runner 跑，平台只读结果。
5. 采纳时检查评审人、需要的采纳人数、项目策略，以及浏览器里显示的那一版，然后调用托管平台的合并接口。
6. 合并成功就记录交付并关闭任务，房间继续可用。

一个任务只有一个 PR（`Task.pr_number`）。改验收卡不会新开 PR；`cheese push-fix` 把新提交推到同一个 PR。

## 合并规则 {#policy}

- 托管平台自己有分支保护时，以它的结论为准；没有时用项目的 `branch_protection` 设置。托管一个项目不要求改它的仓库设置。
- 必需检查可以按改动路径限定。缺失或还在跑的必需检查会挡住采纳。
- 采纳、强制合并、开启自动合并都带着浏览器里显示的那一版。服务端拿它和托管平台上的当前版本比对，新推上来的提交不能悄悄替换已经评审过的内容。
- 强制合并走 `POST /accept-cards/{id}/merge-anyway`，要求在项目的越过名单里（没配置时默认是项目所有者和团队的所有者、管理员），并记下操作人、检查状态和理由。

## 仓库变动怎么回来 {#events}

GitHub App 的 webhook 打到代码托管事件中继（`forge_events_app.py`），中继只把「哪个仓库变了」经 WebSocket 推给订阅它的部署，不转发 webhook 内容和令牌。部署断开期间丢的事件，靠定期对账补回。
