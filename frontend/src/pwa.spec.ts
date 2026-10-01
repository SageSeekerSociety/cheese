// 新版本在下一次应用内跳转时换上：不在原地刷新，也不弹提示问人。
import type { RegisterSWOptions } from 'virtual:pwa-register'

import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const sw = vi.hoisted(() => ({
  options: null as RegisterSWOptions | null,
  activate: vi.fn(async () => {}),
}))

vi.mock('virtual:pwa-register', () => ({
  registerSW: (options: RegisterSWOptions) => {
    sw.options = options
    return sw.activate
  },
}))

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
  sw.activate.mockClear()
  assign = vi.fn()
  vi.stubGlobal('location', { ...window.location, assign })
})

describe('新版本怎么换上', () => {
  it('没有新版本时，跳转照常在页面里完成', async () => {
    const router = await start()
    await router.push('/b')
    expect(router.currentRoute.value.path).toBe('/b')
    expect(assign).not.toHaveBeenCalled()
  })

  it('新版本下好之后，下一次跳转整页加载到目标地址，并让新版本接管', async () => {
    const router = await start()
    sw.options!.onNeedRefresh!()
    await router.push('/b')
    expect(assign).toHaveBeenCalledWith('/b')
    expect(sw.activate).toHaveBeenCalledOnce()
    expect(router.currentRoute.value.path).toBe('/a')
  })

  // 新 worker 被别的标签页换上之后，这一页不刷新，下一次跳转再整页加载。
  it('别的标签页换上了新版本：这一页不刷新，下一次跳转整页加载', async () => {
    const router = await start()
    sw.options!.onNeedReload!()
    expect(assign).not.toHaveBeenCalled()
    await router.push('/b')
    expect(assign).toHaveBeenCalledWith('/b')
  })

  it('只换 query 的跳转不算离开这一页', async () => {
    const router = await start()
    sw.options!.onNeedRefresh!()
    await router.push('/a?tab=2')
    expect(assign).not.toHaveBeenCalled()
  })
})
