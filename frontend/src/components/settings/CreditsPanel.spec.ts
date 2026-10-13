// 项目设置里的「额度」：最后一次调用可以把额度透支一点（计费流程 · 额度从哪来），
// 透支的那一点照实记在「已使用」里，可「剩余」不能写成负数。
import { createVuetify } from 'vuetify'
import { render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CreditsPanel from './CreditsPanel.vue'

import { getProjectCredits } from '@/api'
import { setLocale } from '@/i18n'

vi.mock('@/api', async () => ({
  ...(await vi.importActual<typeof import('@/api')>('@/api')),
  getProjectCredits: vi.fn(),
}))

beforeEach(() => setLocale('zh-CN'))

describe('项目设置里的额度', () => {
  it('透支了一点时剩余写 0，说已用完，已使用照实写', async () => {
    vi.mocked(getProjectCredits).mockResolvedValue({
      unlimited: false,
      credits_total: 1000,
      credits_used: 1000.7635,
      credits_remaining: -0.7635,
      grants: [],
    } as never)
    const view = render(CreditsPanel, { props: { projectId: 'p1' }, global: { plugins: [createVuetify()] } })

    await view.findByText(/点剩余/)
    const left = view.container.querySelector('.credit-remaining')
    expect(left?.textContent?.replace(/\s+/g, '')).toBe('0点剩余')
    expect(view.queryByText(/-0\.76/)).toBeNull()
    expect(view.getByText('1001 / 1000')).toBeTruthy()
    expect(view.getByText(/额度已用完/)).toBeTruthy()
  })
})
