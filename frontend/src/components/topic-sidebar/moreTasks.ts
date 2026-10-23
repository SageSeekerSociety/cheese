// 频道下面「展开其余 N 个」那一行背后的东西：侧栏先只挂和我有关的几条（`my_tasks`），
// 展开后在下面接上这个频道其余进行中的任务。读这些任务是页面的事（ProjectSidebar），
// 侧栏只拿到读到了哪一步。
import type { RoomTask } from '@/cx_types'

/** 侧栏一条任务行要的那几样。
 *
 *  `moving` / `unseen_delivery` / `incomplete_since` 是行尾那盏灯的判据
 *  （`lib/roomLight.ts`）。挂在频道下面的「和我有关的那几条」跟着话题一起来，三样
 *  都带得全；展开后接上的「其余任务」走的是另一个接口（`/projects/{id}/tasks`），
 *  那份响应体大，而 `unseen_delivery` 按人算——加进去会把它的缓存冲掉，所以那边
 *  刻意只给 `moving` 和 `incomplete_since`（它们只在真的有事时变：一条活在跑、
 *  走在交付链路上，或者它坏了 / 恢复了）。那些行因此画得出呼吸绿、黄灯和「停下来
 *  报错」的红灯，画不出交付完没看过的长绿。 */
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
  | 'incomplete_since'
  // 这条活归谁：我负责的（含没主的）那几条，行尾补一盏蓝灯（`useTopicRail.taskLight`）。
  | 'owner_handle'
  // 谁在这件活里（负责人之外还有协作者）：行菜单里的「重命名」只画给这些人。
  | 'contributor_handles'
  // 复制链接要从任务自己的地址拼出来；行菜单不给非进行中的活用状态，但列表接口两边
  // 都带着它，留着不多拉一次。
  | 'project_id'
  | 'status'
  // 不是和我有关的、被借来把这一块凑够三条的那几条（后端 `rail_tasks`）：行上画淡
  // 一档，悬浮说明写清它为什么在这儿。缺省 = 和我有关的。
  | 'filling_in'
  // 行首的状态记号和标题下面那一行（`lib/taskState.ts`）：队友报的那句、什么时候报的，
  // 卡在谁那里审阅、第几版，做它的是哪位队友。两份列表都带（`task_row_json`）。
  | 'progress_note'
  | 'progress_set_at'
  | 'reviewer_handle'
  | 'card'
  | 'agent_handle'
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
