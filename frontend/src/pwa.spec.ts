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

async function start() {
  vi.resetModules()
  const { registerPwa } = await import('./pwa')
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/a', component: blank },
      { path: '/b', component: blank },
    ],
  })
  registerPwa(router)
  await router.push('/a')
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
  document.querySelectorAll('script[data-test-entry]').forEach((s) => s.remove())
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
