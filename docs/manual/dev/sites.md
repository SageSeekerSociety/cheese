---
title: 项目网站
kind: 参考
summary: 把已采纳的网页发布成私有网站：快照、入口、访问控制和上限。
covers:
  - backend/app/domain/site/
  - backend/app/api/routes/project_sites.py
  - backend/app/api/routes/site_sessions.py
  - backend/app/api/preview_host.py
  - frontend/src/views/SiteOpenView.vue
---

# 项目网站 {#sites}

把项目已采纳版本里的一个静态网站目录，复制成一份平台自己的快照，再用一个隔离的内容域把它按项目成员身份服务出去。

> 讲：快照怎么选、怎么校验、内容域上怎么判身份、cookie 与入口是什么形状。不讲：房间预览与网站这两个源的完整流程（见[预览与项目网站](/dev/preview)），人怎么操作（见[发布网站](/sites)）。

## 发布的是什么：先选目录，再校验 {#publish}

发布会从一个**已采纳的版本**里找候选目录（`publication_source`）：

1. 候选入口是提交里叫 `index.html`、mode 是 `100644`/`100755`、不超过 `MAX_ENTRY_BYTES = 1 MiB` 的文件；它所在目录就是候选目录。
2. 该目录下整包过 `_validate_bundle` 与静态入口检查，任一不过就跳过这个候选。
3. `_static_entry` 是那条「这不是个静态网站」的判据：目录里有 `package.json`，或者入口引用到源码扩展名，就不是静态网站。

入口 HTML 的引用会被解析一遍（`_EntryReferences`）：`base href`、`type=text/babel|jsx` 的源码文件、`script/img/source/video/audio` 的 `src`、`link rel=stylesheet|modulepreload|icon`。`_validate_entry_resources` 据此做两件事：

- 引用到 `.ts` / `.tsx` / `.jsx` / `.vue` / `.scss` / `.sass` / `.less` 这类需要构建的东西 → 抛 `BUILD_REQUIRED`：`当前已采纳版本没有可直接发布的静态网站。需要先准备包含全部资源的静态网站，并提交审阅。`
- 引用到一个包里没有的文件 → `网站缺少资源：{path}。…`；引用试图走出发布目录（`_resolve_resource_path`）同样拒。

路径本身的规矩（`_asset_path`）：不许空段、`.`、`..`、反斜杠、NUL；**除了 `.well-known` 之外不许点开头的文件**（`__pycache__`、`.git` 这类自然被挡在门外）。上限是打包上限而不是上传上限：

| 常数 | 值 | 说明 |
| --- | --- | --- |
| `MAX_SITE_FILES` | 2000 | 超过就拒：「网站最多包含 2000 个文件，总大小不能超过 100 MiB」 |
| `MAX_SITE_BYTES` | 100 MiB | 同上；打包会把不受信任的仓库文件复制进平台存储，所以先分配上限再读 blob |
| `MAX_ENTRY_BYTES` | 1 MiB | 入口 HTML 的大小上限（候选筛选与快照两处都用它） |

目录里有软链接或子模块也拒——那是另一句专门的报错，因为「把它当普通文件」和「跟随它」都不能接受。

## 快照与发布记录 {#snapshot}

- 快照落在 `<workspace_root>/.sites/<项目 id>/<发布记录 id>`（`release_directory`），先写进暂存目录再 `os.replace`，manifest 记每个文件的字节数与 sha256。
- 两条记录：`SiteRelease`（项目 id、`source_revision`、`directory`、`entry_file` 默认 `index.html`、manifest、发布人、发布时间）与 `Site`（项目 id 为主键，指向当前发布记录）。
- `read_release_file` 只服务 manifest 里有的路径，且解析后必须落在发布目录内：发布目录里多出来的文件上不了网，`..` 也出不去。
- `publish_site` 的次序是刻意的：先要 `require_site_access`，再要 `can_publish_site`（`MemberService.manages`，负责人或团队管理员；不是就抛「只有项目负责人或团队管理员可以发布网站」），然后**对项目行加 `SELECT … FOR UPDATE`**——为了连第一次发布也串行化（那时还没有 `Site` 行可锁），最后才比对 `expected_source_revision`：不一致就是「项目已采纳的版本发生变化，请刷新后再发布」。
- 旧的发布记录不删：「发布失败时保留上一版」靠的就是 `Site.current_release_id` 指哪条记录，而且历史记录本身是可回溯的产物。

## 之后采纳的改动不会自动发布 {#revision}

网站认的是它发布时那个 `source_revision`。项目之后又采纳了新版本，网站不会自己跟着变——发布是一个明确的动作。接口每次都把当前的 `source_revision` 和候选目录报给页面，页面把「已发布的版本」和「现在已采纳的版本」摆在一起，冲突就由 `expected_source_revision` 挡下，而不是发布一个中间状态的快照。

## 内容域与鉴权 {#origin}

内容域是隔离出来的：`content_origin(project_id)` = `<项目 id 的 32 位 hex>.<SITES_DOMAIN>[:端口]`。三道部署前提，任一不满足就抛 `ValidationError`，页面据此给出「暂不可用」：

| 前置 | 不满足时 |
| --- | --- |
| `SITES_DOMAIN` 配了且形状合法 | `Site 托管域名尚未配置` |
| `SITES_SCHEME` 是 https（localhost 允许 http） | `Site 托管需要 HTTPS` |
| 与平台域名不同域 | `Site 托管域名必须与平台域名隔离` |

`SiteHostMiddleware` **在平台任何中间件与路由之前**认领整个内容域：host 等于域或它的子域就拦下；子域标签必须是 32 位十六进制（否则 404）；websocket 一律 1008 关闭（这里不提供服务端推送的入口）。拦不下的请求原样交给平台应用。

身份是「票据换 cookie」两段：

| 阶段 | 载体 | 期限 |
| --- | --- | --- |
| 票据（grant） | 前端从平台拿到，POST 到 `/_cheese/session`（`AUTH_PATH`） | 30 秒（`GRANT_TTL`） |
| 会话（session） | cookie `__Host-cheese-site`（https；http 本地是 `cheese-site-local`） | 8 小时（`SESSION_TTL`） |

换票这一跳卡得很紧：只收 POST（其余 405），`Origin` 必须就是前端源（否则 403），正文上限 8 KiB（超了 413），`path` 必须 `/` 开头、不以 `//` 开头、不含反斜杠与控制字符（否则 400）。两张 token 都是 HS256，`aud` 绑到该项目的内容域，密钥是 `HMAC(jwt_secret, "cheese:site-read:v1")`，必须带齐 `sub`/`project`/`aud`/`type`/`iat`/`exp`。

- 没有有效 cookie 时：**导航请求**（`Sec-Fetch-Mode: navigate` 的 GET）303 跳到 `{frontend_url}/sites/{项目 id}?path=…`，让人去平台登录；其余请求 401「Sign in through Cheese to open this Site」。
- 有 cookie 之后才查身份与发布记录：`require_site_access` 认项目负责人、项目成员、团队成员三种，都不是就 404 `Site not found`（不是 403——403 会证实这个项目有网站）；没有当前发布记录也是 404 `Site unavailable`。
- 服务文件时：`service-worker: script` 的请求一律 403（成员身份被撤销之后，一个已经装上的 Service Worker 还能持续拦截，所以这里不给它脚本，CSP 里也写了 `worker-src 'none'`）；路径以 `/` 结尾就补 `index.html`；目录存在而文件不存在时 307 补一个斜杠重定向；MIME 按扩展名猜；显式设 `Content-Length`。
- 每个响应都带一副私有头（`_private`）：`Cache-Control: no-store`、`Referrer-Policy: no-referrer`、`X-Content-Type-Options: nosniff`，以及 `sandbox allow-scripts allow-same-origin …; worker-src 'none'; object-src 'none'; frame-ancestors 'self'` 的 CSP。

## 入口与接口 {#api}

| 接口 | 说明 |
| --- | --- |
| `GET /projects/{id}/site` | 当前 `source_revision`、候选目录、`can_publish`、已发布的 `site`；`content_origin` 抛错或没有候选时给 `unavailable_reason` |
| `POST /projects/{id}/site` | 入参 `directory`（≤1024）与 `expected_source_revision`（`^[0-9a-f]{40}$` 或 64 位）；响应前显式提交 |
| `POST /projects/{id}/site-session` | 返回 `{url: content_origin + "/_cheese/session", grant: …}`（`purpose=site-grant`，30 秒），响应 `Cache-Control: no-store`；没有已发布网站时 404「暂无已发布的网站」 |

前端入口是 `/sites/:projectId`（`SiteOpenView.vue`）：grant 用一个隐藏表单 POST 过去，不走 URL，免得它进历史记录、Referer 和日志；`path` 从查询串原样带过去。

## 与房间预览的关系 {#preview}

两者共用 `content_origin`，预览只是把项目 hex 换成 `preview-<话题 hex>`（`preview_host.preview_origin`，`replace("://", "://preview-", 1)`），cookie 名字换一套（`__Host-cheese-preview` / `cheese-preview-local`），并且**支持 websocket**（预览要能连正在跑的应用），房间文件走 `/_cheese/room/`。cookie 属性的差别也有原因：预览在 https 下要 `SameSite=None` 加 `Partitioned`（Python 3.11 的 cookie API 还没有这个 flag，所以是拼上去的），网站用的是 `SameSite=Lax` 不加 Partitioned。两套源都挂在 `main.py` 上。完整流程见[预览与项目网站](/dev/preview)。

## 边界与坑 {#traps}

- `SITES_DOMAIN` 没配时不是「网站坏了」，是 `content_origin` 抛 `ValidationError`：`GET /projects/{id}/site` 把它放进 `unavailable_reason`，页面显示不可用，发布这条路自然走不通。
- 校验与解析都发生在服务端，页面只负责展示候选；一个「能选」的候选不等于是能发布的——发布时还会重算一遍 revision 与快照。
- 发布目录里手动塞进去的文件不会被服务：manifest 就是白名单。
