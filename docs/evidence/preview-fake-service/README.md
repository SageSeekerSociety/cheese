# 预览假服务：请求不出网的证据

后台布局方案 B 的预览是**真组件 + 假数据**。出口在
`frontend/src/proto-preview-transport.ts`（怎么发、发到哪、回什么），样例数据与路由在
`frontend/src/proto-feedback-fixtures.ts` / `proto-admin-fixtures.ts`。只有预览入口
`frontend/src/proto-feedback.ts`、`proto-dashboard.ts` 引它们，真实构建不打包。

这里存的是「假服务真的把每个接口都接住了、一个请求都没出网」的取证，供固定 head 后复核。

## 判定标准

只认两样，别的都不算：

1. **网络层零请求** —— `page.on('request'/'response'/'requestfailed')` 为空。这两个事件
   打在网络栈上，不看 JS 包装顺序；「`window.fetch` 被 mock 了」证明不了任何事。
2. **假数据非空且真显示** —— DOM 里认 id 42 那行的长标题「名字很长的样例」和长简介。

写路径另加两条：

3. **POST 真的发到了行内那条** —— URL、id、请求体、响应体都记下来。
4. **那条记录自己变了** —— 拿 POST 带的 id 去查它，`reviewStatus` 从 PENDING 变 APPROVED。
   **不看列表条数**：条数会随筛选变，看着像写生效，其实是切了页签。

## 两份脚本

| 脚本 | 验什么 | 要求 |
| --- | --- | --- |
| `get-spaces.mjs` | 空间申请页的 GET | `doors`/`net` 皆空，长标题与长简介都在，3 条申请人 |
| `review-click.mjs` | 写路径 `SpacesApi.review`（点行内「通过」） | 下面四条同时成立 |

`review-click.mjs` 的四条：① 点到的是行内按钮；② 假服务收到那条 POST；③ **那条记录自身**
`reviewStatus` PENDING→APPROVED；④ `net` 为空。

**定位必须精确到行内。** `button:has-text("通过")` 是子串匹配，页头 AdminTabs 的「已通过」
页签（原生 `button`、`role="tab"`）会一起被捞进来，取第一个就点到了页签 —— 那是
`changeStatus` → GET 切 APPROVED 筛选，不是 `decide` → POST。曾经就误判过一次：JSON 里
「已通过 1→2、待审核 3→1」看着像审核成功，其实是筛选变了。所以脚本用
`getByRole('button', { name: '通过', exact: true })`（role 只认 button，页签进不来；名字全等，
「已通过」不等于「通过」），并把模糊命中的候选逐个摊开（`fuzzyWho`）——`index 0` 就是那个
页签，误判看得见。

## 怎么重跑

```sh
cd frontend
npx vite build --config vite.feedback-proto.config.mts
# 把 dist-feedback-proto 挂在任意静态服务器上，然后：
PREVIEW_BASE=http://127.0.0.1:5210/feedback-proto.html \
  node docs/evidence/preview-fake-service/get-spaces.mjs
```

同理跑另一个。`PREVIEW_BASE`、`CHROMIUM` 都可用环境变量换；playwright 从 `e2e/` 解析
（那套用 pnpm），脚本里不写死机器上的绝对路径。同目录的 `*.json` 就是这些脚本的输出。

**构建时不给 env。** 目录里只有 `.env.sample`（Vite 不加载它），`VITE_API_BASE_URL` 就是
空的 —— 这正是下面说的那种配置，也是取证该跑的配置。给一台加过 `.env` 的机器看，看不出来。

## 两种路径都要接住

`looksLikeApi` 认两种形状：`/api/...`，以及**无前缀**的 `/admin/...` 与 `/feedback/...`。

因为两条出口发出来的路径本来就不一样：`src/api.ts` 走 `/api/`；`network/api` 的 `baseURL`
直接取 `VITE_API_BASE_URL` 且**没有 fallback**，那个变量为空时它发的就是无前缀的
`/admin/spaces`。而 Vite 只加载 `.env` / `.env.local` / `.env.[mode]*`，**不加载
`.env.sample`**，干净 checkout 里没有 `.env`。

只认 `/api/` 那半边是个**默认配置下才坏**的回归：`SpacesApi.reviews/review` 会落到真网络上，
预览域上是一页「加载失败」；加过 `.env` 的机器上看不出来。所以识别逻辑不许为了减行数动它。

## 两道 axios 出口

axios 在浏览器里默认走 XHR 适配器，**根本不经过 `window.fetch`**。预览里两道都留着：

* **`axios.defaults.adapter = 'fetch'`**（`proto-preview-transport.ts` 模块顶层）—— 只对
  本模块求值**之后**建的实例有效：`axios.create()` 会把当时的 defaults 抄进实例配置。
* **`forceAxiosThroughFetch()`**（改 `Axios.prototype.request`）—— 只对补丁**之后**建的实例
  有效：`utils.extend(instance, Axios.prototype, ...)` 在建实例时就把 `request` **按值抄**到
  实例上了，改原型追不回已经建好的实例。

预览入口把 transport 排在路由模块前面，而路由是懒加载的（`router/feedback.ts` 里
`component: () => import(...)`），所以实际建出来的实例两道都赶得上。真出现两道都没赶上的
实例，它会走 XHR 直接出网，页面上是「加载失败」而不是没有原因的空列表 —— 看得出来。

### `xhr-door` 那一道不在本 PR 里

`xhr-door.mjs` / `xhr-door.json` 验的是**第三道**后手：直接接住 `XMLHttpRequest` 的
`open`/`send`，不看创建时机。**那道门本身不在本 PR**（在分支 `preview/xhr-door-backstop`
上）—— 之前带上时撑爆了文件行数上限（`proto-feedback-fixtures.ts` 2648→2842）和 ESLint
`no-this-alias`。没有当前真实失败就不为假设扩实现；要合它，得先按职责抽窄模块再单独走。

所以这两个文件是**后手的记录**，不是本 PR head 的证据：它们的 JSON 是装着后手时抓的，
在这个 head 上重跑也验不到同一件事。本 PR 的 head 只由上面那两份脚本背书。

另外，`get-spaces.json` / `review-click.json` 要认**构建时间**：预览是静态包，改了源码不重建
就还是旧包。曾经有一次证据跑在 revert 之前的包上，包里还留着两种路径识别，看着一切正常。
