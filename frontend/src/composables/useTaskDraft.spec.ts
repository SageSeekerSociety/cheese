// 发布页、改题页的草稿怎么存：人的每一次改动最后都存进服务端那一份，不管是谁先让它建起来
// （人改了、问芝士、离开页面），也不管改在建的那一阵里还是之后；没存完之前不说「已保存」。
// 草稿接口答在一份内存里的「服务端那一份」上（`stored`），建草稿的请求停着，等测试放行。
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useTaskDraft } from './useTaskDraft'

const server = vi.hoisted(() => ({
  stored: null as Record<string, unknown> | null,
  release: null as null | (() => void),
}))

vi.mock('@/api/taskDrafts', () => ({
  TaskDraftsApi: {
    forTask: async (taskId: number) => {
      await new Promise<void>((resolve) => (server.release = resolve))
      server.stored = { name: 'original' }
      return { id: 61, taskId, spaceId: 7, version: 0, fields: { ...server.stored } }
    },
    start: async (spaceId: number, fields: Record<string, unknown>) => {
      await new Promise<void>((resolve) => (server.release = resolve))
      server.stored = { ...fields }
      return { id: 61, taskId: null, spaceId, version: 0, fields }
    },
    save: async (_id: number, fields: Record<string, unknown>) => {
      server.stored = { ...server.stored, ...fields }
      return { version: 1, updatedAt: '' }
    },
  },
}))

afterEach(() => {
  vi.useRealTimers()
  server.stored = null
  server.release = null
})

/** 改题页（`taskId` 9）或发布页（`null`）上刚打开、还没有草稿的那一份。 */
function page(taskId: number | null = 9) {
  vi.useFakeTimers()
  const draft = useTaskDraft({ spaceId: () => 7, taskId: () => taskId })
  draft.open(null, { name: 'original' })
  return draft
}

/** 等建草稿的请求发出去，再放行它。 */
async function releaseCreate() {
  await vi.waitFor(() => expect(server.release).not.toBeNull())
  server.release!()
}

describe('草稿：人的改动都存下', () => {
  it.each([9, null])('先问芝士、建的那一阵里改了、没等建好就离开：改的存下了（题目 %s）', async (taskId) => {
    const draft = page(taskId)
    const asking = draft.ensure()
    draft.changed({ name: '问芝士那一阵里改的' })
    const leaving = draft.flush()
    await releaseCreate()
    await Promise.all([asking, leaving])

    expect(server.stored?.name).toBe('问芝士那一阵里改的')
    expect(draft.saveState.value).toBe('saved')
  })

  it('第一次存的那一阵里又改了几格就离开：后改的几格都存下', async () => {
    const draft = page()
    draft.changed({ name: 'first' })
    const first = draft.flush()
    draft.changed({ name: 'second', descriptionMarkdown: '新的说明' })
    const leaving = draft.flush()
    await releaseCreate()
    await Promise.all([first, leaving])

    expect(server.stored).toMatchObject({ name: 'second', descriptionMarkdown: '新的说明' })
    expect(draft.saveState.value).toBe('saved')
  })

  it('还有改动没存下时不说已保存；存完了才说', async () => {
    const draft = page()
    draft.changed({ name: 'first' })
    const first = draft.flush()
    draft.changed({ name: 'second' })
    expect(draft.saveState.value).toBe('saving')
    await releaseCreate()
    await first
    expect(server.stored?.name).toBe('second')
    expect(draft.saveState.value).toBe('saved')

    draft.changed({ name: 'third' })
    expect(draft.saveState.value).toBe('saving')
    await vi.advanceTimersByTimeAsync(600)
    expect(server.stored?.name).toBe('third')
    expect(draft.saveState.value).toBe('saved')
  })
})
