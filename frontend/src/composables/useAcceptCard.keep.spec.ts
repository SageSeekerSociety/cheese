/** 点完验收卡上的一个按钮，卡留在屏幕上换成新的一版，不先整张消失再长回来。
 * 只有换了话题才清空。 */
import type { AcceptCard } from '@/cx_types'

import { defineComponent, h, reactive } from 'vue'
import { render } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getAcceptCards = vi.fn()
const reassignCard = vi.fn()
const acceptCard = vi.fn()

// 任务页的卡旁边还有这件任务的审阅意见；这里测的是卡，意见是空的。
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
    reassignCard: (...a: unknown[]) => reassignCard(...a),
    acceptCard: (...a: unknown[]) => acceptCard(...a),
    getPrChecks: vi.fn().mockResolvedValue({ available: false }),
  }
})

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

function deferred<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => (resolve = r))
  return { promise, resolve }
}

function host(props: { topicId: string; topicStatus: string; taskId?: string }) {
  let api!: ReturnType<typeof useAcceptCard>
  render(
    defineComponent(() => {
      api = useAcceptCard(props)
      return () => h('div')
    })
  )
  return api
}

beforeEach(() => {
  setActivePinia(createPinia())
  getAcceptCards.mockReset()
  reassignCard.mockReset().mockResolvedValue({})
  acceptCard.mockReset().mockResolvedValue({ status: 'accepted' })
  useWorkspaceStore().refreshTopicRow = vi.fn().mockResolvedValue(undefined)
})

describe('useAcceptCard keeps the card while it re-reads', () => {
  it('an action re-reads without blanking the card, and unchanged cards keep their object', async () => {
    getAcceptCards.mockResolvedValue({ data: [card(), card({ id: 'old', status: 'accepted' })] })
    const api = host(reactive({ topicId: 't1', taskId: 'k1', topicStatus: 'active' }))
    await flush()
    const before = api.pendingCard.value
    const accepted = api.acceptedCard.value
    expect(before?.id).toBe('card-1')

    const reread = deferred<{ data: AcceptCard[] }>()
    getAcceptCards.mockReturnValue(reread.promise)
    void api.onReassignCard('bob')
    await flush()
    // 重读还在路上：屏幕上仍是原来那张卡。
    expect(api.pendingCard.value).toBe(before)
    expect(api.loaded.value).toBe(true)

    reread.resolve({ data: [card({ reviewer_handle: 'bob' }), card({ id: 'old', status: 'accepted' })] })
    await flush()
    expect(api.pendingCard.value?.reviewer_handle).toBe('bob')
    expect(api.acceptedCard.value).toBe(accepted)
  })

  it('switching topics clears the old topic’s cards at once', async () => {
    getAcceptCards.mockResolvedValue({ data: [card()] })
    const props = reactive({ topicId: 't1', taskId: 'k1', topicStatus: 'active' })
    const api = host(props)
    await flush()
    expect(api.pendingCard.value).not.toBeNull()

    getAcceptCards.mockReturnValue(new Promise(() => {}))
    props.topicId = 't2'
    await flush()
    expect(api.pendingCard.value).toBeNull()
    expect(api.loaded.value).toBe(false)
  })

  it('a re-read that comes back after a newer one does not overwrite it', async () => {
    getAcceptCards.mockResolvedValue({ data: [card()] })
    const api = host(reactive({ topicId: 't1', taskId: 'k1', topicStatus: 'active' }))
    await flush()

    const slow = deferred<{ data: AcceptCard[] }>()
    const fast = deferred<{ data: AcceptCard[] }>()
    getAcceptCards.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise)
    void api.reload()
    void api.reload()
    fast.resolve({ data: [card({ reviewer_handle: 'new' })] })
    await flush()
    slow.resolve({ data: [card({ reviewer_handle: 'stale' })] })
    await flush()
    expect(api.pendingCard.value?.reviewer_handle).toBe('new')
  })
  it('a decided card does not keep a half-written return note open', async () => {
    getAcceptCards.mockResolvedValue({ data: [card()] })
    const api = host(reactive({ topicId: 't1', taskId: 'k1', topicStatus: 'active' }))
    await flush()
    api.showRejectInput.value = true
    api.rejectNote.value = 'half typed'

    getAcceptCards.mockResolvedValue({ data: [card({ status: 'accepted' })] })
    await api.onAcceptCard()
    expect(api.showRejectInput.value).toBe(false)
    expect(api.rejectNote.value).toBe('')
  })

  it('switching topics closes the void and force-merge forms', async () => {
    getAcceptCards.mockResolvedValue({ data: [card()] })
    const props = reactive({ topicId: 't1', taskId: 'k1', topicStatus: 'active' })
    const api = host(props)
    await flush()
    api.showVoidInput.value = true
    api.voidNote.value = 'x'
    api.showForceMergeInput.value = true
    api.forceMergeReason.value = 'y'
    props.topicId = 't2'
    await flush()
    expect([api.showVoidInput.value, api.voidNote.value]).toEqual([false, ''])
    expect([api.showForceMergeInput.value, api.forceMergeReason.value]).toEqual([false, ''])
  })
})

describe('a channel has no card of its own', () => {
  it('answers「没有卡」at once without asking the server, and a reload does not ask either', async () => {
    const api = host(reactive({ topicId: 't1', topicStatus: 'active' }))
    await flush()
    expect(api.loaded.value).toBe(true)
    expect(api.pendingCard.value).toBeNull()

    await api.reload()
    expect(getAcceptCards).not.toHaveBeenCalled()
  })
})
