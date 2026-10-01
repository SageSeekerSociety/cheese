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
})

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
})
