import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ResourceLimitsNotice from './ResourceLimitsNotice.vue'

import { getResourceLimits } from '@/api'
import { setLocale } from '@/i18n'

vi.mock('@/api', () => ({ getResourceLimits: vi.fn() }))
afterEach(cleanup)
// 文案按界面语言取，初始语言跟着浏览器（happy-dom 报 en-US）；断言写的是中文。
beforeEach(() => setLocale('zh-CN'))

function mount() {
  return render(ResourceLimitsNotice, {
    global: { stubs: { VBtn: { template: '<button><slot /></button>' } } },
  })
}

describe('resource limits before project creation', () => {
  it('leads with the concurrency a new project runs at, and keeps the detail one step away', async () => {
    vi.mocked(getResourceLimits).mockResolvedValue({ max_concurrent_turns: 5 })
    const view = mount()
    expect(await view.findByText(/最多同时运行 5 个 AI 任务，超出后排队/)).toBeTruthy()
    // The detail is not in the first view; it is one press away.
    expect(view.queryByText('项目数量：当前未设置上限')).toBeNull()
    await fireEvent.click(view.getByRole('button', { name: '资源限制' }))
    expect(view.getByText('项目数量：当前未设置上限')).toBeTruthy()
    // No cloud machine allowance: cloud runs on the platform's pool.
    expect(view.queryByText(/云端机器|名额/)).toBeNull()
  })

  it('offers retry without inventing limits when the request fails', async () => {
    vi.mocked(getResourceLimits)
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ max_concurrent_turns: 4 })
    const view = mount()
    expect(await view.findByText('资源限制加载失败')).toBeTruthy()
    expect(view.queryByText(/最多同时运行/)).toBeNull()
    await fireEvent.click(view.getByRole('button', { name: '重试' }))
    expect(await view.findByText(/最多同时运行 4 个 AI 任务/)).toBeTruthy()
  })
})
