/** 邀请提醒在验收卡上的两个接头：等他采纳的卡一出现就先看一眼邀请码（别人的卡不看），
 * 采纳真的成了才算一次采纳（合并冲突不算）。提醒本身见 __tests__/useInvitePrompt.spec.ts。 */
import type { AcceptCard } from '@/cx_types'

import { defineComponent, h, reactive } from 'vue'
import { render } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getAcceptCards = vi.fn()
const acceptCard = vi.fn()
const primeInvitePrompt = vi.fn().mockResolvedValue(undefined)
const noteAccepted = vi.fn().mockResolvedValue(undefined)

vi.mock('@/api/reviewComments', () => ({
  listReviewComments: vi.fn(async () => ({ comments: [] })),
  writeReviewComment: vi.fn(),
  editReviewComment: vi.fn(),
  deleteReviewComment: vi.fn(),
}))
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getAcceptCards: (...a: unknown[]) => getAcceptCards(...a),
    acceptCard: (...a: unknown[]) => acceptCard(...a),
    getPrChecks: vi.fn().mockResolvedValue({ available: false }),
  }
})
vi.mock('@/composables/useInvitePrompt', () => ({
  primeInvitePrompt: () => primeInvitePrompt(),
  noteAccepted: () => noteAccepted(),
}))
vi.mock('@/me', async () => ({ ...(await vi.importActual<typeof import('@/me')>('@/me')), myHandle: () => 'alice' }))

import { useAcceptCard } from './useAcceptCard'

import { useWorkspaceStore } from '@/stores/workspace'

function card(over: Partial<AcceptCard> = {}): AcceptCard {
  return {
    id: 'card-1',
    topic_id: 't1',
    reviewer_handle: 'alice',
    status: 'pending',
    note: '',
    approvals: [],
    merge_state: { state: 'clean', who: 'human', reasons: [], head_sha: null, checked_at: null, since: null },
    forge: { reports_checks: false, hosts_proposals: false },
    pr_number: null,
    ...over,
  } as unknown as AcceptCard
}

async function flush() {
  for (let i = 0; i < 6; i += 1) await new Promise((r) => setTimeout(r, 0))
}

function host() {
  let api!: ReturnType<typeof useAcceptCard>
  render(
    defineComponent(() => {
      api = useAcceptCard(reactive({ topicId: 't1', taskId: 'k1', topicStatus: 'active' }))
      return () => h('div')
    })
  )
  return api
}

beforeEach(() => {
  setActivePinia(createPinia())
  getAcceptCards.mockReset()
  acceptCard.mockReset()
  primeInvitePrompt.mockClear()
  noteAccepted.mockClear()
  useWorkspaceStore().refreshTopicRow = vi.fn().mockResolvedValue(undefined)
})

describe('邀请提醒在验收卡上的接头', () => {
  it('等他采纳的卡出现时先看一眼邀请码', async () => {
    getAcceptCards.mockResolvedValue({ data: [card()] })
    host()
    await flush()
    expect(primeInvitePrompt).toHaveBeenCalled()
  })

  it('卡等的是别人，不看', async () => {
    getAcceptCards.mockResolvedValue({ data: [card({ reviewer_handle: 'bob' })] })
    host()
    await flush()
    expect(primeInvitePrompt).not.toHaveBeenCalled()
  })

  it('采纳成了算一次采纳', async () => {
    getAcceptCards.mockResolvedValue({ data: [card()] })
    acceptCard.mockResolvedValue({ status: 'accepted' })
    const api = host()
    await flush()
    await api.onAcceptCard()
    expect(noteAccepted).toHaveBeenCalledTimes(1)
  })

  it('合并冲突没采纳成，不算', async () => {
    getAcceptCards.mockResolvedValue({ data: [card()] })
    acceptCard.mockResolvedValue({ status: 'conflict' })
    const api = host()
    await flush()
    await api.onAcceptCard()
    expect(noteAccepted).not.toHaveBeenCalled()
  })
})
