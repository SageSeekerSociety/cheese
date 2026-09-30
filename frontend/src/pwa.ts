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
 *   本来就会扔掉当前页的临时状态，所以整页加载不多丢任何东西。联网时 HTML 走
 *   NetworkOnly（vite.config.ts），整页加载拿到的一定是新版本，不必等新 worker
 *   接管；同时让它接管，离线缓存和旧缓存的清理归它。
 * - 同一个浏览器里别的标签页：新 worker 一接管，它们也收到通知。它们不刷新，只记下
 *   「已经过期」，各自下一次跳转时整页加载。
 *
 * 代价是一个一直不点任何东西的页面会一直停在旧版本上，直到下一次点击。
 */
import type { Router } from 'vue-router'

import { registerSW } from 'virtual:pwa-register'

/** 新版本已下载、等着接管；或者别的标签页已经换上了新版本。下一次跳转整页加载。 */
let stale = false

let activateWaiting: ((reloadPage?: boolean) => Promise<void>) | null = null

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

export function registerPwa(router: Router): void {
  activateWaiting = registerSW({
    immediate: true,
    onNeedRefresh() {
      stale = true
    },
    // 新 worker 接管了这一页（这一页或别的标签页让它接管的）。不刷新，见文件头。
    onNeedReload() {
      stale = true
    },
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
  router.beforeEach((to, from) => {
    if (!stale || from.matched.length === 0 || to.path === from.path) return
    void activateWaiting?.(false)
    window.location.assign(router.resolve(to).href)
    return false
  })
}
