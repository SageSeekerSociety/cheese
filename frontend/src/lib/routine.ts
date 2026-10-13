// 一条例行事务长什么样：**类型**和**措辞**，没有一处请求。
//
// 类型放在这里而不是 `api/routines.ts`，是因为要画它的组件（`components/routine/*`）不
// 许 import API 层 —— `scripts/import-boundary-ratchet-core.mjs` 按 import 判，纯类型
// 也算一次。这和 `lib/navTarget.ts`（`RouteLocationRaw` 只在那里 import 一次）是同一套
// 做法：画的那一半认这个形状，取数的那一半（`api/routines.ts`）把它原样递给后端。
//
// 措辞（状态怎么叫、一次执行怎么称呼）也只有一个出处：i18n 目录里的 `routines`
// 命名空间，这里的几个函数只是把 key 摆成调用方要的形状。改一个字只改目录。
//
// 「执行条件」那句话不是这里拼的：它是后端 `describe_trigger()` 出的整句，随每个回答
// 一起来（`trigger_text` + `i18n.trigger_text`），因为它要写清时区和星期几，而那是
// 规则自己的时区算出来的 —— 前端再拼一遍就是第二个答案。

import type { NavTarget } from './navTarget'
import type { NoticeMessage } from './noticeText'

import i18n, { t } from '@/i18n'

/** 一条规则。字段和后端 `GET /routines/{id}` 一一对应。 */
export interface Routine {
  id: string
  /**
   * 确认启用和删除归不归**你**管：规则的主人，或者管得了这个项目的管理员。
   *
   * 其余动作（修改、暂停/恢复、立即执行一次）是这个频道里的人都能做的，不看这个
   * 字段 —— 归谁管是后端逐条算好的，这边照着画按钮。
   */
  can_manage: boolean
  /** 这个房间归档了没有。规则自己的状态没变（取消归档后从下一个时刻继续）。 */
  room_archived: boolean
  project_id: string
  topic_id: string
  title: string
  instructions: string
  output_dir: string
  trigger: RoutineTrigger
  /** The server's text, with the key that renders it in the reader's language. */
  trigger_text: string
  i18n?: { trigger_text?: NoticeMessage }
  spec: Record<string, unknown>
  /** 执行范围：管哪几个频道。空数组就是「就管这个频道」。 */
  scope_topic_ids: string[]
  /** 上面那几个频道的名字，照它画卡片那一行和表单那排勾。 */
  scope_topics: RoutineTopicRef[]
  /** 最近一次执行；没有就是还没跑过。 */
  last_run: RoutineLastRun | null
  /** 一共跑过多少次：「看全部 {count} 次」和记录那一页的「共 {count} 次」。 */
  run_count: number
  timezone: string
  state: RoutineState
  agent_handle: string
  owner_handle: string
  proposed_by: string
  confirmed_by: string | null
  confirmed_at: string | null
  next_run_at: string | null
  revision: number
  created_at: string
  updated_at: string
}

/** 执行范围里指到的频道：id 是勾的哪一个，title 是它的名字。 */
export interface RoutineTopicRef {
  id: string
  title: string
}

/** 最近一次执行的两个字段，够卡片那行「上次执行 …」和「排队中」用。 */
export interface RoutineLastRun {
  id: string
  status: RoutineRunStatus
  at: string | null
}

export type RoutineTrigger = 'schedule' | 'library_file_added' | 'task_closed' | 'card_accepted'
export type RoutineState = 'draft' | 'active' | 'paused'
export type RoutineRunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'skipped'

/** 一次执行。`GET /routines/{id}` 的 `runs` 里那些。 */
export interface RoutineRun {
  id: string
  routine_id: string
  trigger_detail: string
  routine_revision: number
  scheduled_for: string | null
  status: RoutineRunStatus
  summary: string
  outputs: string[]
  error: string
  created_at: string
  started_at: string | null
  finished_at: string | null
}

/**
 * 新建和修改发出去的那几个字段，两边一样。
 *
 * `context_scope`（参考资料）不在里面：那是给 AI 自己看的背景，界面不给它一格。
 */
export type RoutineInput = Pick<
  Routine,
  'title' | 'instructions' | 'output_dir' | 'trigger' | 'spec' | 'timezone' | 'scope_topic_ids'
>

/**
 * 一条规则所在的房间 —— 它在哪儿执行、结果也放在哪儿。点「结果」那一行的去处。
 *
 * 路由的名字写在这里而不是组件里：`components/routine/*` 认的是 `NavTarget`（见
 * `lib/navTarget.ts`），这一份是那个形状的**一个**出处。
 */
export function routineRoomTarget(r: Pick<Routine, 'project_id' | 'topic_id'>): NavTarget {
  return { name: 'workspace-topic', params: { projectId: r.project_id, topicId: r.topic_id } }
}

/**
 * 一条规则的全部执行记录：「看全部 {count} 次」那一行点开的整页。
 *
 * 规则 id 就够了：那一页自己问后端这条规则跑过的每一次（`api/routines.ts` 的
 * `listRoutineRuns`），项目 id 只是路由地址的一部分。
 */
export function routineRunsTarget(r: Pick<Routine, 'project_id' | 'id'>): NavTarget {
  return { name: 'project-routine-runs', params: { projectId: r.project_id, routineId: r.id } }
}

export function routineStateLabel(s: RoutineState): string {
  return t(`routines.state.${s}`)
}

export function routineRunLabel(s: RoutineRunStatus): string {
  return t(`routines.run.${s}`)
}

/**
 * 「上次执行 …」那半句：跑过就说上次是什么时候，没跑过就照实说没跑过。
 *
 * 回的是整句（`routines.lastRun` / `routines.neverRun`），调用方直接摆上去：这两句各是
 * 一个完整的说法，拆成「标签 + 时刻」两半，中英文的语序就得在两处各拼一遍。
 *
 * 还没开始跑的那一次（排队中 / 执行中）由调用方另说 —— 那是「这一次在干什么」，不是
 * 「上一次什么时候」，两件事挤在同一行会读成同一件。
 */
export function routineLastRunText(r: Pick<Routine, 'last_run' | 'timezone'>): string {
  if (r.last_run?.at) {
    return t('routines.lastRun', { time: routineMoment(r.last_run.at, r.timezone) })
  }
  return t('routines.neverRun')
}

/**
 * 一个时刻，按规则自己的时区写。
 *
 * 「每周一 09:00（Asia/Shanghai）」旁边那个「下次 …」要对得上：用读者本机的时区写它，
 * 同一个 09:00 会差出半天，看着像规则算错了。
 */
export function formatRoutineTime(iso: string | null, timeZone?: string): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString(i18n.global.locale.value, { timeZone, hour12: false })
  } catch {
    return new Date(iso).toLocaleString(i18n.global.locale.value, { hour12: false })
  }
}

/** 一个时刻拆成规则时区里的那一年、那一月、那一日、那一点。 */
interface ZoneParts {
  /** `2026-10-13`：比「今天」用。 */
  date: string
  year: string
  month: string
  day: string
  hour: string
  minute: string
}

function zoneParts(at: Date, timeZone?: string): ZoneParts | null {
  if (Number.isNaN(at.getTime())) return null
  const take = (zone?: string) =>
    new Intl.DateTimeFormat('en-CA', {
      timeZone: zone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    }).formatToParts(at)
  let parts: Intl.DateTimeFormatPart[]
  try {
    parts = take(timeZone)
  } catch {
    // 时区名后端改了、或者这个环境认不出那个名字：退回读者本机的时区，仍然是懂的时刻。
    parts = take(undefined)
  }
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? ''
  const year = get('year')
  const month = get('month')
  const day = get('day')
  return {
    date: `${year}-${month}-${day}`,
    year,
    month,
    day,
    hour: get('hour'),
    minute: get('minute'),
  }
}

/**
 * 一个时刻缩到一行放得下：今天给「今天 18:02」，今年给「10-12」，跨年给
 * 「2025-12-25」。执行记录那一列用它。
 *
 * 时区跟着规则走（和它旁边「每天 18:00（北京时间）」同一个），不按读者本机：同一个
 * 18:02 会差出半天，看着像规则算错了。
 */
export function routineMoment(iso: string | null, timeZone?: string): string {
  const parts = zoneParts(new Date(iso ?? ''), timeZone)
  if (!parts) return '—'
  const today = zoneParts(new Date(), timeZone)
  if (today && parts.date === today.date) {
    return t('routines.moment.today', { time: `${parts.hour}:${parts.minute}` })
  }
  return parts.year === today?.year ? `${parts.month}-${parts.day}` : parts.date
}
