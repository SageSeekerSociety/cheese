/**
 * 管理端写操作之后：列表要重新拉，详情不能被别人覆盖。
 *
 * 两条都是**真的发生过**的：写完之后列表被清空、而管理端这一页在抽屉关掉时既不会
 * 重新挂载也不会重新拉取，于是表格画出来的是「这一栏没有反馈」——一个假状态；同一
 * 个写请求在飞的时候人可能已经点开了另一条，慢响应回来会把 `detailId` 拉回旧的那条，
 * 新条目的抽屉就一直卡在「加载中…」（它的 `detailLoading` 再也不会被复位）。
 */
import type { FeedbackDetail } from '@/cx_types'

import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getAdminFeedback = vi.fn()
const listAdminFeedback = vi.fn()
const setAdminFeedbackStatus = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getAdminFeedback: (...a: unknown[]) => getAdminFeedback(...a),
    listAdminFeedback: (...a: unknown[]) => listAdminFeedback(...a),
    setAdminFeedbackStatus: (...a: unknown[]) => setAdminFeedbackStatus(...a),
  }
})

import { useFeedbackStore } from '@/stores/feedback'

function detail(id: string, status = 'in_progress'): FeedbackDetail {
  return { id, status, title: `反馈 ${id}`, supports: 0, comments: 0 } as unknown as FeedbackDetail
}

function page(ids: string[]) {
  return {
    data: ids.map((id) => ({ id, title: `反馈 ${id}` })),
    total: ids.length,
    counts: { all: ids.length, hot: 0, active: 0, resolved: 0, unread: 0 },
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  getAdminFeedback.mockReset()
  listAdminFeedback.mockReset()
  setAdminFeedbackStatus.mockReset()
  listAdminFeedback.mockResolvedValue(page(['fb-1', 'fb-2']))
})

describe('管理端的写操作', () => {
  it('写完之后列表重新拉一次，而不是留一张空表', async () => {
    const store = useFeedbackStore()
    await store.loadAdmin()
    expect(store.adminItems).toHaveLength(2)

    setAdminFeedbackStatus.mockResolvedValue(detail('fb-1'))
    // 写完之后的列表已经不含这一条了（它挪进了别的一栏）。
    listAdminFeedback.mockResolvedValue(page(['fb-2']))

    await store.setStatus('fb-1', 'resolved')

    expect(listAdminFeedback).toHaveBeenCalledTimes(2)
    expect(store.adminItems.map((i) => i.id)).toEqual(['fb-2'])
  })

  it('写的那条已经不是当前这条时，不覆盖详情', async () => {
    const store = useFeedbackStore()
    getAdminFeedback.mockResolvedValue(detail('fb-1'))
    await store.loadAdminDetail('fb-1')

    // 写请求在飞……
    let finish: (v: FeedbackDetail) => void = () => {}
    setAdminFeedbackStatus.mockImplementation(
      () =>
        new Promise<FeedbackDetail>((resolve) => {
          finish = resolve
        })
    )
    const writing = store.setStatus('fb-1', 'resolved')

    // ……这段时间里人点开了另一条（它的详情也在飞，所以 detail 是空的）。
    getAdminFeedback.mockImplementation(() => new Promise<FeedbackDetail>(() => {}))
    void store.loadAdminDetail('fb-2')

    finish(detail('fb-1'))
    await writing

    expect(store.detailId).toBe('fb-2')
    expect(store.detail).toBeNull()
  })
})
