---
title: 文档站与问芝士
kind: 流程
summary: 文档站怎么构建和发布、在自己的域名上怎么登录、开发文档怎么只对平台管理员开放，以及问芝士怎么只根据文档回答、怎么防滥用。
covers:
  - docs/site/
  - backend/app/domain/docs_site/
  - backend/app/api/routes/docs_site.py
  - frontend/nginx.conf
  - frontend/nginx/docs/
  - frontend/scripts/docs-mode.sh
  - frontend/src/views/DocsSignIn.vue
---

# 文档站与问芝士 {#docs-ask}

文档站怎么构建和发布、在自己的域名上怎么登录、开发文档怎么只对平台管理员开放，以及问芝士怎么只根据文档回答、怎么防滥用。

> 讲：这个站本身的架构。不讲：怎么写文档页，见 `docs/manual/README.md`。

## 构建与发布 {#build}

`docs/site/build.mjs` 把 `docs/manual/` 下的 Markdown 构建成静态站，随前端镜像一起发布。同一份源构建两次（`src/where.mjs`）：

| 构建 | 站点根 | 产物 | 什么时候用 |
|---|---|---|---|
| `DOCS_BASE=/docs`（默认） | 平台的 `/docs/` | `frontend/public/docs/` | 部署没配 `DOCS_ORIGIN` |
| `DOCS_BASE=`（空） | 文档站自己的域名的根，如 `https://docs.okcheese.com/` | `frontend/docs-host/`，镜像里放在 html 根之外 | 部署配了 `DOCS_ORIGIN` |

两份内容一字不差，差的只是链接前缀、`llms.txt` / `.md` 原文 / RSS 里的绝对地址，以及离开文档站的链接（用量、条款、演示画面；登录不在其中，见[在文档站登录](#sign-in)）。镜像不写死任何一个部署的域名：这两样在构建产物里是占位符，前端容器启动时由 `frontend/scripts/docs-mode.sh` 填上——文档的公开地址填 `DOCS_ORIGIN`（`/docs/` 那份填平台的源），平台的源填 `FRONTEND_URL`；`/docs/` 那份离开文档站的链接本来就是同源的相对地址。`frontend/scripts/check-static-assets.sh` 两份都要，少一份镜像构建失败。

前端容器只发其中一份，由 `DOCS_ORIGIN` 决定（`docs-mode.sh` 从 `frontend/nginx/docs/` 里挑配置）。`DOCS_ORIGIN` 按浏览器写 Origin 的样子规整（小写、去掉默认端口和结尾斜杠，空白算没配），后端（`config.py`）和前端容器读成同一个值；它和平台是同一个主机、或者配了它却没有 `FRONTEND_URL` 时，后端起不来、前端容器也起不来——否则文档站那个 server 会把平台的请求全接走，而健康检查照样是绿的：

- **空**：平台的 server 在 `/docs/` 下发 `/docs` 那份（`under-platform.conf`）。
- **设了**：文档站自己一个 server（`host.conf`，`server_name` 是那个域名），只放行 `/api/docs/` 这一段接口；平台的 `/docs/…` 一律 301 到文档站同一页（`redirect.conf`，查询串照带，`#小节` 浏览器自己保留）。网上只有一份在发，旧链接全落到新地址。

okcheese.com 这个部署配的是 `DOCS_ORIGIN=https://docs.okcheese.com`：文档站在 `https://docs.okcheese.com/`，`https://okcheese.com/docs/…` 一律 301 到那里。这个域名从公网怎么进来（DNS、香港机器上的 Caddy）见 `docs/infrastructure.md` 的「Public edge」一节。

后端读的也是正在发的那一份：`sections.json` 里的 `url` 都是站内路径（`/accept#is-merge`），两份构建一样，后端要给人看的链接再拼上文档站的地址（`docs_site/site.py`）。

- 每个地址都是一个预渲染好的 HTML 文件，`src/app.js` 只负责交互：搜索、问芝士、深浅色和交互演示。
- 样式用产品自己的设计系统：构建时把 `frontend/src/style.css` 的 `:root` 和深色两段 token 原样放在 `src/style.css` 前面，颜色、圆角、字体、动效都只在那一处定义；深色是 `<html data-theme="dark">`，偏好记在 `cheesex.theme`：在 `/docs/` 下和产品共用一份；在文档站自己的域名上是那个源自己的一份（同一个键名，另一个源的存储）。页面里嵌的演示画面是平台的页面，读不到文档站那一份，所以页面在画面地址里告诉它用哪种（`?theme=dark|light`，`src/demo-dom.mjs` 的 `themeStages`；画面那边见 `frontend/src/demo-main.ts`）。
- 站点结构写在 `docs/site/src/structure.mjs`，这是导航和分组的唯一来源。
- 开发文档每页开头必须声明类型和摘要；「流程」「概念」和「参考」三类还要列出涉及的代码路径（`covers`）。缺字段、类型不在五类之内、或 `covers` 指向不存在的路径，构建都会失败；站内链接和锚点也必须全部有效。
- 构建同时产出：公开和开发两份搜索索引、`llms.txt` 与每页的 `.md` 原文、全部文档的压缩包、更新日志 RSS，以及问芝士和 AI 队友检索用的 `sections.json`（公开页）与 `dev/sections.json`（开发文档，在门后）。
- 参考页（CLI、环境变量、CI）和索引页由构建时读代码生成，不手写。

## 交互演示 {#demos}

流程和机制光靠文字读不快，所以页面里可以放一组组件。它们都写成页面里的一段 fence，由 `build.mjs` 在构建时展开成静态 HTML：

- **`demo-panel`**：用户文档的演示。一块简化过的产品界面（只画相关的那一块，其余画成灰条），演一个动作，最多四拍。`parts:` 列出界面上的各块（`msg` 消息、`thread` 支线摘要、`composer` 输入框、`head` 页头、`card` 验收条或验收卡、`checklist` 这一轮的清单、`strip` 页头下的提示条、`tabs` 右侧页签、`doc` 文档、`files` 改动的文件、`notice` 输入框位置的提示、`line` 一行说明（`value:` 接在界面字样后面的数据）、`bars` 灰条、`field` 对话框里的一个输入项、`buttons` 对话框底部的按钮、`row` 列表里的一行：名字、下面一行灰字、右侧的状态和按钮），每块用 `at:` / `until:` 说第几拍出现、第几拍消失，`press:` 说按哪个按钮进入第几拍。构建写进 HTML 的是最后一拍，也就是结果；`src/panel-window.mjs` 在它第一次进入视野时倒回第一拍、播一遍、停在结果上，播完给一个「重播」。没有进度条，只有一行图注（`caption:`）。按钮、状态、占位字这些界面字样由构建对照 `frontend/src/i18n/messages/zh-CN/` 检查（`{name}` 之类的占位能匹配任意文字），对不上就构建失败。
- **`:::walk`**：一列编号步骤加一个带 `walk: true` 的 `demo-panel`。画面第 0 帧是第 1 步之前的屏幕，第 n 帧是第 n 步之后的屏幕，帧数必须等于步数，最多七步，不能有图注。宽屏时步骤在左、画面在右并停在视野里，窄于 1100px 时画面排在步骤下面。`src/walk-window.mjs` 让步骤当控制器：进入视野时从第 0 帧走一遍、停在最后一步，点某一步就切到那一步之后的屏幕。没有脚本或要求减少动态效果时停在最后一帧（减少动态时点步骤仍能切换，只是不动）。画面窗口的高度跟着当前这一帧的内容走：换帧时从上一帧的高度平滑过渡到这一帧的高度，不按最高那一帧留白；单独的 `demo-panel` 播放时也一样。
- **`demo-steps` / `demo-timeline`**：把过程一步步放出来，带上一页 / 下一步 / 播放 / 拖动进度条。每一步可以带一个数字（字符数、token 数），下面有一根按数字画的进度柱；某一步可以设成闸门，播到那里停下等人点继续。`demo-timeline` 只是多一层「这是一条时间线」的样式，数据形状完全一样。
- **`demo-context`**：一个上下文窗口怎么被填满，仿 Claude Code 文档的「Explore the context window」。顶上一根横条就是整个窗口，下面按时间列出装进来的每一样，标明它属于哪一类、房间里谁看得见（对话 / 现场 / 看不见）。`before:` 排在数据集前面，`then:` 排在后面；`cat: sub` 的行在分身自己的窗口里、不占横条，`cat: compact` 那一行只留下 `keeps:` 列出的几类，再加上摘要。
- **`demo-sim`**：拖参数看结论。参数是滑块、开关或下拉；判定规则和读数写在 fence 里，由 `src/demo-model.mjs` 里那个很小的表达式语言求值（数字、`&&` `||` `!`、比较和四则，没有 `eval`）。要跟着代码里的真实数字动的模拟，用 `data:` 绑定构建时算出来的数据集。
- **`demo-ci`**：勾几行改动路径，看 `Required CI` 会要求哪些套件跑、哪些必须跳过，右边每一行说清是谁命中哪条 pattern。左栏有预设场景（只改文档 / 改前端 / 改部署脚本……），也可以自己敲一条路径进去。
- **`demo-flow`**：把一次请求或一次交接按参与方分列，一步一支箭头，切换路线（正常 / 被拦 / 过期）看它停在哪一步。`resident:` 列出「不在这张图里、也不跟着变」的常驻那一层，画成底下那条带子。
- **`demo-memory`**：拖上限看后果：索引几行、每行多少字节、新写的一行多少字符、一条正文多少字，右边立刻说这一版是注入时截断、拒收成 `.rejected.md`，还是接口回 422。
- **`demo-arch`**：把一次经过拆成一张站点图——包从哪个口进、每一站收到什么又交出什么、哪一站把它拦下。`kind:` 选画哪张图（`llm` 一次模型调用、`machines` 一次工具调用），`entries:` × `scenes:` 是读者能换的两组按钮，`blocks:` 声明这个 fence 认为哪些组合会被拦下——图里不再拦了，构建就失败，文案和画面对不上也是。站点和每一站的字段写在 `src/arch.mjs`；地图在 `src/arch-view.mjs`，是纯函数：构建时预渲染成 HTML，浏览器里 `src/arch-window.mjs` 调同一批函数重画，两侧不会说两套话。图里的路径、端口、状态码、上限由 `gen/arch_facts.py` 从代码里读出来，`build.mjs` 比对。窄屏和无脚本时换成一份逐站的清单。

### 用产品里的真组件演 {#demos-embed}

`demo-steps` 多写一行 `embed: <名字>`，步骤列表上方就多一块画面：前端的公开页 `/demo/<名字>?embed=1` 嵌在 iframe 里，用产品自己的消息行（`RoomMessage`）和右边的工作面板（页签条 `PanelTabs` 加 `PanelDoc` / `PanelChanges` / `PanelPreview` / `PanelSite` 这四格）按剧本演，做法和首页的 `LandingRoom.vue` 一样。

- **剧本**在 `frontend/src/views/demo/scenes/<名字>.json`：每一步一串带毫秒时刻的事件（有人说话、一位队友开一轮、现场里一步工具调用、一轮结束、座位卡和机器栏换字）。`demoScene.ts` 把「第几步的第几毫秒」从头重放成一帧，所以往回跳和顺着放得到同一帧。
- **右边停在哪一格**：某一步想让人看别处，就写一行 `panel:`（`overview` / `changes` / `preview` / `site`），再把那一格里的东西写在同一步的 `overview:` / `changes:` / `preview:` 上（改动只写文件路径和那几行 diff，文件头和 hunk 头由 `demoPanels.ts` 补）。写一次就留在那儿，后面几步只说 `panel:` 就行；哪一步都不写就是现场（`site`）。`checkScene` 会挡下「停在一格却没人写它的内容」。
- **谁数步数**：文档的步骤条。它发 `{cheeseDemo: 'go', step, play}` 给画面，画面放完这一步回 `{cheeseDemo: 'done', step}`，步骤条才走下一步。步骤条停下、拖动、点某一步，画面都跟着跳。
- **两份对齐**：构建时读剧本，步数和每一步的标题必须和 fence 里一字不差，不一致就构建失败。改一边就得改另一边。
- **文字版照旧**：`.md`、`llms.txt`、搜索索引里还是 fence 的那几步文字；画面不进文字版。离线的 `manual.zip` 里没有前端，只剩文字。
- 直接打开 `/demo/<名字>`（不带 `embed`）是一页带播放条的完整演示，改剧本时用它看效果。同一个入口上还有一条**不走剧本**的路：`/demo/catalog` 是组件预览站，一页一个组件、一格里一种状态，改组件外观时用它看效果（见[前端结构](/dev/frontend#catalog)）。

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

`source: <构建源名>` 让 fence 只说明它要展示什么（`expect:` 列出套件名、`limits:` 列出常数名），数据由 `build.mjs` 从代码里读出来交给它：名字对不上、常数被改名或删掉、某条路径一条 pattern 都命不中，构建就失败。比这更硬的一层是**对照真代码跑**：CI 那页的选择结果会和真跑一遍 `.github/scripts/required-ci.py` 的 `select()` 逐套件比，记忆那页的截断与拒收会和 `files.py` 的 `fit_index` / `limit_breach` 逐样本比（页面里的 `src/ci-scope.mjs`、`src/memory-limits.mjs` 是这两段代码的移植），差一条就让构建失败。

`demo-arch` 还有一份 DOM 测试：`docs/site/test/demo-arch.test.mjs` 用 happy-dom（借 `frontend/node_modules` 里那一份，不引新依赖）挂载构建产物，真点「下一站」、真换场景，断言包停在哪一站、检视面板说哪一句、哪一站被画成拦下——构建时那份 HTML 和浏览器里那份是同一批字符串，哪边坏了它就红。跑法见文件头。 `demo-panel` 和 `:::walk` 的在 `docs/site/test/demo-panel.test.mjs`：不播放时停在结果、播放时从动作之前开始、停在结果上、点步骤切到那一帧，以及构建拒绝产品里没有的界面字样和超过四拍的演示。

`data: <数据集名>` 的步骤不带数字，它按位置绑到构建时算出来的那份数据上：行数和步骤数必须一致，每一步还可以用 `check:` 断言落在的那一行包含某个词。加了、删了或调了顺序的行会让构建失败，而不是把数字悄悄安到别的步骤上。[上下文那页](/dev/context)的时间线就是这么来的：`docs/site/gen/prompt.py` 真跑一遍 `build_system_prompt`，把输出按行首的 `## ` 切成块，字符数按一个固定比例折成 token（页面上写明是估算）。

几条必须守住的：

- 组件展开成 HTML 时，**每一句话都已经在 HTML 里**。JavaScript 关掉时页面是完整的：步骤全在，模拟器显示默认位置的那份结论。`src/demo-dom.mjs` 只切换状态，不生成文字。几个仪表盘（`demo-ci` / `demo-flow` / `demo-memory` / `demo-arch`）也一样：HTML 里先是一份读得完的清单（套件表加各场景结果、每条路线的分步、四句上限、每条走法一站一站的顺序），脚本上来才把它换成能点、能拖的那一版。
- `prefers-reduced-motion` 下不自动播放，一次全给；窄屏（700px 以下）收起控制器和进度柱，步骤直接铺开。
- 键盘可用。控制器是原生 `button`、`input[type=range]` 和 `select`，焦点在组件里时左右箭头走一步。
- 跟随现有的 CSS 变量，所以深浅色自动跟着走。
- 不引新依赖。组件的行为进公开的 `app.js` bundle（`src/demo-dom.mjs`），**不放在 `dev/` 下**：那条路径有鉴权门禁，而这些组件在公开文档里也要能跑。

### 两个读者，两份东西 {#demos-readers}

同一段 fence 出两份东西：

| 读者 | 拿到什么 | 怎么来的 |
|---|---|---|
| 浏览器 | 组件本身 | 渲染器在 `code` 钩子里展开 |
| 模型 | 一段短的文字版 | `demoText` 生成，替换掉 fence |

文字版是：步骤型给一步一条的列表（带数字和「在这里停下」），模拟型给参数、判定规则和默认位置的读数，路径型（`demo-arch`）给每个「入口 × 场景」一条逐站的线、写清哪一站把它拦下。组件的标记、颜色和控件不进文字版。

于是每页的 `.md` 原文、`llms.txt`、`manual.zip`，以及搜索索引和 `sections.json`（问芝士和 AI 队友检索用的）里，都只有这段文字版。给模型读的东西不必花在按钮上。

### 每页的演示 {#demos-list}

| 页面 | 演示什么 | 组件 | 数据从哪来 |
|---|---|---|---|
| [快速开始](/quickstart) | 新建项目；交给芝士并开始任务；检查并采纳 | 三个 `:::walk` | 页面里的 fence，界面字样对照 `frontend/src/i18n/messages/zh-CN/` |
| [把一件事交给芝士并验收](/working-with-cheese) | 在频道里交给芝士；把消息转为任务并开始；审阅并采纳 | 一个 `demo-panel` 加两个 `:::walk` | 页面里的 fence，界面字样对照 `frontend/src/i18n/messages/zh-CN/` |
| [和同学一起做一个项目](/team-project) | 建团队并批准同学加入；在团队下建项目；把自己负责的部分转为任务；加协作者；查看各人的进度 | 五个 `:::walk` | 页面里的 fence，界面字样对照 `frontend/src/i18n/messages/zh-CN/` |
| [加入别人的团队](/join-a-team) | 用团队链接申请加入，等批准 | 一个 `:::walk` | 同上 |
| [邀请一个人加入你的项目](/invite-to-project) | 邀请一位外部成员 | 一个 `:::walk` | 同上 |
| [把网页发布成网站](/publish-a-site) | 在「总览」中发布网站 | 一个 `:::walk` | 页面里的 fence，界面字样对照 `frontend/src/i18n/messages/zh-CN/` |
| [让芝士在你自己的电脑上工作](/use-your-computer) | 接入这台电脑；让频道改用这台电脑 | 两个 `:::walk` | 页面里的 fence，界面字样对照 `frontend/src/i18n/messages/zh-CN/` |
| [提示词注入与上下文管理](/dev/context) | 一轮里上下文窗口怎么被填满，各占多少、谁看得见 | `demo-context` | `gen/prompt.py` 真跑 `build_system_prompt`，按行首的 `## ` 切块，字符数 ÷ 1.6 折成 token |
| [一条消息怎么变成芝士的一轮](/dev/turn) | 一轮的七步 | `demo-steps` + `embed: turn` | 这一页自己那七节（每步链回本节）；画面是剧本 `scenes/turn.json` |
| [一条消息怎么变成芝士的一轮](/dev/turn#seats) | 两个队友在同一个话题里并行 | `demo-steps` + `embed: seats` | 「同一话题里的几个 AI 队友」那六节；画面是剧本 `scenes/seats.json` |
| [任务 → 分支 → PR → 验收合并](/dev/delivery) | 一条活从开卡到合进主干 | `demo-steps` | 这一页「从任务到验收卡」那六步，采纳那一步是闸门 |
| [模型调用流程](/dev/llm) | 准入对每个请求回答的三件事 | `demo-sim` | 这一页的准入 JSON 和两条路 |
| [模型调用流程](/dev/llm) | 一次请求经过哪几站，被拦在哪 | `demo-steps` + `embed: llm` | 这一页各节；画面是剧本 `scenes/llm.json` |
| [CI 设计](/dev/ci) | 改到哪些路径就跑哪些套件，谁被跳过 | `demo-ci` | `.github/scripts/required-ci-paths.json`，加 `required-ci.yml` 里的套件→工作流；结果和真跑一遍 `required-ci.py` 对过 |
| [话题预览](/dev/preview) | 凭证、cookie、房间访问权三步怎么换，谁在哪一步被挡 | `demo-flow` | `backend/app/api/preview_host.py` 与 `domain/site/hosting.py` 里的 TTL、cookie 属性和每个请求的检查 |
| [部署拓扑](/dev/topology) | 滚动发版时正在跑的轮怎么交接，常驻那层为什么不断 | `demo-flow` | `backend/app/core/ownership.py`、`main.py` 的交接顺序、`handover_timeout_s` |
| [记忆](/dev/memory#limits) | 三个上限分别在哪一步拦住什么 | `demo-memory` | `domain/memory/files.py` 的常数，`gen/memory_limits.py` 读出来，和 `fit_index` / `limit_breach` 对过 |
| [记忆](/dev/memory) | 一轮里记忆怎么流转 | `demo-steps` + `embed: memory` | 这一页各节；画面是剧本 `scenes/memory.json` |
| [设备与机器接入](/dev/machines) | 一台机器怎么接进来、出错时怎么办 | `demo-steps` + `embed: machines` | 这一页各节；画面是剧本 `scenes/machines.json` |
| [计费流程](/dev/billing) | 两道刹车各在什么时候拦 | `demo-sim` | 这一页「额度 = 花费 ÷ 每额度价格」的折算 |

## 页面组件 {#blocks}

除了交互演示，页面还能用四种写法，都由 `build.mjs` 在渲染 Markdown 时展开成静态 HTML，不需要脚本也完整。

| 写法 | 展开成 | 文字版里 |
|---|---|---|
| `:::steps` … `:::`，里面每个 `###` 标题开一步 | 带编号和竖线的步骤列表（`ol.steps`），标题照常进目录 | 去掉 `:::` 两行，剩下普通的 `###` 小节 |
| `:::cards` … `:::`，里面一个列表，每项 `- [标题](/页面#锚点)：一句话` | 两列链接卡片，和首页的卡片同一套样式；窄屏一列 | 去掉 `:::` 两行，剩下链接列表 |
| `:::before` … `:::` | 开头一句下面的灰框，写开始之前需要什么 | 去掉 `:::` 两行 |
| `:::walk` … `:::`，里面一列编号步骤和一个 `demo-panel` | 见[交互演示](#demos) | 步骤原样，画面换成它的文字版 |
| `:::map` … `:::`，里面一个嵌套列表，每项 `- 名字：说明` | 框套框的关系图：子列表画在上一层的框里，同层的框上下排；冒号前的名字加粗 | 去掉 `:::` 两行，剩下嵌套列表 |
| 语言写 `prompt` 的代码块 | 一条「发给芝士」的消息，正文用正文字体、自动换行，右上角复制按钮 | 原样保留 |
| 第一行是 `[!TIP]`、`[!IMPORTANT]`、`[!NOTE]` 或 `[!WARNING]` 的引用 | 提示、重要、说明、注意四种提示框；不带标记的引用仍是说明框。一页最多三个，多了构建失败 | 原样保留 |

- 写错种类会让构建失败：`:::` 后面只认 `steps`、`cards`、`before`、`walk` 和 `map`，提示框只认上面四种，`:::cards` 里只能有一个列表且每项以链接开头，`:::steps` 里至少有一个 `###`。
- 「文字版」是每页的 `.md`、`llms.txt`、搜索索引和 `ask-index.json` 用的那一份，和演示的文字版同一条路（`renderMarkdown` 里的 `text`）。
- 复制按钮和代码块共用 `src/app.js` 里同一个 `[data-copy]` 处理，复制的是消息正文。
- 样式只用设计系统的 token（`--accent-wash`、`--fill`、`--line` 等），深浅色跟着走。
- 什么时候用哪一种，写在 `.agents/skills/cheese-docs-writing/SKILL.md` 的「每类页面的骨架」和「画面」两节。

## 首页与截图 {#home}

首页（`docs/site/src/home.mjs`）不写死内容：分区卡片来自 `structure.mjs`，常见问题来自 `troubleshooting.md`，更新来自 git 历史，`build.mjs` 只负责把数据传进去。

- 使用文档里的界面截图由 `shots/shots.mjs` 在 `shots/fixture.py` 造出的示例项目里拍（需要本地全套服务），落到 `docs/manual/public/images/`。截图是真实界面，页面上的本地地址会换成 `https://okcheese.com`。

## 在文档站登录 {#sign-in}

文档站在自己的域名上时，读不到平台的登录：平台的访问令牌在应用的 `localStorage` 里，那是另一个源。所以平台把登录转交过去，做法和项目网站、话题预览的内容域一样（见[话题预览](/dev/preview#grant)）：

1. 文档站需要知道是谁时（问芝士回 401、开发文档的门回 401），把读者送到 `/api/docs/signin?path=<当前页>`，后端再 303 到平台的 `/docs-signin`：平台在哪由后端的 `FRONTEND_URL` 说了算，不写死在静态页里。
2. 那一页（`frontend/src/views/DocsSignIn.vue`）在平台上：没登录先去登录，回来接着走；登录了就 `POST /api/docs/grant`，拿到一张 30 秒、只能用一次的凭证，写着这个人和他这一次登录（`sid`）。
3. 页面把凭证用表单 `POST` 到文档站的 `/api/docs/session`，凭证不进 URL、历史和 Referer。文档站验签、在 Valkey 里记下这张凭证已用（Valkey 不可用就拒绝，不放行），再确认那次平台登录还在，然后发一张 cookie，303 回到原来那页。
4. 之后文档站上要认人的请求都带这张 cookie，后端每次都重新认（`access.reader`）：签名、受众（就是这个文档站的源）、请求进来的 Host 必须是文档站，以及签发它的那次平台登录还活着。

cookie 名 `__Host-cheese-docs`：HttpOnly、Secure、`Path=/`、不带 `Domain`，所以浏览器只把它发回文档站自己，平台收不到；就算有人把它拿到平台的域名上用，Host 对不上也不认。有效期 8 小时（`DOCS_SESSION_SECONDS`），但它活不过平台那次登录：在平台退出、在设备列表里移除那台设备、改密码，下一个请求文档站就不认了，不用通知文档站。

文档站和平台是**同站**（同一个可注册域 `okcheese.com`），不是同源。SameSite 管的是跨站，挡不住 `okcheese.com` 下别的子域名带着 cookie 发请求，所以靠 Origin：

- 换 cookie 的那一下，`Origin` 必须是平台的源，别的页面没法替读者登录（也就没法把人登到别人的账号上）。
- 文档站上会改东西的请求（问芝士要花读者的额度），`Origin` 必须是文档站自己。
- cookie 用 `SameSite=Lax`，不用 `Strict`：从别处的链接打开一页开发文档是跨站的顶层跳转，`Strict` 时这一下不带 cookie，管理员会先看到门。

没配 `DOCS_ORIGIN` 时文档站就在平台的 `/docs/` 下，走的还是这一套，只是「文档站的源」就是平台的源。文档站自己从来不碰平台的令牌，也不替平台刷新登录。

## 开发文档只给平台管理员 {#dev-access}

静态文件自己不认人，所以每个开发文档的文件（页面、搜索索引、`.md` 原文、架构图）nginx 都先用 `auth_request` 问后端 `GET /api/docs/dev-access/check`，后端按上面那张 cookie 答：

- 204：登录着，而且是平台管理员。管理员名单最多缓存 60 秒，所以被移出管理员的人一分钟内就进不去了。
- 401：没登录，或者 cookie 不作数了。nginx 换成提示页（`dev-gate.html`），提示页自己去平台走一趟登录再回来；一分钟内已经走过一趟还是 401，就停下来给一个「登录」按钮，不来回跳。
- 403：登录了，但不是管理员。提示页说明这一点。

开发文档用 `location ^~` 声明（`/docs/dev/`，或文档站上的 `/dev/`），这样对 `.md` 的正则规则不会绕过鉴权。

## 问芝士 {#ask}

`POST /api/docs/ask`，需要在文档站登录（[在文档站登录](#sign-in)），以 server-sent events 流式返回：`sources`（这次回答可以引用的页面）、`tool`（模型正在搜什么、正在读哪一页）、`delta`（文字）、`error`、`done`。

1. **准入**：同一个人同一时间只能有一个问题在答（`limits.py`，Valkey；不可用时拒绝，不放行）；个人额度用完时拒绝，提示哪天重置；网关上问芝士的模型没有单价时，无法计费，也拒绝。每个进程同时最多答 8 个，满了立刻返回「忙」，不排队。问多少由个人额度决定，不另设次数上限。
2. **模型自己查文档**（默认，`DOCS_ASSISTANT_AGENTIC=true`）：给它三个只读工具（`tools.py`，形状照 OpenAI 的文档服务）——`search_docs`（用 `retrieval.py` 检索 `sections.json`，返回最相关的六节：标题、小节、链接、摘录）、`fetch_doc`（读一页的 `.md` 原文；链接带 `#小节` 时只返回那一节和相邻小节）、`list_docs`（列出全部公开页）。检索词由模型自己换：口语换成文档的说法、英文换中文关键词、代词换成上一轮的对象——「那怎么把他移出去？」这种追问，一次检索是接不上的。最多四轮工具调用，第五轮不带工具、必须作答。
3. **只引用读过的**：答案里只保留这一轮搜到或读过的页面的链接，别的链接降级成文字（`assistant.filter_links`）；工具返回的内容一律转义后当数据交给模型（`_escape`），页面里写什么都成不了指令。`sources` 最终是模型真正读过的页面，在答案开始前给一次、`done` 之前再给一次。工具参数一律不开思考（`thinking: disabled`）：这个模型会把输出额度花在思考上，搜不动；网关不认这个参数时去掉重试一次。
4. **旧路径**（`DOCS_ASSISTANT_AGENTIC=false`）：一轮检索把段落塞进系统提示词，最高分低于 `MIN_SCORE`、或命中的词少于两个时，直接回答「文档里没有讲到」，不调用模型。这条路上模型拿不到工具，成本也只有一次调用。
5. **回答**（`assistant.py`）：系统提示词只让模型根据搜到和读到的内容回答，拒绝无关请求，只说读者问的那种语言，最多 250 字；旧路径里段落和问题里的尖括号会被替换，模型无法闭合或伪造 `<docs>` 区块。最多输出 700 个 token，温度 0.2。模型、长度等参数由后端固定，调用方改不了。
6. **计费**：调用走平台网关，用一个专为问芝士签发的虚拟 key（`service_credentials` 表，首次使用时签发，多进程用 advisory lock 保证只签一次），限 120 rpm，不设预算；上游 key 不出网关。网关上一个别名（这里是 `docs-assistant`）只能挂一个 key，key 的明文也只在签发时给一次，所以表里没有这一行时，签发前先删掉网关上占着这个别名的旧 key。删掉这一行，下一次提问就按当前的限额重新签发。花费记在提问的人头上，从他的个人额度里扣（见[计费流程](/dev/billing#personal)）。agent 这一路上一个问题的 token 是几轮之和，一并扣。
7. **记录**：每个问题一行 `docs_questions`：问了什么、有没有答上、引用了哪些段落、用了多少 token、花了多久。`outcome = no_match` 的问题就是文档该补的地方。90 天后由后台任务清理（`DOCS_QUESTION_RETENTION_DAYS`）。调用了模型的问题另记一行 `resource_usage`（没有项目，记上提问的人），和扣额度在同一个事务里。

浏览器端只渲染一小部分 Markdown，并且只保留指向这次回答可以引用的页面的链接。`tool` 事件显示成「正在搜：…」「正在读：…」：模型还在查的时候是展开的几行，一开始作答就折成一行「查了 N 步」（`src/walk.mjs`）。

**划词问芝士**：在文档页选中一段正文，选区旁出现「问芝士」按钮；点它会打开面板，并把选中的文字作为引用带进下一个问题（请求里的 `quote`，最多 600 字）。旧路径用「引用 + 问题」一起检索，引用和问题一样转义后放在 `<docs>` 围栏之外，只作为「问的是什么」，不作为回答依据；agent 这一路引用和问题一起交给模型，它会自己决定搜什么。

## AI 队友查文档 {#agent-docs}

AI 队友在平台里回答「怎么用」的问题时，用两个平台工具查文档：`cheese_docs_search`（关键词检索，和问芝士同一份索引、同一套排序）和 `cheese_docs_read`（读一页的 `.md` 原文）。后端接口是 `POST /docs/agent/search` 和 `POST /docs/agent/read`（`backend/app/domain/docs_site/library.py`）。

- 使用文档对所有项目开放。
- 开发文档只对「在做知是本身」的项目开放：项目绑定的仓库在 `DOCS_DEV_REPOSITORIES` 里（默认 `SageSeekerSociety/cheese`）。别的项目检索不到开发文档，点名读 `dev/…` 返回 403。
- 两个工具返回的链接是绝对地址，落在文档站上（配了 `DOCS_ORIGIN` 就是那个域名），芝士可以原样交给人。
- 开发文档的索引 `dev/sections.json` 和 `.md` 原文都在 `dev/` 的门后面。后端从前端容器读它们（按 compose 服务名 `frontend`，配了文档站时这个名字由文档站那个 server 接，读到的就是读者看到的那一份），带一张内部通行证（`access.internal_pass`，单独的 audience，五分钟有效），门的检查接口认它；浏览器拿不到这种通行证。

## 相关设置 {#settings}

全部见 [环境变量全表](/dev/ref-env)，以 `DOCS_` 开头：`DOCS_ORIGIN`（文档站自己的域名，如 `https://docs.okcheese.com`；空就在平台的 `/docs/` 下。compose 部署在部署环境 `~/ops/deploy.env` 里设一次，后端和前端容器都从那里读；同一处还要有 `FRONTEND_URL`，和后端的 `FRONTEND_URL` 一样，前端容器靠它填平台的源）、`DOCS_INDEX_URL`、`DOCS_DEV_INDEX_URL`（不设就读本部署的前端容器）、`DOCS_DEV_REPOSITORIES`、`DOCS_ASSISTANT_MODEL`、`DOCS_ASSISTANT_AGENTIC`（默认 `true`；`false` 走一轮检索的旧路径）、`DOCS_ASSISTANT_BUDGET_USD`、`DOCS_ASSISTANT_HOURLY_LIMIT`、`DOCS_ASSISTANT_DAILY_LIMIT`、`DOCS_ASSISTANT_CONCURRENCY`、`DOCS_QUESTION_RETENTION_DAYS`、`DOCS_SESSION_SECONDS`。网关地址和管理密钥沿用 `LLM_GATEWAY_ADMIN_BASE`、`LLM_GATEWAY_ADMIN_KEY`；没配置时问芝士显示暂未开放。
