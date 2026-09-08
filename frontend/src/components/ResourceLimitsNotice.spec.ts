import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ResourceLimitsNotice from './ResourceLimitsNotice.vue'

import { getResourceLimits } from '@/api'

vi.mock('@/api', () => ({ getResourceLimits: vi.fn() }))
afterEach(cleanup)

function mount() {
  return render(ResourceLimitsNotice, {
    global: { stubs: { VBtn: { template: '<button><slot /></button>' } } },
  })
}

describe('resource limits before project creation', () => {
  it('shows deployment values and distinguishes concurrency from inventory', async () => {
    vi.mocked(getResourceLimits).mockResolvedValue({ max_machines_per_team: 7, max_concurrent_turns: 5 })
    const view = mount()
    expect(await view.findByText('项目数量：当前未设置上限')).toBeTruthy()
    expect(view.getByText(/最多同时运行 5 个 AI 任务，超出后排队/)).toBeTruthy()
    expect(view.getByText(/团队默认共享 7 台名额/)).toBeTruthy()
  })

  it('offers retry without inventing limits when the request fails', async () => {
    vi.mocked(getResourceLimits)
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ max_machines_per_team: 3, max_concurrent_turns: 4 })
    const view = mount()
    expect(await view.findByText('资源限制加载失败')).toBeTruthy()
    expect(view.queryByText(/团队默认共享/)).toBeNull()
    await fireEvent.click(view.getByRole('button', { name: '重试' }))
    expect(await view.findByText(/团队默认共享 3 台名额/)).toBeTruthy()
  })
})
