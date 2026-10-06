// 频道主线上说一件任务到了哪一步，只分四档：讨论中、进行中、待审阅、已完成。看板上
// 那一句（`presentation.phrase`）说得更细，是给要动手的人看的；主线上读的人只想知道
// 这件事还在不在谈、有没有人在做、是不是在等人看、是不是做完了。
import type { Presentation } from '../cx_types'

import { phraseLabel } from './board'

import { t } from '@/i18n'

export type ProgressLevel = 'discussing' | 'running' | 'review' | 'done'

/** 一件任务在主线上属于哪一档。没有呈现（老数据）时按开关状态说：关了就是做完了。 */
export function progressLevel(presentation: Presentation | null | undefined, status?: string): ProgressLevel {
  if (!presentation) return status === 'closed' ? 'done' : 'running'
  if (presentation.column === 'done' || presentation.column === 'archived') return 'done'
  if (presentation.phrase === 'awaiting_review') return 'review'
  if (presentation.column === 'not_started') return 'discussing'
  return 'running'
}

/** 这一档在主线上怎么写：和看板同一套词。 */
export function progressLabel(level: ProgressLevel): string {
  if (level === 'discussing') return phraseLabel('discussing')
  if (level === 'review') return phraseLabel('awaiting_review')
  if (level === 'done') return t('work.room.dispatched.done')
  return t('work.room.dispatched.running')
}
