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

```demo-flow
title: 三张牌怎么换：凭证、cookie、房间访问权
note: 左边切一种人，看请求在哪一步被挡。点某一步可以钉住它。
actors:
  - key: browser
    label: 浏览器
    sub: 用户这边
  - key: platform
    label: 平台
    sub: 前端与主 API
  - key: host
    label: 预览内容主机
    sub: preview-<话题 id>.专用域
  - key: machine
    label: 工作机器
    sub: 会话所在那台
routes:
  - key: normal
    label: 私有房间里的成员
    tone: ok
    note: 手里已经有一张换好的 cookie
    result: 内容一路到浏览器：cookie 8 小时有效，中间每个请求都重查过房间访问权。
  - key: removed
    label: 被移出房间的人
    tone: bad
    note: 他手里那张 cookie 还没过期 —— 人是刚被移出房间的
    result: 换 cookie 那一步照旧（cookie 已经在手里），下一个请求就被挡：每次请求都重查房间访问权，被移出就查不到，回 404。cookie 还在，用不了。
  - key: expired
    label: 凭证过期
    tone: bad
    note: 从点开到换 cookie 之间拖过了 30 秒
    result: 换 cookie 被拒（401）。凭证 30 秒有效，回平台重新领一张，重来一次。
steps:
  - from: browser
    to: platform
    label: 点开预览
    desc: 平台先看这个人能不能进这个房间（私有房间要求是成员），能就签一张预览凭证。
    ref: GRANT_TTL = 30
    link: /dev/preview#grant
  - from: browser
    to: host
    label: 用凭证换 cookie
    desc: POST /_cheese/session，带凭证和要去哪一页，Origin 必须是平台。内容主机验签、再查一次房间访问权，通过就回一个 HttpOnly cookie。
    ref: SESSION_TTL = 8 * 3600
    routes: normal, removed
  - from: host
    to: browser
    label: 凭证过期，换不到 cookie
    desc: 凭证 30 秒有效，过期就拒（401），什么 cookie 都不发。
    ref: preview_host.py
    block: true
    routes: expired
  - from: browser
    to: platform
    label: 回平台重签一张
    desc: 预览入口页还在，重签一张再来一次；这个人还是成员就没影响。
    routes: expired
  - from: host
    to: platform
    label: 每次请求都重查房间访问权
    desc: 不是只在换 cookie 时查一次：每个 HTTP 请求都重新验 cookie、重查这个人现在还能不能进这个房间。
    ref: require_preview_access
    routes: normal, removed
  - from: host
    to: browser
    label: 人不在房间里了
    desc: 换 cookie 时他是成员，被移出之后下一个请求就查不到 —— 回 404，静态内容和 WebSocket 都上不来。
    block: true
    routes: removed
  - from: host
    to: machine
    label: 取文件或转发请求
    desc: 静态预览读工作区里选中的文件；运行中的应用（cheese serve）把 HTTP 原样转发过去。
    ref: relay_http
    routes: normal
  - from: machine
    to: browser
    label: 内容到达
    desc: 这一路上内容域只认那张 cookie，平台凭证没有出现在内容域上。
    routes: normal
  - from: browser
    to: host
    label: WebSocket 也连上来
    desc: 预览里的应用开 HMR 连接：同样先验 cookie 和 Origin，连上之后最晚在 cookie 到期时断开。
    ref: preview_host.py，超时 = cookie 的 exp
    routes: normal
```

## 静态预览与运行中的应用 {#kinds}

- 静态预览读选中文件所在目录，相对路径只能在这个目录里，拒绝隐藏文件和逃出目录的软链接。
- 运行中的应用（`cheese serve`）保留原始的 HTTP 和 WebSocket 路径，要求开发机器和服务一直在线。

## 发布快照 {#sites}

发布把项目已采纳版本里被跟踪的文件复制成一个快照，存在工作区卷的 `.sites/<项目 id>/<发布记录 id>`。稳定入口是平台上的 `/sites/<项目 id>`：先让访问者登录，再打开内容源上的当前快照。之后采纳的改动不会自动发布；发布失败时保留上一版。上限 2000 个文件、100MB，入口 HTML 不超过 1MB。

## 部署前提 {#deploy}

`SITES_DOMAIN` 要配一个平台可注册域之外的专用域名，并配好泛解析和泛证书。网关把这个域名的所有路径原样转给主 API，保留 `Host`，不加也不去掉 `/api`。域名没准备好时 `SITES_DOMAIN` 留空，交付页会显示托管不可用。
