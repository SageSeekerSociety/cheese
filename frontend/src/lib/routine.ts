// 一条定时与触发规则长什么样：**类型**和**措辞**，没有一处请求。
//
// 类型放在这里而不是 `api/routines.ts`，是因为要画它的组件（`components/routine/*`）不
// 许 import API 层 —— `scripts/import-boundary-ratchet-core.mjs` 按 import 判，纯类型
// 也算一次。这和 `lib/navTarget.ts`（`RouteLocationRaw` 只在那里 import 一次）是同一套
// 做法：画的那一半认这个形状，取数的那一半（`api/routines.ts`）把它原样递给后端。
//
// 措辞（状态怎么叫、频率有哪几个、星期几写「周一」）也在这里：一条规则在项目总览页
// 和房间右侧那一格里是同一个样子，改一个字只改这一处。

import type { NavTarget } from './navTarget'

/** 一条规则。字段和后端 `GET /routines/{id}` 一一对应。 */
export interface Routine {
  id: string
  /** 这一条此刻归不归**你**管：确认、暂停/恢复、立即执行、删除、修改都要它。 */
  can_manage: boolean
  /** 这个房间归档了没有。规则自己的状态没变（取消归档后从下一个时刻继续）。 */
  room_archived: boolean
  project_id: string
  topic_id: string
  title: string
  instructions: string
  context_scope: string
  output_dir: string
  trigger: RoutineTrigger
  /** 后端写好的那一句话：「每周周一 09:00（Asia/Shanghai）」。 */
  trigger_text: string
  spec: Record<string, unknown>
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

/** 新建和修改发出去的那几个字段，两边一样。 */
export type RoutineInput = Pick<
  Routine,
  'title' | 'instructions' | 'context_scope' | 'output_dir' | 'trigger' | 'spec' | 'timezone'
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

export const ROUTINE_STATE_LABEL: Record<RoutineState, string> = {
  draft: '待确认',
  active: '执行中',
  paused: '已暂停',
}

export const ROUTINE_RUN_LABEL: Record<RoutineRunStatus, string> = {
  queued: '排队中',
  running: '执行中',
  succeeded: '已完成',
  failed: '失败',
  skipped: '未执行',
}

export const ROUTINE_TRIGGERS: { value: RoutineTrigger; title: string }[] = [
  { value: 'schedule', title: '按时间' },
  { value: 'library_file_added', title: '资料库新增文件时' },
  { value: 'task_closed', title: '任务完成时' },
  { value: 'card_accepted', title: '成果被采纳时' },
]

export const ROUTINE_FREQS = [
  { value: 'daily', title: '每天' },
  { value: 'weekly', title: '每周' },
  { value: 'monthly', title: '每月' },
  { value: 'hourly', title: '每小时' },
]

export const ROUTINE_WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

/**
 * 一个时刻，按规则自己的时区写。
 *
 * 「每周一 09:00（Asia/Shanghai）」旁边那个「下次 …」要对得上：用读者本机的时区写它，
 * 同一个 09:00 会差出半天，看着像规则算错了。
 */
export function formatRoutineTime(iso: string | null, timeZone?: string): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('zh-CN', { timeZone, hour12: false })
  } catch {
    return new Date(iso).toLocaleString()
  }
}
