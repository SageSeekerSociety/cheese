// 频道下面「展开其余 N 个」那一行背后的东西：侧栏先只挂和我有关的几条（`my_tasks`），
// 展开后在下面接上这个频道其余进行中的任务。读这些任务是页面的事（ProjectSidebar），
// 侧栏只拿到读到了哪一步。
import type { RoomTask } from '@/cx_types'

/** 侧栏一条任务行要的那几样。
 *
 *  `moving` / `unseen_delivery` / `last_output_at` / `incomplete_since` 是行尾那盏
 *  灯的判据（`lib/roomLight.ts`）。挂在频道下面的「和我有关的那几条」跟着话题一起
 *  来，这几样都带得全；展开后接上的「其余任务」走的是另一个接口
 *  （`/projects/{id}/tasks`），那份响应体大，而 `last_output_at` 每分钟都在变、
 *  `unseen_delivery` 按人算——加进去会把它的缓存冲掉，所以那边刻意只给 `moving`
 *  和 `incomplete_since`（后者只在真的有事时变：一条活坏了，或者它恢复了）。那些
 *  行因此画得出黄灯、呼吸绿，画不出「在跑却没动静」那种红灯和长绿——但画得出
 *  「停下来报错」的红灯，它的时刻那份响应带得回来。 */
export type RailTask = Pick<
  RoomTask,
  | 'id'
  | 'room_id'
  | 'title'
  | 'title_source'
  | 'presentation'
  | 'awaits_me'
  | 'moving'
  | 'unseen_delivery'
  | 'last_output_at'
  | 'incomplete_since'
  // 这条活归谁：我负责的（含没主的）那几条，行尾补一盏蓝灯（`useTopicRail.taskLight`）。
  | 'owner_handle'
  // 不是和我有关的、被借来把这一块凑够三条的那几条（后端 `rail_tasks`）：行上画淡
  // 一档，悬浮说明写清它为什么在这儿。缺省 = 和我有关的。
  | 'filling_in'
>

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
