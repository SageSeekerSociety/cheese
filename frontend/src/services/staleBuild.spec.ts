// 跨着发版开着的一页：点到一个服务器上已经没有的代码块时，先让新版本的 worker
// 接管，再刷新——否则刷新可能又落回旧版，这一页就一直点不开东西。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('virtual:pwa-register', () => ({ registerSW: () => async () => {} }))

let reload: ReturnType<typeof vi.fn>
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
  vi.stubGlobal('navigator', { ...navigator, serviceWorker: container })
}

beforeEach(() => {
  sessionStorage.clear()
  order = []
  reload = vi.fn(() => order.push('reload'))
  vi.stubGlobal('location', { ...window.location, reload })
})
afterEach(() => vi.unstubAllGlobals())

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
