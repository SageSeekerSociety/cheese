/**
 * 自己的清单：在输入框里发一张，点一步前面的记号改它的状态。
 *
 * 和队友的 `todo_write` 是同一个写入（`PUT /topics/{id}/progress`），每次交整份。
 * 新清单和改过的清单都由房间的推送画出来（assistant_block / block_updated）；改一步
 * 时先在本地换上，存不进去再换回原来那一条。
 *
 * 往时间线上放哪一条、错误怎么说，是房间壳的事，从外面交进来。
 */

import type { Block, TodoItem } from '../cx_types'

import { writeChecklist } from '../api/checklist'

export function useOwnChecklist(opts: {
  topicId: () => string | undefined
  /** 把这一条换进时间线（在的话）。 */
  show: (block: Block) => void
  /** 存不进去：告诉看的人。 */
  fail: (error: unknown) => void
}) {
  async function postChecklist(steps: string[]): Promise<boolean> {
    const id = opts.topicId()
    if (!id || !steps.length) return false
    try {
      const todos = steps.map((content) => ({ content, status: 'pending' as const }))
      await writeChecklist(id, todos, { new: true })
      return true
    } catch (e) {
      opts.fail(e)
      return false
    }
  }

  async function changeChecklist(m: Block, items: TodoItem[]) {
    const id = opts.topicId()
    const current = m.meta?.checklist
    if (!id || !current || typeof current !== 'object') return
    opts.show({ ...m, meta: { ...m.meta, checklist: { ...current, items } } })
    try {
      const todos = items.map((item) => ({ content: item.subject, status: item.status }))
      await writeChecklist(id, todos, { message: m.id })
    } catch (e) {
      opts.show(m)
      opts.fail(e)
    }
  }

  return { postChecklist, changeChecklist }
}
