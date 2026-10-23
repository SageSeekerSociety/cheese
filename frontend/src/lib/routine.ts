// 一条例行事务长什么样：**类型**和**措辞**，没有一处请求。
//
// 类型放在这里而不是 `api/routines.ts`，是因为要画它的组件（`components/routine/*`）不
// 许 import API 层 —— `scripts/import-boundary-ratchet-core.mjs` 按 import 判，纯类型
// 也算一次。这和 `lib/navTarget.ts`（`RouteLocationRaw` 只在那里 import 一次）是同一套
// 做法：画的那一半认这个形状，取数的那一半（`api/routines.ts`）把它原样递给后端。
//
// 措辞（状态怎么叫、频率有哪几个、星期几写「周一」）也只有一个出处：i18n 目录里的
// `routines` 命名空间，这里的几个函数只是把 key 摆成调用方要的形状。改一个字只改目录。

import type { NavTarget } from './navTarget'
import type { NoticeMessage } from './noticeText'

import { calendarDaysAgo, clockTime, monthDayTime } from './dateText'
import { renderNoticeMessage } from './noticeText'

import i18n, { t } from '@/i18n'

/** 一条规则。字段和后端 `GET /routines/{id}` 一一对应。 */
export interface Routine {
  id: string
  /** 这一条此刻归不归**你**删：读得到就改得动（暂停/恢复/立即执行/修改），只有删除
   *  收窄到规则主人和项目管理员。 */
  can_delete: boolean
  /** 这个房间归档了没有。规则自己的状态没变（取消归档后从下一个时刻继续）。 */
  room_archived: boolean
  project_id: string
  topic_id: string
  /** 名称。人可以不填：后端取目标的第一句。 */
  title: string
  /** 目标：要它干什么，用这个人自己的话。唯一必填的一格。 */
  instructions: string
  /** 补充说明。可以空着 —— 空着就没有这一项，不替谁编一段。 */
  note: string
  /** '' 是「还没定什么时候跑」：存下来先不跑。 */
  trigger: RoutineTrigger | ''
  /** The server's text, with the key that renders it in the reader's language. */
  trigger_text: string
  i18n?: { trigger_text?: NoticeMessage }
  spec: Record<string, unknown>
  timezone: string
  state: RoutineState
  /** 最近一次执行跑成什么样。还没跑过就是 `null`。
   *
   *  行上那颗图形要用它：规则自己只有「启用中 / 已暂停」两档，正在排队的那条画沙漏、
   *  上一次没跑成的那条画红叉，这两档只在执行记录里。 */
  last_run_status: RoutineRunStatus | null
  /** 这条规则**本月**扣掉的额度（点，1 点 = 1 分钱）。
   *
   *  它跑在共享团队的项目里时，花的是团队额度、不记到任何人头上；「本月」是平台口径
   *  的一个月（北京时间每月 1 日重置），和团队额度页的「本月已用」同一个起点。
   *
   *  只含模型调用（含网页搜索），不含云端算力 —— 算力按频道按分钟记，挂不到某一轮上。 */
  credits_this_month: number
  agent_handle: string
  owner_handle: string
  /** 建这条规则的人：人或者 AI 队友。 */
  created_by: string
  next_run_at: string | null
  revision: number
  created_at: string
  updated_at: string
}

export type RoutineTrigger = 'schedule' | 'library_file_added' | 'task_closed' | 'card_accepted'
export type RoutineState = 'active' | 'paused'
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
  /** 这一次执行**本月**扣掉的额度（点）。`null` 是「还没有数」：还没跑完，或者刚
   *  跑完花费还没落账（几秒到几十秒）。写 0 是在说「这一轮没花钱」，两件事要分开。 */
  credits?: number | null
}

/**
 * 「看全部」那一整页此刻的样子：看的是哪一条、第几页、筛不筛没能执行的、这一页有哪些。
 *
 * 取数的那个 composable（`composables/useRoutineList.ts`）拿着它，画它的那个组件
 * （`components/routine/RoutineRecords.vue`）吃它 —— 两边认同一个形状，所以它在这里。
 * 组件不许 import composable（那是取数那一半）。
 */
export interface RoutineRecordsView {
  /** 看的是哪一条规则的记录。 */
  title: string
  page: number
  failedOnly: boolean
  runs: RoutineRun[]
  /** 筛过之后一共几次。 */
  total: number
  /** 这条规则**本月**扣掉的额度（点）—— 整条规则的，不是这一页的。翻到第几页都是
   *  同一个数：读者问的是「这条规则这个月花了多少」。 */
  creditsThisMonth: number
  pages: number
  loading: boolean
  /** 结果文件那一串整串点开去哪儿。项目总览页给（指回这一条所在的房间 —— 那一页上
   *  没有能开文件的工作面板）；房间面板里那一格不给。 */
  roomTo?: NavTarget
}

/** 新建和修改发出去的那几个字段，两边一样。 */
export type RoutineInput = Pick<Routine, 'title' | 'instructions' | 'note' | 'trigger' | 'spec' | 'timezone'>

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
 * 一条规则在行上说的那一件事 —— 那颗图形。
 *
 * 四档里有两档不在规则自己身上：规则只知道「启用中 / 已暂停」，而正在排队的那条要画
 * 沙漏、上一次没跑成的那条要画红叉，这两档得看最近一次执行（`last_run_status`）。
 * 停着的规则先说话：它不会跑了，最近那次跑成什么样都是过去的事。
 */
export type RoutineStateKind = 'active' | 'paused' | 'queued' | 'missed'

export function routineStateKind(r: Pick<Routine, 'state' | 'last_run_status'>): RoutineStateKind {
  if (r.state === 'paused') return 'paused'
  if (r.last_run_status === 'queued' || r.last_run_status === 'running') return 'queued'
  if (r.last_run_status === 'failed' || r.last_run_status === 'skipped') return 'missed'
  return 'active'
}

export function routineStateText(kind: RoutineStateKind): string {
  return t(`routines.state.${kind}`)
}

/**
 * 四档各画什么。
 *
 * 行上没有一处写状态的字：图形就是「它怎么了」本身，颜色跟着图形走（见 RoutineRow）。
 * 两条竖线是它在跑 —— 和播放器上那两颗一样；三角是停着的，按一下才跑；沙漏是排上队
 * 了；红叉是上一次没能执行。
 */
const STATE_ICONS: Record<RoutineStateKind, string> = {
  active: 'mdi-pause',
  paused: 'mdi-play',
  queued: 'mdi-timer-sand',
  missed: 'mdi-close',
}

export function routineStateIcon(kind: RoutineStateKind): string {
  return STATE_ICONS[kind]
}

/** 额度（点）写成给人读的一句。`null` / 没给是「还没有数」，和 0 分开。 */
export function creditsLabel(credits: number | null | undefined): string {
  if (credits === null || credits === undefined) return t('routines.spend.pending')
  return t('routines.spend.points', { n: fmtCredits(credits) })
}

/** 点数：最多一位小数，千分位分开。和额度页同一套写法（`lib/adminCredits`）。 */
export function fmtCredits(n: number): string {
  return new Intl.NumberFormat(i18n.global.locale.value, { maximumFractionDigits: 1 }).format(n)
}

/** 一颗结果文件在行上的名字：记录里写的是它在频道文件区里的路径。 */
export function routineFileName(path: string): string {
  return path.split('/').pop() || path
}

/**
 * 这一条什么时候跑，写成给人读的一句话。
 *
 * 后端那句话把时区写在括号里，写的是 IANA 的代号（「每周一 09:00（Asia/Shanghai）」）
 * —— 那是给机器认的。这里把括号里那半句换成人读的说法，「Asia/Shanghai」就是「北京
 * 时间」。事件触发的规则没有时区，那句话原样出来。
 *
 * 用的是服务端句子的键和参数（不是它写下的那句中文），所以切语言会跟着换。
 */
export function routineWhenLine(r: Pick<Routine, 'trigger_text' | 'i18n' | 'timezone'>): string {
  const message = r.i18n?.trigger_text
  if (!message) return r.trigger_text
  const params: Record<string, unknown> = { ...(message.params ?? {}) }
  if (typeof params.timezone === 'string') params.timezone = timeZoneText(params.timezone)
  return renderNoticeMessage({ key: message.key, params }, r.trigger_text)
}

/**
 * 时区在人眼前的样子。
 *
 * 常用的几个在 i18n 目录里说清了（「Asia/Shanghai」→「北京时间」）；不认识的时区问
 * `Intl` 要它的长名，再要不到就原样写 —— 不替一个没见过的时区编一个说法。
 */
const ZONE_WORDS: Record<string, string> = { 'Asia/Shanghai': 'beijing' }

export function timeZoneText(timeZone: string): string {
  if (!timeZone) return ''
  const named = ZONE_WORDS[timeZone]
  if (named) return t(`routines.zone.${named}`)
  try {
    const parts = new Intl.DateTimeFormat(i18n.global.locale.value, {
      timeZone,
      timeZoneName: 'long',
    }).formatToParts(new Date())
    return parts.find((part) => part.type === 'timeZoneName')?.value || timeZone
  } catch {
    return timeZone
  }
}

/**
 * 一次执行是什么时候的：今天和昨天说「今天 18:02」，再早说日子（「10月12日 18:02」）。
 *
 * 「今天 / 昨天」这两个词只有调用点知道该用哪一套，所以它们来自这里的键，不来自
 * `lib/dateText.ts`。日子本身按本地日历天算，不按流逝时长。
 */
export function routineRunTime(iso: string | null): string {
  if (!iso) return ''
  const at = Date.parse(iso)
  if (!Number.isFinite(at)) return ''
  const days = calendarDaysAgo(at)
  if (days <= 0) return t('routines.today', { time: clockTime(at) })
  if (days === 1) return t('routines.yesterday', { time: clockTime(at) })
  return monthDayTime(at)
}

export function routineRunLabel(s: RoutineRunStatus): string {
  return t(`routines.run.${s}`)
}

export function routineTriggers(): { value: RoutineTrigger; title: string }[] {
  const values: RoutineTrigger[] = ['schedule', 'library_file_added', 'task_closed', 'card_accepted']
  return values.map((v) => ({ value: v, title: t(`routines.trigger.${v}`) }))
}

export function routineFreqs(): { value: string; title: string }[] {
  return ['daily', 'weekly', 'monthly', 'hourly'].map((v) => ({ value: v, title: t(`routines.freq.${v}`) }))
}

export function routineWeekdays(): string[] {
  return ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'].map((d) => t(`routines.weekday.${d}`))
}
