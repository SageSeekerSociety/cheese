// 频道下面「展开其余 N 个」那一行背后的东西：侧栏先只挂和我有关的几条（`my_tasks`），
// 展开后在下面接上这个频道其余进行中的任务。读这些任务是页面的事（ProjectSidebar），
// 侧栏只拿到读到了哪一步。
import type { RoomTask } from '@/cx_types'

/** 侧栏一条任务行要的那几样。 */
export type RailTask = Pick<RoomTask, 'id' | 'room_id' | 'title' | 'title_source' | 'presentation' | 'awaits_me'>

/** 一个展开了的频道，其余任务读到哪一步。 */
export type MoreTasks = { state: 'loading' } | { state: 'failed' } | { state: 'ready'; rows: RailTask[] }

/** 还开着、也没在合并后的收尾窗口里：和频道那一行的计数（`my_tasks.open`）、「全部任务」
 *  那一页的「进行中」是同一个判据。 */
export function isUnderway(task: Pick<RoomTask, 'status' | 'presentation'>): boolean {
  return task.status === 'open' && task.presentation.column !== 'done'
}

/**
 * 展开后接在「和我有关的那几条」下面的：这个频道其余进行中的任务，最近有动静的在前。
 * 已经挂着的不再列一遍。
 */
export function restOfChannel(
  tasks: readonly RoomTask[],
  channelId: string,
  shown: readonly { id: string }[]
): RoomTask[] {
  const listed = new Set(shown.map((task) => task.id))
  const movedAt = (task: RoomTask) => task.last_message?.created_at ?? task.created_at
  return tasks
    .filter((task) => task.room_id === channelId && isUnderway(task) && !listed.has(task.id))
    .sort((a, b) => movedAt(b).localeCompare(movedAt(a)))
}
