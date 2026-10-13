/** 作废是一张未决卡在界面上的出口。
 *
 * 卡停在一个没人能推进的地方时（这里用 GitHub 拒绝合并那一种），人要能把这次审阅
 * 结束掉。这里断言这个动作真的点得到：从「更多操作」打开、写理由、确认之后打的是作废
 * 端点，卡随之离开待处理。
 */
import type { AcceptCard } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getAcceptCards = vi.fn()
const voidCard = vi.fn()
const rejectCard = vi.fn()

// 任务页的卡旁边还有这件任务的审阅意见；这里测的是卡，意见是空的。
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
    voidCard: (...a: unknown[]) => voidCard(...a),
    rejectCard: (...a: unknown[]) => rejectCard(...a),
    getTopic: vi.fn().mockResolvedValue({ id: 't1', status: 'active' }),
  }
})

import { AcceptPage, chooseMore, dialogButton, stubOverlayGlobals } from './acceptHarness'

import i18n, { setLocale } from '@/i18n'

const STUCK_NOTE = 'PR #12 检查全绿，但 GitHub 拒绝合并：分支保护要求的审批还不够。'

function stuckCard(over: Partial<AcceptCard> = {}): AcceptCard {
  return {
    id: 'card-1',
    topic_id: 't1',
    reviewer_handle: 'alice',
    focus: '',
    change_subject: 'feat: a thing',
    change_body: null,
    status: 'pending',
    decided_by: null,
    decided_at: null,
    note: STUCK_NOTE,
    note_level: 'error',
    created_at: '2026-09-01T00:00:00Z',
    gate_passed_at: null,
    gate_output: '',
    approvals: [],
    approvals_required: 1,
    pr_number: 12,
    pr_url: 'https://github.com/o/r/pull/12',
    forge: {
      kind: 'github_app',
      reports_checks: true,
      hosts_proposals: true,
      can_write_remote: true,
      pushes_to_external_remote: true,
      identity: 'user',
      declaration: '',
    },
    merge_state: {
      state: 'unknown',
      who: 'human',
      reasons: [{ kind: 'no_signal', checks: [], detail: '还没有信号' }],
      head_sha: null,
      checked_at: null,
      since: null,
    },
    auto_merge: { allowed: false, armed_by: null, armed_at: null },
    ...over,
  } as AcceptCard
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

async function mountWith(cards: AcceptCard[]) {
  getAcceptCards.mockResolvedValue({ data: cards, has_more: false })
  const vuetify = createVuetify({ components, directives })
  const utils = render(AcceptPage, {
    props: { topicId: 't1', taskId: 'k1', topicStatus: 'active' },
    global: { plugins: [vuetify, i18n] },
  })
  await flush()
  return utils
}

beforeAll(() => stubOverlayGlobals(vi))
afterAll(() => vi.unstubAllGlobals())

const voidReason = () => document.querySelector<HTMLInputElement>('.v-overlay .confirm-dialog input')!

beforeEach(() => {
  // These assertions read the Chinese copy.
  setLocale('zh-CN')
  setActivePinia(createPinia())
  getAcceptCards.mockReset()
  voidCard.mockReset()
  rejectCard.mockReset()
})

describe('作废一张停住的卡', () => {
  it('作废点得到，确认后卡离开待处理', async () => {
    const card = stuckCard()
    const { container } = await mountWith([card])
    expect(container.textContent).toContain(STUCK_NOTE)

    await chooseMore(container, '作废')
    await fireEvent.update(voidReason(), '换到另一个房间做了')

    const voided = { ...card, status: 'revoked', note_level: 'info' } as AcceptCard
    voidCard.mockResolvedValue(voided)
    getAcceptCards.mockResolvedValue({ data: [voided], has_more: false })
    await fireEvent.click(dialogButton('作废')!)
    await flush()

    expect(voidCard).toHaveBeenCalledWith(card.id, '换到另一个房间做了')
    expect(rejectCard).not.toHaveBeenCalled()
    expect(container.textContent).not.toContain(STUCK_NOTE)
  })

  it('只点开不确认，什么都不发', async () => {
    const { container } = await mountWith([stuckCard()])
    await chooseMore(container, '作废')
    await fireEvent.click(dialogButton('取消')!)
    await flush()
    expect(voidCard).not.toHaveBeenCalled()
  })

  it('没有权限作废时，服务端的拒绝报给人，卡仍在待处理', async () => {
    const card = stuckCard()
    const { container } = await mountWith([card])
    voidCard.mockRejectedValue({ message: '只有被指定审阅的人、项目所有者或团队管理员能作废' })
    await chooseMore(container, '作废')
    await fireEvent.click(dialogButton('作废')!)
    await flush()
    expect(voidCard).toHaveBeenCalledOnce()
    expect(container.textContent).toContain(STUCK_NOTE)
  })
})
