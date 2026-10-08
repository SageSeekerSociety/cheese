// 页面上读到过的任务行，按 id 记着最近的那一份：换到一件任务时，任务页先照着它画，
// 自己那一读回来再原地换（views/workspace/useTaskPage 的 `reset(seed)`），不先清空成
// 一个转圈。任务行来自好几处——侧栏的进行中、频道概览、对话栏里各块带着的——这里不
// 管是哪一处，读到就记。
//
// 只是一份「先画什么」的底：不当真，也不拿它回答任何问题。只活在这个标签页的内存里，
// 换人登录时清空（services/account.ts）。
import type { RoomTask } from '../cx_types'

const MAX_HELD = 500
const held = new Map<string, RoomTask>()

/** 记下这几行（同一件的新一份顶掉旧的）。 */
export function holdTasks(rows: readonly RoomTask[]): void {
  for (const row of rows) {
    held.delete(row.id)
    held.set(row.id, row)
  }
  while (held.size > MAX_HELD) {
    const oldest = held.keys().next()
    if (oldest.done) break
    held.delete(oldest.value)
  }
}

/** 读到过的这一件，没读到过是 undefined。 */
export function heldTask(id: string): RoomTask | undefined {
  return held.get(id)
}

/** 换人登录、测试之间：上一个人的任务不留。 */
export function clearHeldTasks(): void {
  held.clear()
}
