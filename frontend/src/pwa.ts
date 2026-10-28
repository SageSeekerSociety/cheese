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
 * - 问「这一页落后了吗」是有期限的（`PAGE_PROBE_WAIT_MS`）。它排在每一次跳转前面，
 *   而手机上它可能一直不回话——从后台回来的那一瞬问出去，网络刚醒，它既不成功也不
 *   失败。没有期限的话，之后每一次跳转（点链接、底部标签、左上角的返回）都排在它
 *   后面等着，界面看着就是死的，只有整页重开才清得掉。等不到答案按「落后」处理：
 *   落一次旧版，也好过停在一次点击上。
 *
 * 代价是一个一直不点任何东西的页面会一直停在旧版本上，直到下一次点击。修严重问题的
 * 那一版等不起这一下：它把 index.html 里的 `cx-min-build` 调高，落在它之前的页面就
 * 提示刷新（`promptIfBelowMinBuild`）。普通发版不动这个数，照旧不问人。改坏了旧页面在用
 * 的接口的那一版也等不起：它调高 API 代数，后端拒掉旧页面的请求，旧页面提示刷新
 * （lib/apiGeneration.ts）。
 */
import type { Router } from 'vue-router'

import { toast } from 'vuetify-sonner'
import { registerSW } from 'virtual:pwa-register'

import { t } from '@/i18n'

/**
 * 有了新 worker 之后，这一页的代码是不是落后于服务器上现在那一版。为真时下一次
 * 跳转整页加载。null：还没有新 worker 的消息；`undefined`：问过，但没问出来
 * （见 `pageIsBehind`）。
 */
let behind: Promise<boolean | undefined> | null = null

/** 问「这一页落后了吗」最长等多久。这条问话不能没有期限（见 `fetchEntryHtml`）。 */
const PAGE_PROBE_WAIT_MS = 3000

/**
 * 服务器现在发的 index.html 原文；`undefined`：没问出来——断网、服务器在重启，
 * 或者到 `PAGE_PROBE_WAIT_MS` 还没回话。
 *
 * 取的是 `/` 而不是 `/index.html`：后者在预缓存里，旧 worker 会拿它自己那份旧的
 * 顶上；`/` 不在预缓存里（vite.config.ts 的 directoryIndex: null），走网络。
 *
 * 头和体各一个期限，都得有：手机从后台回到前台时，新 worker 刚下好、这一问正好赶在
 * 网络刚醒的那一瞬发出去，它既不成功也不失败，可以一直不回来。而它排在每一次跳转
 * 前面（见 `registerPwa`），没有期限就等于把整个界面锁住——点链接、点底部标签、点
 * 左上角的返回都没反应，只有整页重开才清得掉。
 */
async function fetchEntryHtml(): Promise<string | undefined> {
  try {
    const response = await within(PAGE_PROBE_WAIT_MS, fetch(import.meta.env.BASE_URL, { cache: 'no-store' }))
    if (!response?.ok) return undefined
    return await within(PAGE_PROBE_WAIT_MS, response.text())
  } catch {
    return undefined
  }
}

/**
 * 这一页的入口脚本（文件名带内容哈希）还在不在服务器现在的 index.html 里。
 * `true` 落后、`false` 没落后、`undefined` 没问出来。
 *
 * 「没问出来」得单独是一个答案，不能并进 `true` 里：问这句话的两处对它的做法相反——
 * 跳转那边当落后（宁可再落一次旧版，也不把人停在一次点击上），`reloadIfBehind`
 * 那边不动（那种连不上不是版本的事）。
 */
async function pageIsBehind(): Promise<boolean | undefined> {
  const own = document.querySelector('script[type="module"][src]')?.getAttribute('src')
  if (!own) return undefined
  const html = await fetchEntryHtml()
  if (html === undefined) return undefined
  return !html.includes(own)
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

function checkForUpdate(router: Router) {
  const now = Date.now()
  if (now - lastCheckAt < VISIBILITY_THROTTLE_MS) return
  lastCheckAt = now
  void swRegistration?.update().catch(() => {
    // 断网、或者服务器刚重启，都会走到这里；下一次再看机会。
  })
  void promptIfBelowMinBuild(router)
}

/** index.html 里 `<meta name="cx-min-build">` 的值：只增不减的整数，见 index.html。 */
function minBuildOf(root: ParentNode): number | undefined {
  const value = Number.parseInt(root.querySelector('meta[name="cx-min-build"]')?.getAttribute('content') ?? '', 10)
  return Number.isNaN(value) ? undefined : value
}

/** 把服务器发的 HTML 解析成不执行、不加载任何东西的节点（`<template>` 的内容是惰性的）。 */
function inert(html: string): ParentNode {
  const template = document.createElement('template')
  template.innerHTML = html
  return template.content
}

let minBuildPrompted = false

/**
 * 这一页早于服务器现在要求的最低版本（修严重问题的那一版）时，提示刷新，一直留着。
 *
 * 拿这一页自己 HTML 里的数和服务器现在的 index.html 里的数比：两边都是构建时从同一行
 * 写进去的，修复所在的那一版和它之后的每一版都带着调高后的数，所以只有落在修复之前的
 * 页面会小于它。问不到服务器、或者哪一边没有这个数，都不提示。
 *
 * 门口那几页（`meta.publicLanding`，App.vue 不给它们套外框）没有挂提示条，在那儿弹出的
 * 提示谁也看不见，事后挂上的提示条也不会补出来。所以停在那几页时不算提示过：留到下一次
 * 例行检查，或者人从门口走进应用的那一下（见 `registerPwa`）。
 */
async function promptIfBelowMinBuild(router: Router) {
  if (minBuildPrompted) return
  const own = minBuildOf(document)
  if (own === undefined) return
  const html = await fetchEntryHtml()
  if (html === undefined) return
  const required = minBuildOf(inert(html))
  if (required === undefined || own >= required || minBuildPrompted) return
  if (router.currentRoute.value.meta.publicLanding) return
  minBuildPrompted = true
  toast.warning(t('global.requiredUpdate.message'), {
    // 不刷新，这一页就一直带着那个严重问题；提示留到人点为止。
    duration: Number.POSITIVE_INFINITY,
    action: {
      label: t('global.requiredUpdate.refresh'),
      onClick: () => {
        void takeWaitingWorker({ check: true }).then(() => window.location.reload())
      },
    },
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

/**
 * 房间连接一直连不上时，看看是不是这一页落后了：落后就换上新版。
 *
 * 「下一次跳转再换版本」（见文件头）的前提是旧页面照样能用。后端换掉旧页面要连的接口时
 * （例如 #3185 把房间连接从 /topics/{id}/chat 换成 /rooms/live），旧页面就不只是旧，而是
 * 一直「未连接」：人只看到房间不动，也等不来那一次点击。这时这一页已经连不上，整页加载
 * 不多丢东西（草稿已落盘）。
 *
 * 只认问得到的答案：问不到（断网、服务器在重启）或者等不到（见 `fetchEntryHtml` 的期限）
 * 都不刷新，那种连不上不是版本的事，和跳转那边相反。最多每 `LINK_CHECK_MS` 问一次。
 */
const LINK_CHECK_MS = 5 * 60 * 1000
let lastLinkCheckAt = -Infinity

export async function reloadIfBehind(): Promise<boolean> {
  const now = Date.now()
  if (now - lastLinkCheckAt < LINK_CHECK_MS) return false
  lastLinkCheckAt = now
  const own = document.querySelector('script[type="module"][src]')?.getAttribute('src')
  if (!own) return false
  const served = await fetchEntryHtml()
  if (served === undefined || served.includes(own)) return false
  await takeWaitingWorker({ check: true })
  window.location.reload()
  return true
}

/** 有新 worker 了：这一页落后就等下一次跳转整页加载；没落后就让它现在接管，不刷新。 */
function onNewWorker() {
  behind = pageIsBehind()
  void behind.then((isBehind) => {
    // 只有问出「没落后」才让它现在接管：`true` 下一次跳转要整页加载，`undefined`
    // 没有答案，都轮不到这里替人做主。
    if (isBehind === false) void takeWaitingWorker()
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
    },
  })
  // 不挂在 worker 注册成功上：没有 service worker 的浏览器（一些应用的内置浏览器）
  // 也要能收到「修了严重问题，刷新」的提示。
  window.setInterval(() => checkForUpdate(router), CHECK_INTERVAL_MS)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') checkForUpdate(router)
  })
  // 从门口走进应用：在门口时没能弹出的提示，这时补上。
  router.afterEach((to, from) => {
    if (from.meta.publicLanding && !to.meta.publicLanding) void promptIfBelowMinBuild(router)
  })
  // 首次导航（`from` 没有匹配的路由）本来就是整页加载出来的；只换 hash 或 query
  // 的跳转不算离开这一页。
  //
  // 只有问出「没落后」（`false`）才照常在页面里跳；落后、或者没问出结果，都整页加载。
  // 这条问话有期限（见 `fetchEntryHtml`），所以这一等再久也有头——它原来没有期限，
  // 而它排在每一次跳转前面，问不回来就是把整个界面锁住。
  router.beforeEach(async (to, from) => {
    if (!behind || from.matched.length === 0 || to.path === from.path) return
    if ((await behind) === false) return
    await takeWaitingWorker()
    window.location.assign(router.resolve(to).href)
    return false
  })
}
