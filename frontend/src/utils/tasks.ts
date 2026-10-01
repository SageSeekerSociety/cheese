import type { Task } from '@/types'

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
  if (task.deadline != null && task.deadline < now) return { key: 'closed', tone: 'muted' }
  if (task.registrationStartAt != null && task.registrationStartAt > now) return { key: 'notStarted', tone: 'muted' }
  if (task.participantLimit > 0 && task.participants.total >= task.participantLimit)
    return { key: 'full', tone: 'muted' }
  return { key: 'open', tone: 'ok' }
}
