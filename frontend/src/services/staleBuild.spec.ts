// 跨着发版开着的一页：点到一个服务器上已经没有的代码块时，先让新版本的 worker
// 接管，再刷新——否则刷新可能又落回旧版，这一页就一直点不开东西。
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('virtual:pwa-register', () => ({ registerSW: () => async () => {} }))

type Notice = { id: number; text: string; action?: { label?: string; onClick?: () => void } }
// What is on screen right now.
const notices: Notice[] = []
let nextNotice = 0
vi.mock('vuetify-sonner', () => ({
  toast: {
    warning: (text: string, options?: Omit<Notice, 'id' | 'text'>) => {
      notices.push({ id: ++nextNotice, text, ...options })
      return nextNotice
    },
    dismiss: (id: number) => notices.splice(notices.findIndex((n) => n.id === id) >>> 0, 1),
  },
}))

let reload: ReturnType<typeof vi.fn>
let assign: ReturnType<typeof vi.fn>
let order: string[]

function fakeBrowser({ newVersionOnServer }: { newVersionOnServer: boolean }) {
  const container = new EventTarget()
  const registration: { waiting: { postMessage(m: unknown): void } | null; update: () => Promise<void> } = {
    waiting: null,
    update: vi.fn(async () => {
      order.push('update')
      if (newVersionOnServer)
        registration.waiting = {
          postMessage(message: unknown) {
            order.push(`message:${(message as { type: string }).type}`)
            queueMicrotask(() => {
              order.push('controllerchange')
              container.dispatchEvent(new Event('controllerchange'))
            })
          },
        }
    }),
  }
  Object.assign(container, { getRegistration: async () => registration })
  // 展开只带得走自有属性；language 是原型上的 getter，i18n 一加载就要读它。
  vi.stubGlobal('navigator', { ...navigator, language: navigator.language, serviceWorker: container })
}

beforeEach(() => {
  sessionStorage.clear()
  notices.length = 0
  order = []
  reload = vi.fn(() => order.push('reload'))
  assign = vi.fn((to: string) => order.push(`assign:${to}`))
  vi.stubGlobal('location', { ...window.location, reload, assign })
})
// 每个用例都重新加载模块、重新挂一次监听；上一个用例挂在 window 上的那份不摘掉，
// 就会替这一个用例接住事件。
const listening: [string, EventListenerOrEventListenerObject][] = []
beforeEach(() => {
  const add = window.addEventListener.bind(window)
  vi.spyOn(window, 'addEventListener').mockImplementation((type, listener, options) => {
    if (listener) listening.push([type, listener])
    add(type, listener, options)
  })
})
afterEach(() => {
  for (const [type, listener] of listening.splice(0)) window.removeEventListener(type, listener)
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

async function loaded() {
  vi.resetModules()
  return import('./staleBuild')
}

describe('跨着发版开着的一页', () => {
  it('缺了代码块：先让新版本接管，再刷新', async () => {
    fakeBrowser({ newVersionOnServer: true })
    const { reloadForNewBuild } = await loaded()
    expect(reloadForNewBuild(new TypeError('Failed to fetch dynamically imported module: /assets/x.js'))).toBe(true)
    await vi.waitFor(() => expect(reload).toHaveBeenCalledOnce())
    expect(order).toEqual(['update', 'message:SKIP_WAITING', 'controllerchange', 'reload'])
  })

  it('服务器上没有更新的版本：照样刷新一次', async () => {
    fakeBrowser({ newVersionOnServer: false })
    const { reloadForNewBuild } = await loaded()
    reloadForNewBuild(new TypeError('Failed to fetch dynamically imported module: /assets/x.js'))
    await vi.waitFor(() => expect(reload).toHaveBeenCalledOnce())
  })

  it('别的错误不刷新', async () => {
    fakeBrowser({ newVersionOnServer: true })
    const { reloadForNewBuild } = await loaded()
    expect(reloadForNewBuild(new Error('boom'))).toBe(false)
    await new Promise((r) => setTimeout(r, 10))
    expect(reload).not.toHaveBeenCalled()
  })
})

/**
 * 一个懒加载的页面，它的代码块服务器上已经没有了。和 Vite 的预加载助手一样：
 * 先广播一条可取消的 `vite:preloadError`，没人取消就把错误抛给调用方。
 */
function goneChunk(name: string) {
  return () =>
    new Promise((resolve, reject) =>
      setTimeout(() => {
        const error = new TypeError(`Failed to fetch dynamically imported module: /assets/${name}.js`)
        const event = Object.assign(new Event('vite:preloadError', { cancelable: true }), { payload: error })
        window.dispatchEvent(event)
        if (event.defaultPrevented) resolve({ default: {} })
        else reject(error)
      }, 0)
    )
}

const blank = { template: '<div />' }

async function app() {
  vi.resetModules()
  const staleBuild = await import('./staleBuild')
  const { prefetchNow } = await import('@/lib/routePrefetch')
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/a', component: blank },
      { path: '/b', component: goneChunk('B') },
    ],
  })
  staleBuild.watchForStaleBuild()
  staleBuild.recoverNavigations(router)
  await router.push('/a')
  return { router, prefetchNow }
}

const settled = () => new Promise((r) => setTimeout(r, 30))

describe('发版之后开着的一页，代码块缺了由谁来处理', () => {
  it('预取到一个已经没有的代码块：什么都不发生，不刷新', async () => {
    fakeBrowser({ newVersionOnServer: true })
    const { router, prefetchNow } = await app()
    prefetchNow({ router, to: '/b' })
    await settled()
    expect(reload).not.toHaveBeenCalled()
    expect(assign).not.toHaveBeenCalled()
  })

  it('点到一个代码块已经没有的页面：整页加载到点的那一页，而不是刷新当前页', async () => {
    fakeBrowser({ newVersionOnServer: true })
    const { router } = await app()
    await router.push('/b').catch(() => {})
    await vi.waitFor(() => expect(assign).toHaveBeenCalledWith('/b'))
    expect(reload).not.toHaveBeenCalled()
  })

  it('按下去就预取、紧接着点击，两边要的是同一个缺了的代码块：落到点的那一页', async () => {
    fakeBrowser({ newVersionOnServer: true })
    const { router, prefetchNow } = await app()
    prefetchNow({ router, to: '/b' })
    await router.push('/b').catch(() => {})
    await vi.waitFor(() => expect(assign).toHaveBeenCalledWith('/b'))
    expect(reload).not.toHaveBeenCalled()
  })

  it('页面里别处按需加载的代码块缺了：照旧刷新这一页', async () => {
    fakeBrowser({ newVersionOnServer: true })
    await app()
    await goneChunk('Dialog')().catch(() => {})
    await vi.waitFor(() => expect(reload).toHaveBeenCalledOnce())
  })
})

/**
 * 一次整页加载之后的那一页：模块重新求值，sessionStorage 还是同一份（同一个标签页）。
 * `failing` 里的页面代码块取不到——网络还没好，或者这一页本来就缺。
 */
const pageListeners: [string, EventListenerOrEventListenerObject][] = []
const listen = window.addEventListener.bind(window)
window.addEventListener = (type: string, listener: EventListenerOrEventListenerObject, options?: unknown) => {
  pageListeners.push([type, listener])
  listen(type, listener, options as AddEventListenerOptions)
}

async function pageLoad(at: string, failing: string[]) {
  // A full load leaves nothing of the last page behind: its window listeners go with it.
  for (const [type, listener] of pageListeners.splice(0)) window.removeEventListener(type, listener)
  vi.resetModules()
  const staleBuild = await import('./staleBuild')
  const router = createRouter({
    history: createMemoryHistory(),
    routes: ['/a', '/b'].map((path) => ({
      path,
      component: failing.includes(path) ? goneChunk(path.slice(1)) : blank,
    })),
  })
  staleBuild.watchForStaleBuild()
  staleBuild.recoverNavigations(router)
  await router.push(at).catch(() => {})
  await settled()
  return router
}

describe('网络不好：整页加载过一次，要的页面还是取不到', () => {
  it('打开就要的那块取不到：只整页加载这一次，不来回刷', async () => {
    fakeBrowser({ newVersionOnServer: false })
    const first = await pageLoad('/a', ['/b'])
    await first.push('/b').catch(() => {})
    await vi.waitFor(() => expect(assign).toHaveBeenCalledWith('/b'))

    await pageLoad('/b', ['/b'])
    await settled()
    expect(assign).toHaveBeenCalledOnce()
    expect(reload).not.toHaveBeenCalled()
  })

  it('取不到的时候告诉人，点「重试」整页加载到要去的那一页', async () => {
    fakeBrowser({ newVersionOnServer: false })
    const first = await pageLoad('/a', ['/b'])
    await first.push('/b').catch(() => {})
    await vi.waitFor(() => expect(assign).toHaveBeenCalledOnce())

    await pageLoad('/b', ['/b'])
    expect(notices).toHaveLength(1)
    notices[0].action?.onClick?.()
    await vi.waitFor(() => expect(assign).toHaveBeenCalledTimes(2))
    expect(assign).toHaveBeenLastCalledWith('/b')
  })

  it('之后别的页面打开了：提示收起来', async () => {
    fakeBrowser({ newVersionOnServer: false })
    const first = await pageLoad('/a', ['/b'])
    await first.push('/b').catch(() => {})
    await vi.waitFor(() => expect(assign).toHaveBeenCalledOnce())

    const second = await pageLoad('/b', ['/b'])
    expect(notices).toHaveLength(1)
    await second.push('/a')
    expect(notices).toHaveLength(0)
  })

  it('整页加载救回来、页面打开过之后，下一次缺块还会再整页加载', async () => {
    fakeBrowser({ newVersionOnServer: false })
    const first = await pageLoad('/a', ['/b'])
    await first.push('/b').catch(() => {})
    await vi.waitFor(() => expect(assign).toHaveBeenCalledOnce())

    const second = await pageLoad('/a', ['/b'])
    await second.push('/b').catch(() => {})
    await vi.waitFor(() => expect(assign).toHaveBeenCalledTimes(2))
    expect(notices).toHaveLength(0)
  })
})
