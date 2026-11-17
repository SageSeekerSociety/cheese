/** 主线上的「采纳」：采纳的是此刻等着审阅的那一版；没有了就什么也不做；合并冲突、被拒都要
 * 让人知道；按完按钮回到能按的样子。 */
import type { AcceptCard } from '@/cx_types'

import { beforeEach, describe, expect, it, vi } from 'vitest'

const getAcceptCards = vi.fn()
const acceptCard = vi.fn()
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getAcceptCards: (...a: unknown[]) => getAcceptCards(...a),
    acceptCard: (...a: unknown[]) => acceptCard(...a),
  }
})
vi.mock('@/composables/useInvitePrompt', () => ({ noteAccepted: vi.fn(async () => {}) }))

import { useAdoptInPlace } from './adoptInPlace'

import { setLocale } from '@/i18n'

const pending = {
  id: 'card-1',
  status: 'pending',
  merge_state: { head_sha: 'abc' },
  deliverable: { revision: 'r2' },
} as unknown as AcceptCard

function setup() {
  const store = {
    error: null as string | null,
    agentName: '芝士',
    reportError: vi.fn(),
    refreshTopicRow: vi.fn(async () => {}),
  }
  const opened: string[] = []
  const views: string[] = []
  const returns: string[] = []
  const adopt = useAdoptInPlace({
    me: () => 'alice',
    store: store as never,
    topicId: () => 'room',
    openTask: (id) => void opened.push(id),
    showView: (id, view) => void views.push(`${id}:${view}`),
    askReturn: (id) => void returns.push(id),
  })
  return { store, adopt, opened, views, returns }
}

beforeEach(() => {
  setLocale('zh-CN')
  getAcceptCards.mockReset()
  acceptCard.mockReset()
})

describe('主线上采纳', () => {
  it('采纳的是此刻等着审阅的那一版，按看到的版本去采', async () => {
    getAcceptCards.mockResolvedValue({ data: [{ ...pending, id: 'old', status: 'rejected' }, pending] })
    acceptCard.mockResolvedValue({ ...pending, status: 'accepted' })
    const { adopt, store } = setup()
    await adopt.onDelivery({ taskId: 't1', view: 'adopt' })
    expect(acceptCard).toHaveBeenCalledWith('card-1', 'alice', 'abc', 'r2')
    expect(store.refreshTopicRow).toHaveBeenCalledWith('room')
    expect(adopt.adopting.value).toBeNull()
  })

  it('已经没有在等审阅的那一版（别人刚定了）就什么也不做', async () => {
    getAcceptCards.mockResolvedValue({ data: [{ ...pending, status: 'accepted' }] })
    const { adopt } = setup()
    await adopt.onDelivery({ taskId: 't1', view: 'adopt' })
    expect(acceptCard).not.toHaveBeenCalled()
  })

  it('合并碰到冲突时说一句', async () => {
    getAcceptCards.mockResolvedValue({ data: [pending] })
    acceptCard.mockResolvedValue({ ...pending, status: 'conflict' })
    const { adopt, store } = setup()
    await adopt.onDelivery({ taskId: 't1', view: 'adopt' })
    expect(store.error).toContain('冲突')
  })

  it('被拒时报出来，按钮回到能按的样子', async () => {
    getAcceptCards.mockResolvedValue({ data: [pending] })
    acceptCard.mockRejectedValue(new Error('版本过时'))
    const { adopt, store } = setup()
    await adopt.onDelivery({ taskId: 't1', view: 'adopt' })
    expect(store.reportError).toHaveBeenCalled()
    expect(adopt.adopting.value).toBeNull()
  })
})

describe('主线上退回', () => {
  it('打开这件任务、停在改动上，开出写理由的那一块', async () => {
    const { adopt, opened, views, returns } = setup()
    await adopt.onDelivery({ taskId: 't1', view: 'return' })
    expect(opened).toEqual(['t1'])
    expect(views).toEqual(['t1:changes'])
    expect(returns).toEqual(['t1'])
  })
})
