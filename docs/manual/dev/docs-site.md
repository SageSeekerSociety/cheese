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
- 开发文档每页开头必须声明类型和摘要；「流程」「概念」和「参考」三类还要列出涉及的代码路径（`covers`）。缺字段、类型不在五类之内、或 `covers` 指向不存在的路径，构建都会失败；站内链接和锚点也必须全部有效。
- 构建同时产出：公开和开发两份搜索索引、`llms.txt` 与每页的 `.md` 原文、全部文档的压缩包、更新日志 RSS，以及问芝士和 AI 队友检索用的 `ask-index.json`（公开页）与 `dev/ask-index.json`（开发文档，在门后）。
- 参考页（CLI、环境变量、CI）和索引页由构建时读代码生成，不手写。

## 交互演示 {#demos}

流程和机制光靠文字读不快，所以页面里可以放两个组件。它们都写成页面里的一段 fence，由 `build.mjs` 在构建时展开成静态 HTML：

- **`demo-steps` / `demo-timeline`**：把过程一步步放出来，带上一页 / 下一步 / 播放 / 拖动进度条。每一步可以带一个数字（字符数、token 数），下面有一根按数字画的进度柱；某一步可以设成闸门，播到那里停下等人点继续。`demo-timeline` 只是多一层「这是一条时间线」的样式，数据形状完全一样。
- **`demo-sim`**：拖参数看结论。参数是滑块、开关或下拉；判定规则和读数写在 fence 里，由 `src/demo-model.mjs` 里那个很小的表达式语言求值（数字、`&&` `||` `!`、比较和四则，没有 `eval`）。要跟着代码里的真实数字动的模拟，用 `data:` 绑定构建时算出来的数据集。

下面这段就是 `demo-steps` 的全文（整段缩进四格，免得被当成真的组件展开）：

    ```demo-steps
    title: 一条消息怎么变成芝士的一轮
    steps:
      - label: 发消息与寻址
        desc: 消息落库，点名通知和它在同一个短事务里写好。
        link: /dev/turn#address
      - label: 采纳
        desc: 检查评审人、需要的采纳人数、项目策略，以及浏览器里显示的那一版，然后调用托管平台的合并接口。
        gate: true
        go: 人来采纳
    ```

`demo-sim` 的一段：

    ```demo-sim
    title: 两道刹车读同一份额度
    vars:
      - key: tokens
        label: 已经用掉的 token
        unit: 千
        min: 0
        max: 1000
        step: 20
        value: 300
    derived:
      - key: spent
        expr: tokens / 10
    rules:
      - label: 额度用完
        when: 'spent >= 50'
        text: 准入拒绝，房间里出现平台提示。
        tone: bad
    out:
      - label: 已用，折算成额度
        expr: spent
        unit: 额度
    ```

fence 的正文是 YAML 的一个很小的子集：顶格的 `key: value`；`key:` 后面留空就跟一列 `- key: value`；条目下再缩进一层写 `key: value`。`- ` 条目里的 `key: value` 一律是这条自己的字段。看不懂的行会让构建带着「哪一页、第几行」失败，不会静默出一个空组件。规则的 `text` 里 `{key}` 会换成那个参数的值；`out` 和 `derived` 是表达式，同时给构建和浏览器用，所以组件在脚本加载前后不会说两套话。

`data: <数据集名>` 的步骤不带数字，它按位置绑到构建时算出来的那份数据上：行数和步骤数必须一致，每一步还可以用 `check:` 断言落在的那一行包含某个词。加了、删了或调了顺序的行会让构建失败，而不是把数字悄悄安到别的步骤上。[上下文那页](/dev/context)的时间线就是这么来的：`docs/site/gen/prompt.py` 真跑一遍 `build_system_prompt`，把输出按行首的 `## ` 切成块，字符数按一个固定比例折成 token（页面上写明是估算）。

几条必须守住的：

- 组件展开成 HTML 时，**每一句话都已经在 HTML 里**。JavaScript 关掉时页面是完整的：步骤全在，模拟器显示默认位置的那份结论。`src/demo-dom.mjs` 只切换状态，不生成文字。
- `prefers-reduced-motion` 下不自动播放，一次全给；窄屏（700px 以下）收起控制器和进度柱，步骤直接铺开。
- 键盘可用。控制器是原生 `button`、`input[type=range]` 和 `select`，焦点在组件里时左右箭头走一步。
- 跟随现有的 CSS 变量，所以深浅色自动跟着走。
- 不引新依赖。组件的行为进公开的 `app.js` bundle（`src/demo-dom.mjs`），**不放在 `/docs/dev/` 下**：那条路径有鉴权门禁，而这些组件在公开文档里也要能跑。

### 两个读者，两份东西 {#demos-readers}

同一段 fence 出两份东西：

| 读者 | 拿到什么 | 怎么来的 |
|---|---|---|
| 浏览器 | 组件本身 | 渲染器在 `code` 钩子里展开 |
| 模型 | 一段短的文字版 | `demoText` 生成，替换掉 fence |

文字版是：步骤型给一步一条的列表（带数字和「在这里停下」），模拟型给参数、判定规则和默认位置的读数。组件的标记、颜色和控件不进文字版。

于是每页的 `.md` 原文、`llms.txt`、`manual.zip`，以及搜索索引和 `ask-index.json`（问芝士和 AI 队友检索用的）里，都只有这段文字版。给模型读的东西不必花在按钮上。

### 每页的演示 {#demos-list}

| 页面 | 演示什么 | 组件 | 数据从哪来 |
|---|---|---|---|
| [提示词注入与上下文管理](/dev/context) | 一轮的上下文按什么顺序装进窗口，各占多少 | `demo-timeline` | `gen/prompt.py` 真跑 `build_system_prompt`，按行首的 `## ` 切块，字符数 ÷ 1.6 折成 token |
| [一条消息怎么变成芝士的一轮](/dev/turn) | 一轮的七步 | `demo-steps` | 这一页自己那七节（每步链回本节） |
| [任务 → 分支 → PR → 验收合并](/dev/delivery) | 一条活从开卡到合进主干 | `demo-steps` | 这一页「从任务到验收卡」那六步，采纳那一步是闸门 |
| [模型调用流程](/dev/llm) | 准入对每个请求回答的三件事 | `demo-sim` | 这一页的准入 JSON 和两条路 |
| [计费流程](/dev/billing) | 两道刹车各在什么时候拦 | `demo-sim` | 这一页 1 额度 = 1 万 token 的折算 |

[记忆](/dev/memory)那页还要两个：一轮之内记忆的七步流转，和拖动索引行数看 200 行 / 25KB 的截断与警告。等它手上那次改动落地再加。

## 首页与截图 {#home}

首页（`docs/site/src/home.mjs`）不写死内容：分组来自 `structure.mjs`，常见问题来自 `troubleshooting.md`，更新来自 git 历史，`build.mjs` 只负责把数据传进去。

- 「问芝士」导览每一屏对应一类文档的一页。左侧那一页不是截图，是用站点自己的侧栏和正文组件现场渲染的开头几节（`inert`，只看不能点），所以文档改了它跟着变，暗色也自动跟随；开发文档那一屏只画锁住的轮廓，不把内容放进公开首页。
- 使用文档里的界面截图由 `shots/shots.mjs` 在 `shots/fixture.py` 造出的示例项目里拍（需要本地全套服务），落到 `docs/manual/public/images/`。截图是真实界面，页面上的本地地址会换成 `https://okcheese.com`。
- 首页标题用一份切过子集的显示字体（三极行楷简体，免费商用）：`gen/font.sh` 只保留首页标题用到的字，生成 `src/fonts/display.woff2`。改了首页标题文案要重跑，否则新字会回落成普通字体。

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

**划词问芝士**：在文档页选中一段正文，选区旁出现「问芝士」按钮；点它会打开面板，并把选中的文字作为引用带进下一个问题（请求里的 `quote`，最多 600 字）。后端用「引用 + 问题」一起检索，引用和问题一样转义后放在 `<docs>` 围栏之外，只作为「问的是什么」，不作为回答依据。

## AI 队友查文档 {#agent-docs}

AI 队友在平台里回答「怎么用」的问题时，用两个平台工具查文档：`cheese_docs_search`（关键词检索，和问芝士同一份索引、同一套排序）和 `cheese_docs_read`（读一页的 `.md` 原文）。后端接口是 `POST /docs/agent/search` 和 `POST /docs/agent/read`（`backend/app/domain/docs_site/library.py`）。

- 使用文档对所有项目开放。
- 开发文档只对「在做知是本身」的项目开放：项目绑定的仓库在 `DOCS_DEV_REPOSITORIES` 里（默认 `SageSeekerSociety/cheese`）。别的项目检索不到开发文档，点名读 `dev/…` 返回 403。
- 开发文档的索引 `dev/ask-index.json` 和 `.md` 原文都在 `/docs/dev/` 的门后面。后端读它们时带一张内部通行证（`access.internal_pass`，单独的 audience，五分钟有效），门的检查接口认它；浏览器拿不到这种通行证。

## 相关设置 {#settings}

全部见 [环境变量全表](/dev/ref-env)，以 `DOCS_` 开头：`DOCS_INDEX_URL`、`DOCS_DEV_INDEX_URL`、`DOCS_DEV_REPOSITORIES`、`DOCS_ASSISTANT_MODEL`、`DOCS_ASSISTANT_BUDGET_USD`、`DOCS_ASSISTANT_HOURLY_LIMIT`、`DOCS_ASSISTANT_DAILY_LIMIT`、`DOCS_ASSISTANT_CONCURRENCY`、`DOCS_QUESTION_RETENTION_DAYS`、`DOCS_DEV_SESSION_SECONDS`。网关地址和管理密钥沿用 `LLM_GATEWAY_ADMIN_BASE`、`LLM_GATEWAY_ADMIN_KEY`；没配置时问芝士显示暂未开放。
