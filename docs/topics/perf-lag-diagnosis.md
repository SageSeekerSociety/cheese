## 目标

@符露夀 反馈「平台卡卡的」，补充说明是「**每进一个页面都要加载，感觉很麻烦**」。怀疑是预加载/缓存没做好。

这个话题回答两件事：

1. **我们到底卡在哪** —— 用数据说话，而不是先入为主认定是缓存问题。
2. **multica 和 agent-orchestrator** 这两个同类开源项目在「让界面感觉快」上是怎么设计的，能抄什么。

## 结论：确实是缓存没做，而且是最基本的那一层没做

@符露夀 的直觉是对的，而且比「缓存没做好」更准确的说法是：**页面级的缓存压根没有**。已核实的三条，构成一条完整的因果链：

### 1. 页面组件不保活，切走就销毁

`<&frontend/src/App.vue>` 里有三处 `<keep-alive>`，但**包的全是顶栏、左侧 rail、底部 tab 这些外壳**。真正装页面的那个 `<router-view />`（在 `<v-main>` 里，App.vue:33）**没有被 keep-alive 包**。嵌套的两层也一样没有：`<&frontend/src/layouts/RouterPassThrough.vue>`、`<&frontend/src/views/workspace/ProjectShell.vue>`。

后果：**每一次页面跳转，当前页组件被整个销毁；回来时是一个全新的组件，状态全丢。** 浏览器点「后退」也一样，不存在「回到刚才那一屏」。

### 2. 每个页面都是「先清空、再转圈、再拉数据」

各个视图是同一个写法：`onMounted(load)` → `loading.value = true` → 请求 → 渲染。模板里 `v-if="loading"` 直接把内容换成一个转圈。核对过的页面：`OverviewView.vue`、`ProjectDocsView.vue`、`MemberView.vue`、`CalendarView.vue`、`ProjectAgentsView.vue`，无一例外。

其中总览页 `<&frontend/src/views/OverviewView.vue>` 每次挂载并发打 **4 个请求**（overview / inbox / contributions / credits）。这四个请求的结果**一次都没有被缓存**——第二次进这个页面，和第一次进是完全一样的成本。

同理，切换项目时 `<&frontend/src/stores/workspace.ts>` 的 `openProject` 会把 `topics / members / unreadMap` 全部清空再重拉。

### 3. 已有的缓存只覆盖两个地方，其余是空白

不是完全没做——`<&frontend/src/lib/blockCache.ts>`（按话题缓存聊天消息窗口，带后台刷新和 2 条并发闸，写得很讲究）和 `<&frontend/src/lib/projectCache.ts>`（项目列表进 sessionStorage）都在。**但它们只覆盖了「聊天消息」和「项目列表」两样东西**，文档、任务、成员、总览、日历、算力…… 这些页面一个都没有。

所以现象才会是「**每进一个页面**都要加载」——恰好就是没被这两层覆盖到的那些页面。

## 次要发现：首屏 4.18 MB，其中约 1.6 MB 是首屏用不到的

本地实测（`pnpm build`，dist 共 26 MB）：

- **首屏要下 19 个文件，4.18 MB 原始 / 1.13 MB gzip。** 原始体积才是浏览器要解析执行的量，这部分是主线程卡顿的直接来源。
- 其中 **富文本编辑器占 1.09 MB**：`vuetify-pro-tiptap` 570 KB + `prosemirror` 281 KB + `tiptap` 241 KB。原因是 `<&frontend/src/plugins/index.ts>` 里 `app.use(vuetifyProTipTap)` 是**全局无条件注册**的，连登录页都要下。
- **katex 523 KB** 也在首屏。
- **Service Worker 预缓存 336 个文件、12.5 MB**，其中最大的一块是 `monaco` 编辑器 **4.13 MB**——它被 `globIgnores` 漏掉了（只排除了 `*.worker-*.js`），所以哪怕你从不打开代码面板，后台也会替你下完这 4 MB。配置在 `<&frontend/vite.config.ts>` 的 workbox 段。

## 也查了但不是主因（排除掉，省得后面有人重查）

- **后端未读统计不是 N+1**：`<&backend/app/domain/topic/repositories.py>` 的 `unread_counts` 是单条 group-by SQL。**但「不是 N+1」不等于「不慢」——那一条 SQL 自己就要 217 毫秒，见下面「后端实测」一节。**
- **消息接口分页是 cursor 式的，reactions 是批查询**，`<&backend/app/api/routes/topics.py:332>`，写得干净。
- **轮询频率不高**：每 30 秒一次未读 + 话题列表（`<&frontend/src/views/workspace/ProjectShell.vue>`）。
- **路由本身是懒加载的**（104 处 `() => import`），页面 chunk 都在 15–125 KB，第二次进有 SW 的 CacheFirst 兜着，不是主要成本。
- **GitHub issue 里没有任何一条在跟性能**，不是重复劳动。

## 外部对照：multica 和 agent-orchestrator 怎么做的

完整报告在 <&docs/topics/perf-external-comparison.md>（560 行，8 个机制维度逐条给了「仓库:文件:行号」级证据，所有「他们没做 X」都附了搜索命令）。对我们最有用的五条：

1. **两家都有一层专门的数据缓存层（TanStack Query），我们没有。** 这正好对上我们实测出的病根。两家的取舍不同：multica 设 `staleTime: Infinity`——缓存里的数据就是权威，只有 WebSocket 事件才能让它失效；agent-orchestrator 设 `staleTime: 10s` 做真正的 stale-while-revalidate，因为它跑在本机，重取几乎不要钱。**我们跨公网，处境更像 multica，但我们的推送覆盖面远不如它**，所以不能直接照抄 `Infinity`。
   顺带一条代价警示：multica 自己踩过——某个缓存 key 漏在失效前缀外，因为 `staleTime: Infinity` 完全靠推送，结果收件箱显示了新评论、时间线却不显示。**这个设计里每漏一个事件就是一个可见 bug。**
2. **语法高亮：agent-orchestrator 把语言手工枚举成 13 个，放在动态 `import()` 后面**，注释原话是「Adding a grammar is a bundle decision」（加一门语言是一个包体积决定）。判据也给得很好：列表应该是**编码 agent 实际会吐出的语言**，不是高亮库提供的语言。正对我们的 `prismjsPlugin({ languages: 'all' })`——**这是全篇性价比最高、改动成本最低的一条**，纯 Vite 配置 + 一个 `import()`，不依赖任何框架。
3. **两家都不对「发消息」做乐观更新**，multica 还专门写了段注释论证为什么要等服务端回了再渲染。乐观更新的正确位置是「已读 / 置顶 / 归档 / 排序」这类幂等翻转。所以别被「乐观更新 = 快」带偏。
4. **agent-orchestrator 的重取并发闸**：每个缓存 key 最多一条请求在飞 + 一条排队，不取消已经在飞的，外加 150 毫秒的合并窗口。我们 `blockCache.ts` 已经有「一次只放两个」的并发闸，缺的正是「同一个 key 不重复排队」这一半。
5. **两家的轮询都是条件化的**（比如只在「有 agent 在线」时才轮询），而且都靠 TanStack Query 的默认行为拿到「页面切到后台就不轮询」。**我们是无条件 30 秒，而且因为没有那个库，这个默认我们不会自动拥有**——得自己接 `document.visibilityState`。

一条纠偏，值得记下来：multica 的数据库读副本机制虽然实现完整，但今天只有 2 个调用点，连声明它的那个页面自己都没用上。**不要照着一个别人自己都没接上的机制改架构。**

两家还有两件事都**没有**做，可以省掉我们的纠结：没有 CI 包体积闸门（首屏靠框架路由分包 + 个别 MB 级依赖手工 `import()`），也没有 web vitals 埋点。multica 倒是有个别的东西——一个「卡顿探测器」，专门抓超过 2 秒的主线程长任务。说明这类产品真正该测的是**运行时冻结**，不是首屏分数。

## 后端实测：一条 SQL 吃掉 217 毫秒，每 30 秒来一次

完整报告在 <&docs/topics/perf-backend-hotpath.md>（652 行）。测法是造了一个真实规模的库（10 个项目 / 2230 个话题 / **99 万条消息** / 625 MB），真 uvicorn 真 HTTP，40 次采样 + 8 轮预热，SQL 条数是数出来的不是推的。

### 头号发现：未读红点的那个请求在全表扫描

`GET /projects/{id}/topic-unread`——就是每 30 秒给侧栏红点用的那个——在 99 万条消息上走**全表扫描**：扫 44 万行只为算出 125 个数字，读了 63666 个数据块（约 500 MB，其中 4 万多块是真从磁盘读的），p50 **217.6 毫秒**。

**每 30 秒一次，每个开着的标签页各来一次。**

加一条对症的索引之后：变成索引扫描、不回表，数据块 63666 → 1295（**降 98%**），端到端 **217.6 → 39.8 毫秒**。

最值得记的一点：**这个开销随平台总量涨，不随你的项目大小涨**。同一条查询在一个只有 10.8 万条消息的单项目库上只要 45 毫秒。所以在小的测试环境里它永远不会暴露——只有平台上人多了才会显形，而那正是最不该慢的时候。

对照组很能说明问题：`private-unread` 跑的是**同一套逻辑**，却只要 10 毫秒——因为私聊那个条件先把话题收窄到 30 行，数据库于是选了另一种执行计划。同样的代码形状，相反的结果。

### 其余四个接口

- `members`（成员名册）有**真 N+1**：35 条 SQL 里 30 条是重复的，占这个接口耗时的 31%。
- `blocks?limit=50` 里的 `total` 是**死重量**：占该接口 SQL 时间的 38%，而且是唯一随房间历史增长的那部分——**前端根本没人读它**。
- 话题列表：小问题，`_last_activity()` 算了两遍（3.3 毫秒）。
- `private-unread`：**明确没得省**，已经很干净。

### 两个被推翻的怀疑（省得后面有人重查）

- **中间件不是问题。** 我上一轮怀疑后端叠的 4 层中间件在拖慢大响应。实测：4 层加起来 0.645 毫秒，2 MB 的响应上也只有 6.68 毫秒（3.4%）。相比那条 207 毫秒的 SQL 不值一提。**别再有人去动那 4 层。**
- **响应体积也不是问题。** 用我们自己的 nginx 配置复现真实边缘，120 KB 的响应出去只有 10.1 KB。想砍字段能省 23% 原始字节，gzip 之后只剩 0.5% 差别。

### 顺带发现一个悬崖（不是当前的 bug）

`<&frontend/nginx.conf>` 没设 `gzip_proxied`，而 nginx 这个选项默认是关的——意味着**只要有任何前置代理加一个 `Via` 头，全站压缩会整个静默失效**（实测 10116 字节 vs 122709 字节）。今天没人加 `Via`，所以它是个悬崖不是坑，但一行配置就能填掉。

## 待验证的怀疑（已派人在查）

- **聊天消息的 markdown 每次重渲染都重新解析一遍**：`<&frontend/src/components/ChatPanel.vue:1620>` 是 `v-html="renderMarkdown(m.content)"`——模板里直接调函数，不是 computed，`<&frontend/src/lib/renderMessage.ts>` 里也没有任何记忆化。屏幕上 50 条消息、agent 流式输出时高频重渲染，这个开销是多少还没量。**这条是我的怀疑，未证实。**
  - **一处自我更正**：上一版这里写的是「这条链路是 marked + Prism 高亮 + KaTeX + DOMPurify」，不对。聊天正文走的是 `<&frontend/src/lib/markdown.ts>`，它**只有** marked + CJK 扩展，注释里白纸黑字写着「chat code blocks are NOT highlighted and math is NOT rendered」。带 Prism 和 KaTeX 的那个是 `<&frontend/src/components/chat/services/markdownRenderer.ts>`，服务于 AI 建议那一路，不是聊天主链路。所以这条的量级比我原先说的小，仍值得量，但优先级降一档。
- **后端中间件叠了 4 层** `BaseHTTPMiddleware`（`<&backend/app/main.py>`），Starlette 的这个基类每层都要把响应过一遍 memory stream。对几百 KB 的响应有多少额外延迟，在测。

## 进展 / 分工

### 诊断（已完成）

| 活 | 状态 |
|---|---|
| 前端首屏 + 页面切换链路 | ✅ 结论见上 |
| 外部对照：multica / agent-orchestrator | ✅ <&docs/topics/perf-external-comparison.md> |
| 后端 5 个热点接口体检 | ✅ <&docs/topics/perf-backend-hotpath.md> |

### 动手修（2026-09-08 起，@符露夀 已拍板）

四条活并行，共用分支 `topic/c738f686`，路径互不重叠：

| 活 | 内容 | 状态 |
|---|---|---|
| 页面缓存 + 组件保活 | 新增一层「先拿缓存渲染、后台再刷新」的取数层，覆盖总览/文档/成员/日历/算力五个页面；给页面容器加保活（白名单，登录注册页排除） | 🔄 |
| 预加载 | 鼠标停在侧栏话题/导航上 120–200ms 就预取页面代码和消息；复用现有并发闸，省流量模式让路，触屏不误触，预取不改已读状态 | 🔄 |
| 首屏瘦身 | 富文本编辑器移出首屏（1.09 MB）、语法高亮改白名单（569 KB）、Service Worker 别再预下载 monaco（4.13 MB） | 🔄 |
| 后端热点修复 | 未读那条加索引（217→40 毫秒）、members 的 N+1、blocks 的死重量、nginx 压缩悬崖 | 🔄 |

## 下一步（待 @符露夀 / @andy 拍板要不要做）

诊断完成后会给出方案。目前看**性价比最高的三件事**，按先后：

1. **给页面 router-view 加 keep-alive** —— 一处改动，来回切页面立刻不再重新加载。风险是内存和「回来时数据是旧的」，需要配一个「回来时后台静默刷新」的约定。
2. **把 blockCache 那套「先渲染缓存、后台刷新」推广成通用的一层**，覆盖总览/文档/成员/任务这些页面。现在这套思路已经在聊天上验证过了，是我们自己的，不用引第三方库。
3. **首屏瘦身**：编辑器改成按需加载（不在全局 plugin 里注册），语法高亮从「全语言」改成显式白名单 + 动态 import（外部对照里性价比最高的一条），SW 预缓存把 monaco 排除掉。

这三条互相独立，可以分开做、分开验证。

### 一处需要说明的口径修正

<&docs/topics/perf-external-comparison.md> 第 525 行写着「我们现在还没证明『数据层缺失』是卡顿的主因」——那是写报告时房间的实测刚落盘、它没读到。**以本文档为准：数据层/页面级缓存缺失就是主因，不是待证。** 这不改变那份报告里「不要为此整体迁移到 TanStack Query」的建议——先手写一层薄的、覆盖那几个高频页面，验证收益之后再决定要不要付一个库的成本。
