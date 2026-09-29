# 参照：成熟平台的公开 API 惯例

用途：评判「我们的接口能不能这样优化」时的依据。全部条目都来自公开文档或对公开 API 的实测（2026-09-28）。

## 1. 分页

- **惯例**：列表接口返回 `link` 响应头，条目形如 `<url>; rel="next"` / `rel="prev"` / `rel="first"` / `rel="last"`；客户端照抄 URL 翻页，不要自己拼参数。
- **实测**（`curl -i 'https://api.github.com/search/repositories?q=cheese&per_page=2'`）：
  `link: <https://api.github.com/search/repositories?q=cheese&per_page=2&page=2>; rel="next", <...&page=500>; rel="last"`
- **惯例**：`per_page` 上限（GitHub 多数接口 100），**超限不报错、静默夹到上限**；`page` 偏移与 `before`/`after` 游标并存，游标用于大表。
- **出处**：https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api
- **可搬到我们平台哪里**：`response.page(items, total)` 现在只回 `{"data", "total"}`，调用方要么猜上限要么拉全量。可在信封里加一个 `page_info`（`has_more` / `next_cursor`），或至少给列表接口统一 `limit` 上限与 `total` 语义。

## 2. 错误响应

- **惯例**：错误体固定三段 —— `message`（人读）、`documentation_url`（文档深链）、`status`（字符串化的状态码）；校验错误再带 `errors[]`（`resource`/`field`/`code`/`message`）。
- **实测**（`curl -i https://api.github.com/repos/this-repo-does-not-exist-xyz/none`）：
  ```json
  {"message": "Not Found", "documentation_url": "https://docs.github.com/rest/repos/repos#get-a-repository", "status": "404"}
  ```
- **可搬到我们平台哪里**：我们已有 `app.core.errors.BaseError.to_response_body()` → `{"code","message","error":{name,message,data,retryable}}`。**信封是项目规范定死的，不能换 shape**；能搬的是补两样——`documentation_url`（把报错指到 docs 里对应条目）和 `request_id`（对齐 `X-GitHub-Request-Id`，排障时拿它串日志）。

## 3. 限流

- **惯例**：每响应带 `x-ratelimit-limit` / `x-ratelimit-remaining` / `x-ratelimit-used` / `x-ratelimit-resource` / `x-ratelimit-reset`（UTC epoch 秒）；限流时给 `retry-after`（秒）。
- **惯例**：二次限流（secondary rate limit）靠串行化 + 写操作间隔 ≥1s 缓解；撞限流继续打可能被封集成。
- **出处**：https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api
- **可搬到我们平台哪里**：登录接口已经按用户名限速锁定（项目记忆里踩过），但**没有任何响应头告诉调用方还剩多少、什么时候能重试**。给限流相关接口补 `Retry-After` + `X-RateLimit-*` 是低风险高收益的一条。

## 4. 条件请求与缓存

- **惯例**：`etag` / `last-modified` 响应头 + `if-none-match` / `if-modified-since` 请求头 → 未变更回 `304`；**304 不计入主限流配额**；只对 GET/HEAD 有效。
- **惯例**：轮询方应固定查询参数与排序，才能稳定命中 304。
- **出处**：https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api
- **我们已落地的**：`app/api/conditional.py` 已抽好 `if_none_match_hits()` / `etag_for_json()`，头像与管理员名单在用。
- **可搬到我们平台哪里**：轮询类接口（任务状态、话题消息、机器状态、通知）大多没接条件请求。把 `etag_for_json` 推广到这些读接口，浏览器轮询能省掉大量全量序列化。

## 5. 幂等

- **惯例**：`Idempotency-Key` 请求头（建议 UUID v4，≤255 字符）；服务端记住「键 → 首次的状态码与响应体」24 小时，同键重放原结果；参数不一致则报错；校验失败的结果不记。GET/DELETE 不收（本身幂等）。
- **出处**：https://docs.stripe.com/api/idempotent_requests
- **可搬到我们平台哪里**：创建类 POST（开话题、建任务、发消息、递验收卡）在网络抖动重试时会重复创建。给这几类加 `Idempotency-Key` 能根治重复提交，比在前端禁用按钮可靠。

## 6. 请求标识与可排障

- **惯例**：每个响应带 `x-github-request-id`，客户端报障时带上它，服务端能一次定位到那一次请求。
- **可搬到我们平台哪里**：我们有 structlog，但响应体/响应头里没回 request id。补一个响应头成本极低。

## 7. 文档与可发现性

- **惯例**：OpenAPI 自带 `servers`（我们已做，见 `docs/api-conventions.md`）；错误体里给 `documentation_url` 深链；接口有 examples。
- **出处**：https://docs.github.com/en/rest/using-the-rest-api/getting-started-with-the-rest-api

## 8. skills.sh（Vercel 的 Agent Skills 目录）

- **是什么**：公开的 Agent Skills 注册表（vercel-labs/skills），`npx skills add <owner/repo>` 安装；有 packs / topics / official / security audits 分区，以及公开 API（https://skills.sh/docs/api）。
- **可借鉴的**：它的**清单式组织**（publisher/repo/skill-name 三层、每个 skill 一条可独立安装的单元、带安装量与活跃度）适合搬到我们的 skills 体系（仓库里已有 `skills-lock.json`、`backend/app/api/routes/project_skills.py`）：让 skill 成为可检索、可版本化、可统计的单元，而不是一堆散文件。

## 最值得先搬的 6 条（按投入产出）

1. 限流接口补 `Retry-After` + `X-RateLimit-*` 响应头（改一处中间件，全接口受益）。
2. 所有响应补 `X-Request-Id`，并写进 structlog 上下文（排障成本直降）。
3. 错误体补 `documentation_url`（模板化，按错误类型映射到 docs 锚点）。
4. 轮询类读接口接 `etag_for_json` + `304`（已有共用件，推广即可）。
5. 创建类 POST 支持 `Idempotency-Key`（先覆盖话题/任务/消息/验收卡）。
6. 列表接口统一 `limit` 上限与 `has_more` 语义（不必一步换成 cursor）。
