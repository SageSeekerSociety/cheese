---
title: 功能数据
kind: 流程
summary: 后台「功能数据」怎么加一页：服务端和前端各登记一次，数字从功能自己已经在写的表里取，花费按网关价目表估算并标出来；以及这一片页面的隐私底线——不记 IP、原始提问旁边不出现提问的人、只留 90 天、只对平台管理员开放。
covers:
  - backend/app/domain/feature_stats/
  - backend/app/api/routes/admin_feature_stats.py
  - frontend/src/views/admin/features/
  - docs/site/src/visit.js
---

# 功能数据 {#feature-stats}

后台「功能数据」是 `/admin/feature-stats` 这一片：一个**不带任何数字**的目录页，加上每个功能一页自己的数。

> 讲：这一片页面是什么形状、怎么加一页、数字和花费从哪来、隐私上不许越过哪条线。不讲：问芝士本身怎么回答（见[文档站与问芝士](/dev/docs-site#ask)），后台还有哪些模块（见[平台管理员](/dev/admins#console)），前端路由和 i18n 的通用规矩（见[前端结构](/dev/frontend#i18n)）。

## 什么时候该加一页 {#when}

一个功能值得一页，当且仅当**有人要为它做一个决定**（继续做、砍掉、改哪里），而这个决定需要的数字功能本身已经在写。两件事缺一个都别加：

- 「有人在写字」：数字要是功能**已经在记的行**，不是为这一页新加的埋点。要为了这一页去给每条请求加一次写，那是另一个项目。
- 「有人在读」：说不清谁会看、看完会做什么决定，加出来就是一页没人看的图表。

反过来说也成立：**这一片不做通用埋点**。功能开关、灰度、每次请求都记一条的仪表盘，都不属于这里。

## 一页由三块组成 {#shape}

一页由服务端的注册表、前端的注册表、以及共用的一条路由和一个导航入口组成，三块之间只有一个握手点。

- `backend/app/domain/feature_stats/` —— 服务端的**注册表**（`registry.py`）和每个功能一个模块（`features/`）。注册表里一条 `Feature` 只有四样：`id`、`title`、`summary`（一句话）、`load`（取报告的函数）。
- 注册表**不带前端路由和组件路径**：路由是前端的，Python 里写一个 Vue 文件名，等于把 `router/feedback.ts` 抄了第二份，两份一定会分叉。
- `frontend/src/views/admin/features/registry.ts` —— 前端的注册表：`id` + 两个 i18n 键 + 那个页面组件（懒加载）。**两边的 `id` 是唯一的握手点。**
- 路由与导航是这一片共用的：`frontend/src/router/feedback.ts` 里两条（`feature-stats` 与 `feature-stats/:id`），`AdminLayout.vue` 侧栏里一个入口。

两边的 id 对不上时不许崩：目录页拿服务端的 `title`/`summary` 兜底，点进一个前端还没写页面的 id 会看到一句「这个功能还没有数据页」。

## 加一页的步骤 {#steps}

照着下面六步做，最后两步是这一片最容易漏的。

1. 服务端：在 `features/` 下加一个模块，写死 `FEATURE_ID`/`TITLE`/`SUMMARY`，写一个 `load(session, *, days)` 返回这一页要的整份报告。形状是这个功能自己的事，接口不统一成一套通用块。
2. 服务端：在 `registry.py` 的 `FEATURES` 里加一条。
3. 前端：在 `frontend/src/views/admin/features/` 下写页面组件，用现成的卡片（`AdminKpiCard`、`AdminLineChart`、`AdminMetricList`、`AdminHistogram`、`AdminShareBar`、`AdminQuestionTable`、`AdminTabs`）。图表组件都是纯道具的，页面里**不要**自己 `fetch`。
4. 前端：在 `registry.ts` 里加一条，写好两个 i18n 键。
5. 文案：`src/i18n/messages/{zh-CN,en}/featureStats.json` 两个语言都写，en 里不许出现中文。
6. 测试：见[测试](#tests)一节。路由和导航不用动。

## 数字从哪来 {#numbers}

这一片只读功能**已经在写**的表，不为页面新增任何跟踪。问芝士那份报告读的是 `docs_questions`（一次提问一行：结果、两个方向的 token、耗时、提问时所在页）和 `docs_visits`（[访问记录](#visits)）；按天的分桶用 `platform_stats/windows.py` 的 `utc_day_window`、`utc_day` 和 `dense_series`——按 UTC 天切，没人用的那天补 0，曲线才不断。

两条规矩每页都要守：

- **「没读到」是 `null`，不是 `0`。** 没有提问就没有中位 token 数；分母为空的比率（没人访问时的「用了问芝士的比例」）也是 `null`。前端把它画成长破折号，不画 0——0 读起来是「确实是零」，那是另一句话。
- **一组分布是一次算完的。** 分位数和直方图由 `stats.py` 用**同一个列表**算出（`describe` 给五个数，`histogram` 分桶），不推给 SQL：行数只有 90 天那么多，读回进程里算，分位数和直方图就不会各说一套。

报告里每个字段是不是数值、`null` 表示什么，都要在前端页面上对应得上；接口不发、页面不画的东西，别在文档里承诺。

## 花费是估算，页面上写着 {#spend}

花费按网关的价目表乘这一页记的 token 算出来（`pricing.py`），**不是账单数字**，页面上标着「估算」。

为什么不用真花费：网关确实存着真数，但按窗口取只能靠 `/spend/logs`——那是全表扫描，实测一次 24–102 秒；`/key/info` 很便宜，却答的是另一个问题（一辈子累计，没有窗口可除）。所以用估算：缓存折扣上会偏，量级上是对的，而且是瞬时的。

价目表读不到时（网关不可达）**不给数**：`usd` 是 `null`，页面画破折号并把标签换成「估算不了」，不是 $0。价目表里没有价的那部分 token 单独报出来（`unpriced_tokens`），页面说明白，而不是悄悄少算。

## 隐私底线 {#privacy}

这一片页面展示的是「平台被怎么用、被多少人用」，所以下面每条都是硬要求，加新一页时同样要满足。

- **只对平台管理员开放。** 两个接口都挂 `admin_common.PlatformAdminDep`：这些数字不是随便一个登录用户该看的。
- **不记 IP、不记 User-Agent。** 访问记录只有两个字段：浏览器自己编的随机串、落地那一页的 slug。请求里没有 IP、没有 UA，表里也就没有。
- **原始提问旁边不出现提问的人。** 「答不上来的问题」按问题原文分组，只有问题、页、次数三列——**没有也不会有「谁问的」**。这不是还没加：表里是用户可能在里面写了任何东西的原文，把它和身份并排放，等于把「这个人在问什么」交给管理员。**接口都不发这个字段**（`_unanswered` 只选三列），前端也就画不出来。要加这一列之前，先问那是不是这张表该做的事。
- **超过保留期就删。** 提问和访问都只留 `docs_question_retention_days`（默认 90 天），更早的在后台定时任务里删；两张表由**同一个清理任务**（`purge_old_questions`）扫，所以「保留 90 天」是一个承诺，不是两个会走散的数字。
- **不记轨迹。** 访问一人一天一行，只记落地的那一页，不是一次浏览的路径。

## 文档站的访问记录 {#visits}

文档站是静态文件，服务端看不到任何一次页面加载，所以由页面自己发一个信标：`docs/site/src/visit.js` 发一次 `POST /api/docs/visit`（路由在 `backend/app/api/routes/docs_visit.py`）。它只发两个字段——`visitor`（浏览器自己编的随机串，存在 localStorage）和 `page`（落地那一页的 slug）——开发文档页只记访问不记 slug（那些名字不公开）。

读法上有四点：

- **一人一天一行。** 去重靠表上的唯一键 `(day, visitor_id)`（插入时 `ON CONFLICT DO NOTHING`），不靠客户端记住自己发过——换标签页、清缓存都会让「客户端记的那一份」说错话。也因此，行里那个 `page` 就是「今天落地的第一页」。
- **登录按账号算，匿名按随机串算。** token 已经在本地就带上，同一个人换浏览器还是一个人；没带就是 `v:<随机串>`。两者都没有的信标收下但不记。
- **限流只是防放大器。** 一个访问者一小时 120 次（`VisitLimits`，和问芝士的限流同一形状）。Valkey 挂掉时**放行**而不是拒绝：丢几条计数远好过把这段时间的访问全丢掉，去重本来就由唯一键兜着。
- **失败一律安静。** 信标是「发完不管」——页面已经渲染完了，没有地方给读者看一个错误；后端对任何情况都回 204。

## 测试 {#tests}

这一片的测试断言的多数不是「有没有画出来」，而是**读法**对不对。

- **服务端单测**：`backend/tests/unit/test_feature_stats_math.py` —— 分位数、分桶这些纯函数的边界（空列表、单个值、正好落在桶边界上）。
- **服务端集成测**：`backend/tests/integration/test_feature_stats.py` —— 每个功能的取数函数在真库上跑：窗口边界、`null` 与 `0` 的区别、`POST /docs/visit` 的去重与限流、非管理员拿 403。改表结构的话，迁移测试在 `backend/tests/integration/test_docs_visits_migration.py`。
- **前端**：每个新组件一个 `*.spec.ts`（`@testing-library/vue`），页面再一个。要断言的是：比例和分母一起画、`null` 画破折号不画 0、切窗口是**重新取数**（带 `days`）而不是在本地筛、目录页上一个数字都不许有（`AdminFeatureStatsPage.spec.ts` 里那条 `expect(/\d/.test(...))`）、以及分页表里没有提问者。
- **文档站**：`docs/site/test/visit-beacon.test.mjs` —— 信标发的就是那两个字段、公开页才发、失败不抛、构建出来的包里还带着这个调用。新加测试文件记得挂进 `docs/site/package.json` 的 `test`。
- 文案都走 i18n：`src/i18n/catalog.spec.ts` 会拦下没有 en 对映、或者没被任何地方引用的键。

## 问芝士：照着抄的范例 {#worked-example}

第一页就是「问芝士」。[文档站那一页](/dev/docs-site#ask)讲的是问芝士本身；这里讲它的数据页。

服务端是 `features/docs_assistant.py`：`_visits`（访客、其中登录的、逐日）、`_questions`（提问的人、次数、人均、回答成功率）、`_tokens_and_latency`（两个分布）、`_unanswered`（答不上来的问题，最多 50 行）四段，最后 `load` 拼成一份报告。注册表里一条 `id="docs-assistant"`，接口是 `GET /admin/feature-stats/docs-assistant?days=30`（目录是 `GET /admin/feature-stats`）。

报告里有什么：访客（其中登录的）、提问的人（占登录访客的比例）、提问次数（人均）、回答成功率（已答 / 总数）、花费与每题均摊；逐日的访客 / 提问者 / 提问次数；每题的 token 分布（平均、中位、最小、p90、最大 + 直方图）；结果三档（答上 / 没找到 / 失败）与耗时分布；「答不上来的问题」表。

前端是 `frontend/src/views/admin/features/DocsAssistantPage.vue`，注册一项在 `frontend/src/views/admin/features/registry.ts`。**整页没有一句 `fetch`**，取数走 `api.ts` 的 `getDocsAssistantReport(days)`。

这一页踩过的两个坑，加新页时也会踩：`AdminTabs` 是字符串泛型，切窗口要把值转回数字再发给接口；组件忘了 `import` 不报错，只是那一块什么都不画——测试也就断言不到东西。
