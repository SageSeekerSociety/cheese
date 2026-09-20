// 更新提示条：它在，用户才知道自己被留在旧版本上；它响了，用户点得动。
//
// 「稍后」只收起这一次——这一条钉在这里，是因为它和「永远别再问我」看起来只差
// 一行代码，而后者会让这个页面一直跑着旧版本。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  updateReady: null as unknown as { value: boolean },
  applyUpdate: vi.fn(),
  dismissUpdate: vi.fn(),
}))

vi.mock('@/pwa', async () => {
  const { ref } = await import('vue')
  mocks.updateReady = ref(false)
  return {
    updateReady: mocks.updateReady,
    applyUpdate: mocks.applyUpdate,
    dismissUpdate: mocks.dismissUpdate,
  }
})

import UpdateBanner from './UpdateBanner.vue'

const Banner = UpdateBanner as unknown as Parameters<typeof render>[0]

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  mocks.updateReady.value = false
  mocks.applyUpdate.mockClear()
  mocks.dismissUpdate.mockClear()
})

const mount = () => render(Banner, { global: { plugins: [vuetify] } })

describe('有新版本时', () => {
  it('显示提示和两个动作', () => {
    mocks.updateReady.value = true
    const { container } = mount()

    const banner = container.querySelector('.update-banner')
    expect(banner).toBeTruthy()
    expect(banner!.textContent).toContain('有新版本')
    expect(banner!.textContent).toContain('立即更新')
    expect(banner!.textContent).toContain('稍后')
  })

  it('「立即更新」交给 pwa.ts 去接管', async () => {
    mocks.updateReady.value = true
    const { getByText } = mount()

    await fireEvent.click(getByText('立即更新'))
    expect(mocks.applyUpdate).toHaveBeenCalledTimes(1)
  })

  it('「稍后」只收起这一条，不动 service worker', async () => {
    mocks.updateReady.value = true
    const { getByText } = mount()

    await fireEvent.click(getByText('稍后'))
    expect(mocks.dismissUpdate).toHaveBeenCalledTimes(1)
    // 关掉提示不等于放弃更新：新 worker 还在 waiting 里等下一次。
    expect(mocks.applyUpdate).not.toHaveBeenCalled()
  })
})

describe('没有新版本时', () => {
  it('什么都不显示（不能占着顶栏）', () => {
    const { container } = mount()
    expect(container.querySelector('.update-banner')).toBeNull()
  })
})

describe('离线时', () => {
  it('让位给离线横幅：顶上只有一个位置', async () => {
    mocks.updateReady.value = true
    const spy = vi.spyOn(window.navigator, 'onLine', 'get').mockReturnValue(false)
    const { container } = mount()

    window.dispatchEvent(new Event('offline'))
    await new Promise((r) => setTimeout(r, 0))

    expect(container.querySelector('.update-banner')).toBeNull()
    spy.mockRestore()
  })
})
