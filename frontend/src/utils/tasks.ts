import type { Task } from '@/types'

import dayjs from 'dayjs'

export type TaskStateKey = 'pending' | 'rejected' | 'closed' | 'notStarted' | 'full' | 'open'

/**
 * 一道题此刻对所有人是什么状态（和我领没领无关）。题目页标题旁那一枚、列表每一行右边那一行
 * 都读它，文案在 `tasks.page.state.<key>`。
 *
 * `participantLimit` 的 0 是「不限」，不是「一个人都不许」。
 */
export function taskState(
  task: Pick<Task, 'approved' | 'deadline' | 'registrationStartAt' | 'participantLimit' | 'participants'>,
  now = Date.now()
): { key: TaskStateKey; tone: 'ok' | 'muted' | 'danger' } {
  if (task.approved === 'NONE') return { key: 'pending', tone: 'muted' }
  if (task.approved === 'DISAPPROVED') return { key: 'rejected', tone: 'danger' }
  if (deadlineState(task.deadline, now)?.passed) return { key: 'closed', tone: 'muted' }
  if (task.registrationStartAt != null && task.registrationStartAt > now) return { key: 'notStarted', tone: 'muted' }
  if (task.participantLimit > 0 && task.participants.total >= task.participantLimit)
    return { key: 'full', tone: 'muted' }
  return { key: 'open', tone: 'ok' }
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
