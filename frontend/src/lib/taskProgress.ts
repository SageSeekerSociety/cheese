// 频道主线上说一件任务到了哪一步，只分五档：讨论中、进行中、待审阅、待处理、已完成。
// 任务列表上那一句（`presentation.phrase`）说得更细，是给要动手的人看的；主线上读的人
// 只想知道这件事还在不在谈、有没有人在做、是不是在等人、是不是做完了。「待处理」是下一
// 步在人手上、又不是等审阅（检查没过、被退回、在等回答）：这时说「进行中」是在说假话。
import type { Presentation } from '../cx_types'

import { columnLabel, phraseLabel } from './board'

import { t } from '@/i18n'

export type ProgressLevel = 'discussing' | 'running' | 'review' | 'waiting' | 'done'

/** 一件任务在主线上属于哪一档。没有呈现（老数据）时按开关状态说：关了就是做完了。 */
export function progressLevel(presentation: Presentation | null | undefined, status?: string): ProgressLevel {
  if (!presentation) return status === 'closed' ? 'done' : 'running'
  if (presentation.column === 'done' || presentation.column === 'archived') return 'done'
  if (presentation.phrase === 'awaiting_review') return 'review'
  if (presentation.column === 'needs_you') return 'waiting'
  if (presentation.column === 'not_started') return 'discussing'
  return 'running'
}

/** 这一档在主线上怎么写：和任务列表同一套词。 */
export function progressLabel(level: ProgressLevel): string {
  if (level === 'discussing') return phraseLabel('discussing')
  if (level === 'review') return phraseLabel('awaiting_review')
  if (level === 'waiting') return columnLabel('needs_you')
  if (level === 'done') return t('work.room.dispatched.done')
  return t('work.room.dispatched.running')
}
