# 外部对照：multica 与 agent-orchestrator 怎么让界面「感觉快」

## 这份文档回答什么

只回答一个问题：这两个同类产品为了让界面感觉快，各自在代码里做了哪些**具体的工程设计**，我们能抄哪几条、哪几条不适用。

按**机制**组织，不按项目。每条结论后面跟「仓库 + 文件路径 + 行号」；没找到的写「没有」，并写清我搜了什么关键词——「没有」本身是结论。

### 对照对象与版本

| 代号 | 仓库 | HEAD | 形态 |
|---|---|---|---|
| **multica** | `multica-ai/multica`（本机 clone 于 `/tmp/multica`） | `de5eaf5`（2026-09-08） | Go 后端 + Next.js 16 Web + Electron 桌面 + Expo 移动端；**多租户 SaaS**，前端跨公网连服务器 |
| **AO** | `Untrivial-ai/agent-orchestrator`（clone 于 `/tmp/agent-orchestrator`，11075 stars，Go） | `ddf1e54`（2026-09-07） | Go 本地 daemon + Electron 渲染进程（React 19 + TanStack Router/Query + zustand）；**本地单机应用**，前端连 `127.0.0.1` |

**读之前必须先记住这条形态差异**：AO 的前端和后端在同一台机器上，RTT ≈ 0，首屏是 `file://` 本地读盘。它做的很多事跟「网络慢」无关，抄之前必须先问「这条在跨公网的场景下还成立吗」。multica 是跟我们同构的 Web SaaS，参考价值更高。

两个项目**都**用 TanStack Query 作为数据层（`multica: packages/web/package.json` 依赖 `@tanstack/react-query`；`AO: frontend/package.json` 同）。我们用 Vue 3 + Pinia + axios，没有对应的库，所以下面每条「数据层机制」在建议一节都要单独算迁移成本。

---

## 机制 1：首屏与包体积

### multica：靠框架默认的路由级分包，重依赖只隔离了 mermaid；**没有**任何体积闸门

- Web 端是 Next.js 16 App Router，`apps/web/package.json:21` 的 build 脚本是 `next build --webpack`。Next 的 App Router 天然按路由段分包，这是它「首屏不大」的主要来源——不是手写的。
- `apps/web/next.config.ts:41-98` 是完整的 `nextConfig`：只有 `transpilePackages`、`images`、`rewrites`。**没有** `webpack.optimization.splitChunks`、没有 chunk 手工切分、没有 performance budget。
- 手写懒加载全仓**只有 2 处**：`packages/views/common/avatar-upload-control.tsx:26` 和 `packages/ui/components/common/quick-emoji-picker.tsx:8`，都是 emoji picker。（搜法：`grep -rn "next/dynamic\|React.lazy\|lazy(" apps/web packages/views packages/ui`）
- 真正的重依赖隔离只做了一处，但做得很自觉：**mermaid 动态 import**，`packages/views/editor/mermaid-diagram.tsx:43`：
  ```ts
  mermaidPromise ??= import("mermaid").then(({ default: mermaid }) => mermaid);
  ```
- 语法高亮是**静态**引入的，用 lowlight 的 `common`（37 种语言，不是 `all`），并且顺手关掉了自动语言探测——`packages/views/editor/syntax-highlight.ts:1-17`：
  ```ts
  const baseLowlight = createLowlight(common);
  export const codeLowlight = {
    ...baseLowlight,
    highlightAuto(value) { return baseLowlight.highlight("plaintext", value); },
  };
  ```
  注释原话：`highlightAuto` 会「run every registered grammar over the full block, which makes large documents expensive to mount」。这是一条 **CPU 成本**优化，不是体积优化，但直接决定长消息列表挂载时卡不卡。
- 骨架屏是段级的：`apps/web/app/[workspaceSlug]/(dashboard)/loading.tsx:1-19`，Next 会把它当成该路由段的 Suspense fallback。
- 但整个 dashboard 是客户端 SPA：`apps/web/app/[workspaceSlug]/(dashboard)/layout.tsx:1` 第一行就是 `"use client"`。也就是说 multica 没有用 RSC 做服务端取数，**它的「感觉快」全部押在客户端缓存 + WebSocket 上**，跟我们的处境一样。
- **CI 里没有体积闸门。** `.github/workflows/ci.yml` 的前端 job 是 `turbo build typecheck lint`（:282）加 `pnpm knip`（:311）。搜法：`grep -rn "bundlesize\|bundle-size\|size-limit\|maxSize\|budgets\|webpack-bundle" .github/ scripts/ apps/web/ packages/`，命中的全是 UI 组件的 `maxSize` 列宽属性，无一条与打包体积有关。knip 查死代码和幽灵依赖，算间接的体积卫生，但不是闸门。

### AO：路由级自动分包 + 两个重依赖动态 import + **手工枚举语法列表**；同样**没有**体积闸门

- `frontend/vite.renderer.config.ts:184-190`，TanStack Router 插件开了自动分包：
  ```ts
  TanStackRouterVite({
    routesDirectory: "./src/renderer/routes",
    generatedRouteTree: "./src/renderer/routeTree.gen.ts",
    target: "react",
    autoCodeSplitting: true,
  }),
  ```
- **没有** `manualChunks`、没有 `chunkSizeWarningLimit`。搜法：`grep -rn "bundlesize\|size-limit\|maxSize\|chunkSizeWarning\|manualChunks" .github/ scripts/ frontend/`，全仓仅命中一个 `ResizablePanel maxSize`。
- mermaid 动态 import，`frontend/src/renderer/lib/mermaid-diagram.ts:100`，理由写在 `:10-12`：「Mermaid is ~2MB, so it is dynamically imported on first use... A conversation without a diagram never downloads or parses it.」DOMPurify 也是用到才 import（`:142`）。
- **这条最值得抄**：语法高亮的语法包被单独拆成一个文件放在动态 import 后面，而且语言是**手工枚举 13 个**的，`frontend/src/renderer/lib/code-highlight-engine.ts:1-26`。文件头注释原话：

  > The list is what coding agents actually emit, not what highlight.js offers. Adding a grammar is a bundle decision — `common` (37 languages) is roughly four times this set, for languages that will not appear in an AO session.

  即：把「加一门语言」明确定义成一次**打包决策**，需要有人签字。对照我们的 `prismjsPlugin({ languages: 'all' })`，这是同一个位置上的相反选择。
- 需要打折扣的地方：AO 是 Electron，首屏 JS 从本地磁盘读，`file://` 没有网络传输。所以「AO 没做体积闸门」不能推出「体积闸门没必要」——它只是没有这个约束。
- CI 里唯一沾边前端质量的是 `.github/workflows/react-doctor.yml:32-34`，跑 `millionco/react-doctor`，但 `:6-12` 的 `paths` 限定只扫 `frontend/src/landing/**`（营销落地页），**不覆盖产品界面**。

**小结**：两家在首屏体积上都**没有 CI 闸门**，都靠「框架默认的路由分包 + 对个别巨型依赖手动 `import()`」。差别在语法高亮：AO 把它拆成独立 chunk 且只装 13 种语言，multica 装 37 种但静态、并关掉了自动探测。两家**都没有**把整套语言全打进首屏。

---

## 机制 2：数据缓存层

### multica：TanStack Query，`staleTime: Infinity` —— 缓存永不自然过期，新鲜度**完全由 WebSocket 事件驱动**

- `packages/core/query-client.ts:3-17`（全文）：
  ```ts
  export function createQueryClient(): QueryClient {
    return new QueryClient({
      defaultOptions: {
        queries: {
          staleTime: Infinity,
          gcTime: 10 * 60 * 1000, // 10 minutes
          refetchOnWindowFocus: false,
          refetchOnReconnect: true,
          retry: 1,
        },
        mutations: { retry: false },
      },
    });
  }
  ```
  `packages/core/provider.tsx:8-14` 把它挂在 `QueryClientProvider` 上。

  这是一个**很强的架构表态**：默认没有 stale-while-revalidate，缓存里的数据被当成权威，直到有人显式 invalidate。谁来 invalidate？WebSocket（见机制 5）。代价写在 `packages/core/realtime/use-realtime-sync.ts:670-676` 的注释里——曾经因为「per-issue 缓存 key 里不带 wsId，前缀失效够不着它们，而它们 staleTime: Infinity 完全靠 WS」，导致收件箱显示了新评论、issue 时间线却不显示（#3953）。**这个设计的每一个漏网事件都会变成一个可见 bug**。
- 缓存键是分层工厂，前缀键与全键分离，`packages/core/issues/queries.ts:45-105`：
  ```ts
  export const issueKeys = {
    all: (wsId) => ["issues", wsId] as const,
    /** PREFIX for invalidation — no sort. */
    list: (wsId) => [...issueKeys.all(wsId), "list"] as const,
    /** FULL KEY for queryOptions — includes sort. */
    listSorted: (wsId, sort) => [...issueKeys.list(wsId), sort ?? {}] as const,
    ...
  ```
  注释直接标注了哪个是「失效用的前缀」哪个是「查询用的全键」——因为排序/过滤参数进了键，不分层就没法批量失效。
- 换过滤条件/排序时不闪白：`placeholderData: keepPreviousData`，见 `packages/core/issues/queries.ts:297, 327, 369` 与 `packages/core/dashboard/queries.ts:89-91`。
- **没有**任何缓存持久化。搜法：`grep -rn "persistQueryClient\|createSyncStoragePersister\|createAsyncStoragePersister"` 全仓（排除 node_modules）零命中。
- **没有** Service Worker。搜法：`grep -rn "serviceWorker\|workbox\|next-pwa" apps/web packages` 零命中。

### AO：TanStack Query，`staleTime: 10s` —— 是真 SWR，但因为服务在本机所以近乎免费

- `frontend/src/renderer/lib/query-client.ts:3-18`（全文）：
  ```ts
  export const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 10_000,
        refetchOnWindowFocus: false,
        // AO talks to a localhost daemon, so its queries must run regardless of
        // the browser's online flag. React Query's default (networkMode
        // "online") pauses every query when navigator.onLine is false ...
        networkMode: "always",
      },
      mutations: { networkMode: "always" },
    },
  });
  ```
  `networkMode: "always"` 这条**明确是本地 daemon 专属**，对我们完全不适用（我们断网时就该暂停）。
- 键**没有**工厂，是散落的字面量数组：`["project", scopedProjectId]`（`frontend/src/renderer/routes/_shell.tsx:289`）、`["session", sessionId]`（`hooks/useWorkspaceQuery.ts:315`）、`workspaceQueryKey`（`hooks/useWorkspaceQuery.ts:171`）。规模比 multica 小得多，所以没付这份抽象成本。
- `gcTime` 未设置（用 TanStack 默认 5 分钟）。搜法：`grep -rn "gcTime" frontend/src/renderer` 零命中。
- **没有**缓存持久化（同样搜法零命中）。

**小结**：两家都不持久化缓存，都不在 focus 时重取。分歧点在 staleTime：multica 选 `Infinity`（推送即真理，代价是漏一个事件就是一个 bug），AO 选 `10s`（本机 RTT 让 SWR 几乎免费）。**我们的处境更接近 multica**（跨公网），但我们的推送覆盖面远不如它。

---

## 机制 3：预取

### multica：hover/focus 触发的**路由预取**（不是数据预取）+ 一处后台预热 + 一处结构性限流

- hover 与 focus 都触发，`packages/views/navigation/app-link.tsx:118-126`：
  ```ts
  const handleMouseEnter = (e) => { prefetch?.(href); onMouseEnter?.(e); };
  const handleFocus = (e) => { prefetch?.(href); onFocus?.(e); };
  ```
  列表行同理：`packages/views/navigation/use-row-link.ts:85` — `onMouseEnter: () => prefetch?.(href)`。
- 但 `prefetch` 具体做什么由平台适配器决定，Web 端是 `apps/web/platform/navigation.tsx:89-94`：
  ```ts
  // router.prefetch is a no-op in dev mode by Next.js design; in production
  // it warms the RSC payload + route chunk so the next push() commits with
  // no network round-trip. Safe to call repeatedly — Next dedupes internally.
  prefetch: (path: string) => { router.prefetch(path); },
  ```
  **注意**：这预热的是**路由 chunk 和 RSC payload**，不是业务数据。桌面端更是直接不实现（`packages/views/navigation/types.ts:35-36`：desktop「leaves it undefined because react-router already loads the whole SPA」）。
- 真正的**数据**预热只有一处，而且是「订阅式预热」而非 `prefetchQuery`：`packages/core/agents/use-workspace-presence-prefetch.ts:24-29`
  ```ts
  export function useWorkspacePresencePrefetch(wsId: string | undefined): void {
    useQuery({ ...agentListOptions(wsId ?? ""), enabled: !!wsId });
    useQuery({ ...runtimeListOptions(wsId ?? ""), enabled: !!wsId });
    useQuery({ ...agentTaskSnapshotOptions(wsId ?? ""), enabled: !!wsId });
    useQuery({ ...squadListOptions(wsId ?? ""), enabled: !!wsId });
  }
  ```
  注释说清了目的：不预热的话，第一次 hover 头像卡片 / 打开 @mention 列表会闪骨架屏。挂载点 `packages/views/layout/workspace-presence-prefetch.tsx:10-14`，只在「workspace 已解析」的子树里挂一次。
- `prefetchQuery` 全仓**没有**；只有 3 处 `ensureQueryData`（`packages/views/search/search-command.tsx:465,483`、`packages/core/realtime/use-realtime-sync.ts:542`、`apps/web/app/(auth)/login/page.tsx:133`），且 search-command 那处注释明说「`ensureQueryData` only fetches on a cold cache」。搜法：`grep -rn "prefetchQuery\|prefetchInfiniteQuery\|ensureQueryData"`。
- **并发限流：没有显式的**。搜法：`grep -n "concurren\|inflight\|dedupe\|semaphore" packages/core/api/client.ts` 只命中一条无关注释。唯一的限流是**结构性**的——图片预览只预热前后各一张邻居，`packages/views/editor/image-sequence-context.tsx:202-215`，靠 JSX 里只渲染两个 `<PreviewImagePrefetch>` 把并发钉死在 2。这跟我们 `blockCache.ts` 的 2 条并发闸是同一种思路，只是他们用组件结构表达、我们用信号量表达。

### AO：路由级 intent 预取（框架给的）+ 菜单打开时批量预取 + **一个真正的重取并发闸**

- `frontend/src/renderer/router.tsx:18,28`：
  ```ts
  defaultPreload: "intent",
  // Always re-run loaders when a route is preloaded or visited so React
  // Query's cache is the single source of truth for staleness.
  defaultPreloadStaleTime: 0,
  ```
  `"intent"` 就是 TanStack Router 的 hover/touch 意图预取。`defaultPreloadStaleTime: 0` 是一条重要的配合：把新鲜度判断**完全交给 React Query**，路由层不再自己缓存一份，避免两套 staleness 打架。
- 路由 loader 里带数据预取，`frontend/src/renderer/routes/_shell.tsx:57-65`：
  ```ts
  export const Route = createFileRoute("/_shell")({
    // Prefetch the workspace list for the whole shell (parent loaders run before
    // children); pairs with the router's defaultPreload: "intent" so a hovered
    // nav target is warm before the click.
    loader: async ({ context }) => { ...
      return context.queryClient.fetchQuery({ ...workspaceQueryOptions, staleTime: 0 });
    },
  ```
- **后台预热**：停在某个项目上时，提前把「新建任务」弹窗要用的模型目录取回来，`frontend/src/renderer/routes/_shell.tsx:284-309`，注释原话「so the picker never shows a loading flash the first time they actually open the dialog」。
- **菜单打开时批量预取**：`frontend/src/renderer/components/ReviewerSelect.tsx:108-119`，菜单一开就为每个候选 agent 预取模型目录。这是「比 hover 更早一格」的触发点。
- **并发限流：有，而且是本次调研里最值得抄的一段代码。** `frontend/src/renderer/lib/event-transport.ts:66-85`：
  ```ts
  // Do not repeatedly cancel a slow fetch under continuous CDC traffic. A
  // key receives at most one in-flight refresh and one queued catch-up.
  const refreshes = new Map<string, { dirty: boolean }>();
  const invalidate = (queryKey: QueryKey) => {
    if (disposed) return;
    const key = JSON.stringify(queryKey);
    const running = refreshes.get(key);
    if (running) { running.dirty = true; return; }
    // A fetch from polling/mounting may already predate this event. Wait
    // for it, then refresh once so joining its promise cannot lose the event.
    const state = { dirty: queryClient.isFetching({ queryKey, type: "active" }) > 0 };
    refreshes.set(key, state);
    const settled = () => {
      refreshes.delete(key);
      if (state.dirty && !disposed) invalidate(queryKey);
    };
    void queryClient.invalidateQueries({ queryKey }, { cancelRefetch: false }).then(settled, settled);
  };
  ```
  外加一层 150ms 批处理窗口（`:19` `const INVALIDATE_WINDOW_MS = 150;`，`:168-172` 定时器 flush）。**每个 key 最多一条在飞 + 一条排队**，且 `cancelRefetch: false` 保证不会取消一条已经跑了一半的请求——这正是「预取/失效把真实请求挤掉」的解法。
- 另一条限流不是限并发而是**限订阅**：`frontend/src/renderer/components/CommandPalette.tsx:60-63`
  ```ts
  // The palette stays mounted to preserve its close animation and global
  // shortcut. While closed, commands are invisible, so retain the cached
  // snapshot without subscribing this hidden surface to streamed updates.
  const workspaces = useWorkspaceQuery({ subscribed: isOpen }).data ?? [];
  ```
  `subscribed: false` 让「挂载但不可见」的组件**读缓存但不参与轮询**（同样用法见 `:93-97` 的 `useQueries` 与 `hooks/useWorkspaceQuery.ts:313`）。

**小结**：hover 预取两家都有，但**两家 hover 预取的都是路由/代码，不是业务数据**；业务数据预热都是「进入某个上下文后主动预热下一步要用的东西」。并发限流方面，multica 靠结构限死、AO 有一套完整的按 key 去重 + 批窗 + 不取消在飞请求的失效队列。

---

## 机制 4：乐观更新

### multica：全面乐观（78 处 `onMutate`），但**发消息这一条明确不乐观**

- 规模：`grep -rn "onMutate" packages apps`（排除测试与 node_modules）= **78 处**，覆盖 issues / inbox / labels / pins / projects / properties / autopilots / chat / notification-preferences / 移动端。
- 标准三段式（乐观写 + 回滚 + 收尾失效），`packages/core/chat/mutations.ts:144-161`：
  ```ts
  onMutate: async (sessionId) => {
    await qc.cancelQueries({ queryKey: chatKeys.sessions(wsId) });
    const prevSessions = qc.getQueryData<ChatSession[]>(chatKeys.sessions(wsId));
    const clear = (old?) => old?.map((s) => (s.id === sessionId ? { ...s, has_unread: false, unread_count: 0 } : s));
    qc.setQueryData<ChatSession[]>(chatKeys.sessions(wsId), clear);
    return { prevSessions };
  },
  onError: (err, sessionId, ctx) => {
    logger.error("markChatSessionRead.error.rollback", { sessionId, err });
    if (ctx?.prevSessions) qc.setQueryData(chatKeys.sessions(wsId), ctx.prevSessions);
  },
  onSettled: () => { qc.invalidateQueries({ queryKey: chatKeys.sessions(wsId) }); },
  ```
  注意 `cancelQueries` 是必须的第一步——不取消在飞的读，它落地时会把乐观值覆盖回去。
- **反过来，发消息是显式的「等服务端再渲染」**，`packages/views/chat/components/use-chat-controller.ts:565-570`：
  ```
  // Await-then-render: the composer keeps the user's text and attachments
  // in place (editor locked, button spinning via `submitting`) until the
  // server accepts the send. Nothing is written into the caches, and the
  // draft is never cleared, before the roundtrip settles — a slow send never
  // reads as "posted but the box is still full", and a rejected one keeps
  // the draft for retry (ChatInput never cleared it).
  ```
  服务端返回后再用**响应体**构造消息写进缓存（`:603-616`），并且用一个幂等的 upsert 单入口，注释在 `:612-615`：「idempotent by id, so this row and the `chat:message` echo of the same send converge in either arrival order」。
- 这是一条**有权衡的**结论，不是「他们忘了做」：**元数据类改动（已读/置顶/归档/改名/状态）乐观，内容类创建（发消息）不乐观**。理由是发消息可能被 403 拒（权限中途被撤），乐观渲染后回滚等于「消息消失」，比多等一个 RTT 更伤。

### AO：几乎不乐观（11 处 `onMutate`），发消息只乐观一个「正在发」的状态位

- 规模：`grep -rn "onMutate" frontend/src`（排除测试）= **11 处**，只分布在 3 个文件（`hooks/useShellTerminals.ts`、`hooks/useConversation.ts`、`components/SessionInspector.tsx`）。
- 发消息的 `onMutate` 不写消息，只写一条 dispatch 追踪记录，`frontend/src/renderer/hooks/useConversation.ts:300-316`：
  ```ts
  onMutate: (variables: ConversationSendMutationInput) => {
    queryClient.setQueryData<ConversationDispatchTrackingBySession>(
      conversationDispatchTrackingQueryKey,
      (current = {}) => { ... [variables.targetSessionId]: {
        operation: "send", requestId: variables.clientMessageId, state: "pending" } };
  ```
  即：只把 UI 切到「发送中」，消息本体等 daemon 回来。跟 multica 的 await-then-render 是同一个结论，不同实现。
- 唯一一处教科书式带回滚的乐观更新是**队列重排**，`frontend/src/renderer/hooks/useConversation.ts:608-628`：`cancelQueries` → 存 `previous` → `setQueryData` 重排 → `onError` 回滚 → `onSettled` 失效。拖拽排序必须乐观，否则手指抬起来会看到卡片跳回去。

**小结**：**两家在「发消息」这件事上都选择了不乐观。** 这是本次调研里最反直觉、也最该记住的一条：乐观更新用在**幂等的小状态翻转**上（已读、置顶、排序），不用在**可能被服务端拒绝的内容创建**上。

---

## 机制 5：实时性——推送 vs 轮询

### multica：WebSocket 为主，**增量 patch + 精准失效**双管；轮询只作条件性补充

- 传输：自研 `WSClient`，`packages/core/api/ws-client.ts:16-17` 指数退避 + jitter，1s 起、30s 封顶：
  ```ts
  const RECONNECT_BASE_DELAY_MS = 1_000;
  const RECONNECT_MAX_DELAY_MS = 30_000;
  ```
  注释解释了为什么不用固定间隔：服务端重启后所有客户端会同时回冲（thundering herd）。
- **增量**：WS 事件直接 patch 缓存，不重拉。例如删消息，`packages/core/realtime/use-realtime-sync.ts:402-427`，对普通列表缓存和 infinite 分页缓存**两份都改**：
  ```ts
  qc.setQueryData<InfiniteData<ChatMessagesPage> | undefined>(
    chatKeys.messagesPage(sessionId),
    (old) => ({ ...old, pages: old.pages.map((page) => ({
      ...page, messages: page.messages.filter((m) => m.id !== messageId) })) }),
  );
  ```
- **精准失效**：不能安全 patch 的（如 chat task 事件）就 invalidate 对应的 key，`use-realtime-sync.ts:143-144, 165, 205`；注释 `:152` 明说「invalidate, NOT an optimistic setQueryData」。
- **重连补账**：断线期间漏掉的事件靠一次全量失效补，`use-realtime-sync.ts:642-696`，把 22 类 workspace 作用域的 key 全部 invalidate，并额外处理「key 里不带 wsId 所以前缀够不着」的 per-issue / per-session 缓存（`:670-694`）。这是 `staleTime: Infinity` 必须配套的东西。
- **轮询**（补充，非主力），全部是**条件轮询**：
  - `packages/core/workspace/queries.ts:55-63`：agent 列表只在**有 agent 在线/不稳定**时才 30s 一轮，否则 `false` 不轮询。
    ```ts
    refetchInterval: (query) =>
      query.state.data?.some((agent) => !agent.archived_at &&
        (agent.runtime_availability === "online" || agent.runtime_availability === "unstable"))
        ? 30_000 : false,
    ```
  - `packages/core/dashboard/queries.ts:50-51`：`STALE_TIME = 60s`，`REFETCH_INTERVAL = 5min`；`:47-49` 注释明确写了「Neither fires for unmounted queries or backgrounded windows.」
  - 同样条件轮询的还有 `packages/core/billing/queries.ts:99`、`packages/core/runtimes/cloud-runtime.ts:64`、`packages/core/dingtalk/queries.ts:35,49`、`packages/views/onboarding/components/use-runtime-picker.ts:35`（拿到数据就停）。
- **页面不可见时是否停**：停。两家都**没有**设置 `refetchIntervalInBackground`（multica 搜法 `grep -rn "refetchIntervalInBackground"` 全仓零命中），而 TanStack Query 该项默认为 `false`，即窗口后台化时定时重取不触发。上面 `dashboard/queries.ts:49` 的注释就是在陈述这个默认行为。
- 服务端扇出：`server/internal/realtime/redis_relay.go:17-33` 用 Redis Stream（`XADD` + 每 scope 一条流 + 节点心跳）在多个 API 进程间转发 WS 事件，`heartbeatTTL = 90s / heartbeatPeriod = 30s`。这是「多副本部署时 WS 怎么不丢」的答案。

### AO：SSE（CDC 变更流）为主，**事件只当失效信号、不带业务数据**；轮询覆盖面反而更广

- 服务端：`backend/internal/httpd/events.go:98` 写 `text/event-stream`，`:184` 每帧带序号：
  ```go
  fmt.Fprintf(w, "id: %d\nevent: %s\ndata: %s\n\n", e.Seq, sseEventName(e.Type), data)
  ```
  `id:` 就是 CDC 序号，配合 `Last-Event-ID` 可断点续传；`:119` 还有心跳注释帧。
- 客户端订阅 10 类 CDC 事件，`frontend/src/renderer/lib/event-transport.ts:29-40`（`session_created` / `session_updated` / `pr_created` / `pr_updated` / `pr_check_recorded` / …）。
- **关键设计：事件不是数据，是信号。** `event-transport.ts:42-48` 的文件头注释：「Wires live server state into the TanStack Query cache... Both invalidate the `["workspaces"]` query so the UI refetches. Invalidations are batched because a single user action can emit a burst of CDC events.」——**全量重拉，但重拉被批处理和去重保护**（就是机制 3 里那段 `refreshes` map）。这跟 multica 的「增量 patch」是相反的取舍：AO 用一致性简单换掉了带宽，而它的带宽是本机回环。
- 断流兜底做得很细，`event-transport.ts:98-105`：当 daemon 的事件日志被截断（客户端起点被夹到队尾、没有 CDC 会补发）时，因为 `EventSource` 读不到那个报告截断的 header，**索性把所有会话全部失效**，宁可多拉也不让已打开的聊天冻在旧快照上。
- 重连退避：`frontend/src/renderer/lib/sse-backoff.ts` 的 `computeSseRetryDelayMs`，在 `event-transport.ts:180-187` 按重试次数递增调用（注释 `:175-177`：避免对一直拒绝连接的 daemon 以固定节奏猛敲，#4323）。
- **轮询**（覆盖面比 multica 广，因为本机轮询几乎免费）：
  - `frontend/src/renderer/hooks/useWorkspaceQuery.ts:170-176`：workspace 列表 `staleTime: 10s` + `refetchInterval: 15_000`
  - `hooks/useWorkspaceQuery.ts:268`：云会话 5s（注释：provisioning 状态没有客户端动作也会变）
  - `hooks/useSettings.ts:40`：15s
  - `hooks/useConversation.ts:1015`：配置项 5s，且**写入期间暂停**（`writing ? false : ...`，`:1028-1032` 用 `onMutate/onSettled` 把 `writing` 开关夹住这个窗口，防止轮询结果把刚改的值冲掉）
  - `components/settings/UpdatesSection.tsx:47`：3s
  - `hooks/useShellTerminals.ts:71` 有一条反向注释：「No refetchInterval: shell terminals only change when this client opens or...」——刻意不轮询。
- 页面不可见时：同样靠 TanStack 默认（`grep -rn "refetchIntervalInBackground" frontend/src` 零命中）。另有 `hooks/useDaemonStatus.ts:99` 在 `visibilitychange` 时**主动刷一次**（回到前台立刻对齐）。

**小结**：multica = WebSocket + 增量 patch + 条件轮询；AO = SSE + 全量重拉（但有批窗和并发闸保护）+ 广泛轮询。**两家都不把「轮询」当羞耻**，都保留了轮询作为推送的补充，区别只在于哪些数据值得轮、以及轮询是否条件化。

---

## 机制 6：分页与列表

### multica

- **聊天消息**：keyset cursor（`created_at` + `id` 复合游标），页大小 **50**，`packages/core/chat/queries.ts:147-158`：
  ```ts
  export function chatMessagesPageOptions(sessionId: string, limit = 50) {
    return infiniteQueryOptions({
      queryKey: chatKeys.messagesPage(sessionId),
      queryFn: ({ pageParam }) => api.listChatMessagesPage(sessionId, { before: pageParam, limit }),
      initialPageParam: null as { created_at: string; id: string } | null,
      getNextPageParam: (lastPage) => lastPage.has_more ? lastPage.next_cursor ?? undefined : undefined,
      enabled: !!sessionId,
      staleTime: Infinity,
    });
  }
  ```
- **issue 看板**：每个状态列 **50** 条，`packages/core/issues/queries.ts:241` `export const ISSUE_PAGE_SIZE = 50;`；首屏是 **7 个并行请求**（每个状态大类一个），`:268-273` 的 `Promise.all`。这个 7 是刻意钉死的，注释 `:249-254`：按「状态大类」分桶而不是按「状态」分桶，是为了让扇出**固定为 7**，否则工作区每加一个自定义状态就多一个请求。
- **issue 表格**：cursor 分页，页大小 **100**，`packages/core/issues/queries.ts:282-299`。
- **虚拟滚动：有，而且是主力。** react-virtuoso 用在聊天消息列表（`packages/views/chat/components/chat-message-list.tsx:333`）、issue 泳道视图（`packages/views/issues/components/swimlane-view.tsx:1493`）、列表视图、看板列、issue 时间线。
- 最值得读的是 issue 时间线的**双模式**，`packages/views/issues/components/issue-detail.tsx:3336-3368`：
  > Timeline entries — virtualized via react-virtuoso to keep first-paint cost O(viewport) instead of O(N). On a 500-comment issue the unvirtualized `.map` froze the page for several seconds (markdown parse + lowlight code highlight runs per CommentCard on mount).

  但当用户是**从收件箱深链进来要定位到某条评论**、或者按了页内 Cmd+F 时，改成平铺渲染——注释 `:3364-3367`：「virtualization and "land precisely on a target" have fundamentally opposed contracts (estimated heights vs real heights)」。这是一条**很实用的经验**：虚拟滚动和精确定位天然冲突，别试图在一条路径里同时满足。
- **单次响应体积**：本文档**没有实测字节数**（任务范围是读源码，不运行）。可推的量级是结构性的：聊天 50 条/页，看板首屏 7×50=350 条 issue 摘要。

### AO

- **会话时间线**：cursor（`beforeSequence`，一个单调序号），页大小 **200**，`frontend/src/renderer/hooks/useConversation.ts:165, 192-210`：
  ```ts
  const CONVERSATION_PAGE_SIZE = 200;
  ...
  query: { beforeSequence: pageParam, limit: CONVERSATION_PAGE_SIZE },
  ...
  getNextPageParam: (page) => (page.hasMoreBefore ? page.oldestSequence : undefined),
  select: (data) => mergeConversationPages(data.pages),
  ```
  页大小是 multica 的 4 倍——本机传输，所以敢一次拉 200 个 turn。
- **虚拟滚动：只有一处，且不在聊天上。** 唯一的 `useVirtualizer` 在 diff 视图，`frontend/src/renderer/components/WorkspaceDiffView.tsx:236`（`:180` 注释：「scrolled into view are mounted, via @tanstack/react-virtual」）。聊天时间线 `frontend/src/renderer/components/chat/ChatTimelineItems.tsx`（2606 行）**没有虚拟化**，也没有用 `content-visibility` 兜底。搜法：`grep -rn "useVirtualizer\|react-virtual" frontend/src/renderer` 只有 WorkspaceDiffView；`grep -rn "content-visibility\|contain-intrinsic" frontend/src` 零命中。
- 它靠的是别的：分页只加载首屏一页（200），加上大量 `useMemo` 缓存单条渲染结果（`ChatTimelineItems.tsx:136, 1061, 1112, 1637, 2397`），以及对超大内容硬性截断——`lib/mermaid-diagram.ts:36-40`：
  > Above this, the fence stays source text. Mermaid lays out synchronously on the render thread, and a pasted 100KB "diagram" is really a log file with the wrong label — rendering it would hang the timeline.
  >
  > `export const MAX_DIAGRAM_CHARS = 20_000;`

**小结**：分页两家都用 **cursor（keyset）不用 offset**。虚拟滚动 multica 全面铺开（因为它有 500 条评论的 issue），AO 只在 diff 上用（因为它的对话被 200 条一页限住、且本机加载快）。

---

## 机制 7：后端热点接口

### multica

- **未读数：单条 SQL 一次算出所有 workspace 的未读**，`server/pkg/db/queries/inbox.sql:149-170`：
  ```sql
  -- name: CountUnreadInboxByWorkspace :many
  SELECT newest.workspace_id, count(*) AS count
  FROM (
      SELECT DISTINCT ON (i.workspace_id, COALESCE(i.issue_id, i.id))
          i.workspace_id, i.read
      FROM inbox_item i
      JOIN member m ON m.workspace_id = i.workspace_id AND m.user_id = i.recipient_id
      WHERE i.recipient_type = 'member' AND i.recipient_id = $1 AND i.archived = false
      ORDER BY i.workspace_id, COALESCE(i.issue_id, i.id), i.created_at DESC
  ) newest
  ```
  注释 `:150-159` 讲了为什么必须是「按 issue 分组后取最新那条的已读状态」而不是数原始行——数原始行会让用户看着空收件箱、切换器却亮着红点（MUL-3695）。**这跟我们 `unread_counts` 是同一个结论**（单条 SQL、不 N+1）。
- **ETag + 304**：轮询密集的 daemon workspace 列表走条件请求，`server/internal/handler/daemon_workspace.go:69-76`：
  ```go
  etag := daemonWorkspacesETag(resp)
  w.Header().Set("Cache-Control", "private, no-cache")
  w.Header().Set("ETag", etag)
  if r.Header.Get("If-None-Match") == etag {
      w.WriteHeader(http.StatusNotModified)
      return
  }
  ```
  ETag 是响应体 JSON 的 sha256（`:83-87`）。注意它**只省带宽不省查询**——DB 已经查完了才算 ETag。
- **读副本路由 + 熔断**：`server/internal/dbreader/selector.go`，`:16-21` 两秒冷却的熔断器，`:31-35` 用不透明的 `Business` 类型限制指标标签基数，`:37-42` 强/弱一致性显式二选一，`:64-66` 挂 Prometheus。设计上「primary 是安全默认，想走副本必须显式调 `Read`」（`:68-70`）。
  但要看它**今天真正接了什么**：全仓只有 **2 个调用点** —— `server/internal/handler/daemon_workspace.go:30,53` 和 `server/internal/integrations/ghsnapshot/refresh.go:302`。`BusinessDashboard`（`selector.go:31`）**声明了但没有任何调用点在用**（搜法：`grep -rn "dbreader.Read(\|dbreader.Business" server/internal --include=*.go | grep -v _test`）。所以「multica 用读副本扛住了 dashboard」这个说法是**不成立**的。
- **服务端缓存**：有 Redis，但用在任务认领而不是读接口上。`server/internal/service/empty_claim_cache.go:34-45`，把「这个 runtime 当前没有排队任务」这个**否定结论**缓存 3 分钟，并用一个每次入队都 `INCR` 的版本号来做失效（`:13-33` 详细画了竞态时序）。作用是「让稳态空转的轮询不打 Postgres」。
- **超时**：HTTP server 的 `ReadTimeout` / `WriteTimeout` 被**刻意设为 0**，有测试锁住这个事实：`server/cmd/server/main_test.go:371-375`（`if got := srv.ReadTimeout; got != 0 { t.Errorf(...) }`）——因为要跑 WebSocket 和长连接。业务级超时是各处 `context.WithTimeout`（例如 `server/cmd/server/health.go:149` 健康检查 2s）。
- 限流有独立中间件：`server/internal/middleware/ratelimit.go`、`plugin_ratelimit.go`。

### AO

- **微缓存 + singleflight**：`backend/internal/service/session/workspace_cache.go:10-19`
  ```go
  // workspaceCacheTTL bridges the short window between a session's workspace
  // file list loading and the user immediately expanding a file, so the
  // expansion doesn't redundantly re-resolve the compare base and re-run the
  // same git status/diff/numstat calls the list request just made. It is not
  // the primary correctness mechanism for freshness — the filesystem watcher
  // that powers the workspace SSE stream invalidates entries the instant a
  // real change is observed ...
  const workspaceCacheTTL = 3 * time.Second
  ```
  **3 秒**——短到几乎不可能读到脏数据，长到足以吸收「列目录 → 立刻展开某个文件」这一串连击。真正的失效靠文件系统 watcher，TTL 只是 watcher 连上之前的兜底。配套 `golang.org/x/sync/singleflight`：`backend/internal/service/session/service.go:187` 的 `workspaceGroup singleflight.Group`，在 `workspace_files.go:934-965` 把并发的同 key 请求合并成一次 git 调用。
- 另有 `backend/internal/pricing/cache.go`、`backend/internal/adapters/scm/gitlab/cache.go`（第三方 API 结果缓存）。
- **超时**：`backend/internal/httpd/server.go:72-74`
  ```go
  // ReadHeaderTimeout guards against slow-loris even on loopback;
  ReadHeaderTimeout: 10 * time.Second,
  ```
  同 `lan_listener.go:167`。**没有** `WriteTimeout` / `TimeoutHandler`（同样因为要跑 SSE 和终端 WS）。
- **没有** Redis / 外部缓存——它是单机 daemon（SQLite via `better-sqlite3` / Go 侧 storage 包），谈不上。
- 客户端侧的「降级」倒是明确的：`hooks/useWorkspaceQuery.ts:178-181` 注释——云项目单独一条 query，「a control-plane failure can never break the local list: on error TanStack keeps this query's last known data」。以及 `hooks/useConversation.ts:173-178` 的 `PERMANENT_CODES` 集合，把「重试也没用」的错误码列出来直接不重试。

**小结**：热点接口上唯一**两家都做了**的事是「把高频查询压成一次调用」（multica 是一条 SQL 算全部 workspace，AO 是 singleflight 合并 git 调用）。ETag/304 只有 multica 有，且只用在一个 daemon 接口上。两家都**没有**在读接口前面挂通用的 Redis 缓存层。

---

## 机制 8：可观测

### multica：**有一个前端「卡顿探测器」**，但没有 web vitals

- **long task 观测器 → 上报 `client_unresponsive` 事件**，`packages/core/diagnostics/freeze-watchdog.ts`：
  ```ts
  const FREEZE_THRESHOLD_MS = 2000;   // :26
  const COOLDOWN_MS = 60_000;         // :35
  ...
  const observer = new PerformanceObserver((list) => {   // :52
    for (const entry of list.getEntries()) {
      if (entry.duration < FREEZE_THRESHOLD_MS) continue;
      const now = Date.now();
      if (now - lastEmitMs < COOLDOWN_MS) continue;
      lastEmitMs = now;
      captureEvent("client_unresponsive", {
        source: "longtask", duration_ms: Math.round(entry.duration), path: resolveDiagnosticPath(),
      });
    }
  });
  observer.observe({ type: "longtask" });   // :69
  ```
  几个细节值得抄：阈值 2s 的理由写在 `:23-25`（「正常切换/渲染实测 50–600ms，2s 意味着用户真的感到卡了」）；60s 冷却是因为一次长冻结会被浏览器拆成多条 longtask 条目，逐条上报会让事件量随冻结时长无界增长（MUL-3331）;不开 `buffered: true`，免得把慢启动误报成运行时冻结（`:67-68`）。
- **没有 web-vitals**。搜法：`grep -rn "web-vitals\|onLCP\|onCLS\|onINP\|reportWebVitals\|useReportWebVitals"` 全仓，唯一命中是一个测试 fixture JSON 里的字符串。
- 分析走 PostHog，且**把所有自动采集都关了**，`packages/core/analytics/index.ts:113-147`：`capture_pageview: false`、`autocapture: false`、`capture_heatmaps: false`、`capture_dead_clicks: false`，只留 `capture_exceptions: true`。理由在 `:119-124`（自动采集会淹没漏斗、烧事件预算、还可能采到用户输入内容）。异常还有脱敏（`before_send`）和会话级去重指纹（`:142-146`）。
- 后端：每请求 duration 结构化日志，`server/internal/middleware/request_logger.go:134-141`（含 `request_id` / `user_id` / `client_platform`）；`/health` 被跳过（`:118`）。**没有慢查询/慢请求阈值日志**——搜法 `grep -rin "slow" server/internal --include=*.go`，只有一条 `middleware/daemon_auth.go:25` 的注释提到「slow-log attribution」，但没有对应的慢日志实现。
- Prometheus 指标齐全：`server/internal/metrics/db.go:8-45`（pgx 连接池的获取耗时、空池等待、取消次数等 13 个指标，按 primary/replica 分角色）、`metrics/realtime.go`、`metrics/daemonws.go`。

### AO：**没有**任何前端性能测量；有产品埋点和后端请求耗时日志

- **没有** web vitals、**没有** longtask 观测。搜法：`grep -rn "web-vitals\|onLCP\|onINP\|PerformanceObserver\|longtask" frontend/src`，零命中。
- 埋点是 PostHog + Sentry（`@sentry/electron`），`frontend/src/renderer/lib/telemetry.ts`，值得注意的是**客户端侧事件配额**，`:40-41`：
  ```ts
  const EVENTS_PER_NAME_PER_MINUTE = 5;
  const EVENTS_PER_NAME_PER_DAY = 200;
  ```
  加上 `:14-17` 的本地 URL/路径脱敏常量。
- 后端每请求耗时日志：`backend/internal/httpd/log.go:53`（`"duration", time.Since(start)`）与 `:79`（`"duration": time.Since(start).Milliseconds()`）。**没有**慢查询阈值日志（`grep -rn "slow\|Slow" backend/internal/httpd/*.go` 只命中 slow-loris 注释）。
- CI 里有个 React Doctor（`.github/workflows/react-doctor.yml:32-34`），但 `paths` 只覆盖 `frontend/src/landing/**`（营销页），**不扫产品界面**。

**小结**：**只有 multica 有一件真正测量「界面卡不卡」的东西**（longtask → `client_unresponsive`），而且它测的不是 LCP/INP 这类首屏指标，是**运行时冻结**。两家都没有 web vitals；两家的后端都只记每请求耗时、都没有慢查询日志。

---

# 对知是的建议

按「他们的做法 / 我们现状 / 抄不抄 / 为什么」四要素逐条写。排序大致按「投入产出比」从高到低。

## 1. 语法高亮：从「全语言」改成「显式白名单 + 动态 import」

- **他们的做法**：AO 把语法包单独拆一个文件放在动态 `import()` 后面，手工枚举 **13** 种语言，并把「加一门语言」定义成需要签字的打包决策（`agent-orchestrator: frontend/src/renderer/lib/code-highlight-engine.ts:1-26`）。multica 用 lowlight `common`（37 种）静态引入，但额外把 `highlightAuto` 降级为 plaintext，避免「每个未标语言的代码块跑一遍全部语法」（`multica: packages/views/editor/syntax-highlight.ts:12-17`）。
- **我们现状**：`frontend/vite.config.ts` 里 `prismjsPlugin({ languages: 'all' })`，全部语言进首屏 vendor chunk（注释自述约 5 MB，且点名 monaco / tiptap / prismjs-all）。
- **抄不抄**：**抄，优先级最高。** 两条一起抄：白名单化 + 挪到动态 import 后面。
- **为什么**：这是本次对照里**唯一一条「两个项目都比我们做得克制、且我们改动成本几乎为零」**的。它不依赖 React、不依赖 TanStack Query，纯 Vite 配置 + 一个 `import()`。而且 AO 的注释给了现成的判据——「列表应该是**编码 agent 实际会吐出的语言**，不是高亮库提供的语言」，这对我们这种 agent 协作平台完全同构。风险是某种语言没在白名单里会退化成纯文本，可以接受（AO 就是这么定的）。

## 2. 首屏体积：不要指望 CI 闸门，但要把重依赖挪出首屏

- **他们的做法**：**两家都没有 CI 体积闸门**（搜法见机制 1）。他们的首屏靠的是①框架自带的路由级分包（multica: Next App Router；AO: `TanStackRouterVite({ autoCodeSplitting: true })`，`agent-orchestrator: frontend/vite.renderer.config.ts:189`）②对个别 MB 级依赖手工 `import()`（mermaid：`multica: packages/views/editor/mermaid-diagram.tsx:43`，`agent-orchestrator: frontend/src/renderer/lib/mermaid-diagram.ts:100`）。
- **我们现状**：单个 5 MB vendor chunk，重依赖（monaco / tiptap / prismjs）全在首屏；没有路由级懒加载的证据。
- **抄不抄**：**抄「路由级懒加载 + 重依赖动态 import」，不抄「上 CI 体积闸门」**（因为没人做，说明它不是这两家眼里的必要条件；而且我们连基线都还没测出来，先定闸门值是拍脑袋）。
- **为什么**：Vue Router 的 `component: () => import(...)` 是标准做法，改动局限在路由表，不涉及数据层；monaco 和 tiptap 只在编辑器界面用得到，跟着路由走天然就出了首屏。这条是纯收益、没有架构风险的。至于闸门——等测出基线、且做完前两步之后再谈，否则会变成一条「谁碰谁红」的噪声规则。

## 3. 轮询：从固定 30 秒改成**条件轮询**，并确认后台不可见时是停的

- **他们的做法**：multica 的轮询全部条件化——agent 列表只在「有 agent 在线或不稳定」时才 30s 一轮，否则 `refetchInterval` 直接返回 `false`（`multica: packages/core/workspace/queries.ts:55-63`）；dashboard 是 5 分钟 + 60 秒 staleTime（`packages/core/dashboard/queries.ts:50-51`）。AO 的写操作期间会暂停对应轮询（`agent-orchestrator: frontend/src/renderer/hooks/useConversation.ts:1015` 的 `writing ? false : ...`，配合 `:1028-1032` 的 `onMutate/onSettled` 夹窗），并且刻意标注哪些数据**不该轮询**（`hooks/useShellTerminals.ts:71`）。两家都靠 TanStack Query `refetchIntervalInBackground` 默认 `false` 拿到「后台不轮询」（两边搜该关键词均零命中，即都没改默认）。
- **我们现状**：`frontend/src/views/workspace/ProjectShell.vue` 每 30 秒无条件拉一次未读 + 话题列表。我们没有 TanStack Query，所以**这个默认行为我们不会自动拥有**——得自己接 `document.visibilityState`。
- **抄不抄**：**抄条件化和后台暂停，两条都抄。**
- **为什么**：这是「不改技术栈也能抄」的一条——我们的轮询是手写的 `setInterval`，加两个判断（页面不可见时跳过；没有活跃话题时降频或停）比引入任何库都便宜。而且「后台标签页还在每 30 秒打后端」在多标签场景下是纯浪费，用户还感知不到收益。注意：他们把这个当**默认**（框架给的），我们要把它当**必须显式写的代码**——这正是没有数据层库的隐性成本，值得在实现时留一行注释说明。

## 4. 消息发送：**保持现状不做乐观更新**，别被「乐观更新=快」带偏

- **他们的做法**：**两家都不对发消息做乐观渲染。** multica 在 `use-chat-controller.ts:565-570` 用整段注释论证「await-then-render」：编辑器锁住、按钮转圈，服务端接受前不写任何缓存、不清草稿；被拒时草稿原样保留可重试。服务端返回后用**响应体**构造消息、经一个幂等 upsert 单入口写入（`:603-616`），使它和随后到达的 `chat:message` WS 回声**任意顺序都能收敛**。AO 的 `onMutate` 只写一个 `state: "pending"` 的 dispatch 记录（`agent-orchestrator: frontend/src/renderer/hooks/useConversation.ts:300-316`）。
- **我们现状**：聊天走 WebSocket；发送路径是否乐观本文档未核实（不在调研范围）。
- **抄不抄**：**抄这个结论——发消息不要乐观。** 如果我们已经乐观了，值得重新评估。
- **为什么**：乐观更新的代价是「回滚时内容会消失」。发消息是**可能被服务端拒绝**的动作（权限、限流、会话状态），multica 就是被 403（权限中途撤销）教育过的。而且乐观渲染要和后续的推送回声去重，这需要一个幂等 upsert 单入口，是实打实的复杂度。**乐观更新的正确用武之地是幂等的小状态翻转**（下一条）。

## 5. 乐观更新：用在已读 / 置顶 / 归档 / 排序这类翻转上，配 `cancel → snapshot → rollback` 三段式

- **他们的做法**：multica 有 78 处 `onMutate`，几乎全是元数据翻转，模板见 `multica: packages/core/chat/mutations.ts:144-161`——先 `cancelQueries`（否则在飞的读落地时会把乐观值盖掉），存快照，写乐观值，`onError` 回滚，`onSettled` 失效。AO 的拖拽排序同模板（`agent-orchestrator: frontend/src/renderer/hooks/useConversation.ts:608-628`）。
- **我们现状**：Pinia + axios，没有 mutation 生命周期钩子，这套三段式要手写。
- **抄不抄**：**抄模式，不抄库。** 挑 1–2 个真正高频的动作（最可能是「标记已读」——它直接影响未读红点这个用户天天看的东西）手写一遍三段式，不要为此引入 TanStack Query。
- **为什么**：迁移成本不对称。TanStack Query 有 Vue 版本（`@tanstack/vue-query`），但把 Pinia + axios 的取数全量迁过去是一次跨整个前端的重构，而我们现在还没证明「数据层缺失」是卡顿的主因（`docs/topics/perf-lag-diagnosis.md` 的实测还没出）。手写一个 `markRead` 的乐观翻转是几十行、影响面可控；**先用最贵的那一个动作验证收益，再决定要不要付库的成本**。特别提醒 `cancelQueries` 那一步——手写时最容易漏的就是它，漏了会表现为「点了已读，红点闪一下又回来」。

## 6. 失效/重取的并发闸：把 AO 那套「每 key 一条在飞 + 一条排队 + 不取消在飞请求」抄进 blockCache

- **他们的做法**：`agent-orchestrator: frontend/src/renderer/lib/event-transport.ts:66-85` 的 `refreshes` map——每个 query key 最多一条在飞、一条排队（用一个 `dirty` 标志表示「跑完还要再跑一次」），并且 `invalidateQueries(..., { cancelRefetch: false })` 明确**不取消**已经在跑的请求；外加 `:19` 的 150ms 批处理窗口，把「一次用户操作引发的一串 CDC 事件」压成一次重取。multica 那边的对应物是结构性的：图片预览只挂两个预取组件把并发钉死在 2（`multica: packages/views/editor/image-sequence-context.tsx:202-215`）。
- **我们现状**：`frontend/src/lib/blockCache.ts` 已经有 2 条并发闸和后台刷新——**方向是对的，我们不是从零开始。**
- **抄不抄**：**抄两个细节：①「一条在飞 + 一条排队」的 dirty 标志；②合并窗口。** 不抄整体架构（它是围绕 TanStack Query 的 `invalidateQueries` 写的）。
- **为什么**：全局 2 条并发闸解决的是「别打爆后端」，但解决不了「同一个话题被连续 5 个事件触发 5 次重取」。dirty 标志用一个布尔值就把 N 次压成 2 次（当前这次 + 收尾一次），代码量极小。`cancelRefetch: false` 那条更是一句话的经验：**取消一条跑到一半的请求，等于把已经花掉的等待时间扔了**——这在跨公网的我们身上比在 AO 身上更值钱。

## 7. 虚拟滚动：只在「实测到长列表冻结」之后再上，且要为「精确定位」留一条平铺路径

- **他们的做法**：multica 全面用 react-virtuoso（聊天、泳道、看板列、issue 时间线），触发原因写得很实在——500 条评论的 issue 用普通 `.map` 会冻结页面数秒，因为每张 CommentCard 挂载时都要跑 markdown 解析 + 代码高亮（`multica: packages/views/issues/components/issue-detail.tsx:3336-3340`）。但它同时保留了**平铺模式**：深链定位到某条评论、或页内 Cmd+F 时不虚拟化，理由是「虚拟化和精确落点的契约根本对立（估算高度 vs 真实高度）」（`:3364-3367`）。AO 反而**几乎不虚拟化**（唯一一处在 diff 视图 `agent-orchestrator: frontend/src/renderer/components/WorkspaceDiffView.tsx:236`），靠 200 条/页的分页 + `useMemo` + 对超大内容硬截断（`lib/mermaid-diagram.ts:40` 的 `MAX_DIAGRAM_CHARS = 20_000`）撑住。
- **我们现状**：`blockCache.ts` 按窗口缓存消息，虚拟滚动情况本文档未核实。
- **抄不抄**：**先抄 AO 的「硬截断超大内容」（便宜、立刻见效），虚拟滚动等实测数据出来再说。**
- **为什么**：AO 是活证据——**不虚拟化也可以不卡**，只要每页有上限、单条渲染成本有上限。虚拟滚动是有代价的（滚动位置恢复、跳转定位、Cmd+F 全都会变复杂，multica 为此专门开了第二条平铺路径），在没测出「长列表挂载确实是我们的瓶颈」之前上它，是拿确定的复杂度换不确定的收益。而「一条 100KB 的消息被当图表渲染会挂起时间线」这种硬截断，是几行代码的防御。

## 8. 前端可观测：抄 multica 的 longtask 冻结探测器，不抄 web vitals

- **他们的做法**：multica 装一个 `PerformanceObserver({ type: "longtask" })`，阈值 2s（理由：正常渲染实测 50–600ms）、60 秒全局冷却（理由：一次长冻结会被浏览器拆成多条 entry，逐条上报会让事件量随冻结时长无界增长）、不开 `buffered`（免得把慢启动误报成运行时冻结），上报 `client_unresponsive` 带 `duration_ms` 和路由（`multica: packages/core/diagnostics/freeze-watchdog.ts:26,35,52-69`）。**两家都没有 web vitals**（搜法见机制 8）。
- **我们现状**：卡顿是靠人反馈的（「平台卡卡的」），没有任何客户端性能信号。
- **抄不抄**：**抄冻结探测器，不抄 web vitals。**
- **为什么**：我们现在最缺的不是「首屏 LCP 是多少」，而是「谁在什么页面上卡了多久」——用户抱怨的是**交互冻结**，不是首次绘制。这段代码约 40 行、无依赖（`PerformanceObserver` 是浏览器原生）、跟 Vue/React 无关，可以直接照抄逻辑。三个参数（2s 阈值、60s 冷却、不 buffered）都有写明的理由，直接沿用即可。**注意它必须接一个能收事件的地方**——如果我们没有埋点通道，先落到后端一个日志端点也行；没有出口的观测器等于没有。

## 9. Service Worker 的 NetworkFirst：我们有、他们都没有——**重新审视它，而不是因为「他们没有」就删**

- **他们的做法**：multica **没有** Service Worker（搜法 `grep -rn "serviceWorker\|workbox\|next-pwa" apps/web packages` 零命中）；AO 是 Electron，不适用。两家也都**没有**把 query 缓存持久化到磁盘（`persistQueryClient` 两边零命中）。
- **我们现状**：`frontend/vite.config.ts` 的 workbox `runtimeCaching` 对所有 `/api/` GET 做 NetworkFirst。
- **抄不抄**：**不抄「删掉」这个动作，但要单独核实它今天在做什么。**
- **为什么**：这是一条**「没有」不构成建议**的情况——两家都不做，是因为一个有 WebSocket 全量推送、一个后端在本机，都不需要这一层；不代表我们不需要。但 NetworkFirst 对**所有** `/api/` GET 生效是很粗的粒度：它在网络正常时不省任何时间（还是先走网络），只在离线/超时时兜底；而它和我们自己的 `blockCache` / `projectCache` 是三层叠在一起的缓存，三层的失效时机互不知情。**这值得作为 `perf-lag-diagnosis` 的一个独立核查项**（比如：一个已被 SW 缓存的旧响应，会不会在推送到达后仍然被读到），而不是在这份对照文档里下结论。

## 10. 后端：未读数我们已经和 multica 同构；ETag 和读副本先别动

- **他们的做法**：multica 的跨 workspace 未读是单条 SQL（`multica: server/pkg/db/queries/inbox.sql:149-170`），ETag/304 只用在一个 daemon 轮询接口上（`server/internal/handler/daemon_workspace.go:69-76`），读副本路由虽然实现完整（`server/internal/dbreader/selector.go`）但**今天只有 2 个调用点**、且 `BusinessDashboard` 声明后无人使用。AO 的服务端优化是 3 秒微缓存 + singleflight（`agent-orchestrator: backend/internal/service/session/workspace_cache.go:19`、`backend/internal/service/session/service.go:187`）。
- **我们现状**：未读统计已是单条 SQL（`backend/app/domain/topic/repositories.py` 的 `unread_counts`），不是 N+1。
- **抄不抄**：**未读数已经对齐，不用动。ETag 和读副本都不抄。** 如果实测发现某个接口被并发重复调用，可以抄 singleflight 的思路（Python 侧就是一个 in-flight future 字典）。
- **为什么**：ETag 只省响应体带宽、不省 DB 查询——而 multica 自己也只在一个接口上用了它，说明收益有限；我们的未读响应本来就小，省不下什么。读副本更是：**multica 建了这套机制，但今天几乎没在用**——照着一个别人自己都没接上的机制去改架构，是最典型的「看菜单不看点单」。至于 singleflight，它的前提是「同一个昂贵查询被并发重复发起」，我们得先有证据（比如同一话题的未读查询在一秒内被打了 5 次）再做。
