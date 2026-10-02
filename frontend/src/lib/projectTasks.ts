// 一个项目的活（`GET /projects/{id}/tasks`），屏幕上所有要它的地方共用一次读。
//
// 看板（RunningWorkView，15 秒一拉）和话题列表顶上那一行摘要（BoardSummary，30 秒
// 一拉）读的是同一份东西，而这一份是整个项目的活、每条带简报和结论 —— 两边各拉
// 各的，就是同一份数据隔几秒取两次。所以：
//
// - 一次读还没回来，谁再要都等这一次，不另发一次；
// - 不急的那一位可以说「多旧以内的我都收」（`maxAgeMs`）：看板在的时候它刚读过，
//   摘要直接拿它那一份，于是两边一起在时按快的那个节奏读，只有摘要自己在时才按
//   摘要的节奏读。
//
// 失败的那一次不留：下一位要的时候重新读，不把一次网络抖动当成答案发下去。
import type { ListPayload, RoomTask } from '@/cx_types'

import { listProjectTasks } from '@/api'

interface Read {
  /** 这一次读发出去的时刻 —— 新旧按它算，它说的是「这一份反映的是哪一刻」。 */
  at: number
  pending: boolean
  promise: Promise<ListPayload<RoomTask>>
}

const latest = new Map<string, Read>()

export function readProjectTasks(projectId: string, opts: { maxAgeMs?: number } = {}): Promise<ListPayload<RoomTask>> {
  const held = latest.get(projectId)
  const now = Date.now()
  if (held && (held.pending || (opts.maxAgeMs !== undefined && now - held.at < opts.maxAgeMs))) {
    return held.promise
  }
  const read: Read = { at: now, pending: true, promise: listProjectTasks(projectId) }
  latest.set(projectId, read)
  read.promise.then(
    () => {
      read.pending = false
    },
    () => {
      if (latest.get(projectId) === read) latest.delete(projectId)
    }
  )
  return read.promise
}
