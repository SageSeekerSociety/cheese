---
title: 登录与令牌
kind: 参考
summary: 浏览器里存了什么、每次请求带什么，以及芝士和机器用什么令牌。
covers:
  - backend/app/api/routes/users/
  - backend/app/core/sandbox_auth.py
  - backend/app/api/preview_host.py
---

# 登录与令牌 {#auth}

浏览器里存了什么、每次请求带什么，以及芝士和机器用什么令牌。

> 讲：几种令牌和它们的边界。不讲：谁能做什么，见[席位与权限判定](/dev/seats)。

## 浏览器 {#browser}

| 令牌 | 存在哪 | 有效期 | 发给谁 |
|---|---|---|---|
| 访问令牌 | `localStorage` | 15 分钟（`access_token_expires_seconds`） | 每个 API 请求的 `Authorization` 头 |
| 刷新令牌 | HttpOnly cookie `cheese_refresh` | 30 天（`refresh_token_expires_seconds`） | 只发给登录路由 `/api/users/auth` |
| 受信设备 | HttpOnly cookie `cheese_trusted_device` | 由授予时决定 | 只发给登录路由，用来跳过两步验证 |

刷新令牌每次刷新都轮换。cookie 带 `Secure`（开发和测试环境除外）和 `SameSite=Lax`，并且带 `Max-Age`：否则浏览器关掉会话就删掉它，而 `localStorage` 里的访问令牌还在，下一次刷新就会把用户登出（`backend/app/api/routes/users/auth.py`）。

用 GitHub 登录经过 cheesex-app 的用户授权。登录回调保存一条 `github_app` 用户连接；连接项目仓库时，平台用这条连接确认安装和仓库权限，任务负责人署名也读取同一条连接。用户授权过期且无法刷新时，重新用 GitHub 登录。GitHub App 的用户授权不请求 OAuth scopes；读取已验证的邮箱依赖 App 的 `Email addresses: Read-only` 权限。

从 app 内（账号安全、项目设置的「仓库与署名」、任务里 PR 下方的署名提示）连接或重新授权 GitHub 是连接操作，不是登录。前端用当前知是会话取得短时、一次性的意图（`POST /users/auth/oauth/github-app/intent`，带上回到哪一页；桌面 app 发起时带 `X-Cheese-App`）。打开意图的浏览器必须持有同一账号仍有效的登录（登录路由读它的刷新 cookie），否则返回 `GITHUB_CONNECT_SIGNED_OUT`，意图不被消费：转发出去的链接在别的浏览器里什么也连不上。之后的 `state` 由 `OAUTH_STATE` cookie 绑定到这个浏览器。回调里：GitHub 身份已属于该账号就更新连接令牌；该账号还没有 GitHub 连接、这个身份也未被关联，就直接连接到该账号，不验证密码；身份已属于其他账号时返回 `ALREADY_LINKED`；该账号已连接另一个 GitHub 身份时返回 `ACCOUNT_MISMATCH`。这条路径不签发登录会话：成功后回到发起的页面，桌面 app 发起的结果经 `/account/to-app` 交回 app（`app/api/app_return.py`）。登录页不带意图，仍走决定页和已有账号的密码验证。

## 芝士和机器 {#agents}

- **会话令牌**：每个会话一张平台签发的短期令牌（`CHEESE_TOKEN`），声明里带项目、频道和作者。它用来调用平台 API、通过模型准入，也用来换 GitHub 安装令牌。
- **机器令牌**：连接器登录后得到自己的凭证；远端机器调用模型只带这张令牌，由平台换成项目的虚拟网关 key。令牌只在交给机器的那一刻生成一次：人接入的机器在批准后第一次轮询时拿到，同一个设备码再轮询什么也拿不到；平台自己开的云机器每次装机时换一张新的（`DeviceService.issue_token`、`bootstrap_token`）。库里只存它的 SHA-256 摘要，读得到数据库也冒充不了任何一台机器。
- **上游凭证**：模型厂商 key 在网关，GitHub App 私钥在主 API，都不下发。

## 内容域 {#content}

预览和项目网站用单独的内容域和各自的 cookie，平台令牌不会到达那里，见[频道预览](/dev/preview#grant)。
