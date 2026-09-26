---
title: 预览与项目网站
kind: 流程
summary: 房间里的预览和项目网站怎么托管、怎么鉴权。
covers:
  - backend/app/api/preview_host.py
  - backend/app/domain/site/
---

# 预览与项目网站 {#preview}

房间里的预览和项目网站怎么托管、怎么鉴权。

> 讲：两个独立源、凭证交换、发布快照。不讲：用户怎么发布网站，见使用文档[发布网站](/sites#sites)。

## 两个独立的源 {#origins}

| | 地址 | 内容 |
|---|---|---|
| 话题预览 | `preview-<话题 id>.<SITES_DOMAIN>` | 选中的文件或正在运行的应用，随工作区变化 |
| 项目网站 | `<项目 id>.<SITES_DOMAIN>` | 发布的那个快照 |

内容域名和平台域名不共用可注册域，平台 cookie 永远不会发到内容域上。预览和网站的内容里拿不到任何平台凭证（`backend/app/api/preview_host.py`）。

## 凭证怎么换 {#grant}

1. 平台签一张 30 秒有效的预览凭证。
2. 内容主机用它换一个 8 小时的 cookie，HttpOnly。https 部署下还带 Secure、SameSite=None 和 Partitioned；项目网站那份是 SameSite=Lax，不带 Partitioned。话题预览的实现在 `backend/app/api/preview_host.py`，项目网站在 `backend/app/domain/site/hosting.py`。
3. 之后每个 HTTP 请求和 WebSocket 连接都重新检查房间访问权：私有房间要求是房间成员。WebSocket 最晚在会话到期时断开。

在预览里运行的应用可以正常用自己的 cookie 登录：这些 cookie 会被改写成只属于该主机的 Partitioned cookie；应用自己的 Bearer 鉴权原样转发。

## 静态预览与运行中的应用 {#kinds}

- 静态预览读选中文件所在目录，相对路径只能在这个目录里，拒绝隐藏文件和逃出目录的软链接。
- 运行中的应用（`cheese serve`）保留原始的 HTTP 和 WebSocket 路径，要求开发机器和服务一直在线。

## 发布快照 {#sites}

发布把项目已采纳版本里被跟踪的文件复制成一个快照，存在工作区卷的 `.sites/<项目 id>/<发布记录 id>`。稳定入口是平台上的 `/sites/<项目 id>`：先让访问者登录，再打开内容源上的当前快照。之后采纳的改动不会自动发布；发布失败时保留上一版。上限 2000 个文件、100MB，入口 HTML 不超过 1MB。

## 部署前提 {#deploy}

`SITES_DOMAIN` 要配一个平台可注册域之外的专用域名，并配好泛解析和泛证书。网关把这个域名的所有路径原样转给主 API，保留 `Host`，不加也不去掉 `/api`。域名没准备好时 `SITES_DOMAIN` 留空，交付页会显示托管不可用。
