import type { Task } from '@/types'

import dayjs from 'dayjs'

export type TaskStateKey =
  | 'pending'
  | 'rejected'
  | 'closed'
  | 'expired'
  | 'notStarted'
  | 'full'
  | 'open'
  | 'openToStart'

/**
 * 一道题此刻对所有人是什么状态（和我领没领无关）。题目页标题旁那一枚、列表每一行右边那一行
 * 都读它，文案在 `tasks.page.state.<key>`。
 *
 * `participantLimit` 的 0 是「不限」，不是「一个人都不许」。开放着的题按开始方式说：
 * 申请制「可申请」，直接开始「可开始」。
 */
export function taskState(
  task: Pick<Task, 'approved' | 'deadline' | 'registrationStartAt' | 'participantLimit' | 'participants' | 'startMode'>,
  now = Date.now()
): { key: TaskStateKey; tone: 'ok' | 'muted' | 'danger' } {
  if (task.approved === 'NONE') return { key: 'pending', tone: 'muted' }
  if (task.approved === 'DISAPPROVED') return { key: 'rejected', tone: 'danger' }
  if (deadlineState(task.deadline, now)?.passed) return { key: 'closed', tone: 'muted' }
  if (task.registrationStartAt != null && task.registrationStartAt > now) return { key: 'notStarted', tone: 'muted' }
  // 名额只被通过了的申请占，和服务端通过时数的是同一个数。
  if (task.participantLimit > 0 && task.participants.approved >= task.participantLimit)
    return { key: 'full', tone: 'muted' }
  return { key: task.startMode === 'DIRECT' ? 'openToStart' : 'open', tone: 'ok' }
}

type TaskForViewer = Parameters<typeof taskState>[0] & Pick<Task, 'submittable' | 'userDeadline' | 'endedAt'>

/**
 * 我手上那份通过了的领取还能不能动：开始做，或者在自己的截止之前接着做、交。自己的截止过了、题目
 * 结束了都不能；直接开始的题，题目的截止是所有人的，过了也不能。申请制的题，题目的截止是申请截止，
 * 只挡申请，不挡通过了的人。只对持有通过了的领取（`submittable`）的人有意义。
 */
export function canStillAct(task: TaskForViewer, now = Date.now()): boolean {
  if (task.endedAt != null) return false
  if (task.userDeadline != null && task.userDeadline <= now) return false
  return task.startMode !== 'DIRECT' || !deadlineState(task.deadline, now)?.passed
}

/**
 * 这道题对正在看的人是什么状态。没有通过了的领取的人，看的是题目对所有人的状态（`taskState`）。
 * 有一份的人只看自己还能不能动（`canStillAct`）：动不了，因为题目结束了或直接开始的题截止了，是
 * 「已截止」；因为过了自己的截止，是「已过期」—— 截止的是这一份领取，不是题目，题目也许还在收别人的
 * 申请。还能动就没有这一枚（`null`）：「可申请」「已截止」说的是题目对别人的状态，他已经过了申请这一步，
 * 旁边那一枚「进行中」/「在做」说的就是他。和服务端列表分组
 * （`TaskRepository._apply_lifecycle_filter` 按看的人算）是同一条判据。
 */
export function taskStateFor(
  task: TaskForViewer,
  now = Date.now()
): { key: TaskStateKey; tone: 'ok' | 'muted' | 'danger' } | null {
  const state = taskState(task, now)
  if (!task.submittable) return state
  if (!canStillAct(task, now)) {
    const ownDeadlinePassed = task.endedAt == null && task.userDeadline != null && task.userDeadline <= now
    return { key: ownDeadlinePassed ? 'expired' : 'closed', tone: 'muted' }
  }
  // 还能动：申请、名额这些已经与他无关，状态由他自己那一份（「进行中」/「在做」）说。
  return null
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
