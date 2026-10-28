// 频道主线上的任务卡：一件任务一张，挂在它出自的那条消息下面，或者是创建它的人发出的
// 「新建了任务」那一条。卡在原处变化，主线底部不再追加任何一行。
//
// 状态不在这里算（后端 `presentation` 算好了），状态那一格长什么样在 `lib/taskState.ts`；
// 这里只补卡自己的几样：采纳过几步、合进去的是哪个 PR、最后说的一句。
import type { RoomTask } from '../cx_types'
import type { TaskState } from './taskState'

import { replySnippet } from './blockDisplay'
import { taskState } from './taskState'

import { t } from '@/i18n'

/** 频道的任务列表（`GET /topics/{频道}/tasks`）比别处多带的几样：采纳过几次交付、
 *  最近那次是哪个 PR。 */
export type ChannelTask = RoomTask & {
  accepted_count?: number
  last_accepted_pr?: number | null
}

export interface TaskLine {
  id: string
  title: string
  /** 负责人的 handle；名字由画它的地方按房间的叫法换。 */
  owner: string | null
  /** 创建它的人：「新建了任务」那一条署他的名。 */
  creator: string | null
  /** 状态那一格（`lib/taskState.ts`）：图标、颜色、动不动、标签和下面那一行。采纳后
   *  关掉的任务，标签换成合进去的是哪个 PR。 */
  state: TaskState
  /** 等的是看的人自己：单独成块时描一道边。 */
  mine: boolean
  /** 还开着、已经采纳过几步的任务：「已采纳 2 次」。 */
  accepted: string | null
  /** 卡上一次变化的时间。 */
  at: string
  /** 这件任务里最后说的一句，折成一行：状态下面没有话要说时，主线上那张卡的第二行
   *  写它，说它此刻在做什么。 */
  latest?: string | null
}

function acceptedLabel(task: ChannelTask): string | null {
  if (task.presentation.phrase !== 'accepted' || !task.last_accepted_pr) return null
  const n = task.accepted_count ?? 1
  return n > 1
    ? t('work.room.taskCard.acceptedTimesPr', { n, pr: task.last_accepted_pr })
    : t('work.room.taskCard.acceptedPr', { pr: task.last_accepted_pr })
}

/** 一件任务在频道里的那张卡。`agentName` 是做它的 AI 队友叫什么（卡住了没留话时那一句
 *  点它的名）。 */
export function taskLine(
  task: ChannelTask,
  viewer: string,
  nameOf: (handle: string) => string,
  agentName: string
): TaskLine {
  const mine = task.needs_my_action === true
  const open = task.status !== 'closed'
  const n = task.accepted_count ?? 0
  const state = taskState(task, { viewer, nameOf, agentName })
  const label = acceptedLabel(task)
  return {
    id: task.id,
    title: task.title,
    owner: task.owner_handle ?? null,
    creator: task.created_by ?? task.owner_handle ?? null,
    state: label ? { ...state, label } : state,
    mine,
    accepted: open && n > 0 ? t('work.room.taskCard.acceptedTimes', { n }) : null,
    at: task.last_message?.created_at ?? task.updated_at,
    latest: task.last_message ? replySnippet(task.last_message, NO_NAMES, 120) || null : null,
  }
}

/** 卡上那一句里点到的人照 handle 写：卡只要一行能读，不为这一行再去读名册。 */
const NO_NAMES = { mentionNames: {}, topicTitles: {} }

/** 每条消息下面挂着的任务，按创建的先后。主线上是它挂在哪条主线消息下面；`inside` 一段
 *  支线（或支线转成的任务）里，是它出自哪句话，这段对话自己转成的那件不算。 */
export function tasksByOrigin(tasks: readonly RoomTask[], inside?: string | null): Map<string, RoomTask[]> {
  const under = new Map<string, RoomTask[]>()
  for (const task of [...tasks].sort((a, b) => a.created_at.localeCompare(b.created_at))) {
    const origin = !inside ? task.upgraded_from_block_id : task.id === inside ? null : task.source_block_id
    if (!origin) continue
    const list = under.get(origin)
    if (list) list.push(task)
    else under.set(origin, [task])
  }
  return under
}

/** 频道概览「最近做完」那一栏有几件：概览只读这么多已经做完的。 */
export const RECENT_DONE = 3
