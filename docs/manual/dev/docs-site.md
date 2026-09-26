---
title: 文档站与问芝士
kind: 流程
summary: 文档站怎么构建和发布、开发文档怎么只对平台管理员开放，以及问芝士怎么只根据文档回答、怎么防滥用。
covers:
  - docs/site/
  - backend/app/domain/docs_site/
  - backend/app/api/routes/docs_site.py
  - frontend/nginx.conf
---

# 文档站与问芝士 {#docs-ask}

文档站怎么构建和发布、开发文档怎么只对平台管理员开放，以及问芝士怎么只根据文档回答、怎么防滥用。

> 讲：这个站本身的架构。不讲：怎么写文档页，见 `docs/manual/README.md`。

## 构建与发布 {#build}

`docs/site/build.mjs` 把 `docs/manual/` 下的 Markdown 构建成 `frontend/public/docs/`，随前端镜像一起发布，由前端 nginx 在 `/docs/` 下提供。

- 每个地址都是一个预渲染好的 HTML 文件，`src/app.js` 只负责交互：搜索、问芝士、深浅色、首页动效。
- 站点结构写在 `docs/site/src/structure.mjs`，这是导航和分组的唯一来源。
- 开发文档每页开头必须声明类型、摘要和涉及的代码，缺字段或代码路径不存在时构建失败；站内链接和锚点也必须全部有效。
- 构建同时产出：公开和开发两份搜索索引、`llms.txt` 与每页的 `.md` 原文、全部文档的压缩包、更新日志 RSS，以及问芝士用的 `ask-index.json`（只含公开页）。
- 参考页（CLI、环境变量、CI）和索引页由构建时读代码生成，不手写。

## 开发文档只给平台管理员 {#dev-access}

静态文件读不到浏览器 `localStorage` 里的访问令牌，所以换成一张 cookie：

1. 管理员打开 `/docs/dev/...` 时，nginx 先通过 `auth_request` 问后端 `GET /api/docs/dev-access/check`。
2. 没有有效 cookie 时，nginx 返回提示页。页面用访问令牌调用 `POST /api/docs/dev-access`；后端确认调用者是平台管理员后，签发名为 `cheese_docs_dev` 的 cookie：只对 `/docs/dev` 路径有效，HttpOnly、Secure、SameSite=Strict，有效期 1 小时（`DOCS_DEV_SESSION_SECONDS`）。
3. 之后每个文件（页面、搜索索引、`.md` 原文、架构图）都会再校验一次：签名、受众和有效期都对，并且持有人仍在管理员名单里。名单最多缓存 60 秒，所以被移出管理员的人一分钟内就会失去访问。

`/docs/dev/` 用 `location ^~` 声明，这样对 `.md` 的正则规则不会绕过鉴权。

## 问芝士 {#ask}

`POST /api/docs/ask`，需要登录，以 server-sent events 流式返回：`sources`（这次回答可以引用的段落）、`delta`（文字）、`error`、`done`。

1. **限流**（`limits.py`，Valkey）：每人每小时 20 次、每天 100 次；同一个人同一时间只能有一个问题在答；每个进程同时最多答 8 个，满了立刻返回「忙」，不排队。Valkey 不可用时拒绝，不放行。
2. **检索**（`retrieval.py`）：从前端取 `ask-index.json`，每 10 分钟刷新，取不到时沿用上一份。用 BM25 打分，英文按词切、中文按两字切；读者正在看的那一页加权。
3. **找不到就不问模型**：最高分低于 `MIN_SCORE` 时，直接回答「文档里没有讲到」，不调用模型。这一步既防止编造，也让与知是无关的请求花不到钱。
4. **回答**（`assistant.py`）：系统提示词只让模型根据 `<docs>` 里的段落回答；段落和问题里的尖括号会被替换，模型无法闭合或伪造这个区块；拒绝无关请求；只能链接到给出的 url。最多输出 700 个 token，温度 0.2。模型、长度等参数由后端固定，调用方改不了。
5. **成本上限**：调用走平台网关，用一个专为问芝士签发的虚拟 key（`service_credentials` 表，首次使用时签发，多进程用 advisory lock 保证只签一次）。这个 key 每 30 天最多花 `DOCS_ASSISTANT_BUDGET_USD`（默认 20 美元），并限 120 rpm；上游 key 不出网关。
6. **记录**：每个问题一行 `docs_questions`：问了什么、有没有答上、引用了哪些段落、用了多少 token、花了多久。`outcome = no_match` 的问题就是文档该补的地方。90 天后由后台任务清理（`DOCS_QUESTION_RETENTION_DAYS`）。

浏览器端只渲染一小部分 Markdown，并且只保留指向这次检索到的段落的链接。

## 相关设置 {#settings}

全部见 [环境变量全表](/dev/ref-env)，以 `DOCS_` 开头：`DOCS_INDEX_URL`、`DOCS_ASSISTANT_MODEL`、`DOCS_ASSISTANT_BUDGET_USD`、`DOCS_ASSISTANT_HOURLY_LIMIT`、`DOCS_ASSISTANT_DAILY_LIMIT`、`DOCS_ASSISTANT_CONCURRENCY`、`DOCS_QUESTION_RETENTION_DAYS`、`DOCS_DEV_SESSION_SECONDS`。网关地址和管理密钥沿用 `LLM_GATEWAY_ADMIN_BASE`、`LLM_GATEWAY_ADMIN_KEY`；没配置时问芝士显示暂未开放。
