/**
 * 对话栏里画得出来的那几件任务：从哪条消息拆出去的（挂在那条下面）、在房间里新建的
 * （「创建了任务」那一行）。它们跟着那几块一起来——`GET /topics/{id}/blocks` 在每块上
 * 带着 `tasks`——所以对话栏不再读整个房间的任务清单。
 *
 * 之后变了的从同一处补：
 * - 频道说它的任务变了（`state` 的 topics / tasks）、断线又连上：按窗口里那几块重读
 *   一次（`?blocks=`），换掉各块带着的那份；
 * - 推来一块说「创建了任务」的：它不带任务，按它那一块读一次；
 * - 消息里 `<#id>` 指着的、窗口里没有的任务：按 id 读一次，chip 才写得出它叫什么、
 *   点了才知道该开任务还是开话题。
 */
import type { Ref } from 'vue'
import type { Block, RoomTask } from '../cx_types'

import { computed, reactive, watch } from 'vue'

import { listRoomTasks } from '../api'
import { holdTasks } from '../lib/heldTasks'

/** 说「这件任务在这里开始」的那种行（`task_created`，和更早的 `split`）指的是哪件。 */
export function namedTask(block: Block): string | null {
  const meta = block.meta as Record<string, unknown> | null | undefined
  const action = meta?.action
  const id = meta?.task_id
  return (action === 'task_created' || action === 'split') && typeof id === 'string' ? id : null
}

/** 一次重读最多带几块：后端一次只认这么多（`blocks` 的上限）。取最新那几块。 */
const READ_AT_ONCE = 200

const CHIP = /<#([0-9a-f-]{36})>/g

export function useTimelineTasks(opts: {
  /** 这条对话所在的房间：任务挂在房间下。 */
  roomId: () => string | undefined
  /** 窗口里此刻有的块。 */
  blocks: Ref<Block[]>
  /** 这个项目里叫得出名字的话题：`<#id>` 是它们的就不必当任务去问。 */
  topicIds: () => ReadonlySet<string>
  /** 换掉一块（带上新的 `tasks`）。 */
  replace: (block: Block) => void
}) {
  // 按 id 问回来的（chip 指着的、窗口里还没有的）。
  const looked = reactive(new Map<string, RoomTask>())
  const asked = new Set<string>()

  /** 此刻认得的每一件：窗口里各块带着的，加上按 id 问回来的。 */
  const known = computed(() => {
    const all = new Map(looked)
    for (const block of opts.blocks.value) for (const task of block.tasks ?? []) all.set(task.id, task)
    return all
  })
  // 点开其中一件时，任务页先照着这一份画（lib/heldTasks）。
  watch(known, (all) => holdTasks([...all.values()]))

  function carriedBy(block: Block, rows: RoomTask[]): RoomTask[] {
    const named = namedTask(block)
    return rows.filter((task) => task.upgraded_from_block_id === block.id || task.id === named)
  }

  async function readFor(blocks: Block[]) {
    const room = opts.roomId()
    if (!room || !blocks.length) return
    try {
      const rows = (await listRoomTasks(room, { limit: 0, blocks: blocks.map((b) => b.id) })).data
      if (opts.roomId() !== room) return
      for (const block of blocks) {
        const carried = carriedBy(block, rows)
        if (carried.length || block.tasks?.length) opts.replace({ ...block, tasks: carried })
      }
    } catch {
      // 卡片上的状态是装饰：读不到就照旧画上一份，下一次频道说变了再读。
    }
  }

  let refreshing: Promise<void> | null = null
  /** 频道说它的任务变了：窗口里（最新那一截）每块带着的任务重读一次。 */
  function refresh(): Promise<void> {
    refreshing ??= readFor(opts.blocks.value.slice(-READ_AT_ONCE)).finally(() => (refreshing = null))
    return refreshing
  }

  /** 推来的一块说某件任务在这里开始了：它不带那件任务，补上。 */
  function landed(block: Block) {
    if (namedTask(block) && !block.tasks?.length) void readFor([block])
  }

  // 消息里 `<#id>` 指着的任务，窗口里没有的按 id 问一次（问过的不再问）。
  watch(
    () => {
      const ids = new Set<string>()
      for (const block of opts.blocks.value) for (const match of (block.content ?? '').matchAll(CHIP)) ids.add(match[1])
      return [...ids].filter((id) => !opts.topicIds().has(id) && !known.value.has(id) && !asked.has(id))
    },
    async (ids) => {
      const room = opts.roomId()
      if (!room || !ids.length) return
      for (const id of ids) asked.add(id)
      try {
        const rows = (await listRoomTasks(room, { limit: 0, ids })).data
        if (opts.roomId() === room) for (const task of rows) looked.set(task.id, task)
      } catch {
        for (const id of ids) asked.delete(id)
      }
    }
  )

  watch(opts.roomId, () => {
    looked.clear()
    asked.clear()
  })

  return { known, refresh, landed }
}
