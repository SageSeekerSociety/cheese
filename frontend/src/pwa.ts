/**
 * pwa.ts
 *
 * Service-worker registration for offline support. Paired with the VitePWA
 * config in vite.config.ts (`registerType: 'prompt'`, `injectRegister: false`
 * — we register here so nothing is injected into index.html twice).
 *
 * **更新是我们问、用户点，不是自动刷新**（2026-09-17 改的）。原来用
 * `registerType: 'autoUpdate'`：新 worker 一激活，插件就直接
 * `window.location.reload()`（见 node_modules/vite-plugin-pwa/dist/client/build/
 * register.js 里 activated 那一段），开着的页面在用户眼皮底下刷新——正在打的
 * 一段话没了。现在新 worker 停在 waiting，这里把「有新版本」交给界面
 * （components/common/UpdateBanner.vue），用户点「立即更新」才 skipWaiting 并刷新。
 *
 * 关掉提示不等于放弃更新：新 worker 一直在 waiting，下次打开页面会再报一次。
 * 所以「稍后」只是这一次的稍后。
 *
 * 这个文件里的 ref 是应用级单例，和 AccountService 同一套写法。
 */
import { ref } from 'vue'
import { registerSW } from 'virtual:pwa-register'

/** 新版本已经下载完成、等着接管。界面据此显示更新提示条。 */
export const updateReady = ref(false)

let updateServiceWorker: ((reloadPage?: boolean) => Promise<void>) | null = null

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

export function registerPwa(): void {
  updateServiceWorker = registerSW({
    immediate: true,
    onNeedRefresh() {
      updateReady.value = true
    },
    onRegisteredSW(_swUrl, registration) {
      swRegistration = registration ?? null
      if (!registration) return
      window.setInterval(checkForUpdate, CHECK_INTERVAL_MS)
      // 切回这个标签页时问一次：人回来了，正是告诉他「有新版本」的时候。
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') checkForUpdate()
      })
    },
  })
}

/** 用户点了「立即更新」：让新 worker 接管，接管完成会刷新这一页。 */
export async function applyUpdate(): Promise<void> {
  updateReady.value = false
  await updateServiceWorker?.(true)
}

/** 用户点了「稍后」：只收起这条提示。新 worker 继续在 waiting 里等着。 */
export function dismissUpdate(): void {
  updateReady.value = false
}
