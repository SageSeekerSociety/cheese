// 一个项目的活（`GET /projects/{id}/tasks`），屏幕上所有要它的地方共用一次读。
//
// 侧栏（30 秒一拉）、项目总览、全部任务读的是同一份东西，而这一份是整个项目的任务
// —— 各拉各的，就是同一份数据隔几秒取几次。所以：
//
// - 一次读还没回来，谁再要都等这一次，不另发一次；
// - 不急的那一位可以说「多旧以内的我都收」（`maxAgeMs`）：别处刚读过，它就直接拿那
//   一份；
// - 知道刚变过的那一位（`fresh`）不等还没回来的那一次：那一次在变之前就发出去了，带
//   回来的是变之前的样子。它另发一次，之后再要的都等这新的一次。同一次改动常常连着
//   说几遍（任务和它的频道各收到一帧）：刚发出去不到 `FRESH_JOIN_MS` 的那一次就是为
//   它发的，跟着它走。
//
// 失败的那一次不留：下一位要的时候重新读，不把一次网络抖动当成答案发下去。
//
// 只要还在进行的（`open`）是另一份，各自共用：侧栏只挂这些，每 30 秒读一次。
import type { ListPayload, RoomTask } from '@/cx_types'

import { listProjectTasks } from '@/api'
import { rememberNumbered } from '@/lib/addresses'

interface Read {
  /** 这一次读发出去的时刻 —— 新旧按它算，它说的是「这一份反映的是哪一刻」。 */
  at: number
  pending: boolean
  promise: Promise<ListPayload<RoomTask>>
}

const latest = new Map<string, Read>()
const FRESH_JOIN_MS = 100

export function readProjectTasks(
  projectId: string,
  opts: { maxAgeMs?: number; open?: boolean; fresh?: boolean } = {}
): Promise<ListPayload<RoomTask>> {
  const key = opts.open ? `${projectId}:open` : projectId
  const held = latest.get(key)
  const now = Date.now()
  const joinable = opts.fresh
    ? held?.pending && now - held.at < FRESH_JOIN_MS
    : held && (held.pending || (opts.maxAgeMs !== undefined && now - held.at < opts.maxAgeMs))
  if (held && joinable) {
    return held.promise
  }
  const promise = listProjectTasks(projectId, { open: opts.open }).then((page) => {
    rememberNumbered('tasks', page.data)
    return page
  })
  const read: Read = { at: now, pending: true, promise }
  latest.set(key, read)
  read.promise.then(
    () => {
      read.pending = false
    },
    () => {
      if (latest.get(key) === read) latest.delete(key)
    }
  )
  return read.promise
}
