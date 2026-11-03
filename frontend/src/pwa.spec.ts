// 新版本在下一次应用内跳转时换上：不在原地刷新，也不弹提示问人；整页加载之前
// 先让新 worker 接管，免得那一下又落回旧版。
import type { RegisterSWOptions } from 'virtual:pwa-register'

import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const sw = vi.hoisted(() => ({ options: null as RegisterSWOptions | null }))

vi.mock('virtual:pwa-register', () => ({
  registerSW: (options: RegisterSWOptions) => {
    sw.options = options
    return async () => {}
  },
}))

type Notice = { text: string; action?: { label?: string; onClick?: () => void } }
/** 屏幕上弹出过的提示。 */
const notices = vi.hoisted(() => [] as Notice[])
vi.mock('vuetify-sonner', () => ({
  toast: {
    warning: (text: string, options?: Omit<Notice, 'text'>) => {
      notices.push({ text, ...options })
    },
  },
}))

// 提示里的字从语言包来；这里只看提示有没有出来、点了做什么，不取真的语言包。
vi.mock('@/i18n', () => ({ t: (key: string) => key }))

/** 浏览器那一侧：一个 registration，可能有一个等着的新 worker。 */
function fakeBrowser({ waiting, takesOver = true }: { waiting: boolean; takesOver?: boolean }) {
  const container = new EventTarget()
  const messages: unknown[] = []
  const registration = {
    waiting: waiting
      ? {
          postMessage(message: unknown) {
            messages.push(message)
            if (takesOver) queueMicrotask(() => container.dispatchEvent(new Event('controllerchange')))
          },
        }
      : null,
    update: vi.fn(async () => {}),
  }
  Object.assign(container, { getRegistration: async () => registration })
  vi.stubGlobal('navigator', { ...navigator, serviceWorker: container })
  return { messages, registration }
}

const blank = { template: '<div />' }

async function start(path = '/a') {
  vi.resetModules()
  const { registerPwa } = await import('./pwa')
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/a', component: blank },
      { path: '/b', component: blank },
      // 门口那几页（首页、分享的文档……）：App.vue 不给它们套外框，那里没有提示条。
      { path: '/landing', component: blank, meta: { publicLanding: true } },
    ],
  })
  registerPwa(router)
  await router.push(path)
  return router
}

let assign: ReturnType<typeof vi.fn>

beforeEach(() => {
  assign = vi.fn()
  vi.stubGlobal('location', { ...window.location, assign })
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  document.querySelectorAll('[data-test-entry]').forEach((s) => s.remove())
  notices.length = 0
})

/** 这一页是哪一版：index.html 里那颗带内容哈希的入口脚本。 */
function pageRuns(entry: string) {
  // 按 HTML 插进去：和 index.html 里那一颗一样，只是个标签，不会被拿去执行。
  document.head.insertAdjacentHTML(
    'beforeend',
    `<script type="module" crossorigin src="${entry}" data-test-entry></script>`
  )
}

/** 服务器现在发的 index.html 里写的是哪一版；null 表示问不到服务器。 */
function serverServes(entry: string | null) {
  const fetch = vi.fn(async () => {
    if (entry === null) throw new TypeError('Failed to fetch')
    return new Response(`<!doctype html><script type="module" crossorigin src="${entry}"></script>`)
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

describe('新版本怎么换上', () => {
  it('没有新版本时，跳转照常在页面里完成', async () => {
    fakeBrowser({ waiting: false })
    const router = await start()
    await router.push('/b')
    expect(router.currentRoute.value.path).toBe('/b')
    expect(assign).not.toHaveBeenCalled()
  })

  it('新版本下好之后，下一次跳转先让它接管，接管了才整页加载到目标地址', async () => {
    const browser = fakeBrowser({ waiting: true })
    const router = await start()
    sw.options!.onNeedRefresh!()
    await router.push('/b')
    expect(browser.messages).toEqual([{ type: 'SKIP_WAITING' }])
    expect(assign).toHaveBeenCalledWith('/b')
    expect(router.currentRoute.value.path).toBe('/a')
  })

  it('新 worker 迟迟不接管：等一会儿照样整页加载，不把人卡住', async () => {
    fakeBrowser({ waiting: true, takesOver: false })
    const router = await start()
    vi.useFakeTimers()
    sw.options!.onNeedRefresh!()
    const going = router.push('/b')
    await vi.advanceTimersByTimeAsync(1000)
    expect(assign).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(5000)
    await going
    expect(assign).toHaveBeenCalledWith('/b')
  })

  // 新 worker 被别的标签页换上之后，这一页不刷新，下一次跳转再整页加载。
  it('别的标签页换上了新版本：这一页不刷新，下一次跳转整页加载', async () => {
    fakeBrowser({ waiting: false })
    const router = await start()
    sw.options!.onNeedReload!()
    expect(assign).not.toHaveBeenCalled()
    await router.push('/b')
    expect(assign).toHaveBeenCalledWith('/b')
  })

  it('只换 query 的跳转不算离开这一页', async () => {
    fakeBrowser({ waiting: true })
    const router = await start()
    sw.options!.onNeedRefresh!()
    await router.push('/a?tab=2')
    expect(assign).not.toHaveBeenCalled()
  })

  // 部署之后才打开的页面，HTML 是现取的、跑的已经是新版；浏览器顺手装下的新 worker
  // 和它是同一版，没有理由再整页加载一次。
  it('页面跑的已经是服务器上那一版：跳转照常在页面里完成，新 worker 直接接管', async () => {
    pageRuns('/assets/main-new.js')
    serverServes('/assets/main-new.js')
    const browser = fakeBrowser({ waiting: true })
    const router = await start()
    sw.options!.onNeedRefresh!()
    await router.push('/b')
    expect(router.currentRoute.value.path).toBe('/b')
    expect(assign).not.toHaveBeenCalled()
    await vi.waitFor(() => expect(browser.messages).toEqual([{ type: 'SKIP_WAITING' }]))
  })

  it('页面落后于服务器上那一版：下一次跳转整页加载', async () => {
    pageRuns('/assets/main-old.js')
    serverServes('/assets/main-new.js')
    fakeBrowser({ waiting: true })
    const router = await start()
    sw.options!.onNeedRefresh!()
    await router.push('/b')
    expect(assign).toHaveBeenCalledWith('/b')
  })

  it('别的标签页换上了新版本，而这一页本来就是新版：不整页加载', async () => {
    pageRuns('/assets/main-new.js')
    serverServes('/assets/main-new.js')
    fakeBrowser({ waiting: false })
    const router = await start()
    sw.options!.onNeedReload!()
    await router.push('/b')
    expect(router.currentRoute.value.path).toBe('/b')
    expect(assign).not.toHaveBeenCalled()
  })

  it('问不到服务器现在是哪一版：照旧整页加载', async () => {
    pageRuns('/assets/main-new.js')
    serverServes(null)
    fakeBrowser({ waiting: true })
    const router = await start()
    sw.options!.onNeedRefresh!()
    await router.push('/b')
    expect(assign).toHaveBeenCalledWith('/b')
  })

  // 这条问话排在每一次跳转前面。手机从后台回来、网络刚醒的那一瞬问出去，它可能一直
  // 不回来；没有期限的话，之后所有跳转——包括返回——都排在它后面等着，界面从此点不动，
  // 只有重开才清掉。
  it('问服务器的话一直不回来：跳转照旧发生，不把人卡住', async () => {
    pageRuns('/assets/main-old.js')
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>(() => {}))
    )
    fakeBrowser({ waiting: true })
    const router = await start()
    vi.useFakeTimers()
    sw.options!.onNeedRefresh!()
    const going = router.push('/b')
    await vi.advanceTimersByTimeAsync(60_000)
    await going
    expect(assign).toHaveBeenCalledWith('/b')
  })
})

describe('房间连接一直连不上', () => {
  // 后端换掉了旧页面要连的接口时，旧页面一直「未连接」；确认这一页落后了才整页加载，
  // 问不到就不动。
  async function load() {
    vi.resetModules()
    return (await import('./pwa')).reloadIfBehind
  }

  let reload: ReturnType<typeof vi.fn>
  beforeEach(() => {
    reload = vi.fn()
    vi.stubGlobal('location', { ...window.location, assign, reload })
  })

  it('服务器上已经是另一版：新 worker 接管后整页加载', async () => {
    pageRuns('/assets/index-old.js')
    const { messages } = fakeBrowser({ waiting: true })
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('<script type="module" src="/assets/index-new.js"></script>'))
    )
    const reloadIfBehind = await load()

    expect(await reloadIfBehind()).toBe(true)
    expect(messages).toEqual([{ type: 'SKIP_WAITING' }])
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('还是同一版：不刷新', async () => {
    pageRuns('/assets/index-same.js')
    fakeBrowser({ waiting: false })
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('<script type="module" src="/assets/index-same.js"></script>'))
    )
    const reloadIfBehind = await load()

    expect(await reloadIfBehind()).toBe(false)
    expect(reload).not.toHaveBeenCalled()
  })

  it('问不到服务器：不刷新', async () => {
    pageRuns('/assets/index-old.js')
    fakeBrowser({ waiting: true })
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      })
    )
    const reloadIfBehind = await load()

    expect(await reloadIfBehind()).toBe(false)
    expect(reload).not.toHaveBeenCalled()
  })

  // 同一条问话，同样的期限：它不能一直挂在那儿（这里没人等它，但一次问话不该永远不回）。
  it('问话一直不回来：不刷新', async () => {
    pageRuns('/assets/index-old.js')
    fakeBrowser({ waiting: true })
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>(() => {}))
    )
    const reloadIfBehind = await load()
    vi.useFakeTimers()

    const answer = reloadIfBehind()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(await answer).toBe(false)
    expect(reload).not.toHaveBeenCalled()
  })

  it('几分钟内只问一次', async () => {
    pageRuns('/assets/index-same.js')
    fakeBrowser({ waiting: false })
    const fetched = vi.fn(async () => new Response('<script type="module" src="/assets/index-same.js"></script>'))
    vi.stubGlobal('fetch', fetched)
    const reloadIfBehind = await load()

    await reloadIfBehind()
    await reloadIfBehind()
    expect(fetched).toHaveBeenCalledTimes(1)
  })
})

describe('修了严重问题的那一版', () => {
  // 普通发版等下一次跳转再换上，不问人；修严重问题的那一版调高 index.html 里的
  // cx-min-build，落在它之前、还开着的页面要被告知刷新，而不是等一次点击。

  /** 这一页是哪一版：入口脚本，和构建时写进 HTML 的最低版本。 */
  function pageBuilt(entry: string, minBuild: number) {
    pageRuns(entry)
    document.head.insertAdjacentHTML('beforeend', `<meta name="cx-min-build" content="${minBuild}" data-test-entry>`)
  }

  function serverNow(entry: string, minBuild: number) {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            `<!doctype html><head><meta name="cx-min-build" content="${minBuild}" />` +
              `<script type="module" crossorigin src="${entry}"></script></head>`
          )
      )
    )
  }

  /** 开着这一页过了一个小时：例行的「有没有新版本」查过一次。 */
  async function anHourLater(path = '/a') {
    vi.useFakeTimers()
    const router = await start(path)
    await vi.advanceTimersByTimeAsync(60 * 60 * 1000)
    return router
  }

  let reload: ReturnType<typeof vi.fn>
  beforeEach(() => {
    reload = vi.fn()
    vi.stubGlobal('location', { ...window.location, assign, reload })
  })

  it('这一页早于服务器要求的最低版本：提示刷新，点了先让新 worker 接管再刷新', async () => {
    pageBuilt('/assets/main-old.js', 3)
    serverNow('/assets/main-new.js', 4)
    const { messages } = fakeBrowser({ waiting: true })
    await anHourLater()

    expect(notices).toHaveLength(1)
    notices[0]!.action!.onClick!()
    await vi.advanceTimersByTimeAsync(0)
    expect(messages).toEqual([{ type: 'SKIP_WAITING' }])
    expect(reload).toHaveBeenCalledTimes(1)
  })

  // 门口那几页没有提示条，在那儿弹的提示谁也看不见；人走进应用时得补上，不能就此没了。
  it('例行检查时停在门口那几页：走进应用时提示刷新', async () => {
    pageBuilt('/assets/main-old.js', 3)
    serverNow('/assets/main-new.js', 4)
    fakeBrowser({ waiting: false })
    const router = await anHourLater('/landing')
    expect(notices).toEqual([])

    await router.push('/a')
    await vi.advanceTimersByTimeAsync(0)
    expect(notices).toHaveLength(1)
  })

  it('这一页就是修复所在的那一版，或者更新：不提示', async () => {
    pageBuilt('/assets/main-fix.js', 4)
    serverNow('/assets/main-later.js', 4)
    fakeBrowser({ waiting: true })
    await anHourLater()

    expect(notices).toEqual([])
    expect(reload).not.toHaveBeenCalled()
  })

  it('普通发版（最低版本没动）：不提示，等下一次跳转', async () => {
    pageBuilt('/assets/main-old.js', 3)
    serverNow('/assets/main-new.js', 3)
    fakeBrowser({ waiting: true })
    await anHourLater()

    expect(notices).toEqual([])
    expect(reload).not.toHaveBeenCalled()
  })
})
