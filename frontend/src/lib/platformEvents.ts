import type { Block } from '../cx_types'

import { noticeText } from './noticeText'

import { t } from '@/i18n'

/** 事故卡右栏那一个词的档位。`retryable` 是**动作**，不是状态：它只在旁边真有一颗
 *  重试按钮时才画得出来，否则就是一句点不动的话（见 RoomNotice 的事故卡）。 */
export type IncidentStatusKind = 'cleaning' | 'recovering' | 'retryable' | 'needsAdmin'

/** 每一档那句话的键。逐个写出来，`t()` 的键仍是字面量（模板拼的键过不了类型闸门）。 */
const STATUS_KEYS = {
  cleaning: 'work.room.notice.incident.cleaning',
  recovering: 'work.room.notice.incident.recovering',
  retryable: 'work.room.notice.incident.retryable',
  needsAdmin: 'work.room.notice.incident.needsAdmin',
} as const

export interface PlatformErrorPresentation {
  code: string
  title: string
  body: string
  status: string
  statusKind: IncidentStatusKind
  retryable: boolean
}

export function platformErrorPresentation(block: Block): PlatformErrorPresentation | null {
  const meta = block.meta
  if (!meta || meta.event_type !== 'platform_error') return null

  const code = typeof meta.code === 'string' && meta.code ? meta.code : 'platform_error'
  const title = noticeText(block, 'title') || t('work.room.notice.incident.title')
  const retryable = meta.retryable === true
  const statusKind: IncidentStatusKind =
    code === 'storage_exhausted'
      ? 'cleaning'
      : code === 'runtime_image_missing'
        ? 'recovering'
        : retryable
          ? 'retryable'
          : 'needsAdmin'

  return {
    code,
    title,
    body: noticeText(block),
    status: t(STATUS_KEYS[statusKind]),
    statusKind,
    retryable,
  }
}
