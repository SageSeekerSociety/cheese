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

import { dismissFeedbackProposal, listFeedbackProposals } from '@/api'
import i18n, { setLocale } from '@/i18n'
import { queryClient } from '@/query/client'
import { expectRoom, forgetRoomSnapshots, settleRoom } from '@/query/snapshot'
import { useWorkspaceStore } from '@/stores/workspace'
import { seedProject, seedProjects } from '@/test/seedQueries'

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
  forgetRoomSnapshots()
  queryClient.clear()
})

function render_() {
  const pinia = createPinia()
  setActivePinia(pinia)
  seedProjects([])
  seedProject('p1', {
    topics: [],
    members: [{ user_handle: 'cheese-a3689921fba8', name: 'Cedar', agent: true }] as never,
    unread: {},
    privateUnread: {},
    notifyLevels: {},
  })
  useWorkspaceStore().openProject('p1')
  return render(AgentFeedbackCard, {
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
}

async function mount() {
  const view = render_()
  await vi.waitFor(() => expect(view.getByText('云端工作电脑反复创建失败')).toBeTruthy())
  return view
}

/** 离开这个房间再进来：订阅已经发出去了，快照还没到。 */
function reenter() {
  expectRoom('t1', 't1')
  return render_()
}

const snapshot = (feedback_proposals: FeedbackProposal[]) =>
  ({
    members: [],
    feedback_proposals,
    skill_proposals: [],
    tasks: { open: [], recent: [] },
    pins: [],
    threads: { open: [], recycled: [] },
  }) as never

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

describe('从卡上打开的提交表单', () => {
  it('「来自谁的排查」叫提这张卡的那位队友在项目里的名字，不写「芝士」', async () => {
    // 表单开在一个 VOverlay 里，而 happy-dom 没有 visualViewport：不补上弹窗挂不起来。
    vi.stubGlobal('visualViewport', {
      width: 1024,
      height: 768,
      offsetLeft: 0,
      offsetTop: 0,
      addEventListener() {},
      removeEventListener() {},
    })
    const view = await mount()
    try {
      await fireEvent.click(view.getByRole('button', { name: i18n.global.t('feedback.proposal.submit') }))
      await vi.waitFor(() => expect(document.body.textContent).toContain('来自Cedar的排查'))
      expect(document.body.textContent).not.toContain('来自芝士的排查')
      expect(document.body.textContent).toContain('「Cedar提的东西有多少真的发出去了」')
    } finally {
      view.unmount()
      vi.unstubAllGlobals()
    }
  })
})

describe('用户没说过话的卡（FB-100）', () => {
  const saying = (said: Record<string, unknown>) =>
    ({ ...proposal, payload: { ...proposal.payload, ...said } }) as FeedbackProposal

  it('引的是用户原话时，标在「你当时说的」下面', async () => {
    const view = await mount()
    expect(view.container.textContent).toContain('你当时说的')
    expect(view.getByText('开台机器？')).toBeTruthy()
  })

  it.each([
    ['服务端说用户没说过话', { user_said: null, user_said_absent: true }],
    ['还没有这个字段的旧卡，写的是那句约定好的话', { user_said: '用户没有就这个问题说过话' }],
    ['旧卡在那句话后面多写了半句', { user_said: '用户没有就这个问题说过话，以上是芝士自己观察到的' }],
  ])('%s：不把任何话当成用户说的', async (_, said) => {
    vi.mocked(listFeedbackProposals).mockResolvedValueOnce([saying(said)])
    const view = await mount()
    expect(view.container.textContent).not.toContain('你当时说的')
    expect(view.container.textContent).not.toContain('用户没有就这个问题说过话')
    expect(view.container.textContent).toContain('用户没有就此说过话')
  })
})

describe('再进这个房间', () => {
  it('上次在这里的卡不等订阅回来就在，和对话一起出现；快照说它已经没了就收起', async () => {
    ;(await mount()).unmount()

    const view = reenter()
    expect(view.getByText('云端工作电脑反复创建失败')).toBeTruthy()

    settleRoom('t1', 't1', snapshot([]))
    await vi.waitFor(() => expect(view.queryByText('云端工作电脑反复创建失败')).toBeNull())
  })

  it('在这里「不用」掉的卡，再进来不会先冒出来一下', async () => {
    const first = await mount()
    await fireEvent.click(first.getByRole('button', { name: /不用/ }))
    await vi.waitFor(() => expect(first.queryByText('云端工作电脑反复创建失败')).toBeNull())
    first.unmount()

    const view = reenter()
    expect(view.queryByText('云端工作电脑反复创建失败')).toBeNull()
  })
})
