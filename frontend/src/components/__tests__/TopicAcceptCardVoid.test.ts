/** 作废是一张未决卡在界面上的出口。
 *
 * PR 在 GitHub 上被关掉而没有合并时，卡会停在待处理，卡上和房间里都写着「可以重新
 * 打开 PR，或者作废这次审阅」。这里断言那句话说的动作真的点得到：展开、写理由、
 * 确认之后打的是作废端点，卡随之离开待处理。
 */
import type { AcceptCard } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getAcceptCards = vi.fn()
const voidCard = vi.fn()
const rejectCard = vi.fn()

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

import TopicAcceptCard from '../TopicAcceptCard.vue'

const CLOSED_NOTE = 'PR #12 已关闭且没有合并，平台不会自动合并。可以重新打开 PR，或者作废这次审阅。'

function closedPrCard(over: Partial<AcceptCard> = {}): AcceptCard {
  return {
    id: 'card-1',
    topic_id: 't1',
    reviewer_handle: 'alice',
    routing_reason: '',
    change_subject: 'feat: a thing',
    change_body: null,
    status: 'pending',
    decided_by: null,
    decided_at: null,
    note: CLOSED_NOTE,
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
  const utils = render(TopicAcceptCard, {
    props: { topicId: 't1', topicStatus: 'active' },
    global: { plugins: [vuetify] },
  })
  await flush()
  return utils
}

beforeEach(() => {
  setActivePinia(createPinia())
  getAcceptCards.mockReset()
  voidCard.mockReset()
  rejectCard.mockReset()
})

describe('作废一张 PR 已关闭的卡', () => {
  it('卡上说的「作废这次审阅」点得到，确认后卡离开待处理', async () => {
    const card = closedPrCard()
    const { container, getByRole, getByPlaceholderText } = await mountWith([card])
    expect(container.textContent).toContain(CLOSED_NOTE)

    await fireEvent.click(getByRole('button', { name: '作废' }))
    await fireEvent.update(getByPlaceholderText('作废理由（可选）'), '换到另一个房间做了')

    const voided = { ...card, status: 'revoked', note_level: 'info' } as AcceptCard
    voidCard.mockResolvedValue(voided)
    getAcceptCards.mockResolvedValue({ data: [voided], has_more: false })
    await fireEvent.click(getByRole('button', { name: '确认作废' }))
    await flush()

    expect(voidCard).toHaveBeenCalledWith(card.id, '换到另一个房间做了')
    expect(rejectCard).not.toHaveBeenCalled()
    expect(container.textContent).not.toContain('确认作废')
    expect(container.textContent).not.toContain(CLOSED_NOTE)
  })

  it('只点开不确认，什么都不发', async () => {
    const { getByRole } = await mountWith([closedPrCard()])
    await fireEvent.click(getByRole('button', { name: '作废' }))
    await flush()
    expect(voidCard).not.toHaveBeenCalled()
  })

  it('没有权限作废时，服务端的拒绝报给人，卡仍在待处理', async () => {
    const card = closedPrCard()
    const { container, getByRole } = await mountWith([card])
    voidCard.mockRejectedValue({ message: '只有被指定审阅的人、项目所有者或团队管理员能作废' })
    await fireEvent.click(getByRole('button', { name: '作废' }))
    await fireEvent.click(getByRole('button', { name: '确认作废' }))
    await flush()
    expect(voidCard).toHaveBeenCalledOnce()
    expect(container.textContent).toContain(CLOSED_NOTE)
  })
})
