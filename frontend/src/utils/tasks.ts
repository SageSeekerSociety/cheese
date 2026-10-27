import type { Task } from '@/types'

import dayjs from 'dayjs'

export type TaskStateKey =
  | 'pending'
  | 'rejected'
  | 'ended'
  | 'closed'
  | 'applyClosed'
  | 'notStarted'
  | 'full'
  | 'open'
  | 'openToStart'

/**
 * 一道题此刻是什么状态，对谁都一样（和我领没领无关；Zhifei 2026-10-26）。题目页标题旁那一枚、
 * 列表里我手上没有的那些题右边那一行都读它，文案在 `tasks.page.state.<key>`。我自己那一份走到哪
 * 了另说：题目页在右栏「我的进度」，列表里是 `myClaimState`。
 *
 * 截止过了，申请制的题说「申请已截止」——它的截止是申请截止，通过了的人还按自己的截止做；直接开始
 * 的题只有一个截止，说「已截止」。`participantLimit` 的 0 是「不限」，不是「一个人都不许」。开放着
 * 的题按开始方式说：申请制「可申请」，直接开始「可开始」。
 */
export function taskState(
  task: Pick<
    Task,
    'approved' | 'deadline' | 'registrationStartAt' | 'participantLimit' | 'participants' | 'startMode' | 'endedAt'
  >,
  now = Date.now()
): { key: TaskStateKey; tone: 'ok' | 'muted' | 'danger' } {
  if (task.approved === 'NONE') return { key: 'pending', tone: 'muted' }
  if (task.approved === 'DISAPPROVED') return { key: 'rejected', tone: 'danger' }
  if (task.endedAt != null) return { key: 'ended', tone: 'muted' }
  if (deadlineState(task.deadline, now)?.passed)
    return { key: task.startMode === 'APPLY' ? 'applyClosed' : 'closed', tone: 'muted' }
  if (task.registrationStartAt != null && task.registrationStartAt > now) return { key: 'notStarted', tone: 'muted' }
  // 名额只被通过了的申请占，和服务端通过时数的是同一个数。
  if (task.participantLimit > 0 && task.participants.approved >= task.participantLimit)
    return { key: 'full', tone: 'muted' }
  return { key: task.startMode === 'DIRECT' ? 'openToStart' : 'open', tone: 'ok' }
}

type TaskForViewer = Pick<Task, 'userDeadline' | 'endedAt'>

/**
 * 我手上那份通过了的领取还能不能动：开始做，或者接着做、交。只看我自己的提交截止，两种开始方式
 * 都一样（Zhifei 2026-10-26：「这个人的截止期没到就可以开始」）；题目结束了也不能。题目的截止——
 * 申请截止、直接开始的截止——只挡还没有领取的人，和服务端开始那一步
 * （`claims.refuse_start_after_deadline`）是同一条判据。只对持有通过了的领取的人有意义。
 */
export function canStillAct(task: TaskForViewer, now = Date.now()): boolean {
  if (task.endedAt != null) return false
  return task.userDeadline == null || task.userDeadline > now
}

export type MyClaimStateKey = 'applied' | 'doing' | 'submitted' | 'passed' | 'expired' | 'ended'

/**
 * 列表里我那一份的标签：申请了在等、在做、交了在等批改、通过了、过期了、题目结束了。没领过（或
 * 申请被拒了）是 null。判过通过、交了在等批改的，截止过了也照旧这么说：那不是过期。
 * `myClaimStatus` 只说我本人那条领取；团队题没有它，按还能不能动说。
 */
export function myClaimState(
  task: Pick<Task, 'joined' | 'submittable' | 'userDeadline' | 'endedAt' | 'myClaimStatus'>,
  now = Date.now()
): MyClaimStateKey | null {
  if (!task.joined) return null
  if (!task.submittable) return 'applied'
  if (task.myClaimStatus === 'PASSED') return 'passed'
  if (task.myClaimStatus === 'SUBMITTED') return 'submitted'
  if (canStillAct(task, now)) return 'doing'
  return task.endedAt != null ? 'ended' : 'expired'
}

/**
 * 列表里每道题只写一个截止日期，并且说清是哪一个（Zhifei 2026-10-26）：我手上有一份通过了的领取，
 * 写我自己的提交截止（`submit`，和题目页「我的进度」同一天）；没有的，申请制的题写申请截止
 * （`apply`），直接开始的题写题目的截止（`task`）。`at` 为 null 是不限截止。
 */
export function listDeadline(task: Pick<Task, 'startMode' | 'deadline' | 'joined' | 'submittable' | 'userDeadline'>): {
  kind: 'apply' | 'submit' | 'task'
  at: number | null
} {
  if (task.joined && task.submittable) return { kind: 'submit', at: task.userDeadline ?? task.deadline ?? null }
  return { kind: task.startMode === 'APPLY' ? 'apply' : 'task', at: task.deadline ?? null }
}

/**
 * 一个截止时刻此刻是什么情形。过没过只按这一刻比：`at < now` 就是过了，和服务端
 * `past_deadline`（截止之后不收新的一版）是同一条判据；所以「我的进度」和交作业表单
 * 说的永远是同一件事，不会一边「今天截止」一边「已截止」。
 *
 * `days` 是按看的人自己的日历隔了几天（不是除以 24 小时再取整）：0 是今天，
 * 过了就是几天前，没过就是几天后。
 */
export function deadlineState(
  at: number | null | undefined,
  now = Date.now()
): { passed: boolean; days: number } | null {
  if (at == null) return null
  const days = Math.abs(dayjs(at).startOf('day').diff(dayjs(now).startOf('day'), 'day'))
  return { passed: at < now, days }
}
