import type { Block } from '../cx_types'

import { noticeText } from './noticeText'

import { t } from '@/i18n'

export interface PlatformErrorPresentation {
  code: string
  title: string
  body: string
  status: string
  retryable: boolean
}

export function platformErrorPresentation(block: Block): PlatformErrorPresentation | null {
  const meta = block.meta
  if (!meta || meta.event_type !== 'platform_error') return null

  const code = typeof meta.code === 'string' && meta.code ? meta.code : 'platform_error'
  const title = noticeText(block, 'title') || t('work.room.notice.incident.title')
  const retryable = meta.retryable === true

  return {
    code,
    title,
    body: noticeText(block),
    status: t(
      code === 'storage_exhausted'
        ? 'work.room.notice.incident.cleaning'
        : code === 'runtime_image_missing'
          ? 'work.room.notice.incident.recovering'
          : retryable
            ? 'work.room.notice.incident.retryable'
            : 'work.room.notice.incident.needsAdmin'
    ),
    retryable,
  }
}
