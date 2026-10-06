// 侧栏上一个频道下面挂哪几条任务。频道里的任务会很多（房间变成任务之后，「综合」
// 下面挂着整个项目的事），全列出来侧栏就没法看了，所以只列和我有关的那几条：先是我
// 负责的，再是我协作的，各自按最近有动静的排，合起来最多 RAIL_TASKS 条；其余的
// 点「全部任务」看。
import type { RoomTask } from '@/cx_types'

export const RAIL_TASKS = 5

export interface ChannelRailTasks {
  shown: RoomTask[]
  /** 这个频道里还在进行的任务一共几条——「全部任务」那一行后面的数。 */
  total: number
}

function lastMoved(task: RoomTask): number {
  return Date.parse(task.last_activity_at ?? task.updated_at ?? task.created_at) || 0
}

/** 还在进行的任务，按频道分好，每个频道只留和 `me` 有关的那几条。 */
export function railTasksByChannel(tasks: RoomTask[], me: string): Record<string, ChannelRailTasks> {
  const byChannel: Record<string, { mine: RoomTask[]; helping: RoomTask[]; total: number }> = {}
  for (const task of tasks) {
    if (task.status !== 'open' || task.presentation.column === 'done') continue
    const channel = (byChannel[task.room_id] ??= { mine: [], helping: [], total: 0 })
    channel.total += 1
    if (task.owner_handle === me) channel.mine.push(task)
    else if ((task.contributor_handles ?? []).includes(me)) channel.helping.push(task)
  }
  const out: Record<string, ChannelRailTasks> = {}
  for (const [channel, { mine, helping, total }] of Object.entries(byChannel)) {
    const recentFirst = (a: RoomTask, b: RoomTask) => lastMoved(b) - lastMoved(a)
    out[channel] = {
      shown: [...mine.sort(recentFirst), ...helping.sort(recentFirst)].slice(0, RAIL_TASKS),
      total,
    }
  }
  return out
}
