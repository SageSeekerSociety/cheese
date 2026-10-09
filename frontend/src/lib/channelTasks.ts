// 频道主线上的任务卡：一件任务一张，挂在它出自的那条消息下面，或者是创建它的人发出的
// 「新建了任务」那一条。卡在原处变化，主线底部不再追加任何一行。
//
// 状态不在这里算（后端 `presentation` 算好了），这里只决定卡面上那一句怎么写：等的是
// 一个人时写出他的名字（「待 林晓 审阅」），是看的人自己就写「待你审阅」。
import type { RoomTask } from '../cx_types'

import { myPhraseLabel, phraseLabel } from './board'

import { t } from '@/i18n'

/** 频道的任务列表（`GET /topics/{频道}/tasks`）比别处多带的几样：在等谁（「待 某某
 *  审阅」里的某某），采纳过几次交付、最近那次是哪个 PR。 */
export type ChannelTask = RoomTask & {
  waiting_on?: string | null
  accepted_count?: number
  last_accepted_pr?: number | null
}

/** 卡上状态那一格的样子：点的颜色和形状，或者一个勾、一道横线。 */
export type TaskTone = 'discussing' | 'running' | 'idle' | 'waiting' | 'mine' | 'stuck' | 'done' | 'closed'

export interface TaskLine {
  id: string
  title: string
  /** 负责人的 handle；名字由画它的地方按房间的叫法换。 */
  owner: string | null
  /** 创建它的人：「新建了任务」那一条署他的名。 */
  creator: string | null
  status: string
  tone: TaskTone
  /** 还开着、已经采纳过几步的任务：「已采纳 2 次」。 */
  accepted: string | null
  /** 卡上一次变化的时间。 */
  at: string
}

const WAITING_ON: Record<string, string> = {
  awaiting_review: 'work.room.taskCard.waitingReview',
  awaiting_answer: 'work.room.taskCard.waitingAnswer',
  discussing: 'work.room.taskCard.waitingStart',
}

function tone(task: ChannelTask, mine: boolean): TaskTone {
  const { column, phrase } = task.presentation
  if (column === 'done' || column === 'archived') return phrase === 'closed' ? 'closed' : 'done'
  if (mine) return 'mine'
  if (phrase === 'checks_failed') return 'stuck'
  if (column === 'needs_you' || task.waiting_on) return 'waiting'
  if (column === 'not_started') return 'discussing'
  if (phrase === 'running' || phrase === 'fixing_checks' || phrase === 'resolving_conflict') return 'running'
  return 'idle'
}

function status(task: ChannelTask, mine: boolean, nameOf: (handle: string) => string): string {
  const { phrase } = task.presentation
  if (phrase === 'accepted' && task.last_accepted_pr) {
    const n = task.accepted_count ?? 1
    return n > 1
      ? t('work.room.taskCard.acceptedTimesPr', { n, pr: task.last_accepted_pr })
      : t('work.room.taskCard.acceptedPr', { pr: task.last_accepted_pr })
  }
  if (mine) return myPhraseLabel(phrase)
  const waiting = task.waiting_on ? WAITING_ON[phrase] : undefined
  if (waiting && task.waiting_on) return t(waiting, { name: nameOf(task.waiting_on) })
  return phraseLabel(phrase)
}

/** 一件任务在频道里的那张卡。 */
export function taskLine(task: ChannelTask, viewer: string, nameOf: (handle: string) => string): TaskLine {
  const mine = !!task.waiting_on && task.waiting_on === viewer
  const open = task.status !== 'closed'
  const n = task.accepted_count ?? 0
  return {
    id: task.id,
    title: task.title,
    owner: task.owner_handle ?? null,
    creator: task.created_by ?? task.owner_handle ?? null,
    status: status(task, mine, nameOf),
    tone: tone(task, mine),
    accepted: open && n > 0 ? t('work.room.taskCard.acceptedTimes', { n }) : null,
    at: task.last_message?.created_at ?? task.updated_at,
  }
}

/** 每条消息下面挂着的任务，按创建的先后。 */
export function tasksByOrigin(tasks: readonly RoomTask[]): Map<string, RoomTask[]> {
  const under = new Map<string, RoomTask[]>()
  for (const task of [...tasks].sort((a, b) => a.created_at.localeCompare(b.created_at))) {
    const origin = task.upgraded_from_block_id
    if (!origin) continue
    const list = under.get(origin)
    if (list) list.push(task)
    else under.set(origin, [task])
  }
  return under
}

/** 频道概览「最近做完」那一栏有几件：概览只读这么多已经做完的。 */
export const RECENT_DONE = 3
