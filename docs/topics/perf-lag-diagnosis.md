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

- **后端未读统计不是 N+1**：`<&backend/app/domain/topic/repositories.py>` 的 `unread_counts` 是单条 group-by SQL。
- **消息接口分页是 cursor 式的，reactions 是批查询**，`<&backend/app/api/routes/topics.py:332>`，写得干净。
- **轮询频率不高**：每 30 秒一次未读 + 话题列表（`<&frontend/src/views/workspace/ProjectShell.vue>`）。
- **路由本身是懒加载的**（104 处 `() => import`），页面 chunk 都在 15–125 KB，第二次进有 SW 的 CacheFirst 兜着，不是主要成本。
- **GitHub issue 里没有任何一条在跟性能**，不是重复劳动。

## 待验证的怀疑（已派人在查）

- **聊天消息的 markdown 每次重渲染都重新解析一遍**：`<&frontend/src/components/ChatPanel.vue:1620>` 是 `v-html="renderMarkdown(m.content)"`——模板里直接调函数，不是 computed，`<&frontend/src/lib/renderMessage.ts>` 里也没有任何记忆化。而这条链路是 marked + Prism 高亮 + KaTeX + DOMPurify 消毒。屏幕上 50 条消息、agent 流式输出时高频重渲染，这个开销是多少还没量。**这条是我的怀疑，未证实。**
- **后端中间件叠了 4 层** `BaseHTTPMiddleware`（`<&backend/app/main.py>`），Starlette 的这个基类每层都要把响应过一遍 memory stream。对几百 KB 的响应有多少额外延迟，在测。

## 进展 / 分工

| 活 | 归谁 | 状态 |
|---|---|---|
| 前端首屏 + 页面切换链路 | 房间自己 | ✅ 已完成，结论见上 |
| 外部对照：multica / agent-orchestrator 的加速设计 | 分身 | 🔄 进行中 |
| 后端 5 个热点接口体检（SQL 条数、执行计划、响应体积、中间件开销） | 分身 | 🔄 进行中 |

## 下一步（待 @符露夀 / @andy 拍板要不要做）

诊断完成后会给出方案。目前看**性价比最高的三件事**，按先后：

1. **给页面 router-view 加 keep-alive** —— 一处改动，来回切页面立刻不再重新加载。风险是内存和「回来时数据是旧的」，需要配一个「回来时后台静默刷新」的约定。
2. **把 blockCache 那套「先渲染缓存、后台刷新」推广成通用的一层**，覆盖总览/文档/成员/任务这些页面。现在这套思路已经在聊天上验证过了，是我们自己的，不用引第三方库。
3. **首屏瘦身**：编辑器改成按需加载（不在全局 plugin 里注册），SW 预缓存把 monaco 排除掉。

这三条互相独立，可以分开做、分开验证。
