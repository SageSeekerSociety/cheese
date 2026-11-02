/** 对话里请人决定交付的那道题，「采纳」和交付栏上的「采纳」是同一个动作，所以同一屏上
 *  只有一种状态：交付栏的采纳灰着，题上的也灰着，悬停说的是同一句为什么；交付栏的亮着，
 *  题上的也亮着，点下去就是交付栏那一下（带着卡面上此刻的那一版 head）。
 *
 *  页面按任务页的样子挂：页面取一份卡（provideAcceptCard），交付栏和题读同一份。
 */
import type { AcceptCard, Block, MergeStateInfo } from '../../cx_types'
import type { AskAction } from '../../types/ask'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getAcceptCards = vi.fn()
const acceptCard = vi.fn()

vi.mock('@/me', () => ({ myHandle: () => 'alice', myId: () => null }))
vi.mock('@/api/invites', async (actual) => ({
  ...(await actual<typeof import('@/api/invites')>()),
  getMyInvites: vi.fn().mockResolvedValue({ unlocked: true }),
}))
vi.mock('@/api/reviewComments', () => ({
  listReviewComments: vi.fn(async () => ({ comments: [] })),
  writeReviewComment: vi.fn(),
  editReviewComment: vi.fn(),
  deleteReviewComment: vi.fn(),
}))
vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getAcceptCards: (...a: unknown[]) => getAcceptCards(...a),
    getPrChecks: vi.fn().mockResolvedValue({ available: false }),
    acceptCard: (...a: unknown[]) => acceptCard(...a),
    getTopic: vi.fn().mockResolvedValue({ id: 't1', status: 'active' }),
  }
})

import { provideAcceptCard } from '@/composables/useAcceptCard'
import { useAskResponse } from '@/composables/useAskResponse'

import AskQuickReplies from '../ask/AskQuickReplies.vue'
import TopicAcceptCard from '../TopicAcceptCard.vue'

import { stubOverlayGlobals } from './acceptHarness'

import i18n, { setLocale } from '@/i18n'

function mergeState(over: Partial<MergeStateInfo>): MergeStateInfo {
  return {
    state: 'unknown',
    who: 'human',
    reasons: [{ kind: 'no_signal', checks: [], detail: '还没有信号' }],
    head_sha: 'head-on-the-card',
    checked_at: null,
    since: null,
    ...over,
  }
}

/** 绑了 GitHub、骑着 PR #1、等 alice 采纳的卡。 */
function githubCard(state: Partial<MergeStateInfo>): AcceptCard {
  return {
    id: 'card-1',
    topic_id: 't1',
    task_id: 'k1',
    reviewer_handle: 'alice',
    focus: '',
    change_subject: 'fix(login): keep the session',
    change_body: null,
    status: 'pending',
    completes_task: true,
    decided_by: null,
    decided_at: null,
    note: '',
    note_level: null,
    created_at: '2026-09-01T00:00:00Z',
    gate_passed_at: null,
    gate_output: '',
    approvals: [],
    approvals_required: 1,
    pr_number: 1,
    pr_url: 'https://github.com/o/r/pull/1',
    forge: {
      kind: 'github_app',
      reports_checks: true,
      hosts_proposals: true,
      can_write_remote: true,
      pushes_to_external_remote: true,
      identity: 'user',
      declaration: '',
    },
    merge_state: mergeState(state),
    auto_merge: { allowed: false, armed_by: null, armed_at: null },
  } as unknown as AcceptCard
}

/** 芝士问的那道题：一个「采纳」按钮，决定的是 card-1。 */
const question = {
  id: 'q1',
  project_id: 'p1',
  topic_id: 'k1',
  kind: 'message',
  author_type: 'participant',
  author: 'agent',
  content: 'PR #1 还在等你决定。',
  reply_to: null,
  refs: [],
  created_at: '2026-09-01T00:00:00Z',
  meta: {
    asked: ['alice'],
    asked_all: false,
    answer_log: [],
    options: [
      {
        text: 'accept',
        action: {
          kind: 'accept',
          card: 'card-1',
          completes_task: true,
          head_sha: 'head-when-asked',
          deliverable_revision: null,
        },
      },
    ],
  },
} as unknown as Block

/** 任务页的最小形状：页面取一份卡，交付栏和对话里的题读同一份。 */
const Page = defineComponent({
  setup() {
    const accept = provideAcceptCard({ topicId: 't1', taskId: 'k1', topicStatus: 'active' })
    const { decide } = useAskResponse({
      timeline: { replace: () => {} },
      errorMsg: ref(null),
      viewer: ref('alice'),
      acceptCard: accept,
    })
    return () =>
      h('div', [
        h(AskQuickReplies, {
          block: question,
          names: {},
          viewer: 'alice',
          onDecide: (action: AskAction) => decide(question, action),
        }),
        h(TopicAcceptCard, { topicId: 't1', taskId: 'k1', topicStatus: 'active' }),
      ])
  },
})

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

async function mountWith(card: AcceptCard) {
  getAcceptCards.mockResolvedValue({ data: [card], has_more: false })
  const utils = render(Page, { global: { plugins: [createVuetify({ components, directives }), i18n] } })
  await flush()
  return utils
}

function barAccept(container: Element): HTMLButtonElement {
  const button = Array.from(container.querySelectorAll<HTMLButtonElement>('.accept-bar button')).find(
    (b) => b.textContent?.includes('采纳') && !b.classList.contains('accept-bar__status')
  )
  expect(button, '交付栏上应该有采纳').toBeTruthy()
  return button as HTMLButtonElement
}
function questionAccept(container: Element): HTMLButtonElement {
  const button = container.querySelector<HTMLButtonElement>('.ask-replies__option')
  expect(button, '题上应该有采纳').toBeTruthy()
  return button as HTMLButtonElement
}
const hoverText = (button: HTMLElement) => button.closest('[title]')?.getAttribute('title') ?? null

beforeAll(() => stubOverlayGlobals(vi))
afterAll(() => vi.unstubAllGlobals())
beforeEach(() => {
  setLocale('zh-CN')
  setActivePinia(createPinia())
  getAcceptCards.mockReset()
  acceptCard.mockReset()
})

describe('题上的采纳和交付栏上的采纳是同一个按钮的两个位置', () => {
  it('交付栏的灰着时题上的也灰着，悬停说的是同一句，点了什么都不发', async () => {
    const { container } = await mountWith(
      githubCard({ state: 'behind', who: 'human', reasons: [{ kind: 'behind', checks: [], detail: '落后于 main' }] })
    )
    const bar = barAccept(container)
    const asked = questionAccept(container)

    expect(bar.disabled).toBe(true)
    expect(asked.disabled).toBe(true)
    expect(asked.textContent).toContain(bar.textContent?.trim())
    expect(hoverText(asked)).toBe(hoverText(bar))
    expect(hoverText(asked)).toContain('落后于 main')

    await fireEvent.click(asked)
    expect(acceptCard).not.toHaveBeenCalled()
  })

  it('交付栏的亮着时题上的也亮着，点下去就是交付栏那一下', async () => {
    acceptCard.mockResolvedValue(githubCard({ state: 'clean' }))
    const { container } = await mountWith(githubCard({ state: 'clean' }))
    const asked = questionAccept(container)

    expect(barAccept(container).disabled).toBe(false)
    expect(asked.disabled).toBe(false)

    await fireEvent.click(asked)
    await flush()
    expect(acceptCard).toHaveBeenCalledTimes(1)
    // 合的是卡面上此刻那一版，和交付栏一样，不是问的时候记下的那一版。
    expect(acceptCard.mock.calls[0][2]).toBe('head-on-the-card')
  })
})
