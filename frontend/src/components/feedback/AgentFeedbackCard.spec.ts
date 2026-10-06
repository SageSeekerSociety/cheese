// 房间里的反馈卡：谁的判断、反馈说的是什么、详情收着、点开才看。
import type { FeedbackProposal } from '@/cx_types'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AgentFeedbackCard from './AgentFeedbackCard.vue'

import { dismissFeedbackProposal } from '@/api'
import i18n, { setLocale } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'

const proposal = {
  block_id: 'b1',
  author_handle: 'cheese-a3689921fba8',
  authored_at: new Date().toISOString(),
  payload: {
    kind: 'bug',
    title: '云端工作电脑反复创建失败',
    summary: '',
    problem: '',
    visibility: 'private',
    why: '没有可用的工作目录',
    expectation: null,
    what_happened: '创建了三次都失败',
    repro: null,
    evidence: null,
    logs: null,
    session_id: null,
    environment: null,
    user_said: '开台机器？',
  },
} as unknown as FeedbackProposal

vi.mock('@/api', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  listFeedbackProposals: vi.fn(async () => [proposal]),
  dismissFeedbackProposal: vi.fn(async () => ({ dismissed: true })),
}))

beforeEach(() => {
  setLocale('zh-CN')
  setActivePinia(createPinia())
})

async function mount() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useWorkspaceStore().members = [{ user_handle: 'cheese-a3689921fba8', name: 'Cedar', agent: true }] as never
  const view = render(AgentFeedbackCard, {
    props: { topicId: 't1' },
    global: {
      plugins: [
        pinia,
        i18n,
        createRouter({
          history: createMemoryHistory(),
          routes: [{ path: '/:rest(.*)', component: { template: '<div />' } }],
        }),
        createVuetify({ components, directives }),
      ],
    },
  })
  await vi.waitFor(() => expect(view.getByText('云端工作电脑反复创建失败')).toBeTruthy())
  return view
}

describe('房间里的反馈卡', () => {
  it('写的是队友的名字，不是它的 handle；反馈标题只出现一次', async () => {
    const view = await mount()
    expect(view.container.textContent).toContain('Cedar 认为这是平台的问题')
    expect(view.container.textContent).not.toContain('cheese-a3689921fba8')
    expect(view.getAllByText('云端工作电脑反复创建失败')).toHaveLength(1)
  })

  it('详情默认收着，读屏也读不到；点开才在，再点收回去', async () => {
    const view = await mount()
    const detail = view.getByText('创建了三次都失败')
    expect(detail.closest('[inert]')).not.toBeNull()
    await fireEvent.click(view.getByRole('button', { name: /查看详情/ }))
    expect(detail.closest('[inert]')).toBeNull()
    await fireEvent.click(view.getByRole('button', { name: /收起详情/ }))
    expect(detail.closest('[inert]')).not.toBeNull()
  })

  it('服务端记下了这一下「不用」，卡当场收起', async () => {
    const view = await mount()
    await fireEvent.click(view.getByRole('button', { name: /不用/ }))
    await vi.waitFor(() => expect(view.queryByText('云端工作电脑反复创建失败')).toBeNull())
  })

  it('服务端没记下，卡就回到屏幕上 —— 人不会以为这一下算数了', async () => {
    vi.mocked(dismissFeedbackProposal).mockRejectedValueOnce(new Error('500'))
    const view = await mount()
    await fireEvent.click(view.getByRole('button', { name: /不用/ }))
    await vi.waitFor(() => expect(view.getAllByText('云端工作电脑反复创建失败')).toHaveLength(1))
  })
})
