/** 一件任务的批注：写、改、删没被服务器收下时，调用方拿到的是原因，不是一个没人接的异常；
 *  手上那几条也不会因此变样。 */
import type { AcceptCard } from '@/cx_types'

import { effectScope } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const writeReviewComment = vi.fn()
const editReviewComment = vi.fn()
const deleteReviewComment = vi.fn()
vi.mock('@/api/reviewComments', () => ({
  listReviewComments: vi.fn(async () => ({ comments: [] })),
  writeReviewComment: (...a: unknown[]) => writeReviewComment(...a),
  editReviewComment: (...a: unknown[]) => editReviewComment(...a),
  deleteReviewComment: (...a: unknown[]) => deleteReviewComment(...a),
}))

import { useReviewComments } from './useReviewComments'

import { ApiError } from '@/api/http'
import { setLocale } from '@/i18n'

beforeEach(() => {
  setLocale('zh-CN')
  writeReviewComment.mockReset()
  editReviewComment.mockReset()
  deleteReviewComment.mockReset()
})

const CARD = { id: 'card-1', status: 'pending' } as AcceptCard

/** 起好，并等进任务时那一次读回来。 */
async function start() {
  const scope = effectScope()
  const state = scope.run(() => useReviewComments({ taskId: () => 'task-1', pendingCard: () => CARD }))!
  await new Promise((r) => setTimeout(r, 0))
  return state
}

const DRAFT = { path: 'a.ts', line_start: 1, line_end: 1, line_text: 'x', body: '改一下', suggestion: null }

describe('服务器没收下', () => {
  it('写：拿到服务器说的原因，手上的批注不多一条', async () => {
    writeReviewComment.mockRejectedValue(new ApiError(409, '任务已不在待审阅'))
    const state = await start()

    await expect(state.add(DRAFT)).resolves.toBe('任务已不在待审阅')
    expect(state.items.value).toEqual([])
    expect(state.busy.value).toBe(false)
  })

  it('改：拿到原因', async () => {
    editReviewComment.mockRejectedValue(new ApiError(500, '服务器出错了'))
    const state = await start()

    await expect(state.edit('c-1', '新的', null)).resolves.toBe('服务器出错了')
  })

  it('删：连不上服务器时也给一句原因', async () => {
    deleteReviewComment.mockRejectedValue(new TypeError('Failed to fetch'))
    const state = await start()

    const reason = await state.remove('c-1')
    expect(typeof reason).toBe('string')
    expect(reason).not.toBe('')
  })
})

describe('服务器收下了', () => {
  it('写：没有原因，新的一条出现在手上', async () => {
    writeReviewComment.mockResolvedValue({ id: 'c-9', ...DRAFT, state: 'draft', author: '' })
    const state = await start()

    await expect(state.add(DRAFT)).resolves.toBeNull()
    expect(state.items.value.map((c) => c.id)).toContain('c-9')
  })
})
