---
title: 登录与令牌
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

刷新令牌每次刷新都轮换。cookie 带 `Secure`（开发和测试环境除外）和 `SameSite=Lax`，并且带 `Max-Age`：否则浏览器关掉会话就删掉它，而 `localStorage` 里的访问令牌还在，下一次刷新就会把用户登出（`backend/app/api/routes/users.py`）。

## 芝士和机器 {#agents}

- **会话令牌**：每个会话一张平台签发的短期令牌（`CHEESE_TOKEN`），声明里带项目、话题和作者。它用来调用平台 API、通过模型准入，也用来换 GitHub 安装令牌。
- **机器令牌**：连接器登录后得到自己的凭证；远端机器调用模型只带这张令牌，由平台换成项目的虚拟网关 key。
- **上游凭证**：模型厂商 key 在网关，GitHub App 私钥在主 API，都不下发。

## 内容域 {#content}

预览和项目网站用单独的内容域和各自的 cookie，平台令牌不会到达那里，见[预览与项目网站](/dev/preview#grant)。
