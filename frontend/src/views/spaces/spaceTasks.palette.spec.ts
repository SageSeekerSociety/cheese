/** Ctrl+K 搜空间里的题目：每一条写着在哪个空间，选中进题目页；不打字时不问。 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import source from './spaceTasks.palette'

const search = vi.hoisted(() => vi.fn())
vi.mock('@/api/taskBoard', () => ({ TaskBoardApi: { search } }))

const ctx = { projectId: null, router: {} as never }

beforeEach(() => search.mockReset())

describe('空间里的题目', () => {
  it('搜到的题目带着空间名，去题目页', async () => {
    search.mockResolvedValue({
      tasks: [{ id: 88, name: '缓存模拟器实验', spaceId: 7, spaceName: '计算机系统', deadline: null }],
    })
    const items = await source.search!('缓存', ctx)

    expect(search).toHaveBeenCalledWith('缓存')
    expect(items).toEqual([
      expect.objectContaining({
        id: 'space-task:88',
        title: '缓存模拟器实验',
        subtitle: '计算机系统',
        to: { name: 'TasksDetail', params: { spaceId: '7', taskId: '88' } },
      }),
    ])
  })

  it('不打字不问', async () => {
    expect(await source.search!('  ', ctx)).toEqual([])
    expect(search).not.toHaveBeenCalled()
  })
})
