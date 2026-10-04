/**
 * pwa.ts
 *
 * Service-worker registration for offline support. Paired with the VitePWA
 * config in vite.config.ts (`registerType: 'prompt'`, `injectRegister: false`
 * — we register here so nothing is injected into index.html twice).
 *
 * **新版本在下一次跳转时换上，不打断正在看的这一页，也不问人。**
 *
 * - 不在原地刷新：插件的默认做法是新 worker 一接管就 `window.location.reload()`，
 *   开着的页面在人眼皮底下刷新，还在路上、没落库的消息会丢（发件箱有意不落盘，
 *   见 lib/composerDrafts.ts）。所以新 worker 下好后停在 waiting。
 * - 也不弹提示让人点：草稿已经落盘，换版本本身不丢东西，没有什么需要人来决定。
 * - 换在下一次应用内跳转上：新版本在等时，把这次跳转换成一次整页加载。单页跳转
 *   本来就会扔掉当前页的临时状态，所以整页加载不多丢任何东西。整页加载之前先
 *   让新 worker 接管、等它真的接管了再走（`takeWaitingWorker`）：还是旧 worker
 *   接着的话，导航那一下只要网络失败，它就拿缓存里的**旧** index.html 顶上
 *   （vite.config.ts 里那条 NetworkOnly 的 precacheFallback），页面又回到旧版，
 *   而旧版要的代码块服务器上已经没有了。
 * - 同一个浏览器里别的标签页：新 worker 一接管，它们也收到通知。它们不刷新，只记下
 *   「已经过期」，各自下一次跳转时整页加载。
 * - 「有新 worker」不等于「这一页过期了」：部署之后才打开的页面，HTML 是从网上
 *   现取的（NetworkOnly），跑的已经是新版；浏览器在这次导航里顺手查 sw.js，才把
 *   同一版的新 worker 装到 waiting。所以先拿这一页自己的入口脚本去和服务器现在的
 *   index.html 比（`pageIsBehind`）：没落后就不整页加载，直接让新 worker 接管。
 *
 * 代价是一个一直不点任何东西的页面会一直停在旧版本上，直到下一次点击。
 */
import type { Router } from 'vue-router'

import { registerSW } from 'virtual:pwa-register'

/**
 * 有了新 worker 之后，这一页的代码是不是落后于服务器上现在那一版。为真时下一次
 * 跳转整页加载。null：还没有新 worker 的消息。
 */
let behind: Promise<boolean> | null = null

/**
 * 这一页的入口脚本（文件名带内容哈希）还在不在服务器现在的 index.html 里。
 *
 * 取的是 `/` 而不是 `/index.html`：后者在预缓存里，旧 worker 会拿它自己那份旧的
 * 顶上；`/` 不在预缓存里（vite.config.ts 的 directoryIndex: null），走网络。
 * 问不到（断网、服务器在重启、开发环境没有入口脚本）就当落后了，照原来的做法整页加载。
 */
async function pageIsBehind(): Promise<boolean> {
  const own = document.querySelector('script[type="module"][src]')?.getAttribute('src')
  if (!own) return true
  try {
    const response = await fetch(import.meta.env.BASE_URL, { cache: 'no-store' })
    if (!response.ok) return true
    return !(await response.text()).includes(own)
  } catch {
    return true
  }
}

/**
 * 主动问一次「有没有新版本」。
 *
 * 必须自己问：浏览器只在**导航**时顺带查一次 sw.js，而这是个 SPA——开着的一页
 * 可能一整天没有一次真导航，光靠浏览器我们永远发现不了新版。`registration.update()`
 * 是廉价的条件请求（sw.js 由 nginx 按 no-cache 发）。
 */
let swRegistration: ServiceWorkerRegistration | null = null
let lastCheckAt = 0
const CHECK_INTERVAL_MS = 60 * 60 * 1000
const VISIBILITY_THROTTLE_MS = 10 * 60 * 1000

function checkForUpdate() {
  const now = Date.now()
  if (now - lastCheckAt < VISIBILITY_THROTTLE_MS) return
  lastCheckAt = now
  void swRegistration?.update().catch(() => {
    // 断网、或者服务器刚重启，都会走到这里；下一次再看机会。
  })
}

const TAKEOVER_WAIT_MS = 3000

function within<T>(ms: number, work: Promise<T>): Promise<T | undefined> {
  return Promise.race([work, new Promise<undefined>((resolve) => window.setTimeout(() => resolve(undefined), ms))])
}

/**
 * 有新版本在等的话，让它接管这一页，并等到它真的接管了才返回。
 *
 * 整页加载前调它：接管之后，导航就算退回缓存，缓存里也已经是新版的 index.html。
 * 最多等几秒——等不到就照旧加载，宁可再落一次旧版，也不把人卡在一次点击上。
 * `check` 为真时先问一次服务器有没有新版：代码块 404 那条路上，这一页可能还
 * 不知道新版已经发了。
 */
export async function takeWaitingWorker({ check = false } = {}): Promise<void> {
  const container = navigator.serviceWorker
  if (!container) return
  const registration = swRegistration ?? (await within(TAKEOVER_WAIT_MS, container.getRegistration()))
  if (!registration) return
  if (check && !registration.waiting)
    await within(
      TAKEOVER_WAIT_MS,
      registration.update().catch(() => undefined)
    )
  const waiting = registration.waiting
  if (!waiting) return
  const taken = new Promise<void>((resolve) =>
    container.addEventListener('controllerchange', () => resolve(), { once: true })
  )
  // workbox 生成的 sw.js 认这条消息（registerType: 'prompt' 时它不自己 skipWaiting）。
  waiting.postMessage({ type: 'SKIP_WAITING' })
  await within(TAKEOVER_WAIT_MS, taken)
}

/** 有新 worker 了：这一页落后就等下一次跳转整页加载；没落后就让它现在接管，不刷新。 */
function onNewWorker() {
  behind = pageIsBehind()
  void behind.then((isBehind) => {
    if (!isBehind) void takeWaitingWorker()
  })
}

export function registerPwa(router: Router): void {
  registerSW({
    immediate: true,
    onNeedRefresh: onNewWorker,
    // 新 worker 接管了这一页（这一页或别的标签页让它接管的）。不刷新，见文件头。
    onNeedReload: onNewWorker,
    onRegisteredSW(_swUrl, registration) {
      swRegistration = registration ?? null
      if (!registration) return
      window.setInterval(checkForUpdate, CHECK_INTERVAL_MS)
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') checkForUpdate()
      })
    },
  })
  // 首次导航（`from` 没有匹配的路由）本来就是整页加载出来的；只换 hash 或 query
  // 的跳转不算离开这一页。
  router.beforeEach(async (to, from) => {
    if (!behind || from.matched.length === 0 || to.path === from.path) return
    if (!(await behind)) return
    await takeWaitingWorker()
    window.location.assign(router.resolve(to).href)
    return false
  })
}
