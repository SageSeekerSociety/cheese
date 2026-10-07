// 支线里回答的那位队友此刻在等什么：主线上那条消息下面那一行写它（「芝士 排队中，前面
// 还有 2 个」「芝士 重试中（第 3 次）」）。读的是支线里最近那一条运行记录，主线从
// `thread_status` 帧收到它。说不出在等什么的，就只写「正在回复」。

import type { Block } from '../cx_types'

import { t } from '@/i18n'

/** 这几种记录说得出队友在等什么；别的（记忆改了、环境休眠了）不改那一行。 */
export const WAITS = new Set(['turn_queued', 'api_retry', 'context_compact', 'device_waiting'])

function eventType(record: Block): string {
  return String(record.meta?.event_type ?? '')
}

/** 这条记录是不是那一轮还没开始时的排队。 */
export function isQueued(record: Block): boolean {
  return eventType(record) === 'turn_queued'
}

/** 那一行在名字后面写的那几个字；这条记录已经说完了（整理完、机器连上了）就是 null。 */
export function threadWaitLabel(record: Block): string | null {
  const meta = record.meta ?? {}
  switch (eventType(record)) {
    case 'turn_queued': {
      const params = (meta.i18n as { content?: { params?: { ahead?: unknown } } } | undefined)?.content?.params
      const ahead = typeof params?.ahead === 'number' ? params.ahead : 0
      return ahead ? t('work.room.site.status.queuedBehind', { ahead }) : t('work.room.site.status.queued')
    }
    case 'api_retry':
      return typeof meta.attempt === 'number'
        ? t('work.room.site.status.retryingCount', { attempt: meta.attempt })
        : t('work.room.site.status.retrying')
    case 'context_compact':
      return meta.state === 'over' ? null : t('work.room.site.status.compacting')
    case 'device_waiting':
      return meta.state === 'over' ? null : t('work.room.site.status.waiting')
    default:
      return null
  }
}
