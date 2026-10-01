/** 预览的 HTTP 出口：把页面发出去的接口请求接到假数据上。
 *
 *  只有这一层管「怎么发出去、发到哪、回什么」；样例数据和路由在
 *  `proto-feedback-fixtures.ts` / `proto-admin-fixtures.ts`，不在这里。
 *
 *  出口有两条，都要汇到同一份假数据上：
 *    * `src/api.ts` 那一层用 `fetch`；
 *    * `network/api` 那一层用 axios（空间申请、成员管理走它），而 axios 在浏览器里
 *      默认用 XHR 适配器，**根本不经过 `window.fetch`**。
 */
import axios from 'axios'

import { routes } from './proto-feedback-fixtures'

// `?slow=1`：读接口一直不回，用来看各页的「加载中」。
const PREVIEW_SLOW = typeof location !== 'undefined' && new URLSearchParams(location.search).get('slow') === '1'

/** 这条请求是不是接口。
 *
 *  **两种形状都要接**，因为两条出口发出来的路径本来就不一样：
 *    * `/api/...` —— `src/api.ts` 那条出口；`VITE_API_BASE_URL=/api` 时 axios 也发这个。
 *    * 无前缀的 `/admin/...` 与 `/feedback/...` —— **`VITE_API_BASE_URL` 为空时 axios
 *      发的就是这个**（`network/api/index.ts` 的 `baseURL` 直接取那个变量，没有
 *      fallback）。
 *
 *  空 baseURL 不是假想：Vite 只加载 `.env` / `.env.local` / `.env.[mode]*`，**不加载
 *  `.env.sample`**，干净 checkout 里没有 `.env`，这个变量就是 undefined。这时漏接无
 *  前缀那半边，`SpacesApi.reviews/review`（`/admin/spaces`）会直接打到真网络上 ——
 *  在预览域上是一页「加载失败」，而且只在默认配置下坏，加过 `.env` 的机器上看不出来。
 *
 *  图标、字体那些请求不在这两种形状里，照旧走真正的网络栈。 */
export function looksLikeApi(url: URL): boolean {
  return url.pathname.startsWith('/api/') || /^\/(admin|feedback)(\/|$)/.test(url.pathname)
}

/** 把接口请求接到假数据上。 */
export function installPreviewFetch(): void {
  const real = window.fetch.bind(window)
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const raw = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
    const url = new URL(raw, window.location.origin)
    if (!looksLikeApi(url)) return real(input as RequestInfo, init)
    const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase()
    let body: unknown = null
    // 请求体要么在 `init.body` 里（`src/api.ts` 那条出口），要么整个请求是一个
    // `Request` 对象（axios 的 fetch 适配器是这么发的），后者得先把体读出来。
    const rawBody = typeof init?.body === 'string' && init.body ? init.body : await requestBody(input)
    if (rawBody) {
      try {
        body = JSON.parse(rawBody)
      } catch {
        body = null
      }
    }
    if (
      PREVIEW_SLOW &&
      method === 'GET' &&
      !url.pathname.endsWith('/feedback/meta') &&
      !url.pathname.endsWith('/feedback/counts')
    ) {
      await new Promise(() => {})
    }
    const hit = routes(url, method, body)
    if (hit === undefined) {
      // 走到这里说明页面调了一个这里没写的接口。预览里它不该发生；真发生了，
      // 报出来比在界面上留一个没有原因的空列表好。
      console.warn('[preview] 没有假数据的请求', method, url.pathname)
      return envelope(null)
    }
    if ('missing' in hit) return envelope(null, 404, '这条反馈打不开')
    if ('refused' in hit) return envelope(null, 412, hit.refused)
    if ('forbidden' in hit) return envelope(null, 403, hit.forbidden)
    if ('invalid' in hit) return envelope(null, 400, hit.invalid)
    if ('conflict' in hit) return envelope(null, 409, hit.conflict)
    if ('failed' in hit) return envelope(null, 500, hit.failed)
    return envelope(hit.data)
  }
  forceAxiosThroughFetch()
}

/** 读一个 `Request` 的请求体；不是 `Request`（或者读不出来）就当没有体。 */
async function requestBody(input: RequestInfo | URL): Promise<string | null> {
  if (!(input instanceof Request)) return null
  try {
    return await input.clone().text()
  } catch {
    return null
  }
}

// 让 axios 也从 `window.fetch` 出去（也就是上面那个补丁），不再用它默认的 XHR 适配器。
// 放在模块顶层：要赶在任何 `axios.create()` 之前（建实例时会把当时的 defaults 抄进
// 实例配置，之后再改就赶不上了）。
axios.defaults.adapter = 'fetch'

/** 第二道：把适配器写进**当次** config，不依赖 defaults。
 *
 *  两道各自有够不着的实例，所以都留着：
 *    * 上面那行 `axios.defaults.adapter` 只对**本模块求值之后**建的实例有效 ——
 *      `axios.create()` 把当时的 defaults 抄进实例配置，之后再改赶不上。
 *    * 这道补丁只对**打补丁之后**建的实例有效 —— `utils.extend(instance,
 *      Axios.prototype, ...)` 在建实例时就把 `request` **按值抄**到实例上了，改原型
 *      追不回已经建好的实例。
 *
 *  预览入口把本模块排在路由模块前面，而路由是懒加载的，所以实际建出来的实例两道都
 *  赶得上。真出现两道都没赶上的实例，它会走 XHR 直接出网，页面上是「加载失败」而不是
 *  一个没有原因的空列表 —— 看得出来，不会悄悄坏。 */
function forceAxiosThroughFetch(): void {
  const proto = axios.Axios.prototype as unknown as {
    request: (this: unknown, ...args: unknown[]) => Promise<unknown>
  }
  const original = proto.request
  proto.request = function (this: unknown, ...args: unknown[]) {
    const withFetch = (cfg: unknown) =>
      cfg && typeof cfg === 'object' ? { ...(cfg as Record<string, unknown>), adapter: 'fetch' } : cfg
    // 两种调用形状：`request(config)` 与 `request(url, config)`。
    const out = [...args]
    if (typeof out[0] === 'string' || out[0] instanceof URL) out[1] = withFetch(out[1])
    else out[0] = withFetch(out[0])
    return original.apply(this, out)
  }
}

function envelope(data: unknown, code = 200, message = 'ok'): Response {
  return new Response(JSON.stringify({ code, message, data }), {
    status: code === 200 ? 200 : code,
    headers: { 'content-type': 'application/json' },
  })
}
