# 预览假服务：请求不出网的证据

后台布局方案 B 的预览是**真组件 + 假数据**（`frontend/src/proto-feedback-fixtures.ts`，只有
预览入口 `frontend/src/proto-feedback.ts` 引它，真实构建不打包它）。这里存的是
「假服务真的把每个接口都接住了、一个请求都没出网」的取证，供固定 head 后复核。

## 判定标准

只认两样，别的都不算：

1. **网络层零请求** —— `page.on('request'/'response'/'requestfailed')` 为空。这两个事件
   打在网络栈上，不看 JS 包装顺序；「`window.fetch` 被 mock 了」证明不了任何事。
2. **假数据非空且真显示** —— DOM 里认 id 42 那行的长标题「名字很长的样例」和长简介。

两道 JS 出口（`window.fetch`、`XMLHttpRequest.prototype.open`）另记一份账，只用来分辨
走的哪道门，不参与判定。

## 三份脚本

| 脚本 | 验什么 | 要求 |
| --- | --- | --- |
| `get-spaces.mjs` | 空间申请页的 GET | `doors`/`net` 皆空，长标题与长简介都在，3 条申请人 |
| `review-click.mjs` | 写路径 `SpacesApi.review`（点「通过」） | `doors`/`net` 皆空，**且**「已通过」1→2、待审核 3→1 |
| `xhr-door.mjs` | XMLHttpRequest 那道后手 | 裸 XHR 的 GET/POST 都 200 且返回假数据，`net` 为空 |

`review-click` 必须同时要求「零出网」和「页面跟着变」：只有前者说明请求根本没发，
只有后者可能是别处绕过假服务之后由缓存凑出来的。

## 怎么重跑

```sh
cd frontend
npx vite build --config vite.feedback-proto.config.mts
# 把 dist-feedback-proto 挂在任意静态服务器上，然后：
PREVIEW_BASE=http://127.0.0.1:5210/feedback-proto.html \
  node docs/evidence/preview-fake-service/get-spaces.mjs
```

同理跑另外两个。`PREVIEW_BASE`、`CHROMIUM` 都可用环境变量换；playwright 从 `e2e/` 解析
（那套用 pnpm），脚本里不写死机器上的绝对路径。同目录的 `*.json` 就是这三份脚本的输出。

## 为什么会有 `xhr-door` 这一道

axios 在浏览器里默认走 XHR 适配器，**根本不经过 `window.fetch`**。预览里的做法是把
axios 的出口改到 `window.fetch` 上（`forceAxiosThroughFetch`），但 `axios.create()` 会把
当时 `Axios.prototype.request` 的引用抄进实例（`node_modules/axios/lib/axios.js` 的
`bind`），所以改 prototype 只对**之后**建的实例生效。

现在预览入口的路由全是懒加载（`frontend/src/router/feedback.ts` 里
`component: () => import(...)`），`network/api` 的实例建在补丁之后，那道补丁够用。可谁往
入口的静态导入图里加一条通到 `network/api` 的路径，它就静默失效、页面退回 404，而且
不会有任何报错。所以假服务里另有一道不看创建时机的后手：直接接住 `XMLHttpRequest` 的
`open`/`send`。补丁之前建的实例发出来的就是 `xhr-door.mjs` 那种裸 XHR，它被兜住就说明
那条退路是通的。
